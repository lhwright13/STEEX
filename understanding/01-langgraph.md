# 1. How We Use LangGraph

This document explains how [LangGraph](https://langchain-ai.github.io/langgraph/) is wired into STEEX, the core syntax we rely on, and how to add a new agent to the multi-agent system.

---

## 1.1 What LangGraph is doing for us

LangGraph is a library for building **stateful, graph-structured agent workflows**. Instead of hard-coding a `for` loop that calls agents one after another (which is what STEEX used before commit `105e5df`), we describe the pipeline as a **directed graph of nodes and edges**, hand it to LangGraph, and let LangGraph drive execution — including running several nodes in parallel and merging their results.

The three pieces LangGraph gives us:

| Concept | What it is in STEEX | Where |
|---|---|---|
| **State** | A `TypedDict` (`PipelineState`) that flows through the whole run. Every node reads it and returns a partial update. | [src/agents/state.py](../src/agents/state.py) |
| **Graph** | A `StateGraph` we build dynamically from the mode's config (which agents, in what order, which run in parallel). | [src/agents/graph.py](../src/agents/graph.py) |
| **Nodes** | Functions that do work — almost all of them call a sub-agent via the Claude CLI and return their conclusion + trace. | [src/agents/nodes.py](../src/agents/nodes.py) |

> **Important nuance:** The *individual agents* are **not** LangChain/LangGraph LLM objects. Each agent node shells out to the `claude` CLI as a subprocess ([nodes.py:163](../src/agents/nodes.py#L219)). LangGraph orchestrates the *flow between agents*; the Claude CLI runs the *agentic loop inside each agent*. This is unusual but deliberate — it lets each agent use the full Claude Code tool-use loop with MCP, while LangGraph handles fan-out/fan-in and conditional routing between agents.

---

## 1.2 The state object

Everything that passes between nodes lives in one typed dict ([state.py:32](../src/agents/state.py#L32)):

```python
class PipelineState(TypedDict):
    mode: str
    task_context: str
    today: str
    run_id: str
    conclusions: dict[str, dict]                       # name -> agent conclusion
    variant_conclusions: Annotated[list[dict], add]    # parallel writes
    traces: Annotated[list[dict], add]                 # parallel writes
    manager_decision: Optional[dict]
    screen_data: Optional[dict]
    abort: bool
    abort_reason: Optional[str]
```

Each node returns a **partial dict** and LangGraph merges it into the running state. For most keys, a returned value overwrites the old one. But two keys — `variant_conclusions` and `traces` — are special:

### The `Annotated[list, add]` reducer (the thing that broke and got fixed)

When three analysis variants run **in parallel in the same superstep**, they all try to write to `traces` at once. With a normal channel, the second concurrent write raises `InvalidUpdateError`. We annotate those keys with the `add` reducer (`from operator import add`) so LangGraph **concatenates** the lists instead of overwriting:

```python
variant_conclusions: Annotated[list[dict], add]
traces: Annotated[list[dict], add]
```

**Rule for node authors:** a parallel node must return *only its own new entry* — `return {"traces": [my_trace]}` — never the accumulated list, or entries get duplicated. This is documented inline at [state.py:38-44](../src/agents/state.py#L38-L44) and was the root cause fixed in commit `cffe950` ("Fix parallel-variant pipeline crashes").

---

## 1.3 How the graph is built

The graph is **not static** — `build_graph()` constructs it at runtime from the `ModeConfig` for whichever mode is running ([graph.py:35](../src/agents/graph.py#L35)):

```python
def build_graph(mode, mode_config, ctx, format_conclusions_fn, fallback_fn):
    graph = StateGraph(PipelineState)        # 1. create graph over our state type
    # 2. add nodes from mode_config (sub_agents, parallel_agents, manager, ...)
    # 3. add edges (sequential, conditional, fan-out, fan-in)
    # 4. set entry point
    return graph.compile()                   # 5. compile to an executable graph
```

### Core LangGraph syntax we use

```python
from langgraph.graph import StateGraph, END
from langgraph.types import Send

graph = StateGraph(PipelineState)            # graph typed over our state

graph.add_node("risk", risk_node_fn)         # register a node
graph.add_edge("data", "risk")               # unconditional edge: data -> risk
graph.set_entry_point("data")                # where execution starts

# conditional edge: run a router fn, map its return string to a destination
graph.add_conditional_edges(
    "risk",
    route_after_agent,                       # returns "continue" or "fallback"
    {"continue": "fan_out", "fallback": "fallback"},
)

graph.add_edge("report", END)                # terminal edge
```

### Fan-out / fan-in (the parallel variants)

This is the most interesting part. The `fan_out` node returns a **list of `Send` objects**, one per variant. Each `Send` schedules that node to run with a copy of the state, and LangGraph runs them concurrently:

```python
# fan-out node ([nodes.py], make_fan_out_node)
def node(state):
    return [Send(agent_name, state) for agent_name in parallel_agents]
```

All three variant nodes write into `variant_conclusions` (via the `add` reducer). The **`merge_variants`** node then runs *after all of them complete* (fan-in), reads every variant's conclusion, and asks the `meta_analysis` agent to synthesize a consensus.

---

## 1.4 The end-to-end graph for `screen` mode

`screen` mode exercises every feature. Its config ([agents.yaml:180](../config/agents.yaml#L180)):

```yaml
screen:
  sub_agents: [data, risk]
  parallel_agents: [analysis_conservative, analysis_aggressive, analysis_momentum]
  meta_agent: meta_analysis
  manager: manager
  critical_agents: [risk]
  post_actions: [save_screen, report]
  fallback: screen
```

Produces this graph:

```
                                  ┌─> analysis_conservative ─┐
data ─> risk ──(critical?)──> fan_out ─> analysis_aggressive ─┼─> merge_variants ─> manager ─> save_screen ─> report ─> END
            │                     └─> analysis_momentum ─────┘        (meta_analysis)
            └──(risk failed)──> fallback ─> END
```

- **`data` → `risk`**: sequential sub-agents.
- **`risk` is critical**: if it fails, a conditional edge routes to the deterministic `fallback` instead of continuing. (`route_after_agent` returns `"fallback"` when `state["abort"]` is set.)
- **`fan_out`**: emits three `Send`s → the variants run in parallel.
- **`merge_variants`**: fan-in; runs the `meta_analysis` agent on all variant conclusions.
- **`manager`**: synthesizes the final decision.
- **post-actions** (`save_screen`, `report`): run at the end.

Other modes are simpler — `monitor` is just `risk → manager → (maybe) execution → report`. The same `build_graph()` produces all of them from config.

---

## 1.5 How a node actually runs an agent

Almost every node ends up calling `run_agent()` ([nodes.py:163](../src/agents/nodes.py#L163)), which:

1. Finds the `claude` binary.
2. Builds a CLI command with the system prompt + task message, `--output-format json`, and `--max-turns`.
3. If the agent `needs_tools`, attaches `--mcp-config <temp file>` and an `--allowedTools` allowlist (`mcp__steex__run_screening`, etc.).
4. Runs it as a subprocess with a timeout.
5. Parses the JSON envelope, extracts tool calls into the trace, and validates the agent's text output against its Pydantic conclusion model.

```python
cmd = [
    claude_bin,
    "-p", f"{system_prompt}\n\n---\n\n{task_message}",
    "--output-format", "json",
    "--max-turns", str(max_turns),
]
if needs_tools:
    cmd.extend(["--mcp-config", get_mcp_config(ctx)])
    tool_perms = [f"mcp__steex__{t}" for t in allowed_tools] or ["mcp__steex__*"]
    cmd.extend(["--allowedTools", ",".join(tool_perms)])
```

So **LangGraph node → Claude CLI subprocess → MCP tools** is the full chain. (Tools and MCP are covered in detail in [05-cron-tools-mcp.md](05-cron-tools-mcp.md).)

---

## 1.6 Adding a new agent — step by step

The system is **declarative**: agents are defined in YAML, not code. Adding one touches four places.

### Step 1 — Define the agent in [config/agents.yaml](../config/agents.yaml)

```yaml
agents:
  sentiment_scout:                 # new agent
    prompt: "sentiment_scout"      # prompt key (see step 3)
    conclusion: "SentimentConclusion"   # Pydantic model name (step 2)
    max_turns: 12
    needs_tools: true
    allowed_tools:                 # restrict to only the MCP tools it needs
      - get_signal_confidence
      - run_screening_variant
    external_servers: [alphavantage]
```

### Step 2 — Add its conclusion model in [src/agents/conclusions.py](../src/agents/conclusions.py)

Every agent must return a structured, validated result. The model name must match `conclusion:` above.

```python
class SentimentConclusion(BaseModel):
    bullish_tickers: List[str]
    bearish_tickers: List[str]
    market_mood: str
    reasoning: str
```

The registry resolves this by name via `resolve_conclusion_type()` ([registry.py:147](../src/agents/registry.py#L147)).

### Step 3 — Write the prompt

Two options, resolved in this order by the registry:

1. **Disk override (preferred for iteration):** `data/agents/prompts/sentiment_scout.md`
2. **Code default:** `src/agents/prompts/sentiment_scout.py` exporting `SENTIMENT_SCOUT_AGENT_PROMPT`

The prompt should describe the agent's job, the tools it may call, and instruct it to **output its conclusion as JSON** matching the Pydantic model.

### Step 4 — Wire it into a mode in [config/agents.yaml](../config/agents.yaml)

Add it to a mode's `sub_agents` (sequential), `parallel_agents` (fan-out), or as a `meta_agent` / `manager` / `executor`:

```yaml
modes:
  screen:
    sub_agents: [data, risk, sentiment_scout]   # runs after risk, before fan_out
    parallel_agents: [analysis_conservative, analysis_aggressive, analysis_momentum]
    ...
```

**No graph code changes are needed** — `build_graph()` reads the mode config and wires the node automatically. If the new agent should be able to abort the run on failure, add it to `critical_agents`.

### Adding a new *parallel variant* specifically

Variants reuse `AnalysisConclusion` and differ only by prompt + screening parameter presets:

1. Add the agent (e.g. `analysis_value`) with `conclusion: "AnalysisConclusion"`.
2. Add a prompt that frames the strategy.
3. Add a preset to `VARIANT_PARAMS` in [mcp_server.py:74](../src/agents/mcp_server.py#L74) so `run_screening_variant("value")` works.
4. Append it to `parallel_agents` in the mode. The fan-out picks it up automatically.

---

## 1.7 Things to know / gotchas

- **State must be JSON-serializable.** It flows through LangGraph and is also what LangSmith would trace. Conclusions are stored as `.model_dump()` dicts, not Pydantic objects.
- **Parallel nodes return only their own slice** of `variant_conclusions` / `traces`. Returning the whole list duplicates entries.
- **`max_turns` is per-agent** and set in YAML — it caps the Claude CLI tool-use loop length, not the LangGraph graph.
- **Critical agents gate the pipeline.** A failed critical agent (e.g. `risk`) routes the entire run to the deterministic `fallback` rather than producing a half-formed decision.
- **The graph is rebuilt every run** from config. Changing `agents.yaml` changes the topology with no redeploy.
