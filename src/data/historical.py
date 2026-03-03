"""Historical price provider for backtesting without lookahead bias."""

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from .price import PriceProvider


class HistoricalPriceProvider(PriceProvider):
    """PriceProvider that returns data truncated to a reference date.

    Prevents lookahead bias by ensuring all downstream consumers
    (MomentumCalculator, TechnicalIndicators, StockScreener) only see
    data up to and including the reference date. Since all consumers
    accept a PriceProvider via dependency injection, swapping in this
    class makes the entire pipeline historical with zero code changes.
    """

    def __init__(
        self,
        reference_date: datetime,
        price_cache: Dict[str, pd.DataFrame],
    ):
        """Initialize with a reference date and pre-fetched price data.

        Args:
            reference_date: The "as-of" date - no data after this is visible
            price_cache: Dict mapping ticker to full OHLCV DataFrame
        """
        super().__init__(cache_enabled=False)
        self.reference_date = reference_date
        self.price_cache = price_cache

    def _truncate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Truncate DataFrame to reference_date."""
        if df.empty:
            return df
        idx = df.index
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        mask = idx <= self.reference_date
        return df.loc[mask]

    def get_ohlcv(
        self,
        ticker: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get OHLCV data truncated to reference_date.

        Args:
            ticker: Stock ticker symbol
            start: Start date (applied after truncation)
            end: End date (capped at reference_date)
            days: Number of calendar days back from reference_date

        Returns:
            DataFrame with OHLCV data up to reference_date
        """
        df = self.price_cache.get(ticker)
        if df is None or df.empty:
            return pd.DataFrame()

        truncated = self._truncate(df)
        if truncated.empty:
            return truncated

        if days:
            # Take last N calendar days from reference_date
            cutoff = self.reference_date - pd.Timedelta(days=days)
            idx = truncated.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            truncated = truncated.loc[idx >= cutoff]
        elif start:
            idx = truncated.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            truncated = truncated.loc[idx >= start]

        return truncated

    def fetch(
        self,
        ticker: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data (delegates to get_ohlcv)."""
        return self.get_ohlcv(ticker, start=start, end=end, days=days)

    def get_ohlcv_batch(
        self,
        tickers: List[str],
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Get OHLCV data for multiple tickers."""
        results = {}
        for ticker in tickers:
            df = self.get_ohlcv(ticker, start=start, end=end, days=days)
            if not df.empty:
                results[ticker] = df
        return results

    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Get the closing price on reference_date (not today)."""
        df = self.get_ohlcv(ticker, days=5)
        if df.empty:
            return None
        return df["Close"].iloc[-1]

