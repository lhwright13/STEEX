"""VIX (Volatility Index) data provider."""

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from .base import DataProvider


class VixProvider(DataProvider):
    """Fetches VIX data from Yahoo Finance."""

    VIX_TICKER = "^VIX"

    def fetch(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch VIX historical data.

        Args:
            start: Start date
            end: End date (defaults to today)
            days: Number of calendar days to fetch

        Returns:
            DataFrame with VIX OHLCV data
        """
        cache_key = self._get_cache_key(start, end, days)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        if days:
            end = end or datetime.now()
            start = end - timedelta(days=days)

        try:
            vix = yf.Ticker(self.VIX_TICKER)
            if start and end:
                df = vix.history(start=start, end=end)
            elif days:
                df = vix.history(period=f"{days}d")
            else:
                df = vix.history(period="1y")

            if not df.empty:
                self._set_cache(cache_key, df)

            return df
        except Exception:
            return pd.DataFrame()

    def get_current(self) -> Optional[float]:
        """Get the current VIX level.

        Returns:
            Current VIX value or None if unavailable
        """
        df = self.fetch(days=5)
        if df.empty:
            return None

        try:
            return df["Close"].iloc[-1]
        except (IndexError, KeyError):
            return None

    def get_historical(self, days: int = 252) -> pd.DataFrame:
        """Get historical VIX data.

        Args:
            days: Number of calendar days of history

        Returns:
            DataFrame with VIX data
        """
        return self.fetch(days=days)

    def is_elevated(self, threshold: float = 30) -> bool:
        """Check if VIX is above a threshold.

        Args:
            threshold: VIX level to check against

        Returns:
            True if VIX is above threshold
        """
        current = self.get_current()
        if current is None:
            return False
        return current > threshold

    def is_spike(self, exit_threshold: float = 40) -> bool:
        """Check if VIX is in spike territory.

        Args:
            exit_threshold: VIX level indicating a spike

        Returns:
            True if VIX is in spike territory
        """
        current = self.get_current()
        if current is None:
            return False
        return current > exit_threshold

    def get_percentile(self, days: int = 252) -> Optional[float]:
        """Get current VIX percentile relative to historical range.

        Args:
            days: Number of days for historical comparison

        Returns:
            Percentile (0-100) or None
        """
        df = self.fetch(days=days)
        if df.empty or len(df) < 10:
            return None

        try:
            current = df["Close"].iloc[-1]
            historical = df["Close"].iloc[:-1]
            percentile = (historical < current).mean() * 100
            return percentile
        except (IndexError, KeyError):
            return None
