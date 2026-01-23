# STEEX Trading Plan

## Daily Workflow

### Pre-Market (Before 9:30 AM ET)

```bash
cd /Users/lhwri/Desktop/STEEX
source venv/bin/activate
python scripts/morning_routine.py
```

This will:
1. Fetch fresh insider filings (last 7 days)
2. Check VIX level and implications
3. Review current positions for exits
4. Run full screening pipeline
5. Generate buy recommendations

### Data We Collect

| Data | Source | Frequency | Purpose |
|------|--------|-----------|---------|
| Insider Transactions | SEC EDGAR Form 4 | Daily | Core signal for entries |
| Stock Prices | Yahoo Finance | Real-time | Position P&L, screening |
| VIX Level | Yahoo Finance | Real-time | Risk management |
| Moving Averages | Calculated | Daily | Trend confirmation |
| Volume | Yahoo Finance | Daily | Confirmation signal |

### Decision Framework

#### Entry Criteria (ALL must pass)

**Stage 1 - Universe Filter:**
- [ ] Price >= $5
- [ ] Avg Volume >= 500K
- [ ] No earnings in next 5 days

**Stage 2 - Momentum:**
- [ ] 6-month return >= 15%
- [ ] 1-month return >= 5%
- [ ] Price above 50-day MA
- [ ] Price above 200-day MA
- [ ] Not in top 5% (overextended)

**Stage 3 - Insider Activity (any one):**
- [ ] CEO or CFO purchase, OR
- [ ] 3+ unique insider buyers, OR
- [ ] Purchase value >= $100,000

#### Position Sizing

| VIX Level | Position Size |
|-----------|---------------|
| < 20 (Low) | 5% of portfolio |
| 20-30 (Normal) | 5% of portfolio |
| 30-40 (Elevated) | 2.5% of portfolio |
| > 40 (Spike) | NO NEW ENTRIES |

#### Exit Rules

| Condition | Action |
|-----------|--------|
| Price hits initial stop (-12%) | EXIT immediately |
| Trailing stop hit | EXIT |
| Below entry for 10+ days | EXIT (dead money) |
| Max hold time (60 days) | EXIT |
| VIX > 40 | EXIT 50% of all positions |
| Below 50-day MA | EXIT |

### Trailing Stop Schedule

| Gain from Entry | Trail Distance |
|-----------------|----------------|
| 10% | 12% from high |
| 20% | 15% from high |
| 30% | 18% from high |

---

## Tomorrow's Checklist

### Pre-Market (7:00 - 9:30 AM ET)

- [ ] Run morning routine: `python scripts/morning_routine.py`
- [ ] Check VIX level and status
- [ ] Review any position alerts (stops hit, dead money)
- [ ] Review buy candidates
- [ ] Note entry prices and stops for candidates

### Market Open (9:30 - 10:00 AM)

- [ ] Wait 15-30 minutes for opening volatility to settle
- [ ] Check if candidates held overnight levels
- [ ] Execute entries if conditions still valid
- [ ] Set stop orders immediately after entry

### Intraday

- [ ] Monitor positions via dashboard
- [ ] Update stops if new highs reached
- [ ] Check for VIX spikes

### End of Day (3:30 - 4:00 PM)

- [ ] Review day's activity
- [ ] Update trade journal with notes
- [ ] Prepare for next day

---

## Position Tracking

### Adding a Position

After executing a trade, record it:

```python
from src.portfolio.positions import PositionManager
from config.settings import get_settings

pm = PositionManager(get_settings())
pm.add_position(
    ticker="AAPL",
    entry_price=185.50,
    shares=27,
    score=65,
    reasons=["CEO purchase $500K", "Strong momentum", "Above MAs"],
)
```

### Recording an Exit

```python
from src.portfolio.positions import PositionManager
from src.portfolio.tracker import TradeTracker
from datetime import datetime

pm = PositionManager()
tt = TradeTracker()

position = pm.get_position("AAPL")
tt.record_trade(
    ticker="AAPL",
    entry_date=position.entry_datetime,
    exit_date=datetime.now(),
    entry_price=position.entry_price,
    exit_price=195.00,
    shares=position.shares,
    exit_reason="trailing_stop",
    score=position.score,
    reasons=position.reasons,
)
pm.remove_position("AAPL")
```

---

## Risk Limits

| Metric | Limit | Action if Exceeded |
|--------|-------|-------------------|
| Max positions | 20 | No new entries |
| Max single position | 10% | Trim to limit |
| Max sector exposure | 30% | Diversify |
| Min cash reserve | 10% | No new entries |
| Portfolio drawdown 10% | Review strategy |
| Portfolio drawdown 15% | Reduce position sizes |
| Portfolio drawdown 20% | Pause new entries |
| Portfolio drawdown 25% | Exit all positions |

---

## Data Files

| File | Purpose |
|------|---------|
| `data/positions.json` | Current open positions |
| `data/trades.json` | Completed trade history |
| `data/cache/historical_insiders.json` | Cached insider data |
| `data/morning_report.json` | Today's morning analysis |
| `data/trade_journal.json` | Notes and lessons |

---

## Commands Reference

```bash
# Morning routine
python scripts/morning_routine.py

# Manual screening
python scripts/daily_scan.py --verbose

# Fetch fresh insider data
python scripts/fetch_historical_insiders.py --days 30

# Run dashboard
streamlit run dashboard/app.py

# Run backtest
python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31
```

---

## Key Reminders

1. **Never chase** - If a stock gaps up significantly, wait for pullback
2. **Size correctly** - Risk no more than 1% of portfolio per trade
3. **Honor stops** - No exceptions, no "waiting to see"
4. **Journal everything** - Review trades weekly to improve
5. **VIX is king** - When VIX spikes, defense first
