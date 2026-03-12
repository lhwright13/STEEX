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

    def _find_any_cached(
        self,
        ticker: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Optional[pd.DataFrame]:
        """Find any cached price data for this ticker that covers the requested range.

        Searches the L2 cache by key prefix for any PriceProvider entry
        for this ticker. If the cached data's date range covers start..end,
        returns the trimmed DataFrame.
        """
        if self._db_cache is None:
            return None

        prefix = f"PriceProvider:('{ticker}',"
        cached = self._db_cache.find_by_prefix(prefix)

        if cached is None or not isinstance(cached, pd.DataFrame) or cached.empty:
            return None

        # If no specific range requested, return as-is
        if start is None and end is None:
            return cached

        # Check if cached data covers the requested range
        idx = cached.index
        if idx.tz is not None:
            idx = idx.tz_localize(None)

        cached_start = idx.min()
        cached_end = idx.max()

        req_start = pd.Timestamp(start).tz_localize(None) if start else cached_start
        req_end = pd.Timestamp(end).tz_localize(None) if end else cached_end

        if cached_start <= req_start and cached_end >= req_end - pd.Timedelta(days=3):
            mask = (idx >= req_start) & (idx <= req_end)
            result = cached.loc[mask]
            if not result.empty:
                return result

        # Even if range doesn't fully cover, return what we have
        # (partial data is better than no data for backtesting)
        return cached

    def get_ohlcv_batch(
        self,
        tickers: List[str],
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Get OHLCV data for multiple tickers.

        Checks the cache first for each ticker, then bulk-downloads any
        tickers that aren't cached. If the bulk download fails, falls
        back to individual fetches.

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
        uncached = []

        # Check cache first for each ticker
        for ticker in tickers:
            cache_key = self._get_cache_key(ticker, start, end, days)
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                results[ticker] = cached
            else:
                uncached.append(ticker)

        if not uncached:
            return results

        try:
            if start and end:
                data = yf.download(
                    uncached,
                    start=start,
                    end=end,
                    progress=False,
                    threads=True,
                    auto_adjust=False,
                )
            else:
                period = f"{days}d" if days else "1y"
                data = yf.download(
                    uncached,
                    period=period,
                    progress=False,
                    threads=True,
                    auto_adjust=False,
                )

            if not data.empty:
                # yfinance >= 1.2 always returns MultiIndex columns (Price, Ticker)
                # Flatten by extracting each ticker via xs()
                for ticker in uncached:
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
            pass

        # Fall back to individual fetches for any still-missing tickers
        still_missing = [t for t in uncached if t not in results]
        for ticker in still_missing:
            df = self.get_ohlcv(ticker, start=start, end=end, days=days)
            if not df.empty:
                results[ticker] = df
                continue
            # Last resort: find any cached data for this ticker
            df = self._find_any_cached(ticker, start=start, end=end)
            if df is not None and not df.empty:
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

