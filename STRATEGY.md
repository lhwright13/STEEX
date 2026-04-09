# STEEX Strategy Document

**Systematic Trading with Execution and Exit Excellence**

A fully automated, multi-agent trading system that screens the S&P 500 daily for momentum-driven entries, manages positions with adaptive trailing stops, and continuously optimizes its own parameters through a self-learning loop. The system runs on a Mac via cron, executes through Alpaca, and optionally delegates decisions to Claude AI agents backed by a fine-tuned local LLM.

---

## Stage Summary

STEEX breaks the trading day into discrete phases, each handled by a specialized pipeline mode:

| Time (ET) | Mode | What It Does |
|-----------|------|-------------|
| 7:00 AM | **Heartbeat** | Health check: API connectivity, broker sync, market calendar |
| 8:15 AM | **Screen** | 5-stage pipeline filters S&P 500 down to 2-5 ranked candidates |
| 9:45 AM | **Enter** | Executes buy orders after the opening auction settles |
| 11:00 AM | **Monitor** | Midday risk check: trailing stops, VIX spikes, exit signals |
| 1:30 PM | **Monitor** | Afternoon risk check (same logic, fresh prices) |
| 3:45 PM | **Stop Sync** | Updates trailing stops and syncs server-side GTC stops to Alpaca |
| 4:30 PM | **Post-Market** | End-of-day exits, post-mortem analysis, daily report |
| 6:00 PM Fri | **Learning** | Weekly self-optimization: decay analysis, weight tuning, OOS validation |
| 10:00 AM Sun | **Heartbeat** | Weekend connectivity check |

Every phase is gated by the Alpaca market calendar -- no runs on holidays, no entries when the market is closed. Server-side GTC stops on Alpaca protect positions even if the Mac sleeps or crashes.

### Core Thesis

1. **Momentum (2-12 months)** has the strongest academic backing for excess returns
2. **Insider buying clusters** are historically bullish signals
3. **Sentiment shifts** improve entry timing and filter noise
4. **Fundamental quality** avoids value traps and speculative names
5. **Volatility-based sizing and exits** prevent crash losses

### Strategy Overview

| Attribute | Value |
|-----------|-------|
| Strategy Type | Long-only equity |
| Universe | S&P 500 |
| Selection | 2 stocks per day |
| Hold Period | Up to 30 trading days |
| Position Size | 3-6% (volatility-adjusted) |
| Max Positions | 10 concurrent |
| Execution | Alpaca Markets (paper or live) |

### Data Sources

| Data | Source | Purpose |
|------|--------|---------|
| Price / Volume / MA | Yahoo Finance | Screening, P&L, indicators |
| Insider Trades (Form 4) | SEC EDGAR | Core buy signal |
| VIX Index | Yahoo Finance | Regime detection, risk |
| News Sentiment | Finnhub + VADER NLP | Stock-specific sentiment |
| Geopolitical Sentiment | GDELT Project | Macro/sector sentiment |
| Fundamentals | Yahoo Finance | P/E, ROE, debt quality |
| Options Flow | Yahoo Finance | Put/call ratio, IV |
| Earnings Calendar | Yahoo Finance | Blackout avoidance |
| Execution + Holdings | Alpaca Markets | Source of truth |

---

## Table of Contents

1. [Heartbeat](#1-heartbeat)
2. [Screening Pipeline](#2-screening-pipeline)
   - [2.1 Universe Filter (Price & Volume)](#21-stage-1---universe-filter-price--volume)
   - [2.2 Momentum Filter (Technical Trend)](#22-stage-2---momentum-filter-technical-trend)
   - [2.3 Insider Enrichment](#23-stage-3---insider-enrichment)
   - [2.4 Sentiment Filter](#24-stage-4---sentiment-filter)
   - [2.5 Fundamental Filter](#25-stage-5---fundamental-filter)
   - [2.6 Composite Ranking](#26-composite-ranking)
3. [Entry Execution](#3-entry-execution)
4. [Intraday Monitoring](#4-intraday-monitoring)
5. [Stop Sync](#5-stop-sync)
6. [Post-Market](#6-post-market)
7. [Risk & Regime Detection](#7-risk--regime-detection)
8. [Portfolio Construction](#8-portfolio-construction)
9. [Self-Learning Loop](#9-self-learning-loop)
10. [Agent System (Claude AI)](#10-agent-system-claude-ai)
11. [LLM Fine-Tuning Pipeline](#11-llm-fine-tuning-pipeline)
12. [Future Work](#future-work)
13. [Expected Outcomes](#expected-outcomes)

---

## 1. Heartbeat

A lightweight health check that validates system readiness before the trading day begins.

**Schedule:** 7:00 AM ET weekdays, 10:00 AM ET Sundays

**What it checks:**
- Alpaca API connectivity (equity, cash, buying power)
- Market calendar (is today a trading day? next open/close times)
- Position sync (broker positions match local state)
- Stop order integrity (no orphaned or missing stops)
- Last successful run timestamp (staleness detection)

**Code references:**
- `scripts/health_check.py` -- entry point
- `src/strategy/manager.py:QuantManager._sync_broker()` (line ~159) -- broker position reconciliation
- `src/broker/alpaca.py:get_all_stop_orders()` (line 209) -- stop order audit

**Output:** `data/heartbeat.json` with per-check status (OK/WARN/ERROR)

---

## 2. Screening Pipeline

The core alpha-generation engine. Filters the S&P 500 through 5 progressive stages, enriches survivors with multi-factor data, and ranks them by composite score.

**Schedule:** 8:15 AM ET weekdays (before market open)

**Orchestration:** `src/strategy/manager.py:QuantManager.run_screen()` delegates to `src/strategy/screener.py:StockScreener`

### 2.1 Stage 1 - Universe Filter (Price & Volume)

Removes illiquid and low-priced stocks that would produce unreliable signals or high slippage.

| Filter | Threshold | Configurable |
|--------|-----------|-------------|
| Minimum price | $5.00 | `min_price` |
| Minimum avg daily volume | 500,000 shares | `min_volume` |
| Earnings blackout | Exclude if earnings within 5 days | `earnings_blackout_days` |

**Code references:**
- `src/strategy/screener.py:StockScreener.stage_1_universe_filter()` (line 186) -- main filter logic
- `src/data/universe.py:Universe.get_sp500()` (line 37) -- S&P 500 list from Wikipedia with fallback to hardcoded list
- `src/data/universe.py:Universe.filter_by_price_volume()` -- price/volume gate
- `src/data/calendar.py:EarningsCalendar.filter_earnings_blackout()` (line 78) -- earnings date check via yfinance

**Typical output:** ~500 -> ~400 tickers

### 2.2 Stage 2 - Momentum Filter (Technical Trend)

Selects stocks in confirmed uptrends with positive multi-timeframe momentum.

| Filter | Threshold | Configurable |
|--------|-----------|-------------|
| 6-month return | >= 5.0% | `momentum_min_return` |
| 1-month return | >= 0.0% | `short_momentum_min_return` |
| Price vs 50-day SMA | Must be above | `require_above_ma_50` |
| Price vs 200-day SMA | Optional | `require_dual_ma` |
| Overextension filter | Exclude top 5th percentile | `overextension_percentile` (disabled) |

**Code references:**
- `src/strategy/screener.py:StockScreener.stage_2_momentum_filter()` (line 219) -- momentum filtering and MA alignment
- `src/indicators/momentum.py:MomentumCalculator.get_momentum_percentiles()` (line 48) -- 126-day and 21-day return calculation with percentile ranking
- `src/indicators/technical.py:TechnicalIndicators.check_trend_alignment()` -- checks price vs 50-day and 200-day SMA

**Data source:** Yahoo Finance OHLCV via yfinance (126 trading day lookback)

**Typical output:** ~400 -> ~80-120 tickers

### 2.3 Stage 3 - Insider Enrichment

Enriches candidates with SEC Form 4 insider transaction data. This is a **soft scoring stage**, not a hard filter -- all tickers pass through, but insider buying activity boosts the composite ranking score.

**Scoring formula:**

| Signal | Points |
|--------|--------|
| Per unique insider buyer | +10 |
| CEO or CFO purchase | +50 |
| Other officer purchase | +30 |
| Director purchase | +15 |
| 10% owner transaction | -10 |
| Total value > $100k | +20 |
| Total value > $500k | +40 |
| Total value > $1M | +60 |
| Cluster bonus (3+ insiders) | +25 |

**Code references:**
- `src/strategy/screener.py:StockScreener.stage_3_insider_enrich()` (line 278) -- enrichment loop
- `src/sec/scanners/insider.py:InsiderScanner.scan()` (line 20) -- fetches recent Form 4 filings from SEC EDGAR
- `src/sec/scanners/signals.py:calculate_cluster_score()` (line 37) -- insider scoring formula (role-weighted, value-tiered)
- `src/sec/parsers/form4.py` -- XML parser for SEC Form 4 filings

**Data source:** SEC EDGAR (last 30 days, configurable via `insider_lookback_days`). Cached to `data/cache/historical_insiders.json`.

**Typical output:** 100% pass-through with insider scores attached

### 2.4 Stage 4 - Sentiment Filter

Combines stock-specific news sentiment with geopolitical/macro sentiment to filter out stocks facing negative narrative headwinds.

| Filter | Threshold | Configurable |
|--------|-----------|-------------|
| Combined sentiment score | >= 30/100 | `sentiment_min_score` |

**Combined score formula:**
```
combined = (0.6 * stock_sentiment) + (0.4 * geopolitical_sentiment)
```

- **Stock sentiment** (60% weight): Alpha Vantage + Finnhub news feeds analyzed with VADER NLP using an enhanced financial lexicon for trading-relevant terms
- **Geopolitical sentiment** (40% weight): GDELT (Global Database of Events, Language, and Tone) with sector-level impact analysis across 100+ languages

**Sentiment labels:** < 35 Bearish, 35-45 Somewhat Bearish, 45-55 Neutral, 55-65 Somewhat Bullish, 65+ Bullish

**Code references:**
- `src/strategy/screener.py:StockScreener.stage_4_sentiment_filter()` (line 374) -- combined sentiment scoring and filtering
- `src/data/sentiment.py:SentimentProvider.get_sentiment()` -- stock-specific NLP sentiment via Finnhub + VADER
- `src/data/geopolitical.py:GeopoliticalSentimentProvider.get_macro_sentiment()` -- GDELT-based macro/sector sentiment

**Graceful degradation:** On API errors, defaults to neutral score (50) and passes the ticker through.

**Typical output:** ~80-120 -> ~50-80 tickers

### 2.5 Stage 5 - Fundamental Filter

Quality gate that removes over-leveraged, overvalued, or unprofitable stocks.

| Filter | Threshold | Configurable |
|--------|-----------|-------------|
| P/E ratio | <= 50 | `fundamental_max_pe` |
| Return on equity | >= 5% | `fundamental_min_roe` |
| Debt-to-equity | <= 2.0 | `fundamental_max_debt_equity` |

**Additional enrichment** (scoring only, no filtering):
- Volume surge analysis: recent volume / 20-day average volume ratio
- Options flow sentiment: put/call ratios, IV rank, bullish/bearish score (0-100)

**Code references:**
- `src/strategy/screener.py:StockScreener.stage_5_fundamental_filter()` (line 468) -- fundamental filtering + enrichment
- `src/data/fundamentals.py:FundamentalsProvider` (line 54) -- P/E, ROE, debt/equity, revenue growth, margins via yfinance (7-day cache)
- `src/indicators/technical.py:TechnicalIndicators.get_volume_surge_batch()` -- volume ratio analysis
- `src/data/options.py:OptionsProvider.get_options_sentiment()` (line 91) -- options chain analysis (put/call, IV, max pain)

**Typical output:** ~50-80 -> ~5-14 final candidates

### 2.6 Composite Ranking

Final candidates are ranked by a weighted composite score. The top N are selected for entry (default: 2 daily picks).

| Factor | Weight | Source |
|--------|--------|--------|
| Momentum | 30% | 6-month percentile rank |
| Insider Activity | 25% | Cluster score (normalized) |
| Volume Surge | 15% | Volume ratio percentile |
| Sentiment | 15% | Combined score (0-100) |
| Fundamental Quality | 10% | Composite quality score |
| Options Flow | 5% | Bullish/bearish sentiment |

**Code references:**
- `src/strategy/ranking.py:StockRanker.rank_stocks()` (line 122) -- weighted composite scoring, sorts by score descending
- `config/settings.py` (lines 162-169) -- weight definitions with Pydantic validation

**Output:** Ranked `RankedStock` objects with composite score, all component scores, and human-readable selection reasons. Saved to `data/screen_results/` for the entry mode to consume.

---

## 3. Entry Execution

Converts ranked candidates into live orders after the opening auction settles.

**Schedule:** 9:45 AM ET weekdays (15 min after open to avoid auction volatility)

**Sequence:**
1. **Broker sync** -- reconcile positions with Alpaca
2. **Quick risk check** -- abort if VIX spike or immediate exit signals
3. **Load screen results** from the 8:15 AM screening run
4. **Calculate position sizes** (regime-adjusted, volatility-bucketed)
5. **Place limit buy orders** with paired GTC stop-loss orders on Alpaca

### Position Sizing

Base size from config (e.g., 4% of equity), adjusted by:

**Regime multiplier:**

| Regime | Multiplier |
|--------|-----------|
| Risk On | 1.0x |
| Cautious | 0.5x |
| Risk Off | 0.25x |
| Crisis | 0.0x (no entries) |

**Volatility buckets (ATR-based):**

| ATR % | Classification | Position Size |
|-------|---------------|---------------|
| < 3% | Low volatility | 6% of portfolio |
| 3-6% | Medium volatility | 5% of portfolio |
| > 6% | High volatility | 3% of portfolio |

All sizes capped at `max_single_position_pct` (20%).

### Entry Rules

| Rule | Value |
|------|-------|
| Order Type | DAY limit order via Alpaca |
| Max Daily Entries | 2 |
| Min Score | 55.0 composite |
| Cooling-Off | 14 trading days after stop-loss before re-entry |

**Code references:**
- `src/strategy/manager.py:QuantManager.run_enter()` -- entry mode orchestration
- `src/strategy/manager.py:QuantManager._calculate_position_size_pct()` (line 640) -- regime multiplier + ATR volatility bucketing
- `src/strategy/manager.py:QuantManager.generate_buy_list()` -- converts ranked candidates to buy orders
- `src/strategy/manager.py:QuantManager.execute_entries()` -- order placement loop
- `src/broker/alpaca.py:buy()` (line 60) -- limit buy order via Alpaca API
- `src/broker/alpaca.py:place_stop_order()` (line 112) -- GTC stop-loss order (server-side crash protection). Falls back to market sell if stop price is already breached.

**Safety:** Every position gets a GTC stop order on Alpaca at `current_stop * (1 - server_stop_offset_pct)`, offset 0.5% below local stop to avoid noise triggers.

---

## 4. Intraday Monitoring

Checks all open positions for exit signals during trading hours.

**Schedule:** 11:00 AM and 1:30 PM ET weekdays

**Sequence:**
1. Broker sync -- reconcile with Alpaca
2. Data health check -- validate price feed status
3. Market regime assessment
4. Portfolio risk evaluation (trailing stops, P&L, drawdown)
5. Exit signal generation
6. Auto-execute immediate-urgency exits

### Exit Signals

| Signal | Urgency | Trigger |
|--------|---------|---------|
| Stop loss | Immediate | Price <= initial stop (10% below entry) |
| Trailing stop hit | Immediate | Price drops from high by trail % |
| VIX spike | Immediate | VIX > `vix_exit_level` (40) |
| Below 50-day MA | End of day | Price closes below 50-day SMA |
| Max hold exceeded | Next session | Position held > 30 days |
| Dead money | Next session | Flat/negative P&L after N days near entry |

### Trailing Stop Tiers

| Gain from Entry | Trail Distance from High |
|----------------|--------------------------|
| < 10% | 12% (initial stop: 10%) |
| 10-20% | 12% |
| 20-30% | 15% |
| > 30% | 15% |

Stops only ratchet up, never down.

### VIX-Based Risk Actions

| VIX Level | Action |
|-----------|--------|
| > 30 | Tighten all stops to 5% |
| > 40 | Exit 50% of all positions |

### Drawdown Rules

| Portfolio Drawdown | Action |
|--------------------|--------|
| 10% | Review strategy, continue |
| 15% | Reduce position sizes |
| 20% | Pause new entries |
| 25% | Exit all positions |

**Code references:**
- `src/strategy/manager.py:QuantManager.run_monitor()` (line ~1400) -- monitor mode orchestration
- `src/strategy/manager.py:QuantManager.get_exit_signals()` (line 537) -- exit signal aggregation
- `src/portfolio/risk.py:RiskManager.check_all_exits()` (line 71) -- evaluates all exit conditions (stop, VIX, MA, time, dead money) in priority order
- `src/portfolio/risk.py:RiskManager.check_vix_risk()` (line 122) -- VIX spike detection and action routing
- `src/strategy/manager.py:QuantManager.execute_exits()` -- sell order placement

---

## 5. Stop Sync

Pre-close synchronization of trailing stops with the broker.

**Schedule:** 3:45 PM ET weekdays

**Sequence:**
1. Broker sync
2. Update trailing stop levels for all positions (ratchet up on new highs)
3. For each position, cancel-and-replace the GTC stop order on Alpaca with the updated level

**Why this matters:** The local trailing stop logic tracks intraday highs and adjusts stop levels throughout the day. The server-side GTC stop on Alpaca is a crash-proof safety net -- if the Mac dies overnight, Alpaca will still execute the stop. This mode ensures the two stay in sync before close.

**Code references:**
- `src/strategy/manager.py:QuantManager.run_stop_sync()` (line ~1745) -- stop sync orchestration
- `src/portfolio/risk.py:RiskManager.update_stops()` (line 42) -- recalculates trailing stops per tier, returns dict of updated tickers
- `src/broker/alpaca.py:update_stop_order()` (line 177) -- cancel-replace GTC stop at new price
- `src/broker/alpaca.py:get_stop_order()` (line 184) -- query existing stop for a ticker

---

## 6. Post-Market

End-of-day wrap-up: force exits, analyze performance, generate reports.

**Schedule:** 4:30 PM ET weekdays

**Sequence:**
1. Broker sync with final closing prices
2. Portfolio assessment at market close
3. **Force-execute all end-of-day urgency exits** (below-MA signals converted to immediate)
4. Post-mortem analysis of recent trades (if enabled)
5. Generate and save daily report

**Post-mortem analysis** (runs on completed trades over configurable lookback window):
- Categorizes losses: whipsaw, dead money, gap down, regime miss
- Calculates entry score vs outcome correlation
- Estimates missed upside on premature exits
- Generates parameter tuning recommendations

**Code references:**
- `src/strategy/manager.py:QuantManager.run_post_market()` (line ~1452) -- post-market orchestration
- `src/portfolio/postmortem.py:PostMortemAnalyzer.generate_report()` -- trade analysis engine

**Output:** `data/reports/report_YYYYMMDD_HHMMSS.json` and `data/reports/latest.json`

---

## 7. Risk & Regime Detection

A multi-factor regime model that governs position sizing, entry permission, and exit aggressiveness across the entire system.

### Regime Factors

| Factor | Weight | Source | Low Risk | High Risk |
|--------|--------|--------|----------|-----------|
| VIX | 40% | CBOE VIX | < 15 (0-20 pts) | > 35 (70-100 pts) |
| Yield Curve | 20% | 10Y-2Y spread | Normal > 1.0% (10 pts) | Inverted < -0.2% (70-100 pts) |
| Market Breadth | 20% | % above 200-day MA | > 70% (low risk) | < 30% (high risk) |
| Dollar Strength | 20% | DXY trend | Weakening (25 pts) | Strengthening (75 pts) |

### Regime Classifications

| Regime | Composite Score | Sizing Multiplier | Entries Allowed |
|--------|----------------|-------------------|----------------|
| Risk On | < 40 | 1.0x | Yes |
| Cautious | 40-60 | 0.5x | Yes |
| Risk Off | 60-80 | 0.25x | Yes |
| Crisis | >= 80 | 0.0x | No |

**Additional signals:**
- **Sector rotation:** compares cyclical (XLY, XLK) vs defensive (XLP, XLU) 1-month performance -> "cyclical", "defensive", or "mixed"
- **Confidence score:** variance-weighted measure of how decisive the regime signal is (higher = more agreement among factors)

**Code references:**
- `src/regime/detector.py:RegimeDetector.detect_regime()` (line 170) -- weighted composite risk scoring -> MacroRegime
- `src/regime/detector.py:RegimeDetector._vix_risk_score()` -- VIX component (40% weight)
- `src/regime/detector.py:RegimeDetector._yield_risk_score()` -- yield curve component (20% weight)
- `src/regime/detector.py:RegimeDetector._breadth_risk_score()` -- market breadth component (20% weight)
- `src/regime/detector.py:RegimeDetector._dollar_risk_score()` -- dollar strength component (20% weight)
- `src/regime/detector.py:RegimeDetector.get_sector_rotation()` (line 137) -- cyclical vs defensive performance comparison

---

## 8. Portfolio Construction

Builds diversified portfolios from ranked candidates using correlation constraints and risk-parity weighting.

**Selection algorithm:** Greedy -- takes the top-ranked candidate, then iteratively adds the next-highest-scored stock only if:
1. Pairwise correlation with all already-selected stocks is below `max_pairwise_corr`
2. Sector count does not exceed `max_per_sector`

**Risk-parity weighting:** Inverse-variance (1/vol^2) so each position contributes approximately equal risk to the portfolio.

### Position Limits

| Limit | Value |
|-------|-------|
| Max single position | 20% of portfolio |
| Max sector exposure | 30% of portfolio |
| Max positions | 10 concurrent |
| Min cash reserve | 10% of portfolio |

**Code references:**
- `src/portfolio/construction.py:PortfolioConstructor.select_portfolio()` (line 150) -- greedy selection with correlation and sector constraints
- `src/portfolio/construction.py:PortfolioConstructor.compute_correlation_matrix()` (line 59) -- 90-day pairwise return correlations
- `src/portfolio/construction.py:PortfolioConstructor.compute_volatilities()` (line 92) -- annualized volatility (daily std * sqrt(252))
- `src/portfolio/construction.py:PortfolioConstructor.risk_parity_weights()` (line 119) -- inverse-variance weighting for equal risk contribution

---

## 9. Self-Learning Loop

A weekly optimization cycle that analyzes trade outcomes, detects signal decay, proposes weight changes, validates them out-of-sample, and applies them with a full audit trail.

**Schedule:** Fridays 6:00 PM ET

### Phase Chain

```
Phase 1: PostMortem (trade analysis, configurable lookback)
  -> Phase 2: Alpha Decay (signal health monitoring)
    -> [if degrading] Phase 3: Signal Research (factor testing + weight optimization)
      -> Phase 4: Walk-Forward OOS Validation (2-fold, Sharpe > 0, win_rate > 50%)
        -> [if validated] Phase 5: ConfigWriter (applies changes to config.yaml)
          -> Phase 6: Gap Identification (flags unresolvable issues for human review)
```

### Phase 1: Post-Mortem

Analyzes recent trades to identify loss patterns (whipsaw, dead money, gap down, regime miss). Requires minimum trade count before analysis is meaningful.

**Code:** `src/learning/loop.py:LearningLoop._run_postmortem()` (line 194)

### Phase 2: Alpha Decay

Monitors signal performance over time. Triggers research if degrading signals detected or score-outcome correlation drops below 0.10.

**Code:** `src/learning/loop.py:LearningLoop._run_alpha_decay()` (line 231)

### Phase 3: Signal Research

Generates feature matrix from historical signals using the walk-forward backtester, analyzes information coefficients and p-values per signal, identifies redundant signal pairs, and proposes optimized weights.

**Code:** `src/learning/loop.py:LearningLoop._run_signal_research()` (line 252)

### Phase 4: OOS Validation

Runs 2-fold walk-forward backtest with proposed weights. Both folds must achieve Sharpe > 0 and win rate > 50% for changes to be applied.

**Code:** `src/learning/loop.py:LearningLoop._run_oos_validation()` (line 325)

**Backtester:** `src/backtest/walkforward.py:WalkForwardBacktester.run_walk_forward()` (line 248) -- replays the full screening pipeline historically using `HistoricalPriceProvider` truncated to each reference date (no lookahead bias). Disables sentiment, fundamental, and options sources during backtest to avoid stale data contamination.

### Phase 5: Apply Changes

Writes validated parameter changes to `config/config.yaml` with safety bounds.

**Safety constraints:**
- Max weight change per cycle: 10% per parameter
- All weights auto-normalized to sum to 1.0 after changes
- Parameters clamped to absolute bounds (e.g., `initial_stop_pct` between 5-20%)
- **Never applied during market hours** (9:30 AM - 4:00 PM ET)
- Full audit trail in `data/learning/config_history.json`

**Code:**
- `src/learning/config_writer.py:ConfigWriter.propose_changes()` (line 57) -- validates against PARAM_BOUNDS, clamps per-cycle deltas
- `src/learning/config_writer.py:ConfigWriter.apply_changes()` (line 159) -- writes to config.yaml with audit entry
- `src/learning/config_writer.py:ConfigWriter._normalize_weights()` (line 224) -- ensures weights sum to 1.0

### Phase 6: Gap Identification

Flags unresolvable situations for human review: insufficient trade data, persistent signal degradation despite research, dominant loss categories, failed OOS validation.

**Code:** `src/learning/loop.py:LearningLoop._identify_gaps()` (line 422)

**Output files:**
- `data/learning/learning_journal.json` -- timestamped log of all learning actions
- `data/learning/weight_recommendations.json` -- latest recommended weights
- `data/learning/gaps.json` -- knowledge gaps requiring human review
- `data/learning/config_history.json` -- audit trail of all config changes

---

## 10. Agent System (Claude AI)

An alternative execution path where Claude AI agents make trading decisions instead of the deterministic pipeline. Registry-driven architecture -- add new agents via YAML config with no code changes.

### Architecture

```
Orchestrator
  |
  |-- Sub-Agents (run in sequence, each gets MCP tools)
  |     |-- DataAgent:      data health, VIX, insider activity
  |     |-- RiskAgent:      regime, portfolio risk, exit signals
  |     |-- ScreeningAgent: candidate analysis and scoring
  |     |-- TechnicalAgent: MA alignment, support/resistance, trend
  |     |-- SentimentAgent: market narrative and impact
  |
  |-- ManagerAgent (synthesizes sub-agent conclusions -> final decision)
  |     |-- entries_approved: bool
  |     |-- buys: [{ticker, shares, price, stop}]
  |     |-- sells: [{ticker, shares, reason}]
  |
  |-- ExecutionAgent (places orders based on manager decision)
  |-- ReportAgent (compiles session narrative)
```

**MCP server** (`src/agents/mcp_server.py`) exposes ~20 QuantManager tools as a FastMCP stdio server. Agents call these tools to interact with the trading system: `run_screening()`, `get_regime()`, `assess_portfolio_risk()`, `execute_entries()`, `execute_exits()`, etc.

**Prompt resolution:** Disk override (`data/agents/prompts/{name}.md`) takes priority over code default (`src/agents/prompts/{name}.py`), allowing prompt evolution without code changes.

**Fallback:** If a critical agent fails, the orchestrator automatically falls back to the deterministic QuantManager pipeline.

**Prompt evolution:** Agents include self-improvement metadata (`AgentMeta`) in their conclusions. The `PromptEvolver` collects suggestions and can rewrite prompts, with safety constraints that prevent removal of critical phrases (e.g., "source_of_truth", "GTC stop", "stop-loss").

**Code references:**
- `src/agents/orchestrator.py:Orchestrator.run_mode()` (line 407) -- mode entry point, routes to agent pipeline or deterministic fallback
- `src/agents/orchestrator.py:Orchestrator._run_sub_agents()` (line 532) -- sequential sub-agent execution with prompt resolution
- `src/agents/orchestrator.py:Orchestrator._run_manager()` (line 565) -- manager synthesis of sub-agent conclusions
- `src/agents/orchestrator.py:Orchestrator._run_agent()` (line 205) -- core method: launches claude CLI with MCP config, parses JSON conclusion
- `src/agents/registry.py:AgentRegistry` (line 56) -- YAML-driven agent definitions, prompt and conclusion type resolution
- `src/agents/conclusions.py` -- Pydantic models for structured agent output (DataConclusion, RiskConclusion, ScreeningConclusion, ManagerDecision, etc.)
- `src/agents/evolution.py:PromptEvolver` (line 40) -- prompt self-improvement with safety constraints (max 1 rewrite/week/agent)
- `src/agents/mcp_server.py` -- FastMCP stdio server exposing ~20 QuantManager tools
- `config/agents.yaml` -- declarative agent definitions and mode sequences

---

## 11. LLM Fine-Tuning Pipeline

Trains a local LFM2.5-1.2B language model on STEEX trading data to enhance agent decision-making. Uses free GPU platforms with HF Hub as a checkpoint relay for cross-platform training continuity.

### Training Data

| Type | Input | Output |
|------|-------|--------|
| Screening analysis | Signal data (momentum, insider, sentiment, etc.) | Buy/pass recommendation with reasoning |
| Trade post-mortem | Completed trade (entry, exit, P&L) | Loss categorization and lessons |
| Regime assessment | VIX, breadth, yield data | Regime classification and action plan |

### Cross-Platform Relay

```
Session 1: Colab (T4, 12h free/week)
  -> checkpoint uploaded to HF Hub
    -> Session 2: Kaggle (T4x2, 30h free/week)
      -> checkpoint uploaded to HF Hub
        -> Session 3: Modal (A100, ~$30/month free credits)
          -> ...continues until convergence
```

**Platform limits:**

| Platform | GPU | Free Tier |
|----------|-----|-----------|
| Colab | T4 16GB | 12h/session, ~40h/week |
| Kaggle | T4 x2 | 12h/session, 30h/week |
| Lightning AI | T4 | 4h/session, 22h/month |
| Modal | Various (up to A100) | ~$30/month credits |

### Validation & Export

- **Loss spike detection:** abort if loss jumps > 20% in a single step (overfitting signal)
- **Convergence detection:** training complete if loss plateaus for 50 steps
- **Export:** best checkpoint quantized to GGUF (Q4_K_M) and registered with Ollama for local Apple Silicon inference

**Code references:**
- `src/llm/pipeline.py:TrainingPipeline` (line 49) -- controller: dispatch to platforms, monitor progress, validate checkpoints, export
- `src/llm/pipeline.py:TrainingPipeline.dispatch()` (line 254) -- selects best available platform by quota/GPU tier
- `src/llm/pipeline.py:TrainingPipeline.run_check()` (line 152) -- single poll cycle (called by cron every 15 min)
- `src/llm/pipeline.py:TrainingPipeline.export_best()` (line 332) -- GGUF export + Ollama registration
- `src/llm/train.py:train()` (line 257) -- LoRA fine-tuning with Unsloth (2x faster, 50% less memory)
- `src/llm/train.py:CrashSafeCallback` (line 176) -- pushes checkpoints to Hub during training for crash resilience
- `src/llm/hub_relay.py:HubRelay` (line 17) -- HF Hub checkpoint relay (upload, download, sync, hash-based dedup)
- `src/llm/dataset_builder.py:LLMDatasetBuilder` -- converts STEEX trading data to chat-format training examples
- `src/llm/inference.py:LFMInference` (line 36) -- local inference engine (Ollama, llama.cpp, MLX backends)
- `src/llm/checkpoint_validator.py:CheckpointValidator` -- loss spike and convergence detection

---

## Future Work

### Performance Optimization
- **Parallelize SEC insider scan** -- `InsiderScanner.scan()` fetches ~200 filings serially; thread pool (10 workers) would cut Stage 3 from minutes to seconds
- **Parallelize stages 2, 4, 5** -- MA checks, sentiment, and fundamental lookups are independent per-ticker; `ThreadPoolExecutor` would reduce screen mode from 5+ min toward the 90s target
- **Cache-aware prefetching** -- eliminate duplicate VIX/SPY fetches between prefetcher and `refresh_data()`

### Architecture
- **Split QuantManager** -- at 1,800+ lines it has grown into a god object; extract `ScreenPipeline`, `ExecutionEngine`, and `ModeRunner` behind a thin orchestrating facade
- **Structured signal attribution** -- replace free-text `reasons: List[str]` with `EntryAttribution(signal, score, weight)` for direct signal-to-outcome mapping in the learning loop
- **Decompose Broker ABC** -- 17 abstract methods across order types, stops, calendar, assets; split into focused interfaces (`Broker`, `StopManager`, `OrderManager`)

### Strategy Enhancements
- **Execution quality feedback** -- feed `ExecutionQualityTracker` slippage data back into the learning loop to penalize signals that produce high-slippage entries
- **Intraday re-entry logic** -- currently a stock that stops out cannot re-enter the same day, even if conditions recover
- **Sector-aware regime tilting** -- use sector rotation signal to bias screening toward cyclical or defensive names based on regime

### Infrastructure
- **Real-time dashboard** -- current Flask dashboard reads JSON files; upgrade to websocket-based live updates
- **Cache observability** -- expose cache stats (size, hit rate, age), selective invalidation, warm-by-pattern

---

## Expected Outcomes

STEEX implements a momentum + insider + quality factor strategy. Based on academic research and historical backtests of similar approaches:

### Comparable Strategy Benchmarks

| Strategy | Annual Return | Max Drawdown | Sharpe Ratio | Source |
|----------|-------------|-------------|-------------|--------|
| Momentum (top decile, monthly) | 12-16% | 40-55% | 0.5-0.8 | Jegadeesh & Titman (1993) |
| Momentum + Quality | 14-18% | 30-40% | 0.7-1.0 | Asness, Frazzini (2013) |
| Insider following (clustered buys) | 8-12% alpha | N/A | N/A | Lakonishok & Lee (2001) |
| Multi-factor (mom + val + quality) | 10-15% | 25-35% | 0.8-1.2 | Fama-French 5-factor literature |

### STEEX-Specific Expectations

**Advantages over pure factor strategies:**
- Adaptive regime detection reduces drawdowns by cutting position sizes (or halting entries) during elevated-risk environments
- Trailing stops with server-side GTC orders provide hard downside protection regardless of system state
- Self-learning loop continuously re-optimizes signal weights, counteracting alpha decay
- Insider signal adds an information edge not captured by price-only momentum

**Realistic targets for a retail system:**

| Metric | Target Range | Rationale |
|--------|-------------|-----------|
| Annual return | 10-15% | Conservative, accounting for execution costs and cash drag from regime-driven position sizing |
| Max drawdown | 15-25% | Trailing stops + regime sizing should limit tail risk vs unconstrained momentum (40-55%) |
| Sharpe ratio | 0.6-1.0 | Multi-factor diversification improves risk-adjusted returns |
| Win rate | 50-60% | Momentum strategies have moderate win rates with positive skew |
| Average hold | 10-30 days | Configurable via `max_hold_days`, driven by trailing stop exits |

**Key risks:**
- **Momentum crashes** -- sudden factor reversal, historically occurs 2-3x per decade (Daniel & Moskowitz, 2016)
- **Whipsaw in range-bound markets** -- stop-outs followed by recovery, especially in sideways regimes
- **Data source reliability** -- free APIs (yfinance, Finnhub, GDELT) have rate limits and occasional outages
- **Regime detection lag** -- macro indicators (VIX, breadth, yield curve) are inherently backward-looking

### References

- Jegadeesh & Titman (1993) -- Returns to buying winners and selling losers
- Seyhun (1986) -- Insiders' profits, costs of trading, and market efficiency
- Daniel & Moskowitz (2016) -- Momentum crashes
- Barroso & Santa-Clara (2015) -- Momentum has its moments: risk management for momentum
- Asness, Frazzini, Pedersen (2013) -- Quality minus junk

---

> **Disclaimer:** This strategy is for educational and research purposes. Past performance of similar strategies does not guarantee future results. All trading involves risk of loss. Always paper trade before committing real capital, and size positions appropriately for your risk tolerance.

---

## Key Parameters

All tunable in `config/config.yaml` with Pydantic validation in `config/settings.py`:

```yaml
# Momentum
momentum_lookback_days: 126
momentum_min_return: 0.15

# Position management
max_positions: 10
position_size_pct: 0.04
max_sector_pct: 0.30
max_single_position_pct: 0.20

# Exits
initial_stop_pct: 0.10
max_hold_days: 30
trail_stop_10: 0.12
trail_stop_20: 0.15
trail_stop_30: 0.15

# VIX
vix_caution_level: 30
vix_exit_level: 40
vix_tight_stop_pct: 0.05

# Scoring weights
weight_momentum: 0.30
weight_insider: 0.25
weight_volume: 0.15
weight_sentiment: 0.15
weight_fundamental: 0.10
weight_options: 0.05
```
