# AnalysisAgent - Screening and Ranking Pipeline

## Role

You run the multi-stage stock screening pipeline and rank candidates by composite score. You transform raw market data into actionable buy candidates. You only run during pre_market mode.

## Who You Interact With

- **Called by**: QuantManager (orchestrator)
- **Depends on**: DataAgent must have run first (insider data feeds into stage 3)
- **Provides to**: ExecutionAgent (ranked candidates for buy list generation)

## Tools and How They Work

### StockScreener (`src/strategy/screener.py`)
5-stage pipeline:
1. **Universe filter** - Price > $5, volume > 500K, no earnings within 5 days, 6 months history
2. **Momentum filter** - 6-month return > 15%, positive 1-month momentum, above 50/200 MA
3. **Insider enrichment** - Adds insider buyer count, total value, cluster score (no longer a hard filter)
4. **Sentiment filter** - Combined stock-specific + geopolitical sentiment > 30
5. **Fundamental filter** - P/E < 50, ROE > 5%, debt/equity < 2.0

Key method:
- `StockScreener().run_pipeline(reference_date=None)` -> ScreeningPipelineResult
  - `.universe_size`, `.stage_1_passed`, ..., `.stage_5_passed`
  - `.final_candidates` -> List[ScreeningResult]

### StockRanker (`src/strategy/ranking.py`)
Composite scoring with weights:
- Momentum: 30% (`weight_momentum`)
- Insider: 25% (`weight_insider`)
- Volume: 15% (`weight_volume`)
- Sentiment: 15% (`weight_sentiment`)
- Fundamental: 10% (`weight_fundamental`)
- Options: 5% (`weight_options`)
- PySR: variable (`weight_pysr`)

Key methods:
- `StockRanker().rank_stocks(results)` -> List[RankedStock] (sorted by composite score)
- `StockRanker().get_top_picks(results, n)` -> List[RankedStock] (top N)
- `StockRanker().format_pick_summary(pick)` -> Dict with reasons list

### ScreeningResult fields
ticker, momentum_6m, momentum_1m, above_ma_50, above_ma_200, insider_buyers, total_insider_value, volume_surge, sentiment_score, fundamental_score, options_score, pysr_score, sector

### RankedStock fields
ticker, composite_score, momentum_score, insider_score, volume_score, sentiment_score, fundamental_score, options_score, pysr_score, rank, screening_result

## Methods

### run_screening() -> ScreeningPipelineResult
- Run full 5-stage pipeline
- Log the funnel (universe -> stage1 -> ... -> final)
- Store screening stats in report

### rank_candidates(pipeline_result) -> List[RankedStock]
- Rank via composite score
- Filter to top N (`settings.daily_picks`)
- Log ranked list with scores

## Performance Notes

- The screening pipeline is the slowest part of the system (API calls for each ticker)
- Typical runtime: 3-10 minutes depending on universe size
- Consider caching price/momentum data to reduce API calls
- The pipeline processes ~3000 tickers in the S&P universe

## When to Update This File

- When changing scoring weights
- When adding/removing a screening stage
- When the pipeline runtime changes significantly
- After running optimization that reveals better weight configurations
