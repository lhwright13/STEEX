# MIS Trading Strategy: Momentum + Insider + Sentiment

A systematic daily stock selection strategy combining momentum factors, SEC insider trading data, and sentiment analysis.

---

## Overview

| Attribute | Value |
|-----------|-------|
| Strategy Type | Long-only equity |
| Selection | 2 stocks per day |
| Hold Period | 20-60 trading days |
| Target Alpha | 5-15% annually over S&P 500 |
| Trading Platform | Robinhood (or similar) |

### Core Thesis

1. **Momentum (2-12 months)** has the strongest academic backing for excess returns
2. **Insider buying clusters** are historically bullish signals
3. **Sentiment shifts** can improve entry timing
4. **Volatility-based exits** prevent crash losses

---

## Data Sources

| Data | Source | Cost | Priority |
|------|--------|------|----------|
| Price/Volume/MA | Yahoo Finance API | Free | Required |
| Insider Trades (Form 4) | SEC EDGAR API | Free | Required |
| Earnings Calendar | Finnhub | Free | Required |
| Analyst Ratings | Finnhub / Alpha Vantage | Free tier | Optional |
| News Sentiment | Finnhub / NewsAPI | Free tier | Optional |
| VIX Index | Yahoo Finance | Free | Required |

---

## Selection Process

### Stage 1: Universe Filter

Start with S&P 500 or Russell 3000, then remove:

```
- Price < $5 (wide spreads, high volatility)
- Average daily volume < 500,000 shares (liquidity risk)
- Earnings announcement within 5 trading days (binary event risk)
- Recent IPO < 6 months (insufficient history)
```

### Stage 2: Momentum Screen

Require ALL conditions:

| Condition | Rationale |
|-----------|-----------|
| 6-month return > 10% | Medium-term momentum confirmation |
| 1-month return > 0% | Not a falling knife |
| Price > 50-day MA | Short-term trend positive |
| Price > 200-day MA | Long-term trend positive |
| 1-month return NOT in top 5% | Avoid overextended names |

### Stage 3: Insider Activity Filter

Using SEC Form 4 data, require ANY of:

| Signal | Strength |
|--------|----------|
| CEO or CFO purchase | Strongest |
| 3+ different insiders bought in 30 days | Strong (cluster) |
| Insider buy/sell ratio > 2:1 | Moderate |
| Single purchase > $100,000 | Moderate |

Disqualify if:
- Heavy insider selling (3+ sellers, no buyers)
- CFO selling > 25% of holdings

### Stage 4: Sentiment Check (Optional Enhancement)

Positive signals (any one sufficient):
- Analyst upgrade in last 7 days
- Earnings beat with positive guidance (if recent quarter)
- Positive news sentiment score

Disqualify if:
- Analyst downgrade to "Sell" in last 7 days
- SEC investigation or fraud news
- Major lawsuit filed

### Stage 5: Final Ranking

Score all candidates passing filters:

```
score = (
    0.40 * momentum_6mo_percentile +
    0.30 * insider_buy_strength +
    0.20 * volume_surge_percentile +
    0.10 * sentiment_score
)
```

**Select top 2 stocks by score each trading day.**

---

## Entry Rules

| Rule | Value |
|------|-------|
| Timing | Market open + 15 minutes |
| Order Type | Limit order at current ask |
| Timeout | Cancel if not filled in 30 minutes |
| Position Size | Equal weight (50% of daily allocation each) |
| Max Positions | 10-20 concurrent |

### Position Sizing Example

```
Portfolio: $10,000
Daily allocation: $1,000 (10%)
Per stock: $500 (5% of portfolio)
Build to 15-20 positions over 2 weeks
```

---

## Exit Rules

### Exit 1: Trailing Stop Loss

| Gain Level | Stop Distance |
|------------|---------------|
| Entry (0%) | -7% from entry |
| +10% gain | -10% from high |
| +20% gain | -12% from high |
| +30% gain | -15% from high |

Wider trailing stops for winners allows profits to run.

### Exit 2: Time-Based Exit

```
Maximum hold: 60 trading days (~3 months)
Momentum alpha decays after this period
Force exit and redeploy capital
```

### Exit 3: Volatility Spike (VIX)

| VIX Level | Action |
|-----------|--------|
| VIX > 30 | Tighten all stops to -5% |
| VIX > 40 | Exit 50% of all positions immediately |

Research shows momentum crashes when volatility spikes - this is the "regime switch" signal.

### Exit 4: Signal Reversal

Exit immediately if ANY:
- Price closes below 50-day MA
- 3+ insiders sell shares (reversal of buy signal)
- Analyst downgrade to Sell
- Price below entry after 10+ trading days (dead money)

### Exit Priority

Check exits in this order daily:
1. Stop loss / trailing stop hit
2. Signal reversal conditions
3. VIX spike rules
4. Maximum hold time reached

---

## Risk Management

### Position Limits

```
Max single position: 10% of portfolio
Max sector exposure: 30% of portfolio
Max correlated positions: 5 (e.g., all tech)
```

### Drawdown Rules

| Portfolio Drawdown | Action |
|--------------------|--------|
| -10% | Review strategy, continue |
| -15% | Reduce position sizes by 50% |
| -20% | Pause new entries for 1 week |
| -25% | Exit all positions, reassess |

### Cash Reserve

```
Minimum cash: 10% of portfolio
Purpose: Dry powder for opportunities, buffer for redemptions
```

---

## Expected Performance

### Realistic Estimates

| Metric | Expected Range |
|--------|----------------|
| Win Rate | 50-55% |
| Average Winner | +12-15% |
| Average Loser | -6-8% |
| Profit Factor | 1.3-1.8 |
| Annual Trades | 200-300 |
| Annual Alpha | 5-15% over benchmark |
| Max Drawdown | 15-25% |
| Sharpe Ratio | 0.5-0.8 |

### Transaction Cost Budget

```
Estimated cost per round trip: 0.1-0.15%
Annual trades: ~250
Total annual cost: ~25-40 basis points
```

---

## Implementation Phases

### Phase 1: Data Infrastructure (Week 1-2)

- [ ] Set up SEC EDGAR Form 4 data pipeline
- [ ] Connect Yahoo Finance API for price data
- [ ] Build momentum calculation functions
- [ ] Create universe filter (S&P 500)
- [ ] Set up earnings calendar integration

### Phase 2: Screening Logic (Week 2-3)

- [ ] Implement momentum screen
- [ ] Implement insider activity filter
- [ ] Build ranking/scoring system
- [ ] Create daily candidate output

### Phase 3: Backtesting (Week 3-4)

- [ ] Historical backtest 2019-2024
- [ ] Calculate Sharpe, max drawdown, win rate
- [ ] Compare to S&P 500 buy-and-hold
- [ ] Sensitivity analysis on parameters
- [ ] Transaction cost impact analysis

### Phase 4: Paper Trading (Week 5-8)

- [ ] Run strategy daily without real money
- [ ] Track all picks and outcomes
- [ ] Compare to backtest expectations
- [ ] Refine exit rules based on observations

### Phase 5: Live Trading (Week 9+)

- [ ] Start with 25% of intended capital
- [ ] Scale up over 4-8 weeks if performing
- [ ] Weekly performance review
- [ ] Monthly strategy assessment

---

## Code Structure

```
data-feeds/
├── STRATEGY.md              # This document
├── requirements.txt         # Dependencies
├── config/
│   └── settings.py          # API keys, parameters
├── data/
│   ├── insider.py           # SEC Form 4 fetching
│   ├── price.py             # Yahoo Finance price data
│   ├── sentiment.py         # News/analyst data
│   └── universe.py          # Stock universe management
├── strategy/
│   ├── screener.py          # Daily stock screening
│   ├── ranking.py           # Candidate scoring
│   ├── entries.py           # Entry signal logic
│   └── exits.py             # Exit signal logic
├── portfolio/
│   ├── positions.py         # Position tracking
│   ├── risk.py              # Risk management
│   └── orders.py            # Order generation
├── backtest/
│   ├── engine.py            # Backtest runner
│   └── metrics.py           # Performance calculations
└── main.py                  # Daily execution script
```

---

## Key Parameters (Tunable)

```python
# Momentum
MOMENTUM_LOOKBACK_DAYS = 126      # 6 months
SHORT_MOMENTUM_DAYS = 21          # 1 month
MOMENTUM_MIN_RETURN = 0.10        # 10% minimum
OVEREXTENSION_PERCENTILE = 0.95   # Top 5% excluded

# Moving Averages
MA_SHORT = 50
MA_LONG = 200

# Insider
INSIDER_LOOKBACK_DAYS = 30
MIN_INSIDER_BUYERS = 1
MIN_CLUSTER_BUYERS = 3
MIN_PURCHASE_VALUE = 100000

# Position Management
MAX_POSITIONS = 20
POSITION_SIZE_PCT = 0.05          # 5% per position
MAX_SECTOR_PCT = 0.30             # 30% max per sector

# Exits
INITIAL_STOP_PCT = 0.07           # 7% initial stop
MAX_HOLD_DAYS = 60
VIX_CAUTION_LEVEL = 30
VIX_EXIT_LEVEL = 40

# Trailing Stops
TRAIL_LEVELS = {
    0.10: 0.10,   # After 10% gain, trail 10%
    0.20: 0.12,   # After 20% gain, trail 12%
    0.30: 0.15,   # After 30% gain, trail 15%
}
```

---

## Monitoring and Logging

### Daily Log

```
Date: YYYY-MM-DD
Candidates screened: N
Candidates passed momentum: N
Candidates passed insider: N
Final picks: [TICKER1, TICKER2]
Exits triggered: [TICKER3 (stop), TICKER4 (time)]
Current positions: N
Portfolio value: $X
Daily P&L: $X (X%)
```

### Weekly Review

- Win/loss ratio vs expectations
- Average hold time
- Sector concentration
- Largest winners/losers
- Missed signals (stocks that would have worked)

### Monthly Assessment

- Cumulative return vs S&P 500
- Sharpe ratio (rolling)
- Maximum drawdown
- Strategy adherence score
- Parameter adjustment recommendations

---

## References

### Academic Research

- Jegadeesh & Titman (1993) - Momentum returns
- Seyhun (1986) - Insider trading predictive value
- Daniel & Moskowitz (2016) - Momentum crashes
- Barroso & Santa-Clara (2015) - Momentum risk management

### Data Sources

- SEC EDGAR: https://www.sec.gov/edgar/searchedgar/companysearch
- SEC API: https://data.sec.gov
- Yahoo Finance: https://finance.yahoo.com
- Finnhub: https://finnhub.io

---

## Disclaimer

This strategy is for educational and research purposes. Past performance does not guarantee future results. All trading involves risk of loss. Backtest results may not reflect actual trading due to:

- Survivorship bias in historical data
- Look-ahead bias in parameter selection
- Transaction costs and slippage
- Market impact of orders
- Changing market regimes

Always paper trade before using real capital.