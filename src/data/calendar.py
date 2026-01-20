"""Earnings calendar data provider."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd
import yfinance as yf

from .base import DataProvider


class EarningsCalendar(DataProvider):
    """Provides earnings calendar information."""

    def __init__(self, cache_enabled: bool = True):
        """Initialize calendar provider."""
        super().__init__(cache_enabled)
        self._earnings_dates: Dict[str, Optional[datetime]] = {}

    def fetch(self, ticker: str, *args, **kwargs) -> pd.DataFrame:
        """Fetch earnings calendar for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            DataFrame with earnings dates
        """
        dates = self.get_earnings_dates(ticker)
        return pd.DataFrame({"earnings_date": dates})

    def get_earnings_dates(
        self,
        ticker: str,
        limit: int = 4,
    ) -> List[datetime]:
        """Get upcoming and recent earnings dates for a ticker.

        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of dates to return

        Returns:
            List of earnings dates
        """
        cache_key = self._get_cache_key(ticker, limit)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        try:
            stock = yf.Ticker(ticker)
            calendar = stock.calendar

            if calendar is None or calendar.empty:
                return []

            # Extract earnings date
            dates = []
            if "Earnings Date" in calendar.index:
                earnings_date = calendar.loc["Earnings Date"]
                if isinstance(earnings_date, pd.Timestamp):
                    dates = [earnings_date.to_pydatetime()]
                elif hasattr(earnings_date, "__iter__"):
                    dates = [
                        d.to_pydatetime() if isinstance(d, pd.Timestamp) else d
                        for d in earnings_date
                        if pd.notna(d)
                    ]

            self._set_cache(cache_key, dates[:limit])
            return dates[:limit]
        except Exception:
            return []

    def get_next_earnings(self, ticker: str) -> Optional[datetime]:
        """Get the next earnings date for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Next earnings date or None
        """
        if ticker in self._earnings_dates:
            return self._earnings_dates[ticker]

        dates = self.get_earnings_dates(ticker, limit=1)
        next_date = dates[0] if dates else None
        self._earnings_dates[ticker] = next_date
        return next_date

    def has_earnings_soon(
        self,
        ticker: str,
        days: int = 5,
        reference_date: Optional[datetime] = None,
    ) -> bool:
        """Check if a ticker has earnings within N days.

        Args:
            ticker: Stock ticker symbol
            days: Number of days to look ahead
            reference_date: Date to check from (defaults to today)

        Returns:
            True if earnings are within the specified window
        """
        reference = reference_date or datetime.now()
        next_earnings = self.get_next_earnings(ticker)

        if next_earnings is None:
            return False

        # Check if earnings are within the window
        days_until = (next_earnings - reference).days
        return 0 <= days_until <= days

    def filter_earnings_blackout(
        self,
        tickers: List[str],
        blackout_days: int = 5,
        reference_date: Optional[datetime] = None,
    ) -> List[str]:
        """Filter out tickers with upcoming earnings.

        Args:
            tickers: List of ticker symbols
            blackout_days: Days before earnings to exclude
            reference_date: Date to check from (defaults to today)

        Returns:
            Filtered list of tickers without imminent earnings
        """
        passed = []
        for ticker in tickers:
            if not self.has_earnings_soon(ticker, blackout_days, reference_date):
                passed.append(ticker)
        return passed

    def get_earnings_in_range(
        self,
        tickers: List[str],
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Dict[str, List[datetime]]:
        """Get earnings dates for multiple tickers within a date range.

        Args:
            tickers: List of ticker symbols
            start: Start of date range (defaults to today)
            end: End of date range (defaults to 30 days from start)

        Returns:
            Dict mapping ticker to list of earnings dates in range
        """
        start = start or datetime.now()
        end = end or (start + timedelta(days=30))

        results = {}
        for ticker in tickers:
            dates = self.get_earnings_dates(ticker)
            in_range = [d for d in dates if start <= d <= end]
            if in_range:
                results[ticker] = in_range

        return results

    def get_blackout_tickers(
        self,
        tickers: List[str],
        blackout_days: int = 5,
        reference_date: Optional[datetime] = None,
    ) -> Set[str]:
        """Get set of tickers in earnings blackout period.

        Args:
            tickers: List of ticker symbols
            blackout_days: Days before earnings to exclude
            reference_date: Date to check from (defaults to today)

        Returns:
            Set of tickers with upcoming earnings
        """
        blackout = set()
        for ticker in tickers:
            if self.has_earnings_soon(ticker, blackout_days, reference_date):
                blackout.add(ticker)
        return blackout
