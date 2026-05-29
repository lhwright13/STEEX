# 4. Multi-Agent Orchestration Pattern

This document describes the orchestration pattern STEEX uses, why it was chosen, and which alternative patterns are worth considering.

---

## 4.1 The current pattern: Supervisor + Parallel Fan-out/Fan-in, with deterministic fallback

STEEX uses a **hybrid** pattern. It's not one textbook architecture but a deliberate combination, all expressed declaratively in [config/agents.yaml](../config/agents.yaml) and assembled by `build_graph()` ([01-langgraph.md](01-langgraph.md)).

The pieces:

1. **Sequential sub-agents** — preparatory agents run in a fixed order (`data` → `risk`). Each writes its conclusion into shared state for downstream agents.

2. **Parallel fan-out/fan-in (the distinctive part)** — for `screen`, three analysis *variants* run concurrently from the same inputs:
   - `analysis_conservative` — high bars, fundamentals-led.
   - `analysis_aggressive` — low bars, speculative.
   - `analysis_momentum` — trend-following.

   A `fan_out` node emits a `Send` per variant; `merge_variants` waits for all three (fan-in) and feeds them to a **meta-agent** that synthesizes consensus.

3. **Supervisor / manager** — a single `manager` agent takes every sub-agent's conclusion and produces the final, authoritative decision (`ManagerDecision`: approved buys/sells, regime, alerts, reasoning). This is the classic **supervisor** role: workers gather evidence, the supervisor decides.

4. **Conditional execution gate** — the `execution` agent only runs if the manager actually approved trades; otherwise the graph skips straight to reporting.

5. **Critical-agent abort → deterministic fallback** — agents listed in `critical_agents` (e.g. `risk`) can abort the whole run on failure. A conditional edge then routes to a **deterministic, non-LLM fallback** (the original `QuantManager` logic). This is the safety net that makes an LLM-driven trading system tolerable: if the agents misbehave, rules-based code takes over rather than the system producing garbage.

### The consensus mechanism (why three variants)

The meta-agent doesn't average — it looks for **agreement**:
- A candidate picked by **all three** variants = high-conviction.
- Picked by **2 of 3** = consensus (included).
- Picked by **only 1** = speculative (excluded).

So the parallel pattern isn't about speed; it's an **ensemble for robustness** — three different "investing personalities" voting, with the meta-agent enforcing that only broadly-agreed ideas survive.

### Visual

```
sequential prep        parallel ensemble (fan-out/in)      supervisor      gated action
─────────────────   ──────────────────────────────────   ──────────   ──────────────────
data ─> risk ─────> fan_out ─> [conservative              ]
                               [aggressive  ] ─> merge ──> manager ──> (approved?) ─> execution ─> report
                               [momentum    ]   (meta)                    │
        │(critical fail)                                                  └─(no)──────────────────> report
        └──────────────────────────────> deterministic fallback ─> END
```

---

## 4.2 Why this pattern was chosen

This is a **money-handling, autonomous, scheduled** system. That context drives every choice:

- **A supervisor gives a single accountable decision-maker.** Trades need one authoritative output, not a free-for-all. The `manager` is the only agent that can approve orders.
- **The ensemble counters LLM variance and single-strategy bias.** One analysis agent could anchor on a bad framing. Three personalities + consensus filtering means a pick must survive disagreement before risking capital — directly analogous to requiring multiple confirming signals in quant strategies.
- **Parallel, not sequential, variants** because they're independent (same inputs, no dependency) — fan-out is the natural fit and avoids one variant being primed by another's output.
- **Deterministic fallback is non-negotiable for autonomy.** It runs unattended on cron with no human in the loop. If an agent times out or hallucinates, the rules-based path keeps the system safe and operational. This is arguably the most important design decision.
- **Declarative topology** keeps the pattern flexible — `monitor`, `enter`, `post_market`, and `learning` reuse the same machinery with different agent lists, and a new mode is a YAML edit.
- **Bounded autonomy** — `max_turns`, tool allowlists, and the execution gate keep each agent inside guardrails appropriate to a financial system.

In short: **supervisor for accountability, ensemble for robustness, fallback for safety.**

---

## 4.3 Alternative patterns to consider

### A. Hierarchical / team-of-teams supervisor
A top supervisor delegating to *sub-supervisors* (a "research lead" over the analysis variants, a "risk lead" over risk+execution). **Pro:** scales as agents multiply; isolates concerns. **Con:** more layers, more latency, more cost. **When:** only if the agent count grows well beyond today's ~14.

### B. Swarm / handoff (peer-to-peer)
Agents hand control to each other dynamically based on the situation, with no central supervisor (LangGraph's `langgraph-swarm`). **Pro:** flexible, emergent routing. **Con:** harder to predict, audit, and bound — a poor fit for a system that must produce one accountable, traceable decision and run unattended. **Verdict:** not appropriate for the trade-decision path; *could* suit open-ended `research`/`learning` exploration.

### C. Plan-and-execute
A planner agent writes a step plan, an executor carries it out, replanning as needed. **Pro:** adapts to novel situations; good for research. **Con:** non-deterministic flow is hard to gate before order execution. **When:** worth piloting inside `learning` mode (which is already exploratory) rather than the trading path.

### D. Debate / reflection over the variants
Instead of the meta-agent silently synthesizing, have the variants **argue** (one round of critique) before consensus, or add a reflection pass where the manager critiques its own decision before finalizing. **Pro:** can improve decision quality and surfaces *why* variants disagree. **Con:** more cost/latency; needs the eval harness from [02-evaluation.md](02-evaluation.md) to prove it actually helps.

### E. Deeper deterministic-by-default, LLM-on-exception
Invert the current bias: run the cheap rules-based path normally, and only invoke the expensive agent ensemble when the situation is ambiguous or novel (high VIX, regime change, conflicting signals). **Pro:** big cost savings, agents focus where they add value. **Con:** needs a reliable "is this ambiguous?" trigger. **When:** strong candidate as API spend grows — and it composes naturally with today's fallback machinery.

---

## 4.4 Recommendation

Keep the supervisor + ensemble + fallback core — it's well-matched to autonomous trading. The highest-value evolutions, in order:

1. **(E) Deterministic-by-default with LLM-on-exception** — best cost/safety leverage, reuses existing fallback.
2. **(D) Add a reflection pass to the manager** — cheap quality win, gated by the eval harness.
3. **(C) Plan-and-execute inside `learning` only** — let exploration be flexible while keeping the trade path tightly gated.

Hold off on (A) hierarchical and (B) swarm until the agent count or task diversity actually demands them.
