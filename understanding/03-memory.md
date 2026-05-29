# 3. Memory Strategies — Current State and Proposed Upgrades

This document maps what STEEX persists today, what is ephemeral, and how to build **persistent context that survives across sessions and is shared across agents**.

---

## 3.1 What we have today

STEEX has memory, but it's a patchwork of files with different lifetimes and no shared "what do we know" layer that agents read at the start of a run.

### Tier 1 — In-run state (ephemeral)

The LangGraph `PipelineState` ([state.py:32](../src/agents/state.py#L32)) carries everything *during* a single mode run and is discarded when the graph finishes. Within an agent, the MCP server also caches intermediate results between tool calls via module globals (`_pipeline_result`, `_ranked`, `_regime` — [mcp_server.py:66](../src/agents/mcp_server.py#L66)). Both vanish at run end.

> **There is no LangGraph checkpointer.** `graph.compile()` is called with no `checkpointer=`, so LangGraph itself persists nothing between runs.

### Tier 2 — Per-run traces (30-day retention)

Every run writes a full `AgentSession` to `data/agents/sessions/{date}_{mode}_{time}.json` plus a `latest.json` pointer ([trace.py](../src/agents/trace.py)). This captures each agent's tools, output, conclusion, success, and timing. Pruned after 30 days. This is the richest record we keep — but it's **write-only from the agents' perspective**: agents don't read past sessions when they run.

### Tier 3 — Domain state (indefinite, file-based)

- `data/positions.json`, `data/trades.json`, `data/execution_records.json` — portfolio source-of-truth (synced from Alpaca).
- `data/screen_results/latest.json` — the buy list `screen` produces, which `enter` later reads via `load_screen` → `load_screen_results`. **This is the one real cross-session, cross-agent handoff today.**
- `data/cache.db` (SQLite, ~60 MB) — price/fundamental/sentiment/options cache with per-type TTLs.

### Tier 4 — Long-term learning knowledge (indefinite)

The learning loop's journal is the closest thing to durable institutional memory:

- `data/learning/learning_journal.json` — log of every learning action.
- `data/learning/config_history.json` — audit trail of weight changes.
- `data/learning/gaps.json` — unresolved knowledge gaps.
- `data/dashboard.db` — run metadata + report content ([ingest_run.py](../scripts/ingest_run.py)).

### Tier 5 — Prompt memory (disabled)

`evolve_prompts` can write improved prompts to `data/agents/prompts/{agent}.md` based on agent self-suggestions — a form of long-term procedural memory. It's **off by default** (`evolution_enabled: false`).

### Summary of what crosses boundaries today

| Boundary | What carries over | Mechanism |
|---|---|---|
| Tool → tool (same agent) | intermediate results | MCP module globals |
| Agent → agent (same run) | conclusions | `PipelineState.conclusions` |
| `screen` run → `enter` run | the buy list | `screen_results/latest.json` |
| Run → run (general context) | **nothing** | — |
| Learning cycle → strategy | weights, gaps | config files |

The big hole: **agents start every run with a blank slate.** The `monitor` agent at 1:30pm has no memory of what the `monitor` agent at 11:00am concluded, except indirectly through portfolio state.

---

## 3.2 Goal: persistent, shared context across sessions and agents

We want a layer that answers, at the start of any agent's run: *"What has the system already learned, decided, or noticed that's relevant to me right now?"* — and that any agent can append to.

### Option A — Turn on LangGraph checkpointing (lowest effort, biggest immediate win)

LangGraph has first-class persistence. Compile the graph with a checkpointer backed by SQLite (or Postgres):

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("data/checkpoints.db")
graph = build_graph(...).compile(checkpointer=checkpointer)

# invoke with a stable thread_id so state is keyed and resumable
final_state = graph.invoke(initial_state, config={"configurable": {"thread_id": f"{mode}-{today}"}})
```

This gives us:
- **Resumable runs** — if `enter` crashes after `risk` but before `manager`, we can resume instead of restarting.
- **Cross-run threads** — keying by `thread_id` (e.g. all of today's `monitor` runs) lets a run see the prior run's final state.
- **Time-travel debugging** — replay any superstep.

This is the natural, idiomatic fix and directly addresses "persistent context between sessions." See [01-langgraph.md](01-langgraph.md) for where `compile()` is called.

### Option B — A shared "memory store" exposed as MCP tools (best for cross-agent semantic memory)

Add a small persistent store and expose it as tools every agent can use — read at the start, write at the end. Because agents already reach the outside world through MCP ([05-cron-tools-mcp.md](05-cron-tools-mcp.md)), this fits the existing architecture cleanly:

```python
@mcp.tool()
def recall_memory(topic: str, limit: int = 5) -> str:
    """Retrieve relevant facts the system has learned (regimes seen, recurring
    losers, prior decisions). Call this at the start of your reasoning."""
    rows = memory_store.search(topic, limit)   # keyword or vector search
    return _safe_json(rows)

@mcp.tool()
def remember(fact: str, kind: str, tickers: list[str] | None = None) -> str:
    """Persist a durable observation for future runs and other agents."""
    memory_store.add(fact=fact, kind=kind, tickers=tickers, ts=now())
    return _safe_json({"stored": True})
```

Back it with:
- **Structured rows in SQLite** for facts/decisions (queryable, auditable) — reuse `dashboard.db` or a new `memory.db`.
- **A vector index** (e.g. `sqlite-vec`, Chroma, or FAISS) if we want semantic recall ("have we seen this regime + sector combo lose before?").

LangGraph also ships a [`Store`](https://langchain-ai.github.io/langgraph/concepts/persistence/#memory-store) abstraction for exactly this cross-thread, namespaced long-term memory — usable directly if we lean into LangGraph.

### Option C — A pre-run "context brief" node (cheap glue, high leverage)

Add a node at the front of each mode that assembles a compact brief from existing artifacts — recent `latest.json` sessions, open `gaps.json`, recent `config_history.json` changes, current regime — and injects it into `task_context`. No new storage; just stop throwing away what we already write. This can ship today and pairs well with A or B.

---

## 3.3 Recommended path

1. **Now:** Add Option C (context brief) — immediate, no new infra, makes agents aware of recent history.
2. **Next:** Add Option A (SQLite checkpointer) — resumability + run-to-run threads, idiomatic LangGraph.
3. **Then:** Add Option B (MCP memory tools, vector-backed) — true shared semantic memory across agents and time, with explicit `recall`/`remember` so memory writes are visible in traces (and therefore evaluable per [02-evaluation.md](02-evaluation.md)).

### Design notes
- **Make memory writes appear in traces.** If `remember` is an MCP tool, every write shows up in the session trace — observable and auditable, consistent with how we already log tool calls.
- **Scope memory by kind** (regime observations, recurring losers, prior manager decisions, learning gaps) so `recall_memory(topic)` stays relevant and small — context budget matters.
- **Set TTLs / decay.** Markets change; a "recurring loser" from 18 months ago shouldn't outvote current signals. Mirror the learning loop's alpha-decay philosophy.
- **Keep the strategy learning loop as the curator.** The weekly cycle ([02-evaluation.md](02-evaluation.md)) is the right place to *promote* transient observations into durable memory and to *expire* stale ones.
