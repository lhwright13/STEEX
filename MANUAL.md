# STEEX User Manual

## Table of Contents

1. [Installation](#installation)
2. [Daily Workflow](#daily-workflow)
3. [Commands Reference](#commands-reference)
4. [Understanding the Output](#understanding-the-output)
5. [Configuration](#configuration)
6. [Scheduler Setup](#scheduler-setup)
7. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- Python 3.10 or higher
- Alpaca Markets account (paper trading is free)
- Internet connection for market data APIs

### Setup

```bash
cd STEEX

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
pip install -e .
```

### Alpaca Broker Setup (Required)

Alpaca is the source of truth for all holdings and account data. Add API keys to your shell profile:

```bash
# Add to ~/.bash_profile or ~/.zprofile
export ALPACA_API_KEY="your-key"
export ALPACA_SECRET_KEY="your-secret"
```

Test the connection:

```bash
source ~/.bash_profile
python -c "
from src.broker.alpaca import AlpacaBroker
b = AlpacaBroker(paper=True)
acct = b.get_account()
print(f'Equity: \${acct.equity:,.2f}')
print(f'Cash: \${acct.cash:,.2f}')
print(f'Buying power: \${acct.buying_power:,.2f}')
"
```

API keys are never stored in config files. Use environment variables or a `.env` file (gitignored).

### Optional API Keys

These improve sentiment analysis but are not required:

```bash
export FINNHUB_API_KEY="your-key"          # Primary sentiment source
export ALPHA_VANTAGE_API_KEY="your-key"    # Sentiment fallback
```

### Verify Installation

```bash
python scripts/run_manager.py pre_market --paper --dry-run
```

---

## Daily Workflow

### How the Pipeline Works

Each run goes through this sequence:

1. **Broker Sync** - Reconcile positions with Alpaca (source of truth)
2. **Data Refresh** - Fetch insider filings, check VIX, validate price API
3. **Regime Check** - Classify market by VIX level (normal/elevated/crisis)
4. **Risk Assessment** - Update stops, check drawdown, detect exit signals
5. **Execute Exits** - Auto-fire stop-loss and VIX exits
6. **Screening** - Run 5-stage pipeline on S&P 500
7. **Execute Entries** - Present candidates, confirm or auto-execute
8. **Report** - Save JSON report, print summary

### Morning Routine (Pre-Market)

```bash
source venv/bin/activate

# Preview what the system would do (no orders)
python scripts/run_manager.py pre_market --paper --dry-run

# Paper trading with confirmation prompts for each entry
python scripts/run_manager.py pre_market --paper

# Auto-confirm all entries (for scheduler use)
python scripts/run_manager.py pre_market --paper --yes
```

### Midday Check (Monitor)

No screening, no new entries. Checks stops, VIX, and exits only.

```bash
python scripts/run_manager.py monitor --paper
```

### End of Day (Post-Market)

Updates stops with closing prices, executes end-of-day exits, generates daily report.

```bash
python scripts/run_manager.py post_market --paper
```

### Interpreting Results

| Pipeline Result | Action |
|-----------------|--------|
| 2+ candidates with score > 55 | Review top picks, confirm entries |
| 1 candidate | Consider single position or wait |
| 0 candidates | No action today - strategy is selective |
| Exit signals | Stop-loss and VIX exits auto-fire; others are recommendations |

---

## Commands Reference

### QuantManager (Primary)

```bash
python scripts/run_manager.py <mode> [OPTIONS]
```

| Mode | Description |
|------|-------------|
| `pre_market` | Full pipeline (default) |
| `monitor` | Midday risk check |
| `post_market` | End-of-day wrap-up |
| `full` | Run all three in sequence |
| `train` | PySR walk-forward training |

| Flag | Description |
|------|-------------|
| `--paper` | Enable broker, paper trading |
| `--live` | Enable broker, real money |
| `--no-broker` | Force simulation (backtesting only) |
| `--dry-run` | Preview only, no execution |
| `--yes` | Auto-confirm all entries |
| `--verbose` | Extra output |
| `--portfolio N` | Override portfolio value |

### Legacy Scanners (Still Available)

```bash
# Standalone daily scan
python scripts/daily_scan.py
python scripts/daily_scan.py --debug --all-candidates

# Insider scanner
python scripts/scan_insiders.py --days 7 --max 500
python scripts/scan_insiders.py --fast
```

---

## Understanding the Output

### Pipeline Summary

```
0. Broker Sync
   Equity: $50,000 | Cash: $47,000 | Positions: 2

1. Data Refresh
   Sources: 3/3 healthy

2. Data Health
   All data sources healthy

3. Market Regime
   NORMAL (VIX: 18.5)

4. Portfolio Risk
   Positions: 2/10
   P&L: +$350

5. Exit Signals
   No exit signals

6. Screening Pipeline
   503 -> 8 candidates

7. Buy Candidates
   AAPL @ $185.50 x 12 shares ($2,226) | Score: 67.3
```

### Screening Funnel

| Stage | What It Filters |
|-------|-----------------|
| Stage 1 | Price > $5, volume > 500K, no earnings in 5 days |
| Stage 2 | 6M return > 15%, 1M return > 5%, above 50/200 MA |
| Stage 3 | Insider enrichment (adds cluster buy scores) |
| Stage 4 | Combined sentiment > 30 (stock + geopolitical) |
| Stage 5 | P/E < 50, ROE > 5%, debt/equity < 2.0 |

### VIX Regime

| VIX | Regime | Position Size | Entries |
|-----|--------|--------------|---------|
| < 15 | Low vol | 1.0x | Yes |
| 15-25 | Normal | 1.0x | Yes |
| 25-35 | Elevated | 0.5x | Yes (reduced) |
| > 35 | Crisis | 0.0x | No |

### Exit Signals

| Condition | Urgency | Action |
|-----------|---------|--------|
| Stop loss (-10%) | Immediate | Auto-exit |
| Trailing stop | Immediate | Auto-exit |
| VIX > 40 | Immediate | Auto-exit 50% |
| Below 50-day MA | End of day | Auto-exit at post_market |
| Max hold (30 days) | Next session | Recommendation |

---

## Configuration

### Config File

All parameters are in `config/config.yaml`. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `momentum_min_return` | 0.15 | Minimum 6M return (15%) |
| `daily_picks` | 2 | Stocks to pick per day |
| `max_positions` | 10 | Maximum concurrent positions |
| `position_size_pct` | 0.04 | Base position size (4%) |
| `initial_stop_pct` | 0.10 | Initial stop loss (10%) |
| `max_hold_days` | 30 | Maximum holding period |
| `max_sector_pct` | 0.30 | Maximum sector exposure (30%) |
| `broker_enabled` | true | Alpaca broker always on |
| `broker_paper` | true | Paper trading (safety net) |

### Scoring Weights

| Factor | Weight | Source |
|--------|--------|--------|
| Momentum | 30% | 6-month return percentile |
| Insider | 25% | SEC Form 4 cluster buy score |
| Volume | 15% | Volume surge percentile |
| Sentiment | 15% | Finnhub + VADER + geopolitical |
| Fundamental | 10% | P/E, ROE, debt/equity |
| Options | 5% | Put/call ratio |
| PySR | 10% | Symbolic regression (when trained) |

### Environment Variable Overrides

Any setting can be overridden with the `STEEX_` prefix:

```bash
export STEEX_INITIAL_STOP_PCT=0.08
export STEEX_MAX_POSITIONS=15
export STEEX_DAILY_PICKS=3
```

Priority: init settings > environment variables > YAML config > defaults.

---

## Scheduler Setup

The scheduler runs the pipeline automatically via cron and `claude -p` (non-interactive mode). Claude reads the agent docs, reasons about market conditions, and executes the pipeline.

### Install

```bash
# Preview cron entries
scheduler/install.sh --show

# Install cron schedule
scheduler/install.sh

# Verify
crontab -l
```

### Default Schedule (ET, weekdays)

| Mode | Time | Description |
|------|------|-------------|
| pre_market | 8:30 AM | Full pipeline |
| monitor | 12:00 PM | Risk check |
| post_market | 4:30 PM | EOD wrap-up |

### Manual Run

```bash
scheduler/run.sh pre_market
scheduler/run.sh monitor
scheduler/run.sh post_market
```

### Safety Defaults

- `paper: true` - paper trading only
- `dry_run: false` - executes trades (change to true for preview-only)
- `allowed_tools: "Bash,Read,Glob,Grep"` - Claude cannot edit source files
- Cron requires the Mac to be awake during scheduled times

### Uninstall

```bash
scheduler/uninstall.sh
```

See `scheduler/README.md` for details.

---

## Troubleshooting

### "Broker is required but failed to initialize"

Alpaca API keys are not set. Add them to your shell profile:

```bash
export ALPACA_API_KEY="your-key"
export ALPACA_SECRET_KEY="your-secret"
```

Then `source ~/.bash_profile` and retry.

### "No candidates found"

Normal. The strategy is selective. Options:
- Run with `--dry-run` to see the screening funnel
- Check if the market is in "crisis" regime (VIX > 35 blocks entries)
- Run `python scripts/daily_scan.py --debug` to see what passed each stage

### "Fill timeout" on broker orders

The market is closed. Alpaca DAY limit orders need the market to be open to fill. Run during market hours (9:30 AM - 4:00 PM ET).

### "ImportError: attempted relative import"

Run: `pip install -e .`

### Cron not firing

- Mac was probably asleep. Keep it awake during market hours.
- Check Full Disk Access for `/usr/sbin/cron` in System Settings.
- Verify cron entries: `crontab -l`

### Stale local positions

If local `positions.json` is out of sync with Alpaca, the broker sync at the start of each run will fix it automatically - removing local-only positions and adding broker-only positions.

### Data Sources

| Data | Source | Rate Limit |
|------|--------|------------|
| Prices / VIX / Fundamentals / Options | Yahoo Finance | 2000 req/hour |
| Insider (Form 4) | SEC EDGAR | 10 req/second |
| S&P 500 list | Wikipedia | None |
| Sentiment | Finnhub | 60 req/minute (free) |
| Sentiment fallback | Alpha Vantage | 25 req/day (free) |
| Geopolitical | GDELT Project | No limit |
| Execution / Positions | Alpaca Markets | 200 req/minute |

---

## Quick Reference

```
DAILY COMMANDS
--------------
python scripts/run_manager.py pre_market --paper --dry-run  # Preview
python scripts/run_manager.py pre_market --paper             # Paper trade
python scripts/run_manager.py monitor --paper                # Midday check
python scripts/run_manager.py post_market --paper            # End of day

SCORING
-------
Score 70+  = Strong candidate
Score 55+  = Passes entry threshold (manager_min_score_entry)
6M > 15%   = Passes momentum filter
Insiders 3+ = Cluster buy signal

VIX REGIME
----------
< 25  = Trade normally (1.0x size)
25-35 = Reduced position sizes (0.5x)
> 35  = No new entries
> 40  = Exit 50% of positions

POSITION RULES
--------------
Size: 3-6% of portfolio (volatility-adjusted)
Stop: 10% initial, trailing after +10% gain
Max hold: 30 trading days
Max positions: 10
Max sector: 30%

BROKER FLAGS
------------
--paper      Paper trading via Alpaca
--live       Real money via Alpaca
--no-broker  Simulation only (backtesting)
--dry-run    Preview, no execution
--yes        Auto-confirm entries
```
