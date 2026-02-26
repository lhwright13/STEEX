# STEEX - Systematic Trading with Execution and Exit Excellence

## Architecture

STEEX is an automated trading system built around a multi-agent architecture. Each agent has a specific role in the daily pipeline:

- **QuantManager** (`src/strategy/manager.py`) - Head orchestrator, coordinates all agents
- **DataAgent** - Fetches and validates external data (insider, VIX, prices, sentiment, fundamentals)
- **AnalysisAgent** - Runs the 5-stage screening pipeline and ranks candidates
- **RiskAgent** - Monitors positions, manages stops, detects exit signals
- **ExecutionAgent** - Executes entries and exits via Alpaca broker
- **ReportAgent** - Compiles structured reports and audit logs
- **RegimeAgent** - Multi-factor market regime detection (VIX, yield curve, breadth, dollar)
- **PortfolioAgent** - Constructs diversified portfolios with correlation/sector constraints
- **BacktestAgent** - Walk-forward backtesting for parameter validation
- **PostMortemAgent** - Analyzes completed trades, categorizes losses
- **ResearchAgent** - Signal hypothesis testing, alpha decay monitoring, weight optimization

## Key Principles

1. **Alpaca broker is the source of truth** for positions and account data. Never trust local state over broker state.
2. **Server-side stops** - every position gets a GTC stop order on Alpaca as a crash-proof safety net (0.5% below local stop to avoid noise triggers).
3. **No lookahead bias** - historical backtests use HistoricalPriceProvider truncated to the reference date.
4. **Config over code** - all tunable parameters live in `config/config.yaml` with Pydantic validation in `config/settings.py`.
5. **Safety first** - stops auto-execute, VIX spike exits are immediate, entries require confirmation unless `--yes` is passed.
6. **Market calendar awareness** - scheduler gates modes on Alpaca's clock/calendar (no runs on holidays, entries only when market is open).

## Self-Learning Loop

The learning loop (`src/learning/`) continuously optimizes strategy parameters by chaining analysis tools together:

```
PostMortem (trade analysis, 90 days)
    -> Alpha Decay (signal health check)
        -> [if degrading] Signal Research (factor testing + weight optimization)
            -> Walk-Forward OOS Validation (2-fold, Sharpe > 0, win_rate > 50%)
                -> [if validated] ConfigWriter applies changes to config.yaml
                    -> LearningJournal records everything
                    -> Gaps flagged for user review
```

### Rules

- **Never apply changes during market hours** (9:30 AM - 4:00 PM ET weekdays)
- **Max weight change per cycle**: 10% (configurable via `learning_weight_change_cap`)
- **All weights must sum to 1.0** after any change (auto-normalized)
- **OOS validation required** before any parameter is written to config
- **Full audit trail** in `data/learning/config_history.json`
- **Knowledge gaps** are flagged in `data/learning/gaps.json` for human review

### Schedules

- **Weekly**: Fridays 6:00 PM ET (full learning cycle via scheduler)
- **On-demand**: `venv/bin/python scripts/run_learning.py`
- **Dry run**: `venv/bin/python scripts/run_learning.py --dry-run`

### Data Files

- `data/learning/learning_journal.json` - timestamped log of all learning actions
- `data/learning/weight_recommendations.json` - latest recommended weights from signal research
- `data/learning/gaps.json` - knowledge gaps requiring human review
- `data/learning/config_history.json` - audit trail of all config changes

## Daily Schedule

The scheduler (`scheduler/config.yaml`) runs 9 cron jobs that break the trading day into discrete phases. Each phase runs through `claude -p` with a mode-specific prompt and a market calendar gate that skips runs on holidays or when the market is closed.

```
Time ET     Mode              What it does
-------     ----              ------------
7:00 AM     heartbeat         API health, account check, stop reconciliation
8:15 AM     screen            Data refresh + screening + ranking (NO entries)
9:45 AM     enter             Load screen results, execute entries, place server-side stops
11:00 AM    monitor           Risk check, stops, exits
1:30 PM     monitor           Risk check, stops, exits
3:45 PM     stop_sync         Update trailing stops, sync server-side stops before close
4:30 PM     post_market       EOD wrap-up, post-mortem, report
6:00 PM Fri learning          Weekly parameter optimization
10:00 AM Sun heartbeat        Weekend heartbeat
```

The `screen` and `enter` modes replace the old monolithic `pre_market` for daily use. `pre_market` still works as a combined mode for manual runs. Screening happens before market open so entries can wait for the opening auction to settle (9:45 AM). Server-side GTC stops on Alpaca protect positions even if the Mac sleeps or crashes.

## What Has Been Learned

*This section is updated by the learning loop over time.*

- Initial configuration: optimized via manual walk-forward analysis (see commit history)
- Scoring weights: momentum (0.30), insider (0.25), volume (0.15), sentiment (0.15), fundamental (0.10), options (0.05)
- Stops: 10% initial, trailing at 12%/15%/15% for 10%/20%/30% gains
- Hold period: 30 days max

## Development Notes

- Always use the venv: `venv/bin/python`
- Config changes require updating both `config/config.yaml` and `config/settings.py`
- Agent documentation lives in `agents/` - update when behavior changes
- Run tests from project root
