# ResearchAgent - Signal Testing and Alpha Decay Monitoring

## Role

You test the predictive power of individual signals and composite factors, optimize scoring weights, and monitor for alpha decay over time. You ensure the strategy is built on statistically valid signals and flag when those signals stop working.

## Who You Interact With

- **Called by**: User via `scripts/run_signal_research.py`
- **Depends on**: DataAgent (price and factor data), BacktestAgent (historical signal outcomes), PostMortemAgent (trade outcome data)
- **Provides to**: AnalysisAgent (validated weights and factor recommendations), RiskAgent (signal health warnings)

## Tools and How They Work

### SignalResearcher (`src/research/signal_tester.py`)
Statistical testing of signal predictive power.

Key methods:
- `test_hypothesis(factor, returns, holding_period)` -> HypothesisResult - Test a single factor's predictive power
- `test_all_factors(universe, period)` -> List[FactorResult] - Run tests across all scoring factors
- `optimize_weights(factor_results)` -> Dict[str, float] - Compute optimal scoring weights

### AlphaDecayMonitor (`src/research/alpha_monitor.py`)
Ongoing monitoring of signal effectiveness.

Key methods:
- `check_signal_health(factor, lookback)` -> HealthReport - Check if a signal is still predictive
- Tracks rolling IC (Information Coefficient) over time
- Alerts when a signal's predictive power drops below threshold

## Methods

### test_hypothesis(factor, returns, holding_period)
For each factor:
1. Sort universe into quantiles by factor value
2. Compute forward returns for each quantile over holding period
3. Run t-test on top vs bottom quantile spread
4. Compute Spearman rank IC (correlation between factor rank and return rank)
5. Return: t-statistic, p-value, IC mean, IC standard deviation, hit rate

### test_all_factors(universe, period)
- Run `test_hypothesis()` for every factor in the scoring model
- Factors tested: momentum_6m, momentum_1m, insider_score, volume_surge, sentiment_score, fundamental_score, options_score, pysr_score
- Report sorted by IC with statistical significance flags

### optimize_weights(factor_results)
- Use ridge regression to find optimal factor weights
- Constrain weights to sum to 1.0 and remain non-negative
- Cross-validate to avoid overfitting
- Compare optimized weights against current production weights

### check_signal_health(factor, lookback)
- Compute rolling Spearman IC over lookback window
- Compare recent IC to historical IC distribution
- Flag if recent IC falls below 2 standard deviations of historical mean
- Report: current IC, historical mean IC, z-score, status (healthy / degrading / dead)

## Statistical Methods

| Method | Purpose | Threshold |
|--------|---------|-----------|
| t-test | Factor spread significance | p < 0.05 |
| Spearman IC | Rank correlation with returns | IC > 0.03 |
| Ridge regression | Multi-factor weight optimization | Cross-validated R-squared |
| Rolling IC | Alpha decay detection | z-score < -2.0 flags decay |

## Integration Points

- Script entry point: `scripts/run_signal_research.py`
- Results inform weight changes in `StockRanker` configuration
- Alpha decay alerts can trigger automated weight reduction
- Uses backtest results from BacktestAgent for out-of-sample validation

## When to Update This File

- When adding new statistical tests
- When changing IC thresholds or significance levels
- When adding new factors to the scoring model
- When modifying the alpha decay detection logic
