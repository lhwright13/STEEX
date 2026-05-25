# Agent Transparency & System Configuration - Dashboard Supplement

**Purpose:** Give users complete visibility into multi-agent orchestration, scheduled execution, agent capabilities, and data flow.

---

## I. Main View: Agent System Overview

```
MULTI-AGENT SYSTEM CONFIGURATION
──────────────────────────────────────────────────────────────────
[View: Graph | Schedules | Agents | Tools]

MODE: screen  |  Status: Active  |  Last Run: 2026-05-24 10:17:50
Next Scheduled: 2026-05-24 14:30:00 (in 4 min)  |  Frequency: Every 2.5 hours

┌─ GRAPH VISUALIZATION (ASCII) ────────────────────────────────────┐
│                                                                  │
│  ┌─────────────┐                                                │
│  │   START     │                                                │
│  └──────┬──────┘                                                │
│         │                                                       │
│    ┌────▼─────────────────────┐                                │
│    │  [1] DataAgent            │ ← Click for details           │
│    │  Status: ready, Max 10T   │                               │
│    └────┬─────────────────────┘                                │
│         │                                                       │
│    ┌────▼─────────────────────┐                                │
│    │  [2] RiskAgent            │                               │
│    │  Status: ready, Critical  │                               │
│    └────┬─────────────────────┘                                │
│         │                                                       │
│    ┌────▼──────────────────────────────────────────┐           │
│    │  [FAN-OUT] Dispatch to Parallel Analysis      │           │
│    └─┬──────────┬──────────────┬──────────────────┘           │
│      │          │              │                              │
│  ┌───▼──┐  ┌───▼──┐      ┌────▼────┐                          │
│  │ [3A] │  │ [3B] │      │  [3C]   │                          │
│  │Cons. │  │Aggr. │      │Momentum │                          │
│  │(15T) │  │(15T) │      │ (15T)   │                          │
│  └───┬──┘  └───┬──┘      └────┬────┘                          │
│      │         │              │                               │
│      └────────┬┴──────────────┘                               │
│              │                                                │
│    ┌─────────▼──────────────────┐                            │
│    │ [4] MetaAnalysisAgent       │                           │
│    │ Status: ready, Max 5T       │                           │
│    └─────────┬──────────────────┘                            │
│              │                                                │
│    ┌─────────▼──────────────────┐                            │
│    │ [5] ManagerAgent            │                           │
│    │ Status: ready, No Tools     │                           │
│    └─────────┬──────────────────┘                            │
│              │                                                │
│    ┌─────────▼──────────────────┐                            │
│    │ [6] ExecutionAgent          │                           │
│    │ Status: ready, Critical     │                           │
│    └─────────┬──────────────────┘                            │
│              │                                                │
│             [END]                                            │
│                                                               │
└───────────────────────────────────────────────────────────────┘

Legend:
[N] = Execution order
(T) = Max turns (agent reasoning loops)
Red = Critical agent (fails = abort)
Blue = Parallel execution
Green = Requires tools/MCP
```

---

## II. Cron Job Schedule View

```
SCHEDULED EXECUTION CALENDAR
──────────────────────────────────────────────────────────────────
[View: Week | Month | Next 30 Days]

Mode        Frequency        Next Run            Last Run            Status
─────────────────────────────────────────────────────────────────────────
screen      Every 2.5h       2026-05-24 14:30    2026-05-24 10:18    ✓ OK
enter       15 min after     2026-05-24 14:35    2026-05-24 10:33    ✓ OK
monitor     Continuous       Always              2026-05-24 14:32    ✓ Running
post_market 16:00 ET daily   2026-05-24 16:00    2026-05-23 16:00    ✓ OK
learning    2x per week      2026-05-26 09:00    2026-05-22 09:15    ✓ OK

Today's Schedule:
────────────────────────────────────────────────────────────────────
Time      Mode             Action                   Agents Involved
──────────────────────────────────────────────────────────────────────
10:18     screen  [done]   Ran pipeline            data→risk→3-way→mgr
10:33     enter   [done]   Load & enter trades     load→risk→mgr→exec
14:30     screen  [next]   Run screening           data→risk→3-way→mgr
14:35     enter   [queued] Load & enter (2 picks)  load→risk→mgr→exec
16:00     monitor [queue]  Monitor portfolio       risk→mgr
16:00     learning[queue]  Signal research pass    learning_agent→mgr

[Click any scheduled job to see full flow diagram + agent configs]
```

---

## III. Click on Cron Job → Detailed Flow View

```
SCREEN MODE EXECUTION FLOW - Detailed View
──────────────────────────────────────────────────────────────────

Schedule: Every 2.5 hours (last: 10:18, next: 14:30)
Duration: 2-3 minutes (avg 2:17, min 1:52, max 3:45)
Success Rate: 94% (15/16 runs)

┌─────────────────────────────────────────────────────────────────┐
│ DATAFLOW + AGENT CONFIGURATIONS                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ ┌─ AGENT [1]: DataAgent ──────────────────────────────────────┐│
│ │ Role: Validate data sources & prefetch market data           ││
│ │ Status: CRITICAL (blocks pipeline if fails)                 ││
│ │ Max Turns: 10  |  Avg Duration: 8s                          ││
│ │                                                              ││
│ │ ✓ Tools Enabled: [4 allowed]                                ││
│ │   ├─ sync_broker      (check broker connection)             ││
│ │   ├─ prefetch_data    (cache market snapshots)              ││
│ │   ├─ refresh_data     (reload from API)                     ││
│ │   └─ check_data_health (validate freshness)                 ││
│ │                                                              ││
│ │ ✓ External MCP Servers: [alpaca]                            ││
│ │   └─ mcp__alpaca__* (all alpaca tools)                      ││
│ │       • get_quote, get_snapshot, get_positions, etc.        ││
│ │                                                              ││
│ │ Output (DataConclusion):                                    ││
│ │   └─ all_healthy: true/false                                ││
│ │   └─ sources_checked: N                                     ││
│ │   └─ sources_healthy: N                                     ││
│ │   └─ vix_level: float (used downstream)                     ││
│ │   └─ insider_purchases: int                                 ││
│ │   └─ reasoning: string                                      ││
│ │                                                              ││
│ │ [View Preprompt] [View Last Output] [Test Run]              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              ▼                                  │
│ ┌─ AGENT [2]: RiskAgent ──────────────────────────────────────┐│
│ │ Role: Assess market regime, position risk, entry gates      ││
│ │ Status: CRITICAL (abort if fails)                           ││
│ │ Max Turns: 12  |  Avg Duration: 12s                         ││
│ │                                                              ││
│ │ ✓ Tools Enabled: [6 allowed]                                ││
│ │   ├─ sync_broker      (check available cash)                ││
│ │   ├─ get_regime       (market regime detection)             ││
│ │   ├─ assess_portfolio_risk (current holdings risk)          ││
│ │   ├─ get_exit_signals (check stop losses)                   ││
│ │   ├─ get_positions    (current portfolio)                   ││
│ │   └─ get_account      (cash, buying power)                  ││
│ │                                                              ││
│ │ ✓ External MCP Servers: [alpaca]                            ││
│ │                                                              ││
│ │ Output (RiskConclusion):                                    ││
│ │   └─ regime_name: "risk_on" | "cautious" | "risk_off" | ... ││
│ │   └─ regime_confidence: 0.87                                ││
│ │   └─ vix_level: 22.5                                        ││
│ │   └─ [position risks, entry gate status, etc.]              ││
│ │                                                              ││
│ │ [View Preprompt] [View Last Output] [Test Run]              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              ▼                                  │
│ ┌─ PARALLEL EXECUTION: 3-Way Variant Analysis ────────────────┐│
│ │                                                              ││
│ │ Dispatch to 3 agents in parallel:                           ││
│ │                                                              ││
│ │ ┌─ AGENT [3A]: analysis_conservative ──────────────────────┐││
│ │ │ Role: Quality-focused screening (fundamentals + insider) │││
│ │ │ Status: Optional (run all 3 for consensus)              │││
│ │ │ Max Turns: 15  |  Avg Duration: 35s                     │││
│ │ │                                                          │││
│ │ │ ✓ Tools Enabled: [6 tools]                              │││
│ │ │   ├─ get_regime_screening_params   (regime context)     │││
│ │ │   ├─ run_screening_variant("conservative")              │││
│ │ │   ├─ get_signal_confidence         (signal reliability) │││
│ │ │   ├─ rank_candidates_with_weights  (custom scoring)     │││
│ │ │   ├─ get_unusual_options_activity  (options flow check) │││
│ │ │   └─ construct_portfolio           (diversification)    │││
│ │ │                                                          │││
│ │ │ ✓ External MCP Servers:                                 │││
│ │ │   ├─ alpaca    (market data)                            │││
│ │ │   ├─ alphavantage (technical indicators)                │││
│ │ │   └─ polygon   (aggregates, news, options)              │││
│ │ │                                                          │││
│ │ │ Output (AnalysisConclusion):                            │││
│ │ │   └─ candidates: [...]  (8 picks)                       │││
│ │ │   └─ reasoning: string  (quality thesis)                │││
│ │ │   └─ meta.variant: "conservative"                       │││
│ │ │                                                          │││
│ │ │ [View Preprompt] [View Last Output] [Test Run]          │││
│ │ └──────────────────────────────────────────────────────────┘││
│ │                                                              ││
│ │ ┌─ AGENT [3B]: analysis_aggressive ────────────────────────┐││
│ │ │ Role: Growth-focused (momentum + sentiment)              │││
│ │ │ Status: Optional                                         │││
│ │ │ Max Turns: 15  |  Avg Duration: 38s                     │││
│ │ │ Tools: [6 same as conservative]                          │││
│ │ │ External MCP Servers: [alpaca, alphavantage, polygon]    │││
│ │ │ Output: 15 candidates (wider net, lower bars)            │││
│ │ │ [View Preprompt] [View Last Output] [Test Run]           │││
│ │ └──────────────────────────────────────────────────────────┘││
│ │                                                              ││
│ │ ┌─ AGENT [3C]: analysis_momentum ──────────────────────────┐││
│ │ │ Role: Trend-following (pure technicals, no fundamentals) │││
│ │ │ Status: Optional                                         │││
│ │ │ Max Turns: 15  |  Avg Duration: 32s                     │││
│ │ │ Tools: [6 same as conservative]                          │││
│ │ │ External MCP Servers: [alpaca, alphavantage, polygon]    │││
│ │ │ Output: 12 candidates (momentum only)                    │││
│ │ │ [View Preprompt] [View Last Output] [Test Run]           │││
│ │ └──────────────────────────────────────────────────────────┘││
│ │                                                              ││
│ │ Consensus Rules:                                           ││
│ │   HIGH CONVICTION:   All 3 variants agree  → Position: 4.5% ││
│ │   MEDIUM CONVICTION: 2/3 variants agree    → Position: 3.0% ││
│ │   SPECULATIVE:       1/3 variant only      → Exclude        ││
│ │                                                              ││
│ │ Results: 5 consensus picks (1 high, 4 medium conviction)   ││
│ │                                                              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              ▼                                  │
│ ┌─ AGENT [4]: MetaAnalysisAgent ──────────────────────────────┐│
│ │ Role: Synthesize 3-way variant results into consensus list  ││
│ │ Status: Optional (runs after all variants complete)         ││
│ │ Max Turns: 5  |  Avg Duration: 15s                          ││
│ │                                                              ││
│ │ ✗ Tools Disabled: NO TOOLS (pure reasoning)                ││
│ │                                                              ││
│ │ Input: All 3 variant conclusions                            ││
│ │ Output (MetaAnalysisConclusion):                            ││
│ │   └─ candidates: [ConsensusStock]                           ││
│ │   └─ high_conviction_count: 1                               ││
│ │   └─ consensus_count: 5                                     ││
│ │   └─ speculative_excluded: ["KO", "TECH"]                  ││
│ │                                                              ││
│ │ [View Preprompt] [View Last Output] [Test Run]              ││
│ │                                                              ││
│ │ NOTE: Meta-analysis is deterministic reasoning only.        ││
│ │ It reads variant conclusions and applies consensus rules:   ││
│ │   • 3/3 variants → high_conviction=True, max position       ││
│ │   • 2/3 variants → high_conviction=False, standard position ││
│ │   • 1/3 variant → exclude (speculative, low conviction)     ││
│ │                                                              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              ▼                                  │
│ ┌─ AGENT [5]: ManagerAgent ───────────────────────────────────┐│
│ │ Role: Synthesize all conclusions, approve trades            ││
│ │ Status: Normal (continues even if analysis fails)           ││
│ │ Max Turns: 3  |  Avg Duration: 8s                           ││
│ │                                                              ││
│ │ ✗ Tools Disabled: NO TOOLS (pure reasoning synthesis)      ││
│ │                                                              ││
│ │ Input: Data, Risk, Analysis, Consensus conclusions          ││
│ │ Output (ManagerDecision):                                   ││
│ │   └─ entries_approved: true/false                           ││
│ │   └─ buys: [BuyRecommendation]  (up to daily_picks limit)   ││
│ │   └─ sells: [SellRecommendation]                            ││
│ │   └─ reasoning: string (synthesis logic)                    ││
│ │                                                              ││
│ │ [View Preprompt] [View Last Output] [Test Run]              ││
│ │                                                              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              ▼                                  │
│ ┌─ AGENT [6]: ExecutionAgent ─────────────────────────────────┐│
│ │ Role: Place buy/sell orders via broker                      ││
│ │ Status: CRITICAL (fails = trading blocked)                  ││
│ │ Max Turns: 10  |  Avg Duration: 25s                         ││
│ │                                                              ││
│ │ ✓ Tools Enabled: [8 allowed]                                ││
│ │   ├─ sync_broker              (check connection)            ││
│ │   ├─ load_screen_results      (get manager picks)           ││
│ │   ├─ size_buy_list            (position sizing)             ││
│ │   ├─ execute_entries          (place buy orders)            ││
│ │   ├─ execute_exits            (place sell orders)           ││
│ │   ├─ get_order_status         (monitor fills)               ││
│ │   ├─ get_positions            (current portfolio)           ││
│ │   └─ get_account              (cash, buying power)          ││
│ │                                                              ││
│ │ ✓ External MCP Servers: [alpaca]                            ││
│ │                                                              ││
│ │ Output (ExecutionConclusion):                               ││
│ │   └─ entries_executed: 5                                    ││
│ │   └─ exits_executed: 2                                      ││
│ │   └─ total_cost: $92,340                                    ││
│ │                                                              ││
│ │ [View Preprompt] [View Last Output] [Test Run]              ││
│ │                                                              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              ▼                                  │
│ ┌─ POST-ACTIONS ──────────────────────────────────────────────┐│
│ │                                                              ││
│ │ └─ save_screen     (persist results to disk)                ││
│ │ └─ report          (email/log summary)                      ││
│ │                                                              ││
│ └──────────────────────────────────────────────────────────────┘│
│                              │                                  │
│                            [END]                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## IV. Click on Agent → Agent Detail Modal

```
┌──────────────────────────────────────────────────────────────────┐
│ AGENT DETAILS: analysis_conservative                      [x]    │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ METADATA                                                         │
│ ─────────────────────────────────────────────────────────────── │
│ Name:              analysis_conservative                         │
│ Role:              Conservative stock analysis (quality focus)   │
│ Type:              Analysis Variant (parallel execution)         │
│ Status:            ✓ Ready                                       │
│ Critical:          No (optional, runs all 3 for consensus)      │
│ Max Turns:         15 (agent reasoning loops)                    │
│ Avg Duration:      35 seconds                                    │
│ Success Rate:      98% (49/50 runs)                             │
│ Last Run:          2026-05-24 10:15:42 (passed)                │
│                                                                  │
│ CONFIGURATION                                                    │
│ ─────────────────────────────────────────────────────────────── │
│ Prompt File:       src/agents/prompts/analysis_conservative.py   │
│ Conclusion Type:   AnalysisConclusion                           │
│ Needs Tools:       Yes (6 required)                             │
│ Allowed Tools:     [list below]                                 │
│                                                                  │
│ ALLOWED TOOLS (MCP STEEX Server)                                │
│ ─────────────────────────────────────────────────────────────── │
│ ✓ get_regime_screening_params                                   │
│   └─ Read market regime, get param adjustments                  │
│   └─ Returns: regime, confidence, param_overrides               │
│   └─ Example: cautious regime → raise insider weight 0.25→0.30  │
│                                                                  │
│ ✓ run_screening_variant("conservative")                         │
│   └─ Run 5-stage pipeline with conservative presets             │
│   └─ Presets: momentum_min 10%, sentiment 50+, PE<30, ROE>10%   │
│   └─ Returns: universe, candidates, screening funnel            │
│                                                                  │
│ ✓ get_signal_confidence                                         │
│   └─ Check per-signal win rates (30-day rolling)                │
│   └─ Returns: which signals degrading, recommended weights      │
│   └─ Example: "momentum on watch list, reduce confidence"       │
│                                                                  │
│ ✓ rank_candidates_with_weights                                  │
│   └─ Rank candidates with custom scoring weights                │
│   └─ Params: weight_momentum, weight_insider, etc. (all optional)│
│   └─ Conservative uses: momentum 0.25, insider 0.35             │
│   └─ Returns: ranked list with component scores                 │
│                                                                  │
│ ✓ get_unusual_options_activity                                  │
│   └─ Find stocks with unusual call/put imbalance                │
│   └─ Returns: list of call-heavy stocks, IV skew                │
│   └─ Example: "AAPL unusual calls + insider buying = conviction" │
│                                                                  │
│ ✓ construct_portfolio                                           │
│   └─ Apply diversification constraints                          │
│   └─ Conservative uses: max correlation 0.55 (stricter)         │
│   └─ Returns: selected, rejected, sector exposure               │
│                                                                  │
│ EXTERNAL MCP SERVERS                                            │
│ ─────────────────────────────────────────────────────────────── │
│ Alpaca     ✓ Enabled                                            │
│   └─ get_quote, get_snapshot, get_positions, place_order, etc.  │
│   └─ Used by: data agent, risk agent, execution agent           │
│                                                                  │
│ AlphaVantage ✓ Enabled                                          │
│   └─ Technical indicators, economic data, earnings calendar     │
│   └─ Used by: all 3 analysis variants, research agent           │
│                                                                  │
│ Polygon/Massive ✓ Enabled                                       │
│   └─ Options aggregates, news sentiment, unusual options        │
│   └─ Used by: all 3 analysis variants for options data          │
│                                                                  │
│ SYSTEM PROMPT (PREPROMPT)                                       │
│ ─────────────────────────────────────────────────────────────── │
│ [EXPAND TO VIEW] ▼                                              │
│                                                                  │
│ You are a conservative stock analyst. Your role is to           │
│ identify high-quality, low-risk stock candidates through        │
│ rigorous screening and fundamental analysis.                    │
│                                                                  │
│ ## Your approach:                                               │
│ 1. Understand market regime and adjust thresholds               │
│ 2. Run conservative screening (high bars)                       │
│ 3. Get signal confidence scores                                 │
│ 4. Rank candidates using confidence-adjusted weights            │
│ 5. Check for unusual options activity                           │
│ 6. Apply strict portfolio diversification                       │
│                                                                  │
│ ## Conservative prioritization:                                 │
│ - Fundamental quality (PE, ROE, debt) is paramount              │
│ - Insider buying = institutional-grade confidence signal        │
│ - Sentiment must be clearly positive (50+ score)                │
│ - Volume surge indicates conviction, not speculation            │
│ - Options flow confirms thesis                                  │
│ - Reject speculative or thinly-traded names                     │
│                                                                  │
│ [VIEW FULL PROMPT 2,500+ chars]                                │
│                                                                  │
│ VARIANT PARAMETERS (Hardcoded Presets)                          │
│ ─────────────────────────────────────────────────────────────── │
│ When run_screening_variant("conservative") is called:           │
│                                                                  │
│ momentum_min_return:    0.05  →  0.10  (6M return >= 10%)      │
│ sentiment_min_score:    30.0  →  50.0  (higher bar)            │
│ fundamental_max_pe:     50.0  →  30.0  (no speculation)        │
│ fundamental_min_roe:    0.05  →  0.10  (quality required)      │
│                                                                  │
│ WEIGHT OVERRIDES (For this variant)                             │
│ ─────────────────────────────────────────────────────────────── │
│ When rank_candidates_with_weights() is called:                  │
│                                                                  │
│ weight_momentum:        0.30  →  0.25  (conservative reduces)   │
│ weight_insider:         0.25  →  0.35  (emphasizes insider)     │
│ weight_fundamental:     0.10  →  0.20  (doubles importance)     │
│ weight_volume:          0.15  →  0.12  (confirmation only)      │
│ weight_sentiment:       0.15  →  0.05  (conservative skeptical) │
│ weight_options:         0.05  →  0.03  (secondary signal)       │
│                                                                  │
│ LAST EXECUTION DETAILS                                          │
│ ─────────────────────────────────────────────────────────────── │
│ Run ID:           c4d2a1b9_conservative                         │
│ Started:          2026-05-24 10:15:42 UTC                       │
│ Completed:        2026-05-24 10:15:77 UTC (35 seconds)          │
│ Status:           ✓ Success                                     │
│ Output:                                                         │
│   - Universe: 500 stocks                                        │
│   - Passed screening: 8 candidates                              │
│   - Top 3 scores: 72.5, 69.3, 67.8 (AAPL, JPM, KO)             │
│ Error:            None                                          │
│                                                                  │
│ [View Full Output] [Download Output JSON] [Rerun Agent]         │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## V. Tool Access Transparency View

```
MCP TOOL PERMISSIONS MATRIX
──────────────────────────────────────────────────────────────────

Agent Name         │ Steex │ Alpaca │ Polygon │ AlphaVantage │ Notes
─────────────────────────────────────────────────────────────────
DataAgent         │  4    │ Wildcard│   —    │     —        │ Critical
RiskAgent         │  6    │ Wildcard│   —    │     —        │ Critical
analysis_cons     │  6    │ Wildcard│ Wildcard│ Wildcard     │ Parallel
analysis_aggr     │  6    │ Wildcard│ Wildcard│ Wildcard     │ Parallel
analysis_moment   │  6    │ Wildcard│ Wildcard│ Wildcard     │ Parallel
MetaAnalysis      │  —    │   —    │   —    │     —        │ No tools
ManagerAgent      │  —    │   —    │   —    │     —        │ No tools
ExecutionAgent    │  8    │ Wildcard│   —    │     —        │ Critical
ResearchAgent     │  6    │   —    │ Wildcard│ Wildcard     │ Learning
ReportAgent       │  3    │   —    │ Wildcard│     —        │ Async


STEEX SERVER TOOLS (MCP: mcp__steex__*)
──────────────────────────────────────────────────────────────────

Category: Screening & Analysis
├─ run_screening
│  └─ Run 5-stage pipeline, return universe + candidates
│  └─ Allowed by: [data, risk implicitly; analysis_* explicitly]
│
├─ run_screening_variant(variant)
│  └─ Run with conservative/aggressive/momentum presets
│  └─ Allowed by: [analysis_conservative, analysis_aggressive, analysis_momentum]
│
├─ rank_candidates
│  └─ Rank by weighted composite score
│  └─ Allowed by: [analysis_*, research]
│
├─ rank_candidates_with_weights
│  └─ Rank with custom weights (all optional)
│  └─ Allowed by: [analysis_conservative, analysis_aggressive, analysis_momentum]
│
├─ construct_portfolio
│  └─ Apply diversification, return selected portfolio
│  └─ Allowed by: [analysis_*, execution]
│
├─ get_regime_screening_params
│  └─ Get regime-specific threshold overrides
│  └─ Allowed by: [analysis_conservative, analysis_aggressive, analysis_momentum]
│
├─ get_signal_confidence
│  └─ Get per-signal win rates and recommended weights
│  └─ Allowed by: [analysis_*, learning_agent]
│
└─ get_unusual_options_activity
   └─ Get stocks with unusual call/put imbalance
   └─ Allowed by: [analysis_*, research]

Category: Regime & Risk
├─ get_regime
│  └─ Market regime detection (risk_on, cautious, risk_off, crisis)
│  └─ Allowed by: [risk, monitor, learning]
│
├─ assess_portfolio_risk
│  └─ Current position risk analysis
│  └─ Allowed by: [risk, execution]
│
└─ get_exit_signals
   └─ Check for stop losses, hold time limits
   └─ Allowed by: [risk, execution]

Category: Execution & Broker
├─ sync_broker
│  └─ Check broker connection, sync account state
│  └─ Allowed by: [data, risk, execution]
│
├─ size_buy_list
│  └─ Position sizing with volatility adjustment
│  └─ Allowed by: [execution]
│
├─ execute_entries
│  └─ Place buy orders
│  └─ Allowed by: [execution]
│
├─ execute_exits
│  └─ Place sell orders
│  └─ Allowed by: [execution]
│
├─ generate_buy_list
│  └─ Generate buy candidates (deterministic)
│  └─ Allowed by: [manager, execution]
│
└─ generate_sell_list
   └─ Generate sell candidates
   └─ Allowed by: [manager, execution]

Category: Data Fetching
├─ prefetch_data
│  └─ Cache market snapshots for fast analysis
│  └─ Allowed by: [data]
│
├─ refresh_data
│  └─ Reload data from APIs
│  └─ Allowed by: [data, risk]
│
└─ check_data_health
   └─ Validate data freshness & quality
   └─ Allowed by: [data, learning]

Category: Information & History
├─ get_positions
│  └─ Current portfolio holdings
│  └─ Allowed by: [risk, execution, monitor]
│
├─ get_account
│  └─ Cash, buying power, account equity
│  └─ Allowed by: [data, risk, execution]
│
├─ get_trade_history
│  └─ Closed trades, entry/exit prices, P&L
│  └─ Allowed by: [research, report, learning]
│
├─ get_order_status
│  └─ Live order status, fills, rejections
│  └─ Allowed by: [execution, monitor]
│
├─ load_screen_results
│  └─ Load previous screen results from disk
│  └─ Allowed by: [enter mode, execution]
│
└─ size_buy_list
   └─ Volatility-adjusted position sizing
   └─ Allowed by: [execution]


EXTERNAL MCP SERVERS
──────────────────────────────────────────────────────────────────

Alpaca Server (mcp__alpaca__*)
├─ Real-time quotes, snapshots, option chains
├─ Order management (place, cancel, status)
├─ Position tracking, account details
├─ Market hours, circuit breaker status
└─ Used by: DataAgent, RiskAgent, all AnalysisVariants, ExecutionAgent

AlphaVantage Server (mcp__alphavantage__*)
├─ Technical indicators (50+ indicators)
├─ Economic indicators (inflation, unemployment, etc.)
├─ Earnings calendar, company fundamentals
├─ Commodity & forex data
└─ Used by: all AnalysisVariants, ResearchAgent, LearningAgent

Polygon/Massive Server (mcp__polygon__*)
├─ Stock aggregates, daily bars, technical analysis
├─ Options aggregates, unusual options activity
├─ Company news, sentiment analysis
├─ Option chains with IV, Greeks, implied volatility
└─ Used by: all AnalysisVariants, ResearchAgent, MonitorAgent


TOOL EXECUTION TRACING
──────────────────────────────────────────────────────────────────

For each agent run, you can see:
├─ Tool Name                  (which MCP server, which tool)
├─ Input Summary              (what params were passed)
├─ Output Summary             (key results, first 200 chars)
├─ Duration                   (how long tool took)
├─ Success                    (✓ or ✗)
└─ Error Message              (if failed)

Example: analysis_conservative run from 2026-05-24 10:15:42
─────────────────────────────────────────────────────────────────
1. get_regime_screening_params
   ├─ Input: (no params)
   ├─ Output: regime=cautious, insider_weight 0.25→0.30
   ├─ Duration: 0.2s
   └─ Status: ✓

2. run_screening_variant
   ├─ Input: variant="conservative"
   ├─ Output: universe=500, candidates=8, [AAPL, JPM, KO, ...]
   ├─ Duration: 4.1s
   └─ Status: ✓

3. get_signal_confidence
   ├─ Input: (no params, reads AlphaDecayMonitor)
   ├─ Output: win_rate=61.7%, degrading=[sentiment], watch=[momentum]
   ├─ Duration: 0.3s
   └─ Status: ✓

4. rank_candidates_with_weights
   ├─ Input: weight_momentum=0.25, weight_insider=0.35, ...
   ├─ Output: [AAPL 72.5, JPM 69.3, KO 67.8, ...]
   ├─ Duration: 2.8s
   └─ Status: ✓

5. get_unusual_options_activity
   ├─ Input: min_call_volume_ratio=1.5
   ├─ Output: [AAPL (unusual_calls, put_call_ratio=0.42), ...]
   ├─ Duration: 1.2s
   └─ Status: ✓

6. construct_portfolio
   ├─ Input: max_correlation=0.55, max_positions=10
   ├─ Output: selected=8, rejected=0, diversification_ratio=0.75
   ├─ Duration: 3.1s
   └─ Status: ✓

Total: 11.7s (agent spent 35s total including reasoning loops)
```

---

## VI. Workflow Comparison View

```
EXECUTION WORKFLOWS - Side by Side
──────────────────────────────────────────────────────────────────

SCREEN MODE          │ ENTER MODE          │ MONITOR MODE
────────────────────────────────────────────────────────────────
1. DataAgent (10T)   │ 1. load_screen      │ 1. RiskAgent (12T)
2. RiskAgent (12T)   │ 2. RiskAgent (12T)  │ 2. ManagerAgent (3T)
3. FAN-OUT           │ 3. ManagerAgent (3T)│ 3. Fallback/execute
   ├─ Conservative   │ 4. ExecutionAgent   │
   ├─ Aggressive     │ 5. Report           │ [No execution]
   └─ Momentum       │                     │ [Continuous/8H apart]
4. MetaAnalysis (5T) │ Frequency: 15 min   │ Frequency: 2x daily
5. ManagerAgent (3T) │ after screen        │
6. Fallback/execute  │                     │ LEARNING MODE
7. Report            │ POST_MARKET MODE    │ ────────────────
                     │ ────────────────────│ 1. LearningAgent (25T)
Duration: 2-3 min    │ 1. RiskAgent (12T)  │ 2. LearningManager (3T)
Frequency: 2.5h      │ 2. ResearchAgent    │
Critical: 3 agents   │ 3. ManagerAgent (3T)│ Duration: 15-20 min
                     │ 4. ExecutionAgent   │ Frequency: 2x/week
                     │ 5. Report           │ Critical: Learning agent
                     │                     │
                     │ Duration: 3-5 min   │
                     │ Frequency: 1x daily │
                     │ (after market close)│
```

---

## VII. Implementation Notes for UI Agent

**New Components to Add:**

1. **Graph Visualization**
   - ASCII-art flow diagram (easy to parse, old-school)
   - Clickable nodes that open agent detail modal
   - Show execution state (running, done, queued, failed)
   - Show agent type color: red (critical), blue (parallel), green (has tools)

2. **Cron Schedule View**
   - Table of modes with next/last run times
   - Calendar view for visual scheduling
   - Countdown timer to next scheduled run
   - Frequency display (human-readable)

3. **Agent Detail Modal**
   - Metadata (name, role, status, timing)
   - Configuration (prompt file, conclusion type, tools)
   - MCP Tools list with descriptions
   - External servers enabled
   - Full preprompt (expandable, syntax-highlighted)
   - Variant parameters or weight overrides (if applicable)
   - Last execution trace with tool calls

4. **Tool Permissions Matrix**
   - Table showing which agents can use which tools
   - External server access (wildcard or limited)
   - Tooltip on each tool showing description and usage

5. **Workflow Comparison**
   - Side-by-side view of different modes
   - Visual flow from start to end
   - Frequency and duration comparisons
   - Critical agent highlighting

**Data API Endpoints Needed:**

```
GET /api/v1/system/graph/{mode}
  → Returns graph structure (nodes, edges, metadata)

GET /api/v1/system/agents
  → Returns list of all agents with configs

GET /api/v1/system/agents/{agent_name}
  → Returns full agent config, prompt, tools, execution history

GET /api/v1/system/schedules
  → Returns cron schedule for all modes

GET /api/v1/system/tool-matrix
  → Returns MCP tool permissions (which agent can use which tool)

GET /api/v1/system/mcp-servers
  → Returns list of external MCP servers + their capabilities
```

**Frontend Stack:**
- ASCII diagrams rendered in `<pre>` with monospace font
- Expandable sections for prompts (use <details>/<summary>)
- Syntax highlighting for code/prompts (use Prism.js, lightweight)
- Modal overlay for agent details (no heavy libraries)
- Hover tooltips for tool descriptions
- Responsive: stack columns on mobile

---

## VIII. User Actions from Transparency Views

**From Graph View:**
- Click node → Agent detail modal
- Right-click edge → Show edge router logic (conditional vs direct)
- Hover tool icon → Show which MCP server it comes from

**From Schedule View:**
- Click "Next Run" countdown → Edit schedule (cron expression)
- Click "View Flow" → Go to Graph View with this mode selected
- Click "Last Run" → Jump to session history for that run

**From Agent Detail Modal:**
- [View Full Prompt] → Syntax-highlighted prompt text
- [View Last Output] → JSON structure of agent's conclusion
- [Rerun Agent] → Execute just this agent with same inputs
- [Test Run] → Run with synthetic test data
- [Download Output JSON] → Save conclusion as file

**From Tool Matrix:**
- Click tool name → Show full tool definition and example usage
- Click agent name → Go to agent detail modal
- Click MCP server → Show all tools in that server

**From Workflow Comparison:**
- Click mode name → Jump to schedule or last execution
- Show "Time to run" for each mode
- Show "Resources used" (API calls, data fetches)

---

## IX. Key Design Principles for Transparency UI

1. **Show Everything**: No hidden configuration, no magic
2. **Explainability**: Each config has a "why" (regime adaptation, critical gate, tool permission)
3. **Auditability**: Every agent run shows tools called, inputs, outputs
4. **Interactivity**: Click through to understand system deeply
5. **Clarity**: Use existing naming from config (no new abstractions)
6. **Responsiveness**: Loading states, error messages, clear status
7. **Documentation**: Tooltips, examples, "what does this mean?" links
