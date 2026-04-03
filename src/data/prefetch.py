"""Data prefetcher that warms caches before pipeline execution.

Fetches all needed data sequentially in small batches, then stores
results in the existing L1/L2 cache. When the pipeline runs, every
provider call hits warm cache.

NOTE: An earlier version used ThreadPoolExecutor for concurrent fetches.
This caused DNS thread exhaustion, SQLite locking, and NoneType crashes
at scale (500 tickers). Sequential fetching with threads=False is slower
but actually works.
"""

import logging
import time
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
        """Run all prefetch jobs sequentially. Call before run_pipeline().

        Phases (sequential to avoid thread/DNS exhaustion):
        1. Price data (batch yf.download in small chunks, threads=False)
        2. Earnings calendar (sequential per-ticker)
        3. Sentiment (sequential per-ticker, respects rate limits)
        4. Fundamentals (sequential per-ticker)
        """
        report = PrefetchReport()
        start = time.time()

        logger.info("Prefetching data for %d tickers", len(tickers))

        # Phase 1: Prices
        try:
            report.prices_fetched = self._prefetch_prices(
                tickers, days=self.settings.prefetch_price_days
            )
        except Exception as e:
            msg = f"Price prefetch failed: {e}"
            logger.error(msg)
            report.errors.append(msg)

        # Phase 2: Earnings
        try:
            report.earnings_fetched = self._prefetch_earnings(tickers)
        except Exception as e:
            msg = f"Earnings prefetch failed: {e}"
            logger.error(msg)
            report.errors.append(msg)

        # Phase 3: Sentiment
        try:
            report.sentiment_fetched = self._prefetch_sentiment(tickers)
        except Exception as e:
            msg = f"Sentiment prefetch failed: {e}"
            logger.error(msg)
            report.errors.append(msg)

        # Phase 4: Fundamentals
        try:
            report.fundamentals_fetched = self._prefetch_fundamentals(tickers)
        except Exception as e:
            msg = f"Fundamentals prefetch failed: {e}"
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
        """Batch-download price data via yf.download in small chunks.

        Uses threads=False to avoid DNS thread exhaustion. Batches of 20
        tickers keep each yf.download call manageable.
        """
        if not tickers:
            return 0

        batch_size = 20
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
        """Fetch earnings dates sequentially per-ticker."""
        if not tickers:
            return 0

        fetched = 0
        for ticker in tickers:
            try:
                self.earnings_calendar.get_next_earnings(ticker)
                fetched += 1
            except Exception:
                continue

        return fetched

    def _prefetch_sentiment(self, tickers: List[str]) -> int:
        """Fetch sentiment sequentially per-ticker.

        Skips tickers already in cache.
        """
        if not tickers or not self.settings.sentiment_enabled:
            return 0

        fetched = 0
        for ticker in tickers:
            cache_key = f"sentiment:{ticker}"
            if self.sentiment_provider._is_cache_valid(cache_key):
                continue
            try:
                self.sentiment_provider.get_sentiment(ticker)
                fetched += 1
            except Exception:
                continue

        return fetched

    def _prefetch_fundamentals(self, tickers: List[str]) -> int:
        """Fetch fundamental data sequentially per-ticker.

        Skips tickers already in cache.
        """
        if not tickers or not self.settings.fundamental_enabled:
            return 0

        fetched = 0
        for ticker in tickers:
            cache_key = f"fundamentals:{ticker}"
            if self.fundamentals_provider._is_cache_valid(cache_key):
                continue
            try:
                self.fundamentals_provider.get_fundamentals(ticker)
                fetched += 1
            except Exception:
                continue

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
