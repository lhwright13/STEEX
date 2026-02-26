# MIS+ Trading Strategy

Momentum + Insider + Sentiment + Fundamentals + Options

A systematic daily stock selection strategy combining momentum factors, SEC insider trading data, sentiment analysis, fundamental quality filters, and options flow intelligence.

---

## Overview

| Attribute | Value |
|-----------|-------|
| Strategy Type | Long-only equity |
| Universe | S&P 500 |
| Selection | 2 stocks per day |
| Hold Period | Up to 30 trading days |
| Position Size | 3-6% (volatility-adjusted) |
| Max Positions | 10 concurrent |
| Execution | Alpaca Markets (paper or live) |

### Core Thesis

1. **Momentum (2-12 months)** has the strongest academic backing for excess returns
2. **Insider buying clusters** are historically bullish signals
3. **Sentiment shifts** improve entry timing and filter noise
4. **Fundamental quality** avoids value traps and speculative names
5. **Volatility-based sizing and exits** prevent crash losses

---

## Data Sources

| Data | Source | Cost | Purpose |
|------|--------|------|---------|
| Price / Volume / MA | Yahoo Finance | Free | Screening, P&L, indicators |
| Insider Trades (Form 4) | SEC EDGAR | Free | Core buy signal |
| VIX Index | Yahoo Finance | Free | Regime detection, risk |
| News Sentiment | Finnhub + VADER NLP | Free tier | Stock-specific sentiment |
| Geopolitical Sentiment | GDELT Project | Free | Macro/sector sentiment |
| Fundamentals | Yahoo Finance | Free | P/E, ROE, debt quality |
| Options Flow | Yahoo Finance | Free | Put/call ratio, IV |
| Earnings Calendar | Yahoo Finance | Free | Blackout avoidance |
| Execution + Holdings | Alpaca Markets | Free (paper) | Source of truth |

---

## Selection Process

### Stage 1: Universe Filter

Start with S&P 500, then remove:
- Price < $5 (wide spreads, high volatility)
- Average daily volume < 500,000 (liquidity risk)
- Earnings within 5 trading days (binary event risk)
- Less than 126 days of trading history

### Stage 2: Momentum Screen

Require ALL conditions:
| Condition | Value | Rationale |
|-----------|-------|-----------|
| 6-month return | > 15% | Medium-term momentum confirmation |
| 1-month return | > 5% | Not a falling knife |
| Price vs 50-day MA | Above | Short-term trend positive |
| Price vs 200-day MA | Above | Long-term trend positive |

Overextension filter (top 5% excluded) is available but currently disabled - backtesting showed better returns without it.

### Stage 3: Insider Enrichment

Using SEC Form 4 data, enriches candidates with:
| Signal | Strength |
|--------|----------|
| CEO or CFO purchase | Strongest |
| 3+ different insiders in 30 days | Strong (cluster buy) |
| Single purchase > $100,000 | Moderate |

### Stage 4: Sentiment Filter

Combined sentiment must exceed 30/100:
- Stock-specific sentiment (60% weight): Finnhub news + VADER NLP with financial lexicon
- Geopolitical/macro sentiment (40% weight): GDELT event analysis mapped to sector impacts

### Stage 5: Fundamental Quality Filter

| Metric | Threshold | Purpose |
|--------|-----------|---------|
| P/E ratio | < 50 | Filter out speculative |
| ROE | > 5% | Quality earnings |
| Debt/Equity | < 2.0 | Leverage limit |

Plus enrichment with options data (put/call ratio, IV rank) and PySR predictions (when model is trained).

### Final Ranking

Composite scoring:
| Factor | Weight |
|--------|--------|
| Momentum | 30% |
| Insider | 25% |
| Volume surge | 15% |
| Sentiment | 15% |
| Fundamental | 10% |
| Options | 5% |
| PySR | 10% (when available) |

Top 2 stocks by score are selected each trading day.

---

## Entry Rules

| Rule | Value |
|------|-------|
| Order Type | DAY limit order via Alpaca |
| Position Size | 3-6% of portfolio (volatility-adjusted via ATR) |
| Max Daily Entries | 2 |
| Min Score | 55.0 composite |
| Cooling-Off | 14 trading days after stop-loss before re-entry |

### Volatility-Adjusted Sizing

| ATR% | Classification | Position Size |
|------|---------------|---------------|
| < 3% | Low volatility | 6% of portfolio |
| 3-6% | Medium volatility | 5% of portfolio |
| > 6% | High volatility | 3% of portfolio |

All sizes are multiplied by the regime sizing multiplier (1.0x normal, 0.5x elevated) and capped at max_single_position_pct (20%).

---

## Exit Rules

### Exit 1: Stop Loss (Immediate)
- 10% below entry price
- Auto-executes, no confirmation

### Exit 2: Trailing Stop (Immediate)
| Gain Level | Trail Distance |
|------------|---------------|
| +10% | 12% from high |
| +20% | 15% from high |
| +30%+ | 15% from high |

Stops only ratchet up, never down.

### Exit 3: VIX Spike (Immediate)
| VIX Level | Action |
|-----------|--------|
| > 30 | Tighten all stops to 5% |
| > 40 | Exit 50% of all positions |

### Exit 4: Signal Reversal (End of Day)
- Price closes below 50-day MA
- Executes at post_market run

### Exit 5: Time-Based (Next Session)
- Maximum hold: 30 trading days
- Forces capital rotation into fresh opportunities

### Exit Priority
Check exits in this order daily:
1. Stop loss / trailing stop hit
2. VIX spike rules
3. Below MA signal reversal
4. Maximum hold time reached

---

## Risk Management

### Position Limits

| Limit | Value |
|-------|-------|
| Max single position | 20% of portfolio |
| Max sector exposure | 30% of portfolio |
| Max positions | 10 concurrent |
| Min cash reserve | 10% of portfolio |

### Drawdown Rules

| Portfolio Drawdown | Action |
|--------------------|--------|
| 10% | Review strategy, continue |
| 15% | Reduce position sizes |
| 20% | Pause new entries |
| 25% | Exit all positions |

---

## Key Parameters

All tunable in `config/config.yaml` with `STEEX_` env var overrides:

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
```

---

## References

### Academic Research

- Jegadeesh & Titman (1993) - Momentum returns
- Seyhun (1986) - Insider trading predictive value
- Daniel & Moskowitz (2016) - Momentum crashes
- Barroso & Santa-Clara (2015) - Momentum risk management

### Data Sources

- SEC EDGAR: https://data.sec.gov
- Yahoo Finance: https://finance.yahoo.com
- Finnhub: https://finnhub.io
- GDELT Project: https://www.gdeltproject.org
- Alpaca Markets: https://alpaca.markets

---

## Disclaimer

This strategy is for educational and research purposes. Past performance does not guarantee future results. All trading involves risk of loss. Always paper trade before using real capital.
