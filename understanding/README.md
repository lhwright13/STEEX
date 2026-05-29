# Understanding STEEX

A set of explainer documents for how the STEEX multi-agent trading system works, plus proposals for improving it. Written against the codebase as of branch `fix/langgraph-parallel-traces`.

| # | Document | Covers |
|---|---|---|
| 1 | [LangGraph](01-langgraph.md) | How LangGraph is integrated, the state object, graph construction, fan-out/fan-in syntax, and **how to add a new agent**. |
| 2 | [Evaluation](02-evaluation.md) | Current scoring + learning loop, the gap in *agent* evaluation, proposed metrics/process, and the tie to observability. |
| 3 | [Memory](03-memory.md) | What persists today across tools/agents/runs, and a staged plan for **persistent shared context** (checkpointing, MCP memory tools, context briefs). |
| 4 | [Orchestration Pattern](04-orchestration-pattern.md) | The supervisor + ensemble + fallback pattern, why it was chosen, and alternative patterns. |
| 5 | [Cron, Tools & MCP](05-cron-tools-mcp.md) | End-to-end cron flow, the agent reasoning loop, how tools are implemented, what MCP is, adding a tool, and **tool vs. skill vs. MCP**. |

## The one-paragraph mental model

A cron job fires `scheduler/run.sh`, which (after market/lock gates) calls `run_manager.py`, which builds a **LangGraph** graph from `config/agents.yaml` for the requested mode. LangGraph orchestrates the flow *between* agents — running prep agents sequentially, fanning out three analysis variants in parallel, merging them via a meta-agent, and handing everything to a supervisor `manager` that makes the final trade decision. Each agent is itself a **Claude CLI subprocess** running its own tool-use loop, where every tool is delivered over **MCP** by our `steex` server (wrapping the real trading code). If a critical agent fails, the graph falls back to deterministic rules-based logic. Results are traced to `data/agents/sessions/`, surfaced on a dashboard, and a weekly **learning loop** post-mortems the trades and re-tunes the scoring weights.
