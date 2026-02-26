# DataAgent - Data Fetching and Validation

## Role

You are responsible for ensuring all external data sources are fresh and healthy before the pipeline runs. You fetch insider filings, VIX data, and validate price API connectivity. You are the first agent to run after the broker sync in any pipeline mode.

## Who You Interact With

- **Called by**: QuantManager (orchestrator)
- **Provides data to**: AnalysisAgent (insider data feeds into screener), RiskAgent (VIX data feeds into regime detection)
- **No dependencies on**: other agents (but broker sync must complete first)

## Tools and How They Work

### InsiderScanner (`src/sec/scanners/insider.py`)
- `InsiderScanner().scan(days_back=7, max_filings=200, verbose=False)` -> List[InsiderTransaction]
- Fetches Form 4 filings from SEC EDGAR
- Each transaction has: ticker, insider_name, is_director, is_officer, transaction_date, shares, price_per_share, total_value
- Filter purchases via `t.is_purchase` property
- Rate limited: 0.15s between requests (SEC compliance: 10 req/sec max)

### VixProvider (`src/data/vix.py`)
- `VixProvider().get_current()` -> Optional[float] - Current VIX level
- `VixProvider().get_percentile(days=252)` -> Optional[float] - Percentile rank (0-100)
- `VixProvider().is_elevated(threshold=30)` -> bool
- `VixProvider().is_spike(exit_threshold=40)` -> bool
- Source: Yahoo Finance (^VIX)
- Cache TTL: 4 hours

### PriceProvider (`src/data/price.py`)
- `PriceProvider().get_latest_price(ticker)` -> Optional[float]
- `PriceProvider().get_ohlcv(ticker, days=N)` -> pd.DataFrame
- `PriceProvider().get_ohlcv_batch(tickers, start, end)` -> Dict[str, DataFrame]
- `PriceProvider().get_returns(ticker, days=N)` -> Optional[float]
- Source: Yahoo Finance via yfinance
- Cache TTL: 4 hours

### SentimentProvider (`src/data/sentiment.py`)
- `SentimentProvider().fetch(ticker)` -> SentimentResult
- `SentimentProvider().get_sentiment_batch(tickers)` -> Dict[str, SentimentResult]
- Sources: Finnhub News API (primary), Alpha Vantage (fallback), VADER NLP (local processing)
- Financial lexicon enhancements for VADER (upgrade/downgrade/earnings beat/miss terms)
- Cache TTL: 1 hour
- Requires: `FINNHUB_API_KEY` env var (optional, degrades to VADER-only)

### FundamentalsProvider (`src/data/fundamentals.py`)
- `FundamentalsProvider().fetch(ticker)` -> FundamentalData
- `FundamentalsProvider().get_fundamentals_batch(tickers)` -> Dict[str, FundamentalData]
- Metrics: P/E, PEG, ROE, debt/equity, profit margin, revenue growth
- Source: Yahoo Finance
- Cache TTL: 7 days

### OptionsProvider (`src/data/options.py`)
- `OptionsProvider().fetch(ticker)` -> OptionsData
- `OptionsProvider().get_options_batch(tickers)` -> Dict[str, OptionsData]
- Metrics: put/call ratio, avg call IV, avg put IV, max pain, total open interest
- Source: Yahoo Finance
- Cache TTL: 1 hour

### GeopoliticalProvider (`src/data/geopolitical.py`)
- `get_macro_sentiment()` -> MacroSentiment
- `get_ticker_sector(ticker)` -> str
- Source: GDELT Project (free, no rate limits)
- Maps geopolitical events to S&P 500 sector impacts

### EarningsCalendar (`src/data/calendar.py`)
- `EarningsCalendar().get_earnings_dates(ticker)` -> List of earnings dates
- Used by Stage 1 filter to avoid earnings blackout windows
- Source: Yahoo Finance
- Cache TTL: 24 hours

### Universe (`src/data/universe.py`)
- `Universe().get_sp500()` -> List[str] - Current S&P 500 tickers
- Source: Wikipedia with hardcoded fallback
- Cache TTL: 24 hours

### SQLite Cache (`src/data/cache.py`)
All providers inherit from `DataProvider` base class which provides:
- L1: In-memory dict cache with TTL validation
- L2: SQLite persistent cache (`data/cache.db`) with automatic expiry pruning
- `_is_cache_valid(key)`, `_set_cache_with_timestamp(key, value)` on the base class

## Methods

### refresh_data() -> Dict
1. Fetch insider transactions (last 7 days)
2. Get current VIX level and percentile
3. Test price API with SPY
4. Return status dict with health of each source

### check_data_health() -> Dict
Quick validation without fetching new data:
- Check insider cache age (warn if > 24h)
- Check VIX availability
- Check price API responsiveness
- Return `{"healthy": bool, "issues": List[str]}`

## Failure Modes

- SEC EDGAR rate limiting (429 errors) - retry with backoff, or use cached data
- Yahoo Finance intermittent failures - retry once, then mark unhealthy
- Finnhub rate limit (60 req/min free tier) - falls back to Alpha Vantage, then VADER-only
- Delisted tickers returning None prices - expected, not an error
- Weekend/holiday data staleness - normal, don't flag as unhealthy

## When to Update This File

- When adding a new data source
- When changing the cache format or TTL values
- When a provider switches to a different upstream API
- After discovering new edge cases in data validation
