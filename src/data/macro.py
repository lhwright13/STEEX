"""Macro data providers for regime detection.

Three providers that subclass DataProvider for free L1/L2 caching:
- YieldCurveProvider: 10Y-2Y Treasury spread
- MarketBreadthProvider: market breadth via RSP/SPY and constituent MA analysis
- DollarStrengthProvider: USD strength via UUP ETF
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
import yfinance as yf

from .base import DataProvider


class YieldCurveProvider(DataProvider):
    """10Y-2Y Treasury yield spread via Yahoo Finance.

    Uses ^TNX (10-year) and ^IRX (13-week T-bill, proxy for short end).
    A negative spread signals yield curve inversion.
    """

    default_ttl = 4 * 3600  # 4 hours

    TICKER_10Y = "^TNX"
    TICKER_2Y = "^IRX"

    def fetch(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch yield spread history."""
        cache_key = self._get_cache_key("yield_spread", start, end, days)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        period = f"{days}d" if days else "6mo"
        try:
            tnx = yf.Ticker(self.TICKER_10Y).history(period=period)
            irx = yf.Ticker(self.TICKER_2Y).history(period=period)

            if tnx.empty or irx.empty:
                return pd.DataFrame()

            # Align dates
            common = tnx.index.intersection(irx.index)
            if common.empty:
                return pd.DataFrame()

            spread = pd.DataFrame({
                "ten_year": tnx.loc[common, "Close"],
                "short_rate": irx.loc[common, "Close"],
                "spread": tnx.loc[common, "Close"] - irx.loc[common, "Close"],
            })

            if not spread.empty:
                self._set_cache(cache_key, spread)

            return spread
        except Exception:
            return pd.DataFrame()

    def get_current_spread(self) -> Optional[float]:
        """Get the current 10Y - short rate spread."""
        df = self.fetch(days=10)
        if df.empty:
            return None
        try:
            return df["spread"].iloc[-1]
        except (IndexError, KeyError):
            return None

    def get_yield_curve_status(self) -> str:
        """Classify yield curve: normal, flat, or inverted."""
        spread = self.get_current_spread()
        if spread is None:
            return "unknown"
        if spread > 0.5:
            return "normal"
        if spread > -0.2:
            return "flat"
        return "inverted"


class MarketBreadthProvider(DataProvider):
    """Market breadth analysis via equal-weight vs cap-weight comparison.

    Uses RSP (equal-weight S&P 500) / SPY ratio as a breadth proxy.
    When RSP outperforms SPY, breadth is healthy (broad participation).
    When SPY leads, narrow leadership (unhealthy breadth).
    """

    default_ttl = 4 * 3600

    def fetch(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch breadth data (RSP/SPY ratio)."""
        cache_key = self._get_cache_key("breadth", start, end, days)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        period = f"{days}d" if days else "6mo"
        try:
            rsp = yf.Ticker("RSP").history(period=period)
            spy = yf.Ticker("SPY").history(period=period)

            if rsp.empty or spy.empty:
                return pd.DataFrame()

            common = rsp.index.intersection(spy.index)
            if common.empty:
                return pd.DataFrame()

            breadth = pd.DataFrame({
                "rsp": rsp.loc[common, "Close"],
                "spy": spy.loc[common, "Close"],
                "ratio": rsp.loc[common, "Close"] / spy.loc[common, "Close"],
            })

            if not breadth.empty:
                self._set_cache(cache_key, breadth)

            return breadth
        except Exception:
            return pd.DataFrame()

    def get_breadth_score(self, lookback: int = 20) -> Optional[float]:
        """Score breadth from 0-100 based on RSP/SPY trend.

        100 = RSP strongly outperforming (broad rally)
        50  = neutral
        0   = SPY strongly outperforming (narrow leadership)
        """
        df = self.fetch(days=max(lookback * 2, 60))
        if df.empty or len(df) < lookback:
            return None

        try:
            ratio = df["ratio"]
            current = ratio.iloc[-1]
            ma = ratio.rolling(window=lookback).mean().iloc[-1]

            # Score based on current ratio vs its moving average
            if ma == 0:
                return 50.0

            deviation = (current - ma) / ma
            # Map deviation to 0-100 (roughly -5% to +5% maps to 0-100)
            score = 50.0 + (deviation * 1000)
            return max(0.0, min(100.0, score))
        except (IndexError, KeyError):
            return None


class DollarStrengthProvider(DataProvider):
    """US Dollar strength via UUP ETF (Invesco DB USD Index).

    A rising dollar tends to pressure international revenue and
    emerging market assets. Used as a headwind/tailwind signal.
    """

    default_ttl = 4 * 3600

    def fetch(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch UUP price history."""
        cache_key = self._get_cache_key("dollar", start, end, days)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        period = f"{days}d" if days else "6mo"
        try:
            uup = yf.Ticker("UUP").history(period=period)
            if not uup.empty:
                self._set_cache(cache_key, uup)
            return uup
        except Exception:
            return pd.DataFrame()

    def get_dollar_trend(self, short_period: int = 20, long_period: int = 50) -> str:
        """Classify dollar trend: strengthening, weakening, or neutral."""
        df = self.fetch(days=max(long_period * 2, 120))
        if df.empty or len(df) < long_period:
            return "unknown"

        try:
            close = df["Close"]
            short_ma = close.rolling(window=short_period).mean().iloc[-1]
            long_ma = close.rolling(window=long_period).mean().iloc[-1]

            if long_ma == 0:
                return "neutral"

            diff_pct = (short_ma - long_ma) / long_ma
            if diff_pct > 0.01:
                return "strengthening"
            if diff_pct < -0.01:
                return "weakening"
            return "neutral"
        except (IndexError, KeyError):
            return "unknown"
