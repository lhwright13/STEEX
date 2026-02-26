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

**Stage 1 - Universe Filter:**
- Price > $5 (avoid penny stocks)
- Average daily volume > 500K (liquidity)
- No earnings within 5 trading days (avoid binary events)
- Minimum 126 days of trading history

**Stage 2 - Momentum Screen:**
- 6-month return > 15%
- 1-month return > 5%
- Price above 50-day MA
- Price above 200-day MA
- Overextension filter (top 5% excluded) - currently disabled via `overextension_filter_enabled: false`

**Stage 3 - Insider Enrichment:**
- Fetches Form 4 filings from SEC EDGAR
- Adds insider buyer count, total value, cluster buy score
- Not a hard filter - enriches with insider data for scoring

**Stage 4 - Sentiment Filter:**
- Combined stock-specific + geopolitical sentiment
- Finnhub news API + VADER NLP with financial lexicon
- Geopolitical events from GDELT mapped to sector impacts
- Minimum score: 30 (out of 100) to pass

**Stage 5 - Fundamental Filter:**
- P/E ratio < 50 (filter out speculative)
- ROE > 5% (quality filter)
- Debt/equity < 2.0 (leverage limit)
- Plus enrichment: options data (put/call ratio, IV), PySR predictions

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
- PySR: 10% (`weight_pysr`) - when trained model is available

Key methods:
- `StockRanker().rank_stocks(results)` -> List[RankedStock] (sorted by composite score)
- `StockRanker().get_top_picks(results, n)` -> List[RankedStock] (top N)
- `StockRanker().format_pick_summary(pick)` -> Dict with reasons list

### ScreeningResult Fields
ticker, momentum_6m, momentum_1m, above_ma_50, above_ma_200, insider_buyers, total_insider_value, volume_surge, sentiment_score, fundamental_score, options_score, pysr_score, sector

### RankedStock Fields
ticker, composite_score, momentum_score, insider_score, volume_score, sentiment_score, fundamental_score, options_score, pysr_score, rank, screening_result

## Methods

### run_screening() -> ScreeningPipelineResult
- Run full 5-stage pipeline
- Log the funnel (universe -> stage1 -> ... -> final)
- Store screening stats in report

### rank_candidates(pipeline_result) -> List[RankedStock]
- Rank via composite score
- Filter to top N (`settings.daily_picks`, default 2)
- Log ranked list with scores

## Typical Funnel

```
503 universe -> 480 stage 1 -> 100 stage 2 -> 20 stage 3 -> 15 stage 4 -> 8 stage 5 -> 2 entries
```

## Performance Notes

- The screening pipeline is the slowest part of the system (API calls for each ticker)
- Typical runtime: 3-10 minutes depending on universe size
- SQLite L2 cache (`data/cache.db`) reduces repeated API calls
- The pipeline processes the full S&P 500 universe

## When to Update This File

- When changing scoring weights
- When adding/removing a screening stage
- When the pipeline runtime changes significantly
- After running optimization that reveals better weight configurations

## Learning Protocol

- **What I Observe**: Score-return correlation, per-signal information coefficients, screening funnel conversion rates, candidate quality trends
- **What I Learn From**: Signal research hypothesis tests (`src/research/signal_tester.py`), feature matrix analysis, PostMortem score accuracy classification
- **How I Record Learnings**: Recommended weights saved to `data/learning/weight_recommendations.json`; weight changes applied to config after OOS validation
- **Recommended Actions**: When score-return correlation drops below 0.10, trigger signal research; when a signal's IC is consistently near zero, consider reducing its weight; all weight changes are auto-normalized to sum to 1.0
