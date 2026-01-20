"""Momentum indicators and calculations."""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data.price import PriceProvider


class MomentumCalculator:
    """Calculates momentum indicators for stocks."""

    def __init__(self, price_provider: Optional[PriceProvider] = None):
        """Initialize momentum calculator.

        Args:
            price_provider: Price data provider instance
        """
        self.price_provider = price_provider or PriceProvider()

    def calculate_return(
        self,
        prices: pd.Series,
        days: int,
    ) -> Optional[float]:
        """Calculate return over N days from a price series.

        Args:
            prices: Series of closing prices
            days: Number of trading days

        Returns:
            Return as decimal or None if insufficient data
        """
        if len(prices) < days + 1:
            return None

        try:
            end_price = prices.iloc[-1]
            start_price = prices.iloc[-(days + 1)]

            if start_price > 0:
                return (end_price - start_price) / start_price
            return None
        except (IndexError, KeyError):
            return None

    def get_momentum(
        self,
        ticker: str,
        lookback_days: int = 126,
    ) -> Optional[float]:
        """Get momentum (return) for a single ticker.

        Args:
            ticker: Stock ticker symbol
            lookback_days: Number of trading days for calculation

        Returns:
            Return as decimal or None
        """
        # Fetch extra data to ensure we have enough
        calendar_days = int(lookback_days * 1.5) + 20
        df = self.price_provider.get_ohlcv(ticker, days=calendar_days)

        if df.empty:
            return None

        return self.calculate_return(df["Close"], lookback_days)

    def get_momentum_batch(
        self,
        tickers: List[str],
        lookback_days: int = 126,
    ) -> Dict[str, float]:
        """Calculate momentum for multiple tickers.

        Args:
            tickers: List of ticker symbols
            lookback_days: Number of trading days

        Returns:
            Dict mapping ticker to momentum value
        """
        results = {}
        calendar_days = int(lookback_days * 1.5) + 20
        data = self.price_provider.get_ohlcv_batch(tickers, days=calendar_days)

        for ticker, df in data.items():
            if df.empty:
                continue
            momentum = self.calculate_return(df["Close"], lookback_days)
            if momentum is not None:
                results[ticker] = momentum

        return results

    def calculate_percentile_rank(
        self,
        values: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate percentile ranks for a dict of values.

        Args:
            values: Dict mapping ticker to value

        Returns:
            Dict mapping ticker to percentile (0-1)
        """
        if not values:
            return {}

        sorted_items = sorted(values.items(), key=lambda x: x[1])
        n = len(sorted_items)

        return {
            ticker: (rank + 1) / n for rank, (ticker, _) in enumerate(sorted_items)
        }

    def get_momentum_percentiles(
        self,
        tickers: List[str],
        lookback_days: int = 126,
    ) -> Dict[str, Dict[str, float]]:
        """Get momentum values and percentile ranks for tickers.

        Args:
            tickers: List of ticker symbols
            lookback_days: Number of trading days

        Returns:
            Dict mapping ticker to {momentum, percentile}
        """
        momentum_values = self.get_momentum_batch(tickers, lookback_days)
        percentiles = self.calculate_percentile_rank(momentum_values)

        results = {}
        for ticker in momentum_values:
            results[ticker] = {
                "momentum": momentum_values[ticker],
                "percentile": percentiles.get(ticker, 0),
            }

        return results

    def filter_by_momentum(
        self,
        tickers: List[str],
        min_return: float = 0.10,
        lookback_days: int = 126,
        exclude_top_percentile: float = 0.95,
    ) -> Tuple[List[str], Dict[str, Dict[str, float]]]:
        """Filter tickers by momentum criteria.

        Args:
            tickers: List of ticker symbols
            min_return: Minimum return required
            lookback_days: Number of trading days
            exclude_top_percentile: Exclude stocks above this percentile

        Returns:
            Tuple of (filtered tickers, momentum data dict)
        """
        data = self.get_momentum_percentiles(tickers, lookback_days)

        passed = []
        for ticker, info in data.items():
            momentum = info["momentum"]
            percentile = info["percentile"]

            # Check minimum return and not overextended
            if momentum >= min_return and percentile <= exclude_top_percentile:
                passed.append(ticker)

        return passed, data

    def is_positive_short_term(
        self,
        ticker: str,
        short_term_days: int = 21,
    ) -> bool:
        """Check if short-term momentum is positive.

        Args:
            ticker: Stock ticker symbol
            short_term_days: Number of trading days for short-term

        Returns:
            True if short-term return is positive
        """
        momentum = self.get_momentum(ticker, short_term_days)
        return momentum is not None and momentum > 0

    def filter_positive_short_term(
        self,
        tickers: List[str],
        short_term_days: int = 21,
    ) -> List[str]:
        """Filter tickers requiring positive short-term momentum.

        Args:
            tickers: List of ticker symbols
            short_term_days: Number of trading days

        Returns:
            Filtered list of tickers
        """
        momentum_values = self.get_momentum_batch(tickers, short_term_days)
        return [t for t, m in momentum_values.items() if m > 0]

    def calculate_momentum_score(
        self,
        ticker: str,
        momentum_6m: Optional[float] = None,
        momentum_1m: Optional[float] = None,
    ) -> float:
        """Calculate a combined momentum score.

        Args:
            ticker: Stock ticker symbol
            momentum_6m: Pre-calculated 6-month momentum
            momentum_1m: Pre-calculated 1-month momentum

        Returns:
            Momentum score (0-100)
        """
        if momentum_6m is None:
            momentum_6m = self.get_momentum(ticker, 126) or 0
        if momentum_1m is None:
            momentum_1m = self.get_momentum(ticker, 21) or 0

        # Score based on momentum values
        # 6-month momentum: 0-50% return maps to 0-70 score
        score_6m = min(70, max(0, momentum_6m * 140))

        # 1-month momentum: 0-10% return maps to 0-30 score
        score_1m = min(30, max(0, momentum_1m * 300))

        return score_6m + score_1m
