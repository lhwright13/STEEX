"""Price data provider using yfinance."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from .base import DataProvider


class PriceProvider(DataProvider):
    """Fetches OHLCV price data from Yahoo Finance."""

    default_ttl = 4 * 3600  # 4 hours

    def fetch(
        self,
        ticker: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a single ticker.

        Args:
            ticker: Stock ticker symbol
            start: Start date
            end: End date (defaults to today)
            days: Number of calendar days to fetch (alternative to start/end)

        Returns:
            DataFrame with OHLCV data
        """
        return self.get_ohlcv(ticker, start=start, end=end, days=days)

    def get_ohlcv(
        self,
        ticker: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get OHLCV data for a single ticker.

        Args:
            ticker: Stock ticker symbol
            start: Start date
            end: End date (defaults to today)
            days: Number of calendar days to fetch (alternative to start/end)

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume, Adj Close
        """
        cache_key = self._get_cache_key(ticker, start, end, days)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        # Check if a longer-period cache entry exists that covers this request
        if days and not start:
            superset = self._find_cached_superset(ticker, days)
            if superset is not None:
                return superset

        if days:
            end = end or datetime.now()
            start = end - timedelta(days=days)

        try:
            stock = yf.Ticker(ticker)
            if start and end:
                df = stock.history(start=start, end=end, auto_adjust=False)
            elif days:
                df = stock.history(period=f"{days}d", auto_adjust=False)
            else:
                # Default to 1 year
                df = stock.history(period="1y", auto_adjust=False)

            if not df.empty:
                # Standardize column names
                df.columns = [c.replace(" ", "_") for c in df.columns]
                self._set_cache(cache_key, df)

            return df
        except Exception:
            return pd.DataFrame()

    def _find_cached_superset(self, ticker: str, needed_days: int) -> Optional[pd.DataFrame]:
        """Check if a longer cached period exists that covers this request.

        When the prefetcher stores 320 days of data, downstream calls for
        shorter periods (5d, 21d, 126d, 200d) can be served by truncating
        the longer cached entry instead of making a new API call.
        """
        for candidate_days in [320, 200, 126, 21]:
            if candidate_days <= needed_days:
                continue
            key = self._get_cache_key(ticker, None, None, candidate_days)
            cached = self._get_from_cache(key)
            if cached is not None and not cached.empty:
                return cached.iloc[-needed_days:] if len(cached) >= needed_days else cached
        return None

    def get_ohlcv_batch(
        self,
        tickers: List[str],
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Get OHLCV data for multiple tickers.

        Args:
            tickers: List of ticker symbols
            start: Start date
            end: End date (defaults to today)
            days: Number of calendar days to fetch

        Returns:
            Dict mapping ticker to DataFrame
        """
        if days:
            end = end or datetime.now()
            start = end - timedelta(days=days)

        results = {}

        try:
            if start and end:
                data = yf.download(
                    tickers,
                    start=start,
                    end=end,
                    progress=False,
                    threads=True,
                    auto_adjust=False,
                )
            else:
                period = f"{days}d" if days else "1y"
                data = yf.download(
                    tickers,
                    period=period,
                    progress=False,
                    threads=True,
                    auto_adjust=False,
                )

            if data.empty:
                return results

            # yfinance >= 1.2 always returns MultiIndex columns (Price, Ticker)
            # Flatten by extracting each ticker via xs()
            for ticker in tickers:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        ticker_data = data.xs(ticker, axis=1, level=1)
                    else:
                        ticker_data = data
                    if not ticker_data.empty:
                        ticker_data.columns = [c.replace(" ", "_") for c in ticker_data.columns]
                        results[ticker] = ticker_data
                        cache_key = self._get_cache_key(ticker, start, end, days)
                        self._set_cache(cache_key, ticker_data)
                except (KeyError, ValueError):
                    continue

        except Exception:
            # Fall back to individual fetches
            for ticker in tickers:
                df = self.get_ohlcv(ticker, start=start, end=end, days=days)
                if not df.empty:
                    results[ticker] = df

        return results

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Get the latest closing price for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Latest closing price or None if unavailable
        """
        df = self.get_ohlcv(ticker, days=5)
        if df.empty:
            return None
        return df["Close"].iloc[-1]

