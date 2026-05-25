# STEEX Trading System Dashboard - Specification

## I. Dashboard Overview

**Purpose:** Real-time monitoring and historical analysis of the multi-agent trading orchestration system.

**Core Views:**
1. Live Pipeline Status (current run progress)
2. Today's Trade Summary (entries/exits executed)
3. Variant Analysis Results (3-way consensus view)
4. Portfolio State (current holdings, regime, risk)
5. Signal Performance (alpha decay, confidence scores)
6. Session History (past runs, audit trail)
7. Configuration & Weights (current parameter settings)

---

## II. Aesthetic Direction: "Utility First"

**Design Philosophy:**
- Information density over decoration
- Fast scanning over visual flair
- Monospace fonts for data, clean sans-serif for labels
- Minimal color: black text, white background, status colors only (green/red/yellow)
- Borders and tables create structure
- ASCII art headers for major sections
- No animations or transitions
- 800px-1200px readable width (not full-browser)

**Color Palette:**
- Primary: Black text (#000000), White background (#FFFFFF)
- Status: Green (#00AA00) for good/passed, Red (#DD0000) for alert/failed, Yellow (#FFAA00) for caution
- Neutral: Dark gray (#333333) for secondary text, Light gray (#EEEEEE) for borders/backgrounds
- Grid lines: Very light gray (#F5F5F5)

**Typography:**
- Headers: 18px/16px, bold, sans-serif (Arial/Helvetica)
- Body: 14px, sans-serif, line-height 1.5
- Data tables: 13px monospace for alignment, 12px for secondary data
- Borders: 1px solid light gray

**Layout:**
- Page header with app title and current time
- Sidebar (optional): mode selector, quick nav
- Main grid: 2-3 column layout, stacked on mobile
- Cards/panels: bordered boxes with light gray background (#F9F9F9)
- Simple buttons: border, no fill, hover = dark background

---

## III. Page Structure & Widgets

### 3.1 Header (Global)
```
STEEX Trading Dashboard
──────────────────────────────────────────────────────────────────
Current Time: 2026-05-24 14:32:15 UTC  |  Mode: screen  |  Status: running
Last Update: 14:32:05  |  Next Update: 14:33:00  |  Refresh: [ Manual ] [ Auto 5s ]
```

---

### 3.2 Pipeline Status Card (Live)
```
LIVE PIPELINE: screen mode
──────────────────────────────────────────────────────────────────
Run ID: a7f3e2c1  Start: 14:32:15  Elapsed: 0:02:45

Stage                      Status    Agent          Progress
─────────────────────────────────────────────────────────────────
Load Screen Results        ✓ done    system         (load from disk)
Data Health Check          ✓ done    DataAgent      VIX: 22.5, Insider buys: 3
Market Regime Detection    ✓ done    RiskAgent      regime: cautious (conf: 87%)
━━━ FAN-OUT: Parallel Analysis ━━━
├─ Conservative Analysis   ⟳ running analysis_conservative  8/15 turns
├─ Aggressive Analysis     ⟳ running analysis_aggressive     6/15 turns
├─ Momentum Analysis       ▲ queued  analysis_momentum        0/15 turns
━━━ Waiting for merge... (2/3 complete)
Consensus Synthesis        ▲ queued  MetaAnalysisAgent  0/5 turns
Manager Decision           ▲ queued  ManagerAgent       0/3 turns

Next: Merge variant conclusions when all 3 analysis agents complete
```

---

### 3.3 Variant Conclusions Summary (Live)
```
ANALYSIS VARIANTS: Real-time Results
──────────────────────────────────────────────────────────────────

CONSERVATIVE (Quality Focus)
Candidates: 8  |  Top Score: 72.5  |  Avg Score: 61.2  |  Status: Running (8/15)
Top 3:
  1. AAPL  72.5  (insider: 68 | fundamental: 75 | momentum: 55)
  2. JPM   69.3  (insider: 72 | fundamental: 71 | momentum: 48)
  3. KO    67.8  (insider: 65 | fundamental: 78 | momentum: 42)

AGGRESSIVE (Growth Focus)
Candidates: 15  |  Top Score: 78.2  |  Avg Score: 65.4  |  Status: Running (6/15)
Top 3:
  1. NVDA  78.2  (momentum: 85 | sentiment: 72 | volume: 68)
  2. TSLA  76.9  (momentum: 82 | sentiment: 75 | volume: 70)
  3. MSFT  74.1  (momentum: 78 | sentiment: 68 | volume: 65)

MOMENTUM (Trend Following)
Candidates: 12  |  Top Score: 81.5  |  Avg Score: 68.9  |  Status: Queued
Ready for execution after other variants complete...

CONSENSUS SYNTHESIS (Pending)
Awaiting: All 3 variants must complete before synthesis
Status: 2/3 variants reporting data
ETA: ~2 minutes
```

---

### 3.4 Today's Trade Summary Card
```
TODAY'S EXECUTIONS: 2026-05-24
──────────────────────────────────────────────────────────────────
Screen Runs: 3  |  Total Candidates Identified: 47  |  Consensus Picks: 8
Entry Orders: 6 approved, 5 executed (1 pending)  |  Exit Orders: 2 executed

ENTRIES (Consensus Picks)
Ticker  Score  Price   Shares  Cost     Entry Status   P&L%   Days
────────────────────────────────────────────────────────────────────
AAPL    72.5   $185.22  100    $18,522  ✓ filled       +2.3%  0
JPM     69.3   $182.15   75    $13,661  ✓ filled       +0.8%  0
MSFT    74.1   $412.50   50    $20,625  ⟳ pending      —      0
NVDA    78.2   $875.30   20    $17,506  ✓ filled       +4.1%  0
TSLA    76.9   $248.75   40    $9,950   ✓ filled       +1.5%  0
KO      67.8   $61.22    150   $9,183   ✓ filled       -0.3%  0

EXITS
Ticker  Entry Date  Exit Reason       Exit Price  P&L     P&L%
────────────────────────────────────────────────────────────────────
XYZ     2026-05-20  Stop loss hit     $42.10      -$600   -3.5%
ABC     2026-05-18  Hold time expired $78.50      +$245   +1.2%

Portfolio Value: $187,450 | Cash Reserve: $52,100 | Exposure: 62.3%
Max Risk per Trade: $2,225 (12% of entry) | VIX: 22.5 (caution mode)
```

---

### 3.5 Variant Comparison Matrix
```
VARIANT CONSENSUS ANALYSIS
──────────────────────────────────────────────────────────────────

Stock    Conservative  Aggressive  Momentum  Consensus  Conviction  Options Flow
────────────────────────────────────────────────────────────────────────────────
AAPL     ✓ 72.5        ✓ 68.3      ✓ 75.2   HIGH       All 3 agree  Unusual calls
JPM      ✓ 69.3        —           ✓ 71.8   MEDIUM     2/3 agree    —
MSFT     —             ✓ 74.1      ✓ 73.5   MEDIUM     2/3 agree    —
NVDA     —             ✓ 78.2      ✓ 79.4   MEDIUM     2/3 agree    Unusual calls
TSLA     —             ✓ 76.9      ✓ 78.1   MEDIUM     2/3 agree    —
KO       ✓ 67.8        —           —        LOW        1/3 only     —
TECH     —             ✓ 65.2      —        LOW        1/3 only     —

CONSENSUS RULES:
HIGH (all 3): Include with max position size
MEDIUM (2/3): Include with standard position size
LOW (1/3): Exclude (unless options unusual activity confirms)

Summary: 5 picks with strong consensus, 1 pending high-conviction confirmation
```

---

### 3.6 Regime & Risk Status Card
```
MARKET REGIME & POSITION RISK
──────────────────────────────────────────────────────────────────

Current Regime: CAUTIOUS
├─ Confidence: 87%
├─ VIX Level: 22.5 (medium elevation)
├─ Yield Curve: Normal (2yr=4.2%, 10yr=4.5%)
├─ Market Breadth: 55% advancing (neutral)
└─ Dollar Strength: Moderate

Regime-Adjusted Thresholds (Active):
├─ Insider Weight Boost: 0.25 → 0.30 (favor insider buying)
├─ Sentiment Bar Raised: 30 → 40 (more selective)
├─ Momentum Min Return: 0.05 → 0.07 (higher bar for trends)
└─ Position Sizing: standard (no volatility reduction)

Current Portfolio Risk:
├─ Total Exposure: 62.3% of portfolio
├─ Max Single Position: 8.2% (NVDA)
├─ Max Sector: 22.1% (Tech)
├─ Cash Reserve: 27.9%
├─ Realized P&L Today: +$1,445.20
└─ Unrealized P&L: +$3,892.45 (avg +2.4% across 5 live trades)

Entry Gates (Risk-based):
├─ Allow new entries: YES (risk_off threshold not reached)
├─ Max new positions today: 5 remaining (7 allowed, 2 filled)
├─ Position cooling-off: 2 blocked (stop-loss cooldown)
└─ VIX-based caution: ACTIVE (reduce exposure if VIX >30)
```

---

### 3.7 Signal Confidence & Alpha Decay
```
SIGNAL STRENGTH & DEGRADATION TRACKING
──────────────────────────────────────────────────────────────────

Signal Performance (30-day rolling):
Signal           Win Rate  Recent Trend  Status         Weight
─────────────────────────────────────────────────────────────────
Momentum Score   58.2%     ↘ -2.1%       watch list ⚠   0.30 (std)
Insider Signal   72.5%     ↗ +1.8%       strong ✓       0.25 (std)
Volume Surge     61.3%     → stable      healthy ✓      0.15 (std)
Sentiment Score  45.8%     ↘ -4.2%       degrading ✗    0.15 → 0.08
Fundamental      68.9%     ↗ +0.5%       healthy ✓      0.10 (std)
Options Flow     64.2%     ↗ +2.1%       improving ↗    0.05 → 0.12

Overall Win Rate: 61.7% (last 15+ trades completed)
Recommended Weighting (from signal research):
├─ Momentum: Keep at 0.30 (on watch, but still valid)
├─ Sentiment: Reduce to 0.08 (degrading, use as confirmation only)
└─ Options: Raise to 0.12 (improving, strong additional signal)

Current Applied Weights:
├─ Momentum: 0.30 ✓
├─ Insider: 0.25 ✓
├─ Volume: 0.15 ✓
├─ Sentiment: 0.15 → Should be 0.08
├─ Fundamental: 0.10 ✓
└─ Options: 0.05 → Should be 0.12

Action: Run Learning mode to apply recommended weight adjustments
```

---

### 3.8 Session History & Audit Log
```
SESSION HISTORY: screen mode
──────────────────────────────────────────────────────────────────
[View: Last 7 Days | Last 30 Days | Custom Range]

Date        Time      Run ID   Duration  Consensus  Entries  Status   Notes
─────────────────────────────────────────────────────────────────────────────
2026-05-24  14:32:15  a7f3e2c1  (running)  pending   5/7 ready  ⟳      Live now
2026-05-24  10:15:42  c4d2a1b9  2:23      8 picks   6/6 exec   ✓       All filled
2026-05-24  08:02:15  e9f5c2d3  1:58      5 picks   4/5 exec   ✓       1 partial
2026-05-23  15:45:30  b1a2c3d4  2:05      7 picks   7/7 exec   ✓       Perfect exec
2026-05-23  10:20:10  f3d1e2c5  2:17      4 picks   2/4 exec   ⚠       2 rejected
2026-05-22  14:35:22  a5b6c7d8  2:31      6 picks   5/6 exec   ✓       1 pending
2026-05-22  09:55:05  c9d8e7f6  1:52      9 picks   8/9 exec   ✓       Partial fill

[Click row to expand details: variant breakdowns, agent turns, full consensus logic]
```

---

### 3.9 Configuration Snapshot
```
CURRENT CONFIGURATION & PARAMETERS
──────────────────────────────────────────────────────────────────

Scoring Weights (Current):
┌─ Momentum:    0.30 ─────────────────  (primary)
├─ Insider:     0.25 ─────────────────  (quality signal)
├─ Volume:      0.15 ──────────────     (confirmation)
├─ Sentiment:   0.15 ──────────────     (on watch)
├─ Fundamental: 0.10 ──────────────     (basic filter)
└─ Options:     0.05 ────────────       (bullish flow) [Was 0.05, now 0.12 for Tier 1+2]

Screening Thresholds (Default):
├─ Min Price: $5.00
├─ Min Volume: 500K shares/day
├─ Min Momentum (6M): 5.0% return
├─ Min Sentiment: 30 (0-100 scale)
├─ Max P/E Ratio: 50.0
├─ Min ROE: 5.0%
└─ Max Debt/Equity: 2.0

Variant Presets:
┌─ CONSERVATIVE
│  ├─ Momentum Min: 10.0% (vs 5.0%)
│  ├─ Sentiment Min: 50 (vs 30)
│  ├─ P/E Max: 30 (vs 50)
│  └─ ROE Min: 10.0% (vs 5.0%)
├─ AGGRESSIVE
│  ├─ Momentum Min: 2.0% (vs 5.0%)
│  ├─ Sentiment Min: 28 (vs 30)
│  ├─ P/E Max: 80 (vs 50)
│  └─ ROE Min: 0% (fundamental optional)
└─ MOMENTUM
   ├─ Momentum Min: 8.0% (vs 5.0%)
   ├─ Sentiment Min: 35 (vs 30)
   └─ Fundamentals: DISABLED

Position Management:
├─ Max Positions: 10 concurrent
├─ Daily Picks Target: 2
├─ Position Size: 4.0% of portfolio per trade
├─ Max Single Position: 10.0%
├─ Max Sector Exposure: 30.0%
├─ Stop Loss: 12% (tight when VIX >30)
└─ Hold Time: 30 trading days max (or exit signal)

[Edit Mode] [Load Preset] [Save As New] [Diff vs Previous]
```

---

## IV. API Specification

### Base URL
```
http://localhost:5000/api/v1
```

### Authentication
None for local dev. For production: Bearer token in `Authorization` header.

---

### 4.1 Live Pipeline Status

#### GET `/pipeline/current`
**Description:** Fetch live pipeline status and agent progress

**Response (200):**
```json
{
  "run_id": "a7f3e2c1",
  "mode": "screen",
  "status": "running",
  "started_at": "2026-05-24T14:32:15Z",
  "elapsed_seconds": 165,
  "stages": [
    {
      "name": "load_screen",
      "status": "done",
      "agent": "system",
      "message": "Loaded 47 candidates from previous run"
    },
    {
      "name": "data_check",
      "status": "done",
      "agent": "DataAgent",
      "metrics": {
        "vix_level": 22.5,
        "insider_purchases_found": 3,
        "data_freshness_seconds": 45
      }
    },
    {
      "name": "risk_assessment",
      "status": "done",
      "agent": "RiskAgent",
      "metrics": {
        "regime_name": "cautious",
        "regime_confidence": 0.87,
        "vix_level": 22.5
      }
    },
    {
      "name": "analysis_fan_out",
      "status": "running",
      "message": "Dispatching to 3 parallel variant agents",
      "variants": [
        {
          "name": "analysis_conservative",
          "status": "running",
          "progress": "8/15 turns",
          "current_candidates": 8,
          "top_score": 72.5
        },
        {
          "name": "analysis_aggressive",
          "status": "running",
          "progress": "6/15 turns",
          "current_candidates": 15,
          "top_score": 78.2
        },
        {
          "name": "analysis_momentum",
          "status": "queued",
          "progress": "0/15 turns",
          "current_candidates": 0,
          "top_score": null
        }
      ]
    },
    {
      "name": "merge_variants",
      "status": "queued",
      "agent": "MetaAnalysisAgent",
      "message": "Waiting for variant results: 2/3 complete"
    },
    {
      "name": "manager_decision",
      "status": "queued",
      "agent": "ManagerAgent",
      "message": "Awaiting consensus synthesis"
    }
  ],
  "estimated_completion": "2026-05-24T14:35:00Z"
}
```

---

### 4.2 Variant Conclusions (Live)

#### GET `/variants/results`
**Description:** Get current variant analysis results (live or from most recent completed run)

**Query Params:**
- `run_id` (optional): Specific run ID. Default: current/latest completed
- `variant` (optional): Filter to one variant ("conservative", "aggressive", "momentum")

**Response (200):**
```json
{
  "run_id": "a7f3e2c1",
  "timestamp": "2026-05-24T14:34:02Z",
  "variants": [
    {
      "variant": "conservative",
      "status": "complete",
      "candidates": [
        {
          "ticker": "AAPL",
          "composite_score": 72.5,
          "rank": 1,
          "scores": {
            "momentum": 62.0,
            "insider": 78.5,
            "volume": 65.0,
            "sentiment": 75.0,
            "fundamental": 82.0,
            "options": 58.0
          },
          "insider_buyers": 4,
          "momentum_6m": 0.18,
          "sentiment_score": 75.0,
          "fundamental_score": 82.0,
          "volume_surge": 1.3,
          "sector": "Technology"
        },
        {
          "ticker": "JPM",
          "composite_score": 69.3,
          "rank": 2,
          "scores": { /* ... */ }
        }
      ],
      "summary": {
        "total_candidates": 8,
        "avg_score": 61.2,
        "top_3_avg": 70.5
      }
    },
    {
      "variant": "aggressive",
      "status": "running",
      "progress": "6/15 agent turns",
      "candidates": [ /* partial results */ ],
      "summary": {
        "total_candidates": 15,
        "avg_score": 65.4,
        "top_3_avg": 76.4
      }
    },
    {
      "variant": "momentum",
      "status": "queued",
      "progress": "awaiting scheduler",
      "candidates": [],
      "summary": null
    }
  ]
}
```

---

### 4.3 Consensus Synthesis Result

#### GET `/consensus`
**Description:** Get meta-analysis consensus from all 3 variants

**Query Params:**
- `run_id` (optional): Specific run. Default: latest completed

**Response (200):**
```json
{
  "run_id": "c4d2a1b9",
  "timestamp": "2026-05-24T10:17:50Z",
  "status": "complete",
  "candidates": [
    {
      "ticker": "AAPL",
      "composite_score": 72.3,
      "variants_agreeing": 3,
      "high_conviction": true,
      "consensus_reason": "All variants picked. Conservative+aggressive+momentum consensus.",
      "variant_breakdown": {
        "conservative": { "score": 72.5, "rank": 1 },
        "aggressive": { "score": 68.3, "rank": 5 },
        "momentum": { "score": 75.2, "rank": 2 }
      },
      "options_confirmation": {
        "unusual_call_activity": true,
        "put_call_ratio": 0.42,
        "iv_skew": -1.8
      },
      "position_size_suggestion": "4.5% (max for high conviction)"
    },
    {
      "ticker": "MSFT",
      "composite_score": 73.8,
      "variants_agreeing": 2,
      "high_conviction": false,
      "consensus_reason": "Aggressive + momentum agree. Conservative missed (not fundamental enough).",
      "variant_breakdown": {
        "conservative": null,
        "aggressive": { "score": 74.1, "rank": 3 },
        "momentum": { "score": 73.5, "rank": 4 }
      },
      "position_size_suggestion": "3.0% (standard for 2/3 consensus)"
    }
  ],
  "summary": {
    "high_conviction_count": 1,
    "consensus_count": 5,
    "speculative_excluded": ["KO", "TECH"],
    "total_position_size_suggested": "28.5%"
  }
}
```

---

### 4.4 Trade Execution & Portfolio State

#### GET `/trades/today`
**Description:** Get today's entry and exit orders

**Query Params:**
- `status` (optional): "executed", "pending", "rejected", "all"

**Response (200):**
```json
{
  "date": "2026-05-24",
  "entries": [
    {
      "ticker": "AAPL",
      "order_id": "oid_12345",
      "consensus_score": 72.5,
      "entry_price": 185.22,
      "shares": 100,
      "cost": 18522,
      "entry_time": "2026-05-24T10:16:30Z",
      "status": "filled",
      "current_price": 189.35,
      "pnl": 413,
      "pnl_percent": 2.23,
      "stop_loss_price": 162.99,
      "hold_until": "2026-06-24"
    }
  ],
  "exits": [
    {
      "ticker": "XYZ",
      "order_id": "oid_12346",
      "exit_reason": "stop_loss_hit",
      "entry_price": 42.50,
      "exit_price": 42.10,
      "shares": 100,
      "pnl": -40,
      "pnl_percent": -0.94,
      "hold_days": 3,
      "exit_time": "2026-05-24T14:22:15Z",
      "status": "filled"
    }
  ],
  "summary": {
    "entries_approved": 6,
    "entries_filled": 5,
    "entries_pending": 1,
    "exits_executed": 2,
    "total_cost": 92340,
    "total_proceeds": 8920,
    "realized_pnl": 1445.20,
    "unrealized_pnl": 3892.45,
    "cash_reserve": 52100
  }
}
```

---

### 4.5 Portfolio State

#### GET `/portfolio/current`
**Description:** Get current portfolio composition and risk metrics

**Response (200):**
```json
{
  "timestamp": "2026-05-24T14:32:15Z",
  "portfolio_value": 187450,
  "cash": 52100,
  "invested": 135350,
  "exposure_percent": 72.2,
  "positions": [
    {
      "ticker": "AAPL",
      "shares": 100,
      "entry_price": 185.22,
      "current_price": 189.35,
      "market_value": 18935,
      "pnl": 413,
      "pnl_percent": 2.23,
      "sector": "Technology",
      "entry_date": "2026-05-24",
      "stop_price": 162.99,
      "days_held": 0
    }
  ],
  "risk": {
    "max_single_position_percent": 8.2,
    "max_sector_exposure": 22.1,
    "portfolio_vix_adjusted_exposure": 72.2,
    "regime": "cautious",
    "entry_gate_status": "open",
    "max_new_positions_allowed": 5,
    "positions_blocked_by_cooloff": 2
  }
}
```

---

### 4.6 Signal Performance & Alpha Decay

#### GET `/signals/confidence`
**Description:** Get per-signal win rates and degradation status

**Query Params:**
- `lookback_days` (optional): 7, 30, 60. Default: 30

**Response (200):**
```json
{
  "lookback_days": 30,
  "trades_analyzed": 18,
  "overall_win_rate": 0.617,
  "signals": [
    {
      "signal_name": "momentum_score",
      "win_rate": 0.582,
      "signal_count": 15,
      "trend": "degrading",
      "trend_percent": -2.1,
      "status": "watch_list",
      "current_weight": 0.30,
      "recommended_weight": 0.30,
      "comment": "Keep at 0.30 but monitor for further decline"
    },
    {
      "signal_name": "insider_signal",
      "win_rate": 0.725,
      "signal_count": 12,
      "trend": "improving",
      "trend_percent": 1.8,
      "status": "strong",
      "current_weight": 0.25,
      "recommended_weight": 0.25,
      "comment": "Healthy. Continue as-is."
    },
    {
      "signal_name": "options_flow",
      "win_rate": 0.642,
      "signal_count": 8,
      "trend": "improving",
      "trend_percent": 2.1,
      "status": "improving",
      "current_weight": 0.05,
      "recommended_weight": 0.12,
      "comment": "Recently improved. Increase from 0.05 → 0.12 for Tier 1+2"
    }
  ],
  "recommended_actions": [
    "Reduce sentiment weight to 0.08 (degrading)",
    "Increase options weight to 0.12 (improving)"
  ]
}
```

---

### 4.7 Regime & Risk Status

#### GET `/regime/current`
**Description:** Get market regime and risk parameters

**Response (200):**
```json
{
  "timestamp": "2026-05-24T14:32:15Z",
  "regime": {
    "name": "cautious",
    "confidence": 0.87,
    "vix_level": 22.5,
    "yield_curve_status": "normal",
    "market_breadth_percent": 55,
    "dollar_strength": "moderate"
  },
  "regime_adjustments": {
    "insider_weight": {
      "default": 0.25,
      "adjusted": 0.30,
      "reason": "cautious regime favors insider buying as quality proxy"
    },
    "sentiment_min_score": {
      "default": 30,
      "adjusted": 40,
      "reason": "higher bar in cautious environment"
    },
    "momentum_min_return": {
      "default": 0.05,
      "adjusted": 0.07,
      "reason": "harder to trend in mixed market"
    }
  },
  "entry_gate": {
    "allow_new_entries": true,
    "max_new_positions_today": 5,
    "positions_filled_today": 2,
    "vix_caution_level": 30,
    "vix_exit_level": 40,
    "current_vix": 22.5,
    "vix_status": "normal"
  }
}
```

---

### 4.8 Session History

#### GET `/sessions/history`
**Description:** List past pipeline runs with summary stats

**Query Params:**
- `mode` (optional): "screen", "enter", etc. Default: all
- `limit` (optional): Max results. Default: 20
- `days` (optional): Last N days. Default: 7
- `status` (optional): "completed", "failed", "running"

**Response (200):**
```json
{
  "sessions": [
    {
      "run_id": "c4d2a1b9",
      "mode": "screen",
      "started_at": "2026-05-24T10:15:42Z",
      "completed_at": "2026-05-24T10:17:50Z",
      "duration_seconds": 128,
      "status": "completed",
      "consensus_picks": 8,
      "entries_approved": 6,
      "entries_filled": 6,
      "exits_executed": 0,
      "variant_summary": {
        "conservative_candidates": 8,
        "aggressive_candidates": 15,
        "momentum_candidates": 12
      }
    },
    {
      "run_id": "e9f5c2d3",
      "mode": "screen",
      "started_at": "2026-05-24T08:02:15Z",
      "completed_at": "2026-05-24T08:04:08Z",
      "duration_seconds": 113,
      "status": "completed",
      "consensus_picks": 5,
      "entries_approved": 5,
      "entries_filled": 4,
      "exits_executed": 1,
      "variant_summary": {
        "conservative_candidates": 6,
        "aggressive_candidates": 12,
        "momentum_candidates": 9
      }
    }
  ],
  "pagination": {
    "total": 47,
    "limit": 20,
    "offset": 0
  }
}
```

---

### 4.9 Session Details (Drill-down)

#### GET `/sessions/{run_id}/details`
**Description:** Get detailed breakdown of a specific run

**Response (200):**
```json
{
  "run_id": "c4d2a1b9",
  "mode": "screen",
  "started_at": "2026-05-24T10:15:42Z",
  "completed_at": "2026-05-24T10:17:50Z",
  "duration_seconds": 128,
  "agents": [
    {
      "agent_name": "DataAgent",
      "status": "success",
      "duration_seconds": 8,
      "conclusion": {
        "all_healthy": true,
        "sources_checked": 5,
        "sources_healthy": 5,
        "vix_level": 22.5
      }
    },
    {
      "agent_name": "RiskAgent",
      "status": "success",
      "duration_seconds": 12,
      "conclusion": {
        "regime_name": "cautious",
        "regime_confidence": 0.87,
        "vix_level": 22.5
      }
    },
    {
      "agent_name": "analysis_conservative",
      "status": "success",
      "duration_seconds": 35,
      "conclusion": {
        "universe_size": 500,
        "final_candidates": 8,
        "candidates": [ /* ... */ ]
      }
    },
    {
      "agent_name": "analysis_aggressive",
      "status": "success",
      "duration_seconds": 38,
      "conclusion": { /* ... */ }
    },
    {
      "agent_name": "analysis_momentum",
      "status": "success",
      "duration_seconds": 32,
      "conclusion": { /* ... */ }
    },
    {
      "agent_name": "MetaAnalysisAgent",
      "status": "success",
      "duration_seconds": 15,
      "conclusion": {
        "candidates": [ /* consensus picks */ ],
        "high_conviction_count": 2,
        "consensus_count": 8
      }
    },
    {
      "agent_name": "ManagerAgent",
      "status": "success",
      "duration_seconds": 8,
      "conclusion": {
        "entries_approved": 6,
        "buys": [ /* ... */ ],
        "reasoning": "All consensus picks approved for execution"
      }
    }
  ]
}
```

---

### 4.10 Configuration Snapshot

#### GET `/config/current`
**Description:** Get current parameter configuration

**Response (200):**
```json
{
  "timestamp": "2026-05-24T14:32:15Z",
  "scoring_weights": {
    "weight_momentum": 0.30,
    "weight_insider": 0.25,
    "weight_volume": 0.15,
    "weight_sentiment": 0.15,
    "weight_fundamental": 0.10,
    "weight_options": 0.12
  },
  "screening_thresholds": {
    "min_price": 5.0,
    "min_volume": 500000,
    "momentum_min_return": 0.05,
    "sentiment_min_score": 30.0,
    "fundamental_max_pe": 50.0,
    "fundamental_min_roe": 0.05,
    "fundamental_max_debt_equity": 2.0
  },
  "variant_presets": {
    "conservative": {
      "momentum_min_return": 0.10,
      "sentiment_min_score": 50,
      "fundamental_max_pe": 30,
      "fundamental_min_roe": 0.10
    },
    "aggressive": {
      "momentum_min_return": 0.02,
      "sentiment_min_score": 28,
      "fundamental_max_pe": 80,
      "fundamental_min_roe": 0.0
    },
    "momentum": {
      "momentum_min_return": 0.08,
      "sentiment_min_score": 35,
      "fundamental_enabled": false
    }
  },
  "position_management": {
    "max_positions": 10,
    "daily_picks": 2,
    "position_size_pct": 0.04,
    "max_sector_pct": 0.30,
    "max_single_position_pct": 0.10,
    "stop_loss_pct": 0.12,
    "max_hold_days": 30
  }
}
```

---

## V. Data Models (JSON Schemas)

### Candidate Stock
```json
{
  "ticker": "AAPL",
  "composite_score": 72.5,
  "rank": 1,
  "momentum_6m": 0.18,
  "momentum_score": 62.0,
  "insider_buyers": 4,
  "insider_score": 78.5,
  "volume_surge": 1.3,
  "volume_score": 65.0,
  "sentiment_score": 75.0,
  "fundamental_score": 82.0,
  "options_score": 58.0,
  "sector": "Technology"
}
```

### Variant Result
```json
{
  "variant": "conservative",
  "status": "complete|running|queued",
  "candidates": [ /* array of Candidate Stocks */ ],
  "summary": {
    "total_candidates": 8,
    "avg_score": 61.2,
    "top_3_avg": 70.5
  }
}
```

### Consensus Pick
```json
{
  "ticker": "AAPL",
  "composite_score": 72.3,
  "variants_agreeing": 3,
  "high_conviction": true,
  "consensus_reason": "All variants picked...",
  "variant_breakdown": {
    "conservative": { "score": 72.5, "rank": 1 },
    "aggressive": { "score": 68.3, "rank": 5 },
    "momentum": { "score": 75.2, "rank": 2 }
  },
  "position_size_suggestion": "4.5%"
}
```

---

## VI. Refresh Strategy

**Auto-refresh (if enabled):**
- Pipeline Status: 1 second (live polling)
- Variant Results: 2 seconds (while running)
- Consensus: on-demand refresh (after all variants complete)
- Trade Summary: 5 seconds
- Signal Confidence: 30 seconds (slow-changing)
- Session History: 60 seconds (static after session end)

**Manual Controls:**
- Refresh buttons on each card
- Global refresh all
- Toggle auto-refresh on/off
- Set custom refresh interval

---

## VII. Implementation Notes for UI Agent

**Tech Stack Suggestions:**
- Backend: Flask/FastAPI (Python) to expose APIs above
- Frontend: HTML5 + CSS3 (no JS frameworks) for old-school feel
- Styling: Custom CSS grid, semantic HTML, monospace for data
- Real-time: WebSocket for pipeline status, polling for others
- Hosting: Local dev on localhost:5000, deploy behind simple nginx

**Code Organization:**
```
dashboard/
├── backend/
│   ├── api.py (Flask app with endpoints above)
│   ├── data.py (queries to orchestrator)
│   └── models.py (Pydantic schemas)
├── frontend/
│   ├── index.html (main page)
│   ├── css/
│   │   ├── layout.css (grid, responsive)
│   │   ├── typography.css (fonts, text)
│   │   └── components.css (cards, tables, buttons)
│   ├── js/ (minimal - mostly event listeners)
│   │   ├── refresh.js (auto-refresh logic)
│   │   └── realtime.js (WebSocket for pipeline)
│   └── assets/
│       └── favicon.ico
└── requirements.txt
```

**Key Design Principles:**
1. Info density > whitespace (but readable)
2. Tables > cards (for data)
3. Status colors = only color on page (green/red/yellow)
4. One main scroll, no sidebars
5. Copy-paste friendly (monospace for tickers/scores)
6. Keyboard accessible (tab navigation)
7. Mobile responsive (single column on small screens)
8. No loading spinners (show what you have)
9. Server time in header, never client time
10. Timestamps in UTC always

---

## VIII. Success Metrics for Dashboard

✓ Load time < 500ms (cold) / < 100ms (refresh)
✓ Real-time pipeline updates visible within 1 second
✓ All consensus picks clearly visible above the fold
✓ One-click drill-down to full session details
✓ No tooltips or hovers needed (all info in text)
✓ Print-friendly (CSS print styles included)
✓ Works in Chrome, Firefox, Safari, Edge (no JS fancy stuff)
