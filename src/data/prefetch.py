"""Async data prefetcher that warms caches before pipeline execution.

Fetches all needed data using batch APIs and ThreadPoolExecutor,
then stores results in the existing L1/L2 cache. When the pipeline
runs, every provider call hits warm cache.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import yfinance as yf

from config.settings import Settings
from .calendar import EarningsCalendar
from .fundamentals import FundamentalsProvider
from .options import OptionsProvider
from .price import PriceProvider
from .sentiment import SentimentProvider
from .universe import Universe

logger = logging.getLogger(__name__)


@dataclass
class PrefetchReport:
    """Summary of a prefetch run."""

    prices_fetched: int = 0
    earnings_fetched: int = 0
    sentiment_fetched: int = 0
    fundamentals_fetched: int = 0
    options_fetched: int = 0
    cache_hits_skipped: int = 0
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)


class DataPrefetcher:
    """Prefetches data into the L1/L2 cache before pipeline execution.

    Call prefetch_all() with the universe ticker list before run_pipeline().
    Each downstream provider will then hit warm cache instead of making
    individual API calls.
    """

    def __init__(
        self,
        settings: Settings,
        price_provider: Optional[PriceProvider] = None,
        sentiment_provider: Optional[SentimentProvider] = None,
        fundamentals_provider: Optional[FundamentalsProvider] = None,
        earnings_calendar: Optional[EarningsCalendar] = None,
        options_provider: Optional[OptionsProvider] = None,
        universe: Optional[Universe] = None,
    ):
        self.settings = settings
        self.price_provider = price_provider or PriceProvider()
        self.sentiment_provider = sentiment_provider or SentimentProvider()
        self.fundamentals_provider = fundamentals_provider or FundamentalsProvider()
        self.earnings_calendar = earnings_calendar or EarningsCalendar()
        self.options_provider = options_provider or OptionsProvider()
        self.universe = universe or Universe()

    def prefetch_all(self, tickers: List[str]) -> PrefetchReport:
        """Run all prefetch jobs. Call before run_pipeline().

        Phases:
        1. Price data (batch yf.download, then store per-ticker in cache)
        2. Earnings calendar (threaded per-ticker)
        3. Sentiment (threaded per-ticker, respecting rate limits)
        4. Fundamentals (threaded per-ticker)
        """
        report = PrefetchReport()
        start = time.time()

        logger.info("Prefetching data for %d tickers", len(tickers))

        # Phase 1: Prices (batch download, then cache per-ticker)
        try:
            report.prices_fetched = self._prefetch_prices(
                tickers, days=self.settings.prefetch_price_days
            )
        except Exception as e:
            msg = f"Price prefetch failed: {e}"
            logger.error(msg)
            report.errors.append(msg)

        # Phases 2-4 run concurrently via thread pools
        # Each phase uses its own pool to respect different rate limits
        futures = {}

        with ThreadPoolExecutor(max_workers=3) as phase_pool:
            futures["earnings"] = phase_pool.submit(
                self._prefetch_earnings, tickers
            )
            futures["sentiment"] = phase_pool.submit(
                self._prefetch_sentiment, tickers
            )
            futures["fundamentals"] = phase_pool.submit(
                self._prefetch_fundamentals, tickers
            )

            for name, future in futures.items():
                try:
                    count = future.result()
                    if name == "earnings":
                        report.earnings_fetched = count
                    elif name == "sentiment":
                        report.sentiment_fetched = count
                    elif name == "fundamentals":
                        report.fundamentals_fetched = count
                except Exception as e:
                    msg = f"{name} prefetch failed: {e}"
                    logger.error(msg)
                    report.errors.append(msg)

        report.duration_seconds = round(time.time() - start, 1)
        logger.info(
            "Prefetch complete in %.1fs: prices=%d, earnings=%d, "
            "sentiment=%d, fundamentals=%d, errors=%d",
            report.duration_seconds,
            report.prices_fetched,
            report.earnings_fetched,
            report.sentiment_fetched,
            report.fundamentals_fetched,
            len(report.errors),
        )
        return report

    def _prefetch_prices(self, tickers: List[str], days: int = 320) -> int:
        """Batch-download price data via yf.download, then store per-ticker in cache.

        A single yf.download call for 320 calendar days covers every downstream
        lookback (5d, 21d, 126d, 200d, 320d). The batch result is split per-ticker
        and stored using PriceProvider's cache so get_ohlcv() hits warm cache.
        """
        if not tickers:
            return 0

        # Use PriceProvider's batch method which now caches per-ticker results
        batch_size = 100
        total_fetched = 0

        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            try:
                results = self.price_provider.get_ohlcv_batch(batch, days=days)
                total_fetched += len(results)
            except Exception as e:
                logger.warning("Price batch %d-%d failed: %s", i, i + len(batch), e)

        return total_fetched

    def _prefetch_earnings(self, tickers: List[str]) -> int:
        """Fetch earnings dates per-ticker using a thread pool.

        Uses the configured number of workers (default 20).
        Each call goes through EarningsCalendar which handles its own caching.
        """
        if not tickers:
            return 0

        fetched = 0
        max_workers = self.settings.prefetch_earnings_workers

        def _fetch_one(ticker):
            try:
                self.earnings_calendar.get_next_earnings(ticker)
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = pool.map(_fetch_one, tickers)
            fetched = sum(1 for r in results if r)

        return fetched

    def _prefetch_sentiment(self, tickers: List[str]) -> int:
        """Fetch sentiment per-ticker using a thread pool.

        Uses fewer workers (default 5) to respect Finnhub's 60 req/min rate limit.
        Skips tickers already in cache.
        """
        if not tickers or not self.settings.sentiment_enabled:
            return 0

        fetched = 0
        max_workers = self.settings.prefetch_sentiment_workers

        # Only prefetch tickers not already in cache
        uncached = []
        for t in tickers:
            cache_key = f"sentiment:{t}"
            if not self.sentiment_provider._is_cache_valid(cache_key):
                uncached.append(t)

        if not uncached:
            return 0

        def _fetch_one(ticker):
            try:
                self.sentiment_provider.get_sentiment(ticker)
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = pool.map(_fetch_one, uncached)
            fetched = sum(1 for r in results if r)

        return fetched

    def _prefetch_fundamentals(self, tickers: List[str]) -> int:
        """Fetch fundamental data per-ticker using a thread pool.

        Uses the configured number of workers (default 10).
        Skips tickers already in cache.
        """
        if not tickers or not self.settings.fundamental_enabled:
            return 0

        fetched = 0
        max_workers = self.settings.prefetch_fundamentals_workers

        # Only prefetch tickers not already in cache
        uncached = []
        for t in tickers:
            cache_key = f"fundamentals:{t}"
            if not self.fundamentals_provider._is_cache_valid(cache_key):
                uncached.append(t)

        if not uncached:
            return 0

        def _fetch_one(ticker):
            try:
                self.fundamentals_provider.get_fundamentals(ticker)
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = pool.map(_fetch_one, uncached)
            fetched = sum(1 for r in results if r)

        return fetched

    def prefetch_options(self, tickers: List[str]) -> int:
        """Fetch options data per-ticker. Call separately for final candidates only.

        Options data is expensive to fetch and only needed for the ~7-10
        final candidates after screening, not the full universe.
        """
        if not tickers or not self.settings.options_enabled:
            return 0

        fetched = 0
        for ticker in tickers:
            try:
                self.options_provider.get_options_sentiment(ticker)
                fetched += 1
            except Exception:
                continue

        return fetched
