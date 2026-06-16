# 2. Evaluation — Current State and Proposed Upgrades

This document covers how STEEX evaluates itself today, then proposes a richer evaluation + metrics process and how it should tie into observability.

A key thing to understand up front: STEEX has **two different notions of "evaluation"** that are easy to conflate.

1. **Strategy evaluation** — *is the trading strategy making money?* Measured over **real completed trades** by post-mortems, per-signal alpha-decay, and the weekly learning loop. (Synthetic backtesting was removed — evaluation is grounded in the live trade record, not simulation.)
2. **Agent evaluation** — *are the agents themselves behaving well?* (Did the agent pick good tools, return valid output, not time out, reason soundly?) This is **largely absent** today and is where the biggest opportunity is.

---

## 2.1 Current strategy evaluation

### Composite score — how candidates are graded

Stocks are ranked by a weighted composite score (0–100) over six signals ([ranking.py:87](../src/strategy/ranking.py#L87)):

```python
score = (w_momentum  * momentum_score
       + w_insider    * insider_score
       + w_volume     * volume_score
       + w_sentiment  * sentiment_score
       + w_fundamental* fundamental_score
       + w_options    * options_score)
```

Weights live in [config/config.yaml:81](../config/config.yaml#L81) and (after commit `cffe950`) sum to exactly 1.0:

| Weight | Value |
|---|---|
| momentum | 0.2804 |
| insider | 0.2336 |
| volume | 0.1402 |
| sentiment | 0.1402 |
| fundamental | 0.0935 |
| options | 0.1121 |

Invariants enforced by the config writer: weights sum to 1.0, momentum stays dominant (>0.25), options > fundamental.

### Trade metrics

[src/portfolio/tracker.py](../src/portfolio/tracker.py) (`TradeTracker`) is the system of
record for *real* closed trades: win rate, profit factor, avg winner/loser, max drawdown,
hold days, and benchmark alpha — all computed from actual fills, not a simulator.

### The weekly learning loop (observe-only)

[scripts/run_learning.py](../scripts/run_learning.py) → [src/learning/loop.py](../src/learning/loop.py)
runs a deterministic, **observe-only** cycle weekly over real completed trades. It does
**not** self-tune config:

1. **Post-mortem** — analyzes closed trades; classifies losses (`bad_signal`, `bad_timing`, `bad_regime`, `bad_luck`); computes **score↔return correlation** (if < 0.10, the signals aren't predictive → flags a research gap).
2. **Alpha decay** — rolling-window hit rate per signal against the live trade record; flags a signal "degrading" if win rate drops >15% vs baseline ([alpha_monitor.py](../src/research/alpha_monitor.py)).
3. **Gap identification** — flags what it *couldn't* resolve (too little data, degrading signal, dominant loss category) into [data/learning/gaps.json](../data/learning/gaps.json) for human review.

**Parameter changes are the learning *agent's* job** (`propose_config_changes` /
`apply_config_changes`), not the loop's — bounded by the deterministic guardrails in
[config_writer.py](../src/learning/config_writer.py) (`PARAM_BOUNDS`: caps change/cycle,
re-normalizes weights to 1.0, blocks writes during market hours, writes an audit trail).
Promotion is grounded in the *real* trade evidence above, validated by paper-trading the
change — there is no synthetic walk-forward/OOS backtest (that stack was removed).

### Where results are stored

| File | Contents |
|---|---|
| `data/learning/learning_journal.json` | Timestamped log of every learning action |
| `data/learning/weight_recommendations.json` | Latest agent-proposed weights + rationale |
| `data/learning/config_history.json` | Audit trail of applied param changes (old→new, reason) |
| `data/learning/gaps.json` | Unresolved gaps for human review |
| `data/reports/report_*.json` | Daily P&L / risk / trade summaries |
| `data/agents/sessions/*.json` | Per-run agent traces (see observability doc) |

---

## 2.2 The gap: we don't evaluate the agents

Today, an agent run is considered "successful" if the subprocess exits 0 and the output parses into the Pydantic model ([nodes.py:273](../src/agents/nodes.py#L273)). That's a **liveness check, not a quality check.** We currently have no systematic answer to:

- Did the agent call the *right* tools in the *right* order? (e.g. did it call `sync_broker` first as its prompt instructs?)
- Did the three variants actually produce *diverse* candidate lists, or are they collapsing to the same picks?
- Is the `manager`'s reasoning consistent with the conclusions it was given, or did it hallucinate a ticker no agent proposed?
- How often does each agent fall back? Trend over time?
- Are prompts getting better or worse after evolution?

---

## 2.3 Proposed evaluation process

A layered approach — cheap automatic checks on every run, deeper LLM-judged evals periodically, and a golden-set regression suite.

### Layer 1 — Per-run automatic assertions (cheap, every run)

Run these as a post-graph step over the session traces; emit pass/fail metrics. No LLM needed.

| Check | Signal it gives |
|---|---|
| Conclusion schema valid | Already done — keep, but record as a metric not just a gate |
| Required tool called (e.g. `sync_broker` first for risk/execution) | Prompt adherence |
| No hallucinated tickers (manager's buys ⊆ union of variant candidates) | Manager grounding |
| Variant diversity (Jaccard overlap of candidate sets) | Are variants actually different? |
| Turn count vs `max_turns` (hit the cap = probably struggled) | Agent efficiency |
| Latency per agent vs rolling baseline | Regression / contention |
| Tool error rate | Tool/data health |

### Layer 2 — LLM-as-judge (periodic, e.g. nightly on the day's runs)

Use a separate Claude call with a rubric to score each agent's reasoning on a 1–5 scale for: **groundedness** (claims supported by tool outputs), **decision quality** (does the conclusion follow from the evidence), and **instruction adherence**. Store scores alongside the trace. This is the agent analogue of the strategy's post-mortem.

### Layer 3 — Golden-set regression suite (on every prompt change)

Curate ~20–30 frozen scenarios (a fixed `screen_data` snapshot, a fixed portfolio state) with expected behaviors ("in a crisis regime, risk agent must block new entries"). Replay them whenever a prompt is edited or evolved. This catches prompt regressions *before* they hit production — critical since `evolve_prompts` can auto-rewrite prompts.

### Layer 4 — Outcome attribution (closes the loop to money)

Tag every executed trade with the **session/agent decision that produced it**. Then the existing post-mortem can answer not just "was this a bad signal" but "which *agent variant's* picks performed best." Today there's no link from a closed trade back to which variant championed it — adding that turns the strategy eval into an agent eval too.

### Suggested metrics to collect

**Agent-level:** success rate, fallback rate, p50/p95 latency, turns-to-completion, tool-call count, tool-error rate, schema-valid rate, judge scores (groundedness/quality/adherence), variant diversity.

**Strategy-level (mostly exist):** win rate, Sharpe/Sortino, max drawdown, score↔return correlation, per-signal IC, alpha-decay flags — plus the new **per-variant attributed P&L**.

**System-level:** runs/day by mode, end-to-end pipeline latency, API spend per run, gate-skip rate.

---

## 2.4 How this relates to observability

Evaluation and observability are two views of the same data. The traces we already write ([data/agents/sessions/*.json](../data/agents/sessions/)) are the **substrate** for both:

- **Observability** = "what happened" → the dashboard already surfaces this ([frontend/app.py](../frontend/app.py): pipeline state, variant results, consensus, manager decision).
- **Evaluation** = "was it good" → scores *derived from* the same traces.

The practical upgrade is to **compute the Layer-1 metrics as part of trace post-processing** and store them in the existing `dashboard.db` ([scripts/ingest_run.py](../scripts/ingest_run.py)), so the dashboard can show trend lines (fallback rate over time, judge score per agent, variant diversity) rather than just the latest snapshot. That makes regressions visible the moment they appear — which is exactly what you want before letting `evolve_prompts` change prompts autonomously.

Longer term, adopting **LangSmith** (LangGraph's native tracing/eval platform) would give the parallel graph runs proper trace trees, dataset-based evals, and a UI for the LLM-judge scores without building it from scratch. The state is already designed to be JSON-serializable for exactly this ([state.py:36](../src/agents/state.py#L36)). See [03-memory.md](03-memory.md) and [05-cron-tools-mcp.md](05-cron-tools-mcp.md) for how traces and state persist.
