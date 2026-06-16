# 5. Cron Jobs, Tools, and MCP

This document traces the end-to-end flow of a scheduled (cron) run, what each agent's reasoning loop looks like, how tools are implemented, how MCP is used, a worked example of adding a tool, and finally a clear explanation of **tool vs. skill vs. MCP**.

---

## 5.1 End-to-end cron flow

STEEX runs unattended on cron. Nothing about the orchestration is special to cron — cron just invokes the same entry points a human would. The full chain:

```
crontab  ──fires──>  scheduler/run.sh <mode>
                          │
                          ├─ source profile (load API keys)
                          ├─ market_gate.py <mode>      ── should we run now? (market open / day?)
                          ├─ acquire lockfile           ── prevent overlapping runs
                          ├─ ingest_run.py --start      ── record run START in dashboard.db
                          │
                          ├─ route by mode:
                          │     heartbeat  -> health_check.py
                          │     learning   -> run_learning.py   (the 6-phase loop, doc 02)
                          │     *          -> run_manager.py <mode>
                          │
                          ├─ ingest_run.py --finish     ── record FINISH + report in dashboard.db
                          └─ prune logs > 30 days
```

### The schedule

[scheduler/config.yaml](../scheduler/config.yaml) defines each mode's cron expression (in PST). [scheduler/install.sh](../scheduler/install.sh) reads that YAML and writes idempotent crontab entries. Representative day:

| Mode | Time (ET) | Purpose |
|---|---|---|
| `heartbeat` | 7:00 AM | liveness/health check |
| `screen` | 8:15 AM | pre-open: build the buy list (the big ensemble run) |
| `enter` | 9:45 AM | post-open: place approved entries |
| `monitor` | 11:00 AM, 1:30 PM | midday: exits/risk |
| `post_market` | 4:30 PM | EOD: exits + research + report |
| `learning` | Fri 6:00 PM | weekly self-optimization |

### The gates that keep cron safe

- **Market gate** ([scripts/market_gate.py](../scripts/market_gate.py)) — `enter`/`monitor` require the market *open*; `screen` requires a market *day*; `heartbeat`/`learning` always run. If Alpaca is unreachable it defaults to "run anyway."
- **Lockfile** — a second run of the same mode won't start while one is in flight.
- **Dashboard ingestion** ([scripts/ingest_run.py](../scripts/ingest_run.py)) — start/finish are recorded to `data/dashboard.db` so the dashboard ([02-evaluation.md](02-evaluation.md)) can show run history, status, and reports.

### From `run_manager.py` into the agents

[scripts/run_manager.py](../scripts/run_manager.py) constructs the `Orchestrator` and calls `run_mode(mode)`, which builds the LangGraph graph and invokes it ([01-langgraph.md](01-langgraph.md)). So:

```
run.sh -> run_manager.py -> Orchestrator.run_mode -> build_graph -> graph.invoke -> per-agent nodes -> claude CLI subprocess -> MCP tools
```

---

## 5.2 What an agent's reasoning loop looks like

Each agent node calls `run_agent()` ([nodes.py:163](../src/agents/nodes.py#L163)), which launches the **`claude` CLI as a subprocess** with the agent's system prompt, its task, a tool allowlist, and a turn cap:

```python
cmd = [
    claude_bin,
    "-p", f"{system_prompt}\n\n---\n\n{task_message}",
    "--output-format", "json",
    "--max-turns", str(max_turns),
]
if needs_tools:
    cmd.extend(["--mcp-config", get_mcp_config(ctx)])           # which MCP servers
    tool_perms = [f"mcp__steex__{t}" for t in allowed_tools]    # which tools allowed
    cmd.extend(["--allowedTools", ",".join(tool_perms)])
```

Inside that subprocess, Claude runs its **standard agentic tool-use loop**: read prompt → decide to call a tool → receive the tool result → reason → call another tool → … → emit a final answer. The loop is bounded by `--max-turns` (per-agent, from `agents.yaml`).

When it finishes, STEEX:
1. Parses the JSON envelope from stdout.
2. Extracts every tool call into the trace ([nodes.py:267](../src/agents/nodes.py#L267)).
3. Validates the final text against the agent's Pydantic conclusion model ([nodes.py:273](../src/agents/nodes.py#L273)).
4. On any failure (bad exit code, unparseable output), writes a `data/agents/failures/*.log` and returns `None` — which, for a critical agent, triggers the deterministic fallback ([04-orchestration-pattern.md](04-orchestration-pattern.md)).

So there are **two nested loops**: LangGraph's outer graph (agent → agent) and Claude's inner tool-use loop (tool → tool) inside each agent.

---

## 5.3 The tools and how they're implemented

All STEEX tools live in **one FastMCP server**, [src/agents/mcp_server.py](../src/agents/mcp_server.py). Each tool is a Python function decorated with `@mcp.tool()` that wraps an existing `QuantManager` method and returns JSON:

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("steex")

@mcp.tool()
def get_regime() -> str:
    """Detect current market regime (risk_on / cautious / risk_off / crisis)."""
    mgr = _init_manager()          # lazy-init QuantManager on first call
    _regime = mgr.get_regime()
    return _safe_json(_regime)
```

Key implementation facts:
- **The server runs as a stdio subprocess** started by the Claude CLI — never invoked directly. The CLI speaks JSON-RPC to it over stdin/stdout, which is why rich console output is redirected to stderr ([mcp_server.py:32](../src/agents/mcp_server.py#L32)).
- **Lazy init** — `_init_manager()` ([mcp_server.py:125](../src/agents/mcp_server.py#L125)) builds the `QuantManager` (and connects to the broker) only on the first tool call, so starting the server is cheap.
- **Per-call config** — `--paper` / `--dry-run` flags passed in the MCP config decide whether the broker is paper/live and whether orders are simulated.
- **~40 tools**, grouped: data (`prefetch_data`, `refresh_data`), risk (`get_regime`, `assess_portfolio_risk`, `get_exit_signals`), analysis (`run_screening`, `run_screening_variant`, `rank_candidates_with_weights`), execution (`execute_entries`, `execute_exits`), research/learning (`run_postmortem`, `check_alpha_decay`, `get_current_weights`, `propose_config_changes`, `apply_config_changes`), notification (`send_user_message`), and reporting.
- **Per-agent allowlist** — each agent's `allowed_tools` in [agents.yaml](../config/agents.yaml) restricts what it can touch (e.g. only `execution` may place orders).

### External MCP servers

Besides our own `steex` server, the MCP config ([nodes.py](../src/agents/nodes.py), `get_mcp_config`) can attach third-party MCP servers for market data — **alpaca** (quotes/orders), **alphavantage** (indicators), **polygon** (aggregates/news) — toggled in [config.yaml](../config/config.yaml). An agent is granted them via `external_servers` in its YAML.

---

## 5.4 What MCP is

**MCP (Model Context Protocol)** is an open standard for connecting an LLM to external capabilities. An **MCP server** advertises a set of *tools* (functions the model can call), *resources* (readable data), and *prompts*; an **MCP client** (here, the Claude CLI) discovers them and routes the model's tool calls to the right server. It's "USB-C for AI tools" — write the capability once as an MCP server, and any MCP-aware client can use it.

In STEEX, MCP is the bridge between the LLM agents and our trading code:

```
Claude CLI (MCP client)
   ├── steex server (our 40 tools wrapping QuantManager)   [stdio subprocess]
   ├── alpaca server (quotes, orders)
   ├── alphavantage server (indicators)
   └── polygon server (aggregates, news)
```

The agent never imports our Python directly — it asks (via MCP) for `run_screening`, the CLI forwards that to our server, our server runs the real screening pipeline and returns JSON. This keeps the LLM sandboxed behind an explicit, allowlisted interface — exactly what you want when the "function" can place a real trade.

---

## 5.5 Worked example: adding a new tool

Suppose the `risk` agent should be able to check sector concentration. Add one decorated function to [src/agents/mcp_server.py](../src/agents/mcp_server.py):

```python
@mcp.tool()
def get_sector_exposure() -> str:
    """Return current portfolio exposure broken down by sector, as a fraction
    of total equity. Use this to check concentration before approving new buys.

    Returns: {sector: weight_pct} plus the most-concentrated sector.
    """
    mgr = _init_manager()
    positions = mgr.position_manager.get_all_positions()
    equity = mgr._get_portfolio_value()

    by_sector: dict[str, float] = {}
    for pos in positions:
        sector = mgr.get_sector(pos.ticker)          # existing helper
        by_sector[sector] = by_sector.get(sector, 0.0) + pos.market_value

    weights = {s: round(v / equity, 4) for s, v in by_sector.items()}
    top = max(weights, key=weights.get) if weights else None
    return _safe_json({"by_sector": weights, "most_concentrated": top})
```

Then grant it to the agent in [config/agents.yaml](../config/agents.yaml):

```yaml
risk:
  allowed_tools:
    - sync_broker
    - get_regime
    - assess_portfolio_risk
    - get_sector_exposure      # <-- new
```

That's it. The docstring becomes the tool's description the model sees (so write it for the model), the return must be JSON-serializable (use `_safe_json`), and the allowlist entry is what actually exposes it — without it, the tool exists but the agent can't call it. Mention the tool in the agent's prompt so it knows when to reach for it.

---

## 5.6 Tool vs. Skill vs. MCP — what's the difference?

These are often confused because they overlap. The cleanest way to think about it:

| | **Tool** | **Skill** | **MCP** |
|---|---|---|---|
| **What it is** | A single callable function the model can invoke (name + description + input schema). | A packaged bundle of instructions/knowledge (and often scripts) that teaches the model *how* to do a task. | A *protocol/transport* for delivering tools (and resources/prompts) from a server to a client. |
| **Granularity** | One action (`get_regime`). | A whole workflow ("how to run a security review"). | A connection that can expose many tools. |
| **Form** | A function with a schema. | A folder of markdown + optional code, loaded into context when relevant. | A running server speaking JSON-RPC. |
| **In STEEX** | `run_screening`, `execute_entries`, etc. | The agent *prompts* in `src/agents/prompts/` play a skill-like role (procedural know-how), though they're not formal Claude "skills." | The `steex` FastMCP server + alpaca/polygon/alphavantage. |
| **Analogy** | A verb. | A playbook. | The plumbing that delivers the verbs. |

Put differently:

- A **tool** is *capability* — "you can do X." It's an action with a contract.
- A **skill** is *procedure* — "here's how and when to do X well." It's knowledge that shapes behavior; it may *use* tools but doesn't add new ones.
- **MCP** is *delivery* — the standard way a tool gets from a server to the model. A tool can be delivered via MCP, or built directly into a client; MCP is just the most portable option.

### Are there better ways to give agents abilities?

It depends on the ability:

- **For actions (do something in the world):** tools are the right primitive, and **MCP is the best way to deliver them** when you want reuse, sandboxing, and an explicit allowlist — exactly our case (the boundary that stops an agent from placing an unintended trade is worth a lot). Direct function-calling (no MCP) is simpler for a single app but doesn't give the clean server boundary, multi-client reuse, or per-agent allowlisting we rely on.
- **For know-how (do something *well*):** a **skill** (or, today, a good prompt) beats adding more tools. If the `manager` keeps mis-weighting consensus, the fix is better procedural guidance, not a new tool.
- **For read-only context:** MCP **resources** (which we don't use yet) are a lighter-weight option than wrapping every data read in a tool — worth considering for things like "current positions" that agents only read.

For STEEX specifically: keep **MCP-delivered tools** for everything that touches the broker or runs the pipeline (the safety boundary is the whole point), lean on **prompts/skills** for reasoning quality, and consider **MCP resources** for pure-read context to shrink the tool surface. This connects to the memory plan in [03-memory.md](03-memory.md), where `recall`/`remember` are proposed as MCP tools precisely so memory access is explicit and shows up in traces.
