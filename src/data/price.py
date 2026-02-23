"""Price data provider using yfinance."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

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

            # Handle single vs multiple tickers
            if len(tickers) == 1:
                results[tickers[0]] = data
            else:
                for ticker in tickers:
                    try:
                        ticker_data = data.xs(ticker, axis=1, level=1)
                        if not ticker_data.empty:
                            results[ticker] = ticker_data
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

    def get_returns(
        self,
        ticker: str,
        days: int,
        end: Optional[datetime] = None,
    ) -> Optional[float]:
        """Calculate return over N trading days.

        Args:
            ticker: Stock ticker symbol
            days: Number of trading days
            end: End date (defaults to today)

        Returns:
            Return as decimal (e.g., 0.10 for 10%) or None
        """
        # Fetch extra days to account for non-trading days
        calendar_days = int(days * 1.5) + 10
        df = self.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < days:
            return None

        try:
            end_price = df["Close"].iloc[-1]
            start_price = df["Close"].iloc[-min(days, len(df))]

            if start_price > 0:
                return (end_price - start_price) / start_price
            return None
        except (IndexError, KeyError):
            return None

    def get_price_vs_ma(
        self,
        ticker: str,
        ma_period: int,
    ) -> Optional[Dict[str, Union[float, bool]]]:
        """Get current price position relative to moving average.

        Args:
            ticker: Stock ticker symbol
            ma_period: Moving average period (e.g., 50 or 200)

        Returns:
            Dict with price, ma, and above_ma boolean
        """
        # Fetch enough data for MA calculation
        calendar_days = int(ma_period * 1.5) + 20
        df = self.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < ma_period:
            return None

        try:
            current_price = df["Close"].iloc[-1]
            ma_value = df["Close"].rolling(window=ma_period).mean().iloc[-1]

            return {
                "price": current_price,
                "ma": ma_value,
                "above_ma": current_price > ma_value,
            }
        except (IndexError, KeyError):
            return None
