# STEEX

Safe Trading Environment for Experimental Xyz

An agent-based automated trading system that screens S&P 500 stocks using momentum, insider trading, sentiment, fundamentals, and options signals, then executes trades through Alpaca Markets.

## Architecture

STEEX is built around a **QuantManager** orchestrator that coordinates five specialized agents. Each agent owns a specific domain and exposes tools that the orchestrator calls in sequence.

```
QuantManager (orchestrator)
    |
    +-- DataAgent        -- Fetches and validates external data
    +-- AnalysisAgent    -- Runs 5-stage screening and ranking
    +-- RiskAgent        -- Monitors positions, stops, VIX, drawdown
    +-- ExecutionAgent   -- Decides and executes entries/exits via Alpaca
    +-- ReportAgent      -- Compiles reports, logs everything
```

Each agent is documented in `agents/claude_*.md`. These docs are read by the Claude-based scheduler when it runs the pipeline autonomously via `claude -p`.

## Data Sources

| Data | Source | Purpose |
|------|--------|---------|
| Prices / OHLCV | Yahoo Finance | Screening, P&L, indicators |
| Insider trades (Form 4) | SEC EDGAR | Core buy signal |
| VIX | Yahoo Finance | Regime detection, risk management |
| Sentiment | Finnhub + VADER NLP | Stock-specific and macro sentiment |
| Fundamentals | Yahoo Finance | P/E, ROE, debt/equity quality filter |
| Options flow | Yahoo Finance | Put/call ratio, IV rank signal |
| Geopolitical | GDELT Project | Macro/sector sentiment impact |
| S&P 500 list | Wikipedia | Universe definition |
| Execution + Positions | Alpaca Markets | Source of truth for holdings and orders |

## Quick Start

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Set Alpaca API keys (required)
export ALPACA_API_KEY="your-key"
export ALPACA_SECRET_KEY="your-secret"

# Dry run to preview what the system would do
python scripts/run_manager.py pre_market --paper --dry-run

# Paper trading with confirmation prompts
python scripts/run_manager.py pre_market --paper

# Auto-confirm all entries
python scripts/run_manager.py pre_market --paper --yes
```

## Pipeline Modes

| Mode | Command | What It Does |
|------|---------|-------------|
| pre_market | `python scripts/run_manager.py pre_market` | Full pipeline: sync, screen, rank, enter, exit, report |
| monitor | `python scripts/run_manager.py monitor` | Midday risk check: stops, VIX, exits only |
| post_market | `python scripts/run_manager.py post_market` | EOD wrap-up: final exits, update stops, daily report |
| train | `python scripts/run_manager.py train` | PySR symbolic regression walk-forward training |

## Screening Pipeline

```
S&P 500 (~503 stocks)
    |  Stage 1: Universe filter (price > $5, volume > 500K, no earnings blackout)
    v
  ~480
    |  Stage 2: Momentum (6M return > 15%, 1M > 5%, above 50/200 MA)
    v
  ~100
    |  Stage 3: Insider enrichment (SEC Form 4 cluster buys)
    v
   ~20
    |  Stage 4: Sentiment (Finnhub + VADER NLP + geopolitical)
    v
   ~15
    |  Stage 5: Fundamentals (P/E < 50, ROE > 5%, debt/equity < 2)
    v
   ~8 candidates -> Composite scoring -> Top 2 entries
```

## Scoring Weights

| Factor | Weight | Source |
|--------|--------|--------|
| Momentum | 30% | 6-month return percentile |
| Insider | 25% | Cluster buy score from SEC Form 4 |
| Volume | 15% | Volume surge percentile |
| Sentiment | 15% | Finnhub + VADER + geopolitical |
| Fundamental | 10% | P/E, ROE, debt/equity composite |
| Options | 5% | Put/call ratio signal |
| PySR | 10% | Symbolic regression prediction (when trained) |

## Position Management

| Parameter | Value |
|-----------|-------|
| Position size | 3-6% (volatility-adjusted via ATR) |
| Max positions | 10 |
| Max single position | 20% |
| Max sector exposure | 30% |
| Min cash reserve | 10% |
| Daily entries | 2 max |

## Exit Rules

| Condition | Urgency | Default |
|-----------|---------|---------|
| Stop loss | Immediate | -10% from entry |
| Trailing stop | Immediate | -12%/-15%/-15% from high (at +10%/+20%/+30%) |
| VIX spike (>40) | Immediate | Exit 50% of positions |
| Below 50-day MA | End of day | Auto-exit at post_market |
| Max hold time | Next session | 30 trading days |
| VIX elevated (>30) | Ongoing | Tighten stops to 5% |

## Broker Integration

Alpaca Markets is the **source of truth** for all holdings and account data. On every pipeline run:

1. Positions sync from Alpaca (local-only positions are removed, broker-only positions are added)
2. Portfolio value and cash come from `broker.get_account()`, not config
3. Orders execute through Alpaca as DAY limit orders
4. If broker init fails, the pipeline halts (no silent simulation fallback)

## Automated Scheduling

The `scheduler/` directory provides cron-based automation that invokes `claude -p` (non-interactive mode). Claude reads the agent docs, reasons about market conditions, runs the pipeline, and reports results.

| Mode | Schedule (ET) | Description |
|------|--------------|-------------|
| pre_market | 8:30 AM weekdays | Full pipeline |
| monitor | 12:00 PM weekdays | Risk check |
| post_market | 4:30 PM weekdays | EOD wrap-up |

```bash
scheduler/install.sh          # Install cron schedule
scheduler/install.sh --show   # Preview cron entries
scheduler/uninstall.sh        # Remove cron schedule
scheduler/run.sh pre_market   # Manual run
```

See `scheduler/README.md` for details.

## Project Structure

```
STEEX/
  agents/                      # Agent documentation (read by Claude scheduler)
    claude_manager.md          # Orchestrator - sequences all agents
    claude_data_agent.md       # Data fetching and validation
    claude_analysis_agent.md   # Screening and ranking pipeline
    claude_risk_agent.md       # Risk monitoring, stops, VIX, drawdown
    claude_execution_agent.md  # Trade entry/exit execution via Alpaca
    claude_report_agent.md     # Report compilation and logging
  config/
    config.yaml                # All tunable parameters
    settings.py                # Pydantic settings with YAML + env override
  src/
    strategy/
      manager.py               # QuantManager orchestrator
      screener.py              # 5-stage screening pipeline
      ranking.py               # Composite scoring and ranking
      signals.py               # Exit signal generator
    broker/
      base.py                  # Abstract broker interface
      alpaca.py                # Alpaca Markets implementation
    data/
      price.py                 # Yahoo Finance price/OHLCV
      vix.py                   # VIX data provider
      sentiment.py             # Finnhub + VADER sentiment
      fundamentals.py          # Yahoo Finance fundamentals
      options.py               # Yahoo Finance options data
      geopolitical.py          # GDELT geopolitical sentiment
      universe.py              # S&P 500 universe
      calendar.py              # Earnings calendar
      base.py                  # DataProvider ABC with cache
      cache.py                 # SQLite persistent cache (L2)
    portfolio/
      positions.py             # Position tracking (synced from broker)
      tracker.py               # Trade history with strategy metadata
      risk.py                  # Risk manager (stops, drawdown, sectors)
    sec/
      client.py                # SEC EDGAR API client
      scanners/insider.py      # Form 4 insider scanner
    indicators/
      momentum.py              # Momentum calculations
      technical.py             # MA, ATR, trend alignment
    ml/
      trainer.py               # PySR walk-forward training
      predictor.py             # PySR model inference
      features.py              # Feature engineering
      dataset.py               # Training dataset builder
    backtest/                  # Historical backtesting engine
  scripts/
    run_manager.py             # CLI entry point for QuantManager
  scheduler/
    config.yaml                # Scheduler settings (model, budget, tools)
    run.sh                     # Main entry - parses config, runs claude -p
    install.sh                 # Install cron schedule
    uninstall.sh               # Remove cron schedule
    prompts/                   # Prompt templates for each mode
  dashboard/                   # Web dashboard
  data/
    reports/                   # Daily report JSONs
    cache.db                   # SQLite persistent cache
```

## Configuration

All parameters are in `config/config.yaml` with environment variable overrides (prefix `STEEX_`):

```bash
export STEEX_INITIAL_STOP_PCT=0.08
export STEEX_MAX_POSITIONS=15
```

Priority: init settings > environment variables > YAML config > defaults.

API keys are environment variables only - never in config files:
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (required)
- `FINNHUB_API_KEY` (optional, for sentiment)
- `ALPHA_VANTAGE_API_KEY` (optional, sentiment fallback)

## Requirements

- Python 3.10+
- macOS (for cron scheduler; pipeline works on any OS)
- Alpaca Markets paper trading account
- Internet connection for market data APIs
