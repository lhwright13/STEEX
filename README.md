# STEEX

**Systematic Trading with Execution and Exit Excellence**

An automated trading system that screens the S&P 500 daily for momentum-driven entries, manages positions with adaptive trailing stops, and continuously optimizes its own parameters through a self-learning loop. Executes through Alpaca Markets with server-side crash protection, and optionally delegates decisions to Claude AI agents via the Claude CLI.

> For the full strategy breakdown with code references, see [STRATEGY.md](STRATEGY.md).

---

## Architecture

STEEX supports two execution modes:

**Deterministic mode** (default) -- QuantManager runs a 5-stage screening pipeline, composite ranking, regime-aware position sizing, and adaptive trailing stops. Fast, predictable, no external dependencies beyond market data APIs.

**Agent mode** (`--agent` flag) -- An Orchestrator launches Claude AI sub-agents via the claude CLI. Each agent reasons independently with access to trading tools via an MCP server, then a ManagerAgent synthesizes their conclusions into a final decision. Falls back to deterministic mode on failure.

```
Deterministic:                     Agent Mode:
QuantManager                       Orchestrator (registry-driven)
    |                                  |
    +-- 5-stage screening              +-- DataAgent (claude CLI + MCP tools)
    +-- composite ranking              +-- RiskAgent (claude CLI + MCP tools)
    +-- regime detection               +-- AnalysisAgent (claude CLI + MCP tools)
    +-- risk / trailing stops          +-- ManagerAgent (synthesizes conclusions)
    +-- execution via Alpaca           +-- ExecutionAgent (claude CLI + MCP tools)
    +-- reporting                      +-- ReportAgent (claude CLI + MCP tools)
```

Agent definitions live in `config/agents.yaml` -- adding a new agent requires no code changes to the orchestrator.

---

## Daily Schedule

The system breaks the trading day into discrete phases, each run via cron:

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

All modes are gated by the Alpaca market calendar -- no runs on holidays, no entries when the market is closed. Server-side GTC stops on Alpaca protect positions even if the host machine sleeps or crashes.

---

## Screening Pipeline

```
S&P 500 (~503 stocks)
    |  Stage 1: Universe filter (price > $5, volume > 500K, no earnings within 5 days)
    v
  ~400
    |  Stage 2: Momentum (6M return > 5%, 1M > 0%, above 50-day MA)
    v
  ~100
    |  Stage 3: Insider enrichment (SEC Form 4 cluster buy scoring)
    v
  ~100 (soft scoring, no filtering)
    |  Stage 4: Sentiment (Finnhub + VADER NLP + GDELT geopolitical, min 30/100)
    v
   ~60
    |  Stage 5: Fundamentals (P/E < 50, ROE > 5%, debt/equity < 2.0)
    v
   ~10 candidates -> Composite ranking -> Top 2 entries
```

### Scoring Weights

| Factor | Weight | Source |
|--------|--------|--------|
| Momentum | 30% | 6-month return percentile |
| Insider | 25% | SEC Form 4 cluster buy score |
| Volume | 15% | Volume surge percentile |
| Sentiment | 15% | Combined stock + geopolitical NLP |
| Fundamental | 10% | P/E, ROE, debt/equity composite |
| Options | 5% | Put/call ratio, IV rank |

---

## Risk Management

### Regime Detection

A 4-factor model governs position sizing and entry permission:

| Factor | Weight | Source |
|--------|--------|--------|
| VIX | 40% | CBOE VIX index |
| Yield Curve | 20% | 10Y-2Y Treasury spread |
| Market Breadth | 20% | % of S&P 500 above 200-day MA |
| Dollar Strength | 20% | DXY trend direction |

| Regime | Sizing | Entries |
|--------|--------|---------|
| Risk On (score < 40) | 1.0x | Yes |
| Cautious (40-60) | 0.5x | Yes |
| Risk Off (60-80) | 0.25x | Yes |
| Crisis (>= 80) | 0.0x | No |

### Exit Rules

| Condition | Urgency | Default |
|-----------|---------|---------|
| Stop loss | Immediate | -10% from entry |
| Trailing stop | Immediate | -12% to -15% from high (tiered by gain) |
| VIX spike (> 40) | Immediate | Exit 50% of positions |
| Below 50-day MA | End of day | Auto-exit at post_market |
| Max hold time | Next session | 30 trading days |

### Position Limits

| Limit | Value |
|-------|-------|
| Position size | 3-6% (ATR volatility-adjusted) |
| Max positions | 10 concurrent |
| Max single position | 10% of portfolio |
| Max sector exposure | 30% of portfolio |

---

## Self-Learning Loop

A weekly optimization cycle (Fridays 6:00 PM ET) that:

1. **Post-mortem** -- analyzes recent trades, categorizes losses (whipsaw, dead money, gap down)
2. **Alpha decay** -- monitors signal health, detects degrading factors
3. **Signal research** -- tests factor importance, proposes optimized weights
4. **OOS validation** -- 2-fold walk-forward backtest (requires Sharpe > 0, win rate > 50%)
5. **Apply** -- writes validated changes to config with safety bounds (max 10% change/cycle, auto-normalized to sum to 1.0)
6. **Gap flagging** -- flags unresolvable issues for human review

Changes are never applied during market hours. Full audit trail in `data/learning/`.

---

## Data Sources

| Data | Source | Purpose |
|------|--------|---------|
| Prices / OHLCV | Yahoo Finance | Screening, P&L, indicators |
| Insider Trades (Form 4) | SEC EDGAR | Core buy signal |
| VIX | Yahoo Finance | Regime detection, risk |
| Sentiment | Finnhub + VADER NLP | Stock-specific sentiment |
| Geopolitical | GDELT Project | Macro/sector sentiment |
| Fundamentals | Yahoo Finance | P/E, ROE, debt/equity quality |
| Options Flow | Yahoo Finance | Put/call ratio, IV rank |
| Earnings Calendar | Yahoo Finance | Blackout avoidance |
| Execution + Holdings | Alpaca Markets | Source of truth |

---

## Quick Start

### Prerequisites

- Python 3.10+
- macOS (for cron scheduler; pipeline works on any OS)
- Alpaca Markets account (paper trading is free)
- Claude Code CLI (optional, for `--agent` mode only)

### Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env and fill in your Alpaca API keys (paper trading is free at alpaca.markets)
# Optionally add Finnhub and Alpha Vantage keys for sentiment enrichment
```

### Running

```bash
# Preview what the system would do (no orders)
venv/bin/python scripts/run_manager.py screen --paper --dry-run

# Paper trading with confirmation prompts
venv/bin/python scripts/run_manager.py screen --paper
venv/bin/python scripts/run_manager.py enter --paper

# Auto-confirm entries
venv/bin/python scripts/run_manager.py enter --paper --yes

# Agent mode (requires claude CLI)
venv/bin/python scripts/run_manager.py screen --paper --agent
```

### Pipeline Modes

| Mode | Command | Description |
|------|---------|-------------|
| `screen` | `run_manager.py screen` | Pre-open: data refresh, 5-stage screening, ranking |
| `enter` | `run_manager.py enter` | Post-open: load screen results, execute entries, place stops |
| `monitor` | `run_manager.py monitor` | Midday: risk check, trailing stops, exit signals |
| `stop_sync` | `run_manager.py stop_sync` | Pre-close: sync trailing stops to Alpaca GTC orders |
| `post_market` | `run_manager.py post_market` | EOD: final exits, post-mortem, daily report |
| `learning` | `run_learning.py` | Weekly: signal research, parameter optimization |

| Flag | Description |
|------|-------------|
| `--paper` | Paper trading via Alpaca |
| `--live` | Real money via Alpaca |
| `--dry-run` | Preview only, no orders |
| `--yes` | Auto-confirm entries |
| `--agent` | Use Claude AI agents instead of deterministic pipeline |

### Verifying agent buy/sell end-to-end

The `test_roundtrip` mode spins up a single `test_trader` agent that buys a small
paper-mode position via MCP and immediately sells it. Useful for verifying that
orchestrator subprocess spawning, MCP tool routing, and Alpaca paper execution
are all wired correctly without running the full screening pipeline.

```bash
# Agent mode (validates orchestrator -> claude CLI -> MCP -> broker)
venv/bin/python scripts/run_manager.py test_roundtrip --paper --agent --ticker AAPL

# Deterministic fallback (validates broker plumbing only)
venv/bin/python scripts/run_manager.py test_roundtrip --paper --ticker AAPL
```

The `place_paper_order` MCP tool is hard-gated to paper mode and capped at $1000
per call. Live mode and amounts above the cap are refused before any broker call.

### Scheduler

```bash
scheduler/install.sh          # Install cron schedule
scheduler/install.sh --show   # Preview cron entries
scheduler/uninstall.sh        # Remove cron schedule
scheduler/run.sh screen       # Manual run of a specific mode
```

### Tests

```bash
venv/bin/python -m pytest tests/
```

---

## Configuration

All parameters live in `config/config.yaml` with Pydantic validation in `config/settings.py`. Any setting can be overridden with the `STEEX_` prefix:

```bash
export STEEX_INITIAL_STOP_PCT=0.08
export STEEX_MAX_POSITIONS=15
```

Priority: init settings > environment variables > YAML config > defaults.

API keys are environment variables only -- never in config files.

---

## Project Structure

```
STEEX/
  config/
    config.yaml              # All tunable parameters
    settings.py              # Pydantic settings with YAML + env override
    agents.yaml              # Agent definitions and mode sequences
  src/
    strategy/
      manager.py             # QuantManager - deterministic orchestrator
      screener.py            # 5-stage screening pipeline
      ranking.py             # Composite scoring and ranking
      signals.py             # Exit signal generator
    agents/
      orchestrator.py        # Agent mode orchestrator (registry-driven)
      mcp_server.py          # FastMCP server exposing ~30 QuantManager tools
      registry.py            # Config-driven agent/mode loader
      conclusions.py         # Pydantic models for structured agent output
      evolution.py           # Prompt self-improvement with safety constraints
    broker/
      alpaca.py              # Alpaca Markets implementation
    data/
      price.py               # Yahoo Finance OHLCV
      sentiment.py           # Finnhub + VADER NLP sentiment
      fundamentals.py        # Yahoo Finance fundamentals
      options.py             # Options chain analysis
      geopolitical.py        # GDELT geopolitical sentiment
      universe.py            # S&P 500 universe
    portfolio/
      construction.py        # Correlation constraints, risk-parity weighting
      risk.py                # Trailing stops, drawdown, VIX exits
    regime/
      detector.py            # Multi-factor regime detection
    sec/
      scanners/insider.py    # SEC EDGAR Form 4 scanner
    learning/
      loop.py                # Self-learning orchestrator
      config_writer.py       # Safe config updates with audit trail
    backtest/
      walkforward.py         # Walk-forward backtesting (no lookahead bias)
      engine.py              # Backtest simulation engine
  scripts/
    run_manager.py           # CLI entry point
    run_learning.py          # Learning loop CLI
    health_check.py          # Heartbeat health check
  scheduler/
    config.yaml              # Cron schedule settings
    run.sh                   # Entry point with market gating
    install.sh / uninstall.sh
  dashboard/                 # Flask web dashboard
  tests/                     # 376+ tests
  STRATEGY.md                # Full strategy document with code references
```

---

## Troubleshooting

### "Broker is required but failed to initialize"
Alpaca API keys are not set. Add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` to your shell profile.

### "No candidates found"
Normal -- the strategy is selective. Check if the market is in "crisis" regime (VIX > 35 blocks entries). Run with `--dry-run` to see the screening funnel.

### "Fill timeout" on broker orders
The market is closed. Alpaca DAY limit orders need the market open to fill (9:30 AM - 4:00 PM ET).

### Cron not firing
Mac was likely asleep. Keep it awake during market hours. Check Full Disk Access for `/usr/sbin/cron` in System Settings. Verify with `crontab -l`.

### Stale local positions
The broker sync at the start of each run auto-corrects -- removing local-only positions and adding broker-only ones. Alpaca is always the source of truth.

---

## Disclaimer

This project is for educational and research purposes. Past performance of similar strategies does not guarantee future results. All trading involves risk of loss. Always paper trade before committing real capital.

---

## License

Released under the MIT License. See [LICENSE](LICENSE) for full terms, including the trading and financial disclaimer. The authors accept no liability for any losses, damages, or consequences resulting from use of this software.
