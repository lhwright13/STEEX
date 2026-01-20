# STEEX User Manual

## Table of Contents

1. [Installation](#installation)
2. [Daily Workflow](#daily-workflow)
3. [Commands Reference](#commands-reference)
4. [Understanding the Output](#understanding-the-output)
5. [Strategy Overview](#strategy-overview)
6. [Configuration](#configuration)
7. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- Python 3.10 or higher
- Internet connection (for SEC and Yahoo Finance data)

### Setup

```bash
# Clone or navigate to the project directory
cd STEEX

# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install package in development mode
pip install -e .
```

### Verify Installation

```bash
# Test the insider scanner
python scripts/scan_insiders.py --fast

# Test the daily scanner
python scripts/daily_scan.py --debug
```

---

## Daily Workflow

### Morning Routine (Before Market Open)

1. **Check VIX levels** - The daily scanner does this automatically
2. **Run the daily scan** - Get stock picks for the day
3. **Review cluster buys** - Check for strong insider signals
4. **Place orders** - At market open + 15 minutes

### Step-by-Step

```bash
# Activate your environment
source venv/bin/activate

# Run the full daily scan
python scripts/daily_scan.py

# If no candidates found, check what passed momentum
python scripts/daily_scan.py --debug

# See momentum picks without insider requirement (for reference)
python scripts/daily_scan.py --debug --skip-insider

# Check insider activity separately
python scripts/scan_insiders.py --days 7
```

### Interpreting Results

| Stage 3 Result | Action |
|----------------|--------|
| 2+ candidates | Review top picks, place orders |
| 1 candidate | Consider single position or wait |
| 0 candidates | No action today - strategy is selective |

---

## Commands Reference

### Daily Scanner

```bash
python scripts/daily_scan.py [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--date YYYY-MM-DD` | Scan for a specific date (default: today) |
| `--top N` | Show top N picks (default: 2) |
| `--verbose`, `-v` | Show detailed information for picks |
| `--all-candidates` | Show all candidates, not just top picks |
| `--debug` | Show stocks passing each stage |
| `--skip-insider` | Skip insider filter (testing only) |
| `--expanded` | Use expanded universe (S&P 500 + insider activity stocks) |
| `--insider-only` | Only scan stocks with recent insider activity (small-caps) |

**Examples:**

```bash
# Standard daily scan (S&P 500 only)
python scripts/daily_scan.py

# Expanded universe (S&P 500 + stocks with insider activity)
python scripts/daily_scan.py --expanded

# Small-caps only (stocks with recent insider buying)
python scripts/daily_scan.py --insider-only

# See all 121 momentum stocks with debug info
python scripts/daily_scan.py --debug --all-candidates --skip-insider

# Verbose output for top 5 picks
python scripts/daily_scan.py --top 5 --verbose
```

### Insider Scanner

```bash
python scripts/scan_insiders.py [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--days N`, `-d N` | Days to look back (default: 3) |
| `--max N`, `-m N` | Max filings to process (default: 150) |
| `--fast`, `-f` | Use Atom feed (faster, less data) |
| `--quiet`, `-q` | Suppress progress output |

**Examples:**

```bash
# Quick scan of recent filings
python scripts/scan_insiders.py --fast

# Thorough 7-day scan
python scripts/scan_insiders.py --days 7 --max 500

# Quiet mode for scripts
python scripts/scan_insiders.py --quiet
```

---

## Understanding the Output

### Daily Scanner Output

```
Screening Results
Universe size:        503    <- Total S&P 500 stocks
Stage 1 (filters):    476    <- Passed price/volume/earnings filter
Stage 2 (momentum):   121    <- Passed momentum criteria
Stage 3 (insider):    2      <- Had insider buying
Stage 4 (sentiment):  2      <- Passed sentiment (currently passthrough)
```

### Stage Breakdown

| Stage | What It Filters |
|-------|-----------------|
| Stage 1 | Price > $5, Volume > 500K, No earnings in 5 days |
| Stage 2 | 6M return > 10%, 1M return > 0%, Above 50 & 200 MA |
| Stage 3 | Insider purchases in last 30 days |
| Stage 4 | Sentiment check (not yet implemented) |

### Picks Table

```
┏━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
│ Rank │ Ticker │ Score │ 6M Return │ Insiders │ Insider $ │ Reasons         │
```

| Column | Meaning |
|--------|---------|
| Score | Composite score (0-100) |
| 6M Return | 6-month price return |
| Insiders | Number of unique insider buyers |
| Insider $ | Total dollar value of insider purchases |
| Reasons | Why this stock was picked |

### Insider Scanner Output

**Transaction Codes:**

| Code | Meaning | Signal |
|------|---------|--------|
| P | Open market purchase | Bullish |
| S | Sale | Bearish |
| A | Award/grant | Neutral |
| J | Other acquisition | Mixed |

**Cluster Buy Scores (0-100):**

| Score | Strength |
|-------|----------|
| 80-100 | Very strong - multiple insiders, high value |
| 60-79 | Strong - CEO/CFO buying or cluster |
| 40-59 | Moderate - significant purchase |
| 20-39 | Weak - small or single purchase |

### VIX Levels

| VIX Level | Status | Action |
|-----------|--------|--------|
| < 20 | Normal | Trade normally |
| 20-30 | Elevated | Monitor closely |
| 30-40 | High | Tighten stops to 5% |
| > 40 | Spike | Exit 50% of positions |

---

## Strategy Overview

### The MIS Strategy

**M**omentum + **I**nsider + **S**entiment

The strategy looks for stocks with:
1. Strong price momentum (trending up)
2. Insider buying activity (management confidence)
3. Positive sentiment (optional enhancement)

### Why It Works

- **Momentum** has the strongest academic backing for excess returns
- **Insider buying** is historically bullish (they know more than we do)
- **Combining signals** reduces false positives

### Selection Process

```
S&P 500 (503 stocks)
    |
    v
Stage 1: Universe Filter (price, volume, earnings)
    |
    v
Stage 2: Momentum Screen (6M > 10%, above MAs)
    |
    v
Stage 3: Insider Filter (recent purchases)
    |
    v
Stage 4: Sentiment Check (optional)
    |
    v
Final Ranking -> Top 2 Picks
```

### Position Sizing

| Parameter | Value |
|-----------|-------|
| Daily picks | 2 stocks |
| Position size | 5% of portfolio each |
| Max positions | 20 concurrent |
| Max sector | 30% of portfolio |

### Exit Rules

| Condition | Action |
|-----------|--------|
| -7% from entry | Stop loss |
| +10% gain | Trail 10% from high |
| +20% gain | Trail 12% from high |
| +30% gain | Trail 15% from high |
| Below 50-day MA | Exit |
| 60 trading days | Time-based exit |
| VIX > 40 | Exit 50% of positions |

---

## Configuration

### Environment Variables

All settings can be overridden with environment variables prefixed with `STEEX_`:

```bash
export STEEX_DAILY_PICKS=3
export STEEX_MOMENTUM_MIN_RETURN=0.15
export STEEX_INITIAL_STOP_PCT=0.10
```

### Configuration File

Create a `.env` file in the project root:

```env
STEEX_DAILY_PICKS=2
STEEX_MAX_POSITIONS=20
STEEX_POSITION_SIZE_PCT=0.05
STEEX_INITIAL_STOP_PCT=0.07
STEEX_VIX_CAUTION_LEVEL=30
STEEX_VIX_EXIT_LEVEL=40
```

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `momentum_lookback_days` | 126 | 6-month momentum period |
| `short_momentum_days` | 21 | 1-month momentum period |
| `momentum_min_return` | 0.10 | Minimum 6M return (10%) |
| `insider_lookback_days` | 30 | Days to look for insider buys |
| `min_price` | 5.0 | Minimum stock price |
| `min_volume` | 500000 | Minimum daily volume |
| `earnings_blackout_days` | 5 | Days before earnings to avoid |
| `daily_picks` | 2 | Stocks to pick per day |
| `max_positions` | 20 | Maximum concurrent positions |
| `initial_stop_pct` | 0.07 | Initial stop loss (7%) |
| `max_hold_days` | 60 | Maximum holding period |

### Scoring Weights

| Factor | Weight | Description |
|--------|--------|-------------|
| Momentum | 40% | 6-month return percentile |
| Insider | 30% | Insider buying strength |
| Volume | 20% | Volume surge percentile |
| Sentiment | 10% | Sentiment score (stub) |

---

## Troubleshooting

### Common Issues

**"No candidates found matching all criteria"**

This is normal. The strategy is selective. Options:
- Run with `--debug` to see what passed each stage
- Run with `--skip-insider` to see momentum picks
- Check insider scanner separately to see what buying exists

**"ImportError: attempted relative import beyond top-level package"**

Run: `pip install -e .`

**"HTTP Error 403" when fetching S&P 500 list**

The Wikipedia scraper needs a user-agent. This should be fixed, but if it persists, the system falls back to ~100 major stocks.

**Slow performance**

- Use `--fast` flag with insider scanner
- The daily scan processes 500 stocks - this takes 2-3 minutes

**Missing dependency errors**

```bash
pip install -r requirements.txt
pip install lxml  # For Wikipedia parsing
```

### Data Sources

| Data | Source | Rate Limit |
|------|--------|------------|
| Prices | Yahoo Finance | 2000 req/hour |
| Insider (Form 4) | SEC EDGAR | 10 req/second |
| S&P 500 list | Wikipedia | None |
| VIX | Yahoo Finance | Shared with prices |

### Logs and Debugging

```bash
# Verbose daily scan
python scripts/daily_scan.py --debug --verbose

# Check what the screener sees
python -c "
from src.data.universe import Universe
u = Universe()
print(f'Universe size: {len(u.get_sp500())}')
"

# Test SEC connection
python scripts/scan_insiders.py --fast --max 10
```

---

## Quick Reference Card

```
DAILY COMMANDS
--------------
python scripts/daily_scan.py              # Standard scan
python scripts/daily_scan.py --debug      # Show all stages
python scripts/scan_insiders.py --days 7  # 7-day insider scan

WHAT THE NUMBERS MEAN
---------------------
Score 70+  = Strong candidate
Score 50+  = Moderate candidate
6M > 20%   = Good momentum
Insiders 3+ = Cluster buy signal

VIX ACTIONS
-----------
< 30  = Trade normally
30-40 = Tighten stops to 5%
> 40  = Exit 50% of positions

POSITION RULES
--------------
Entry: Market open + 15 min, limit order
Size: 5% of portfolio per stock
Stop: 7% initial, trailing after +10%
Exit: 60 days max hold
```
