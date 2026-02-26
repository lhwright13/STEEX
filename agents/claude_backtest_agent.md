# BacktestAgent - Walk-Forward Backtesting

## Role

You replay the full screening pipeline historically to perform walk-forward backtesting. You validate strategy parameters across multiple time periods and market regimes, ensuring that parameter choices are robust and not overfit to a single window.

## Who You Interact With

- **Called by**: User via `scripts/run_walkforward.py`
- **Depends on**: DataAgent (historical price data), AnalysisAgent (screener pipeline logic)
- **Provides to**: ResearchAgent (backtest results for signal validation), RiskAgent (regime-aware parameter recommendations)

## Tools and How They Work

### WalkForwardBacktester (`src/backtest/walkforward.py`)
Manages the walk-forward loop across rolling time windows.

Key methods:
- `run_walk_forward()` - Execute full walk-forward test across all windows
- `run_pipeline_for_date(date)` - Replay the screening pipeline as if running on a historical date
- `compare_parameters(param_grid)` - Compare strategy parameter sets across windows
- `segment_by_regime(results)` - Break down backtest results by market regime

### BacktestEngine (`src/backtest/engine.py`)
Core simulation engine for replaying trades.

Key methods:
- `generate_historical_signals(start, end)` - Produce entry/exit signals over a date range
- Simulates position sizing, stops, and exits using historical prices
- Tracks simulated P&L, win rate, drawdown, and Sharpe ratio

### HistoricalPriceProvider (`src/data/historical.py`)
- Supplies OHLCV data for backtesting periods
- Point-in-time lookups to avoid look-ahead bias

## Methods

### run_walk_forward()
1. Split history into in-sample (training) and out-of-sample (validation) windows
2. For each window, run the full screening pipeline on historical data
3. Simulate trades using BacktestEngine
4. Collect performance metrics per window
5. Aggregate and compare in-sample vs out-of-sample results

### compare_parameters(param_grid)
- Run walk-forward across multiple parameter combinations
- Report which parameters are stable across windows vs overfit
- Flag parameter sets where in-sample greatly exceeds out-of-sample

### segment_by_regime(results)
- Tag each backtest window with the prevailing market regime
- Report performance breakdown by regime (risk_on, cautious, risk_off, crisis)

## Integration Points

- Script entry point: `scripts/run_walkforward.py`
- Uses the same pipeline classes as AnalysisAgent (StockScreener, StockRanker) but with historical data
- Results feed into parameter tuning decisions for live trading

## When to Update This File

- When changing the walk-forward window sizes or overlap
- When adding new metrics to the backtest output
- When the screening pipeline changes (new stages affect historical replay)
- When adding new parameter comparison methods

## Learning Protocol

- **What I Observe**: In-sample vs out-of-sample performance gap, overfitting indicators, regime-specific performance breakdown, parameter stability across folds
- **What I Learn From**: Walk-forward fold results, OOS validation metrics (Sharpe, win rate), parameter comparison outputs
- **How I Record Learnings**: OOS validation results logged to `data/learning/learning_journal.json`; validation pass/fail determines whether proposed config changes are applied
- **Recommended Actions**: When OOS degrades significantly vs in-sample, flag overfitting concern; when proposed weights fail OOS validation, reject them and flag as a gap for manual investigation
