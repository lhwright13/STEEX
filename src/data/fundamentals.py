"""Fundamental analysis provider using Yahoo Finance data."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import yfinance as yf

from .base import DataProvider


@dataclass
class FundamentalData:
    """Fundamental metrics for a stock."""

    ticker: str
    # Valuation metrics
    pe_ratio: Optional[float] = None  # Trailing P/E
    forward_pe: Optional[float] = None  # Forward P/E
    peg_ratio: Optional[float] = None  # P/E to Growth ratio
    price_to_book: Optional[float] = None  # Price/Book

    # Profitability metrics
    profit_margin: Optional[float] = None  # Net profit margin
    operating_margin: Optional[float] = None  # Operating margin
    return_on_equity: Optional[float] = None  # ROE
    return_on_assets: Optional[float] = None  # ROA

    # Growth metrics
    revenue_growth: Optional[float] = None  # YoY revenue growth
    earnings_growth: Optional[float] = None  # YoY earnings growth

    # Financial health
    debt_to_equity: Optional[float] = None  # Debt/Equity ratio
    current_ratio: Optional[float] = None  # Current assets/liabilities

    # Additional info
    market_cap: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None

    # Scoring
    fundamental_score: float = 50.0  # 0-100 composite score
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class FundamentalsProvider(DataProvider):
    """Provides fundamental analysis data using Yahoo Finance.

    Yahoo Finance is free and unlimited, providing comprehensive
    fundamental data for stocks.
    """

    # Cache TTL in seconds (24 hours for fundamental data)
    CACHE_TTL = 86400

    def __init__(self, cache_enabled: bool = True):
        """Initialize fundamentals provider.

        Args:
            cache_enabled: Whether to cache results
        """
        super().__init__(cache_enabled)
        self._cache_timestamps: Dict[str, datetime] = {}

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cache entry is still valid."""
        if key not in self._cache_timestamps:
            return False
        age = (datetime.now() - self._cache_timestamps[key]).total_seconds()
        return age < self.CACHE_TTL

    def _set_cache_with_timestamp(self, key: str, value: Any) -> None:
        """Set cache with timestamp tracking."""
        self._set_cache(key, value)
        self._cache_timestamps[key] = datetime.now()

    def fetch(self, ticker: str) -> FundamentalData:
        """Fetch fundamental data for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            FundamentalData with fundamental metrics
        """
        return self.get_fundamentals(ticker)

    def get_fundamentals(self, ticker: str) -> FundamentalData:
        """Get fundamental data for a single ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            FundamentalData with fundamental metrics and score
        """
        cache_key = f"fundamentals:{ticker}"

        # Check cache
        if self._is_cache_valid(cache_key):
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached

        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Extract fundamental metrics
            data = FundamentalData(
                ticker=ticker,
                # Valuation
                pe_ratio=self._safe_get(info, "trailingPE"),
                forward_pe=self._safe_get(info, "forwardPE"),
                peg_ratio=self._safe_get(info, "pegRatio"),
                price_to_book=self._safe_get(info, "priceToBook"),
                # Profitability
                profit_margin=self._safe_get(info, "profitMargins"),
                operating_margin=self._safe_get(info, "operatingMargins"),
                return_on_equity=self._safe_get(info, "returnOnEquity"),
                return_on_assets=self._safe_get(info, "returnOnAssets"),
                # Growth
                revenue_growth=self._safe_get(info, "revenueGrowth"),
                earnings_growth=self._safe_get(info, "earningsGrowth"),
                # Financial health
                debt_to_equity=self._safe_get(info, "debtToEquity"),
                current_ratio=self._safe_get(info, "currentRatio"),
                # Additional
                market_cap=self._safe_get(info, "marketCap"),
                sector=info.get("sector"),
                industry=info.get("industry"),
            )

            # Calculate composite score
            data.fundamental_score = self._calculate_score(data)

            self._set_cache_with_timestamp(cache_key, data)
            return data

        except Exception:
            # Return neutral data on error
            return FundamentalData(ticker=ticker, fundamental_score=50.0)

    def get_fundamentals_batch(
        self,
        tickers: List[str],
    ) -> Dict[str, FundamentalData]:
        """Get fundamental data for multiple tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker to FundamentalData
        """
        results = {}
        for ticker in tickers:
            results[ticker] = self.get_fundamentals(ticker)
        return results

    @staticmethod
    def _safe_get(info: Dict, key: str) -> Optional[float]:
        """Safely get a numeric value from info dict.

        Args:
            info: Yahoo Finance info dict
            key: Key to extract

        Returns:
            Float value or None
        """
        value = info.get(key)
        if value is None or value == "N/A":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _calculate_score(self, data: FundamentalData) -> float:
        """Calculate composite fundamental score (0-100).

        Scoring logic:
        - Lower P/E (with positive earnings) = better value
        - PEG < 1.5 = growth at reasonable price
        - ROE > 15% = efficient capital use
        - Debt/Equity < 1 = conservative financing
        - Revenue growth > 10% = growing business
        - Profit margin > 10% = quality business

        Args:
            data: FundamentalData to score

        Returns:
            Composite score from 0 to 100
        """
        scores = []
        weights = []

        # P/E Ratio scoring (weight: 20%)
        if data.pe_ratio is not None and data.pe_ratio > 0:
            if data.pe_ratio < 10:
                pe_score = 90
            elif data.pe_ratio < 15:
                pe_score = 80
            elif data.pe_ratio < 20:
                pe_score = 70
            elif data.pe_ratio < 25:
                pe_score = 60
            elif data.pe_ratio < 35:
                pe_score = 50
            elif data.pe_ratio < 50:
                pe_score = 40
            else:
                pe_score = 20
            scores.append(pe_score)
            weights.append(0.20)
        elif data.pe_ratio is not None and data.pe_ratio < 0:
            # Negative P/E (losses) - penalize unless high growth
            if data.revenue_growth and data.revenue_growth > 0.30:
                scores.append(40)  # High growth offsets losses
            else:
                scores.append(20)  # Losing money without high growth
            weights.append(0.20)

        # PEG Ratio scoring (weight: 15%)
        if data.peg_ratio is not None and data.peg_ratio > 0:
            if data.peg_ratio < 1.0:
                peg_score = 90  # Great growth value
            elif data.peg_ratio < 1.5:
                peg_score = 75  # Good GARP
            elif data.peg_ratio < 2.0:
                peg_score = 60
            elif data.peg_ratio < 3.0:
                peg_score = 45
            else:
                peg_score = 30
            scores.append(peg_score)
            weights.append(0.15)

        # ROE scoring (weight: 15%)
        if data.return_on_equity is not None:
            roe = data.return_on_equity * 100  # Convert to percentage
            if roe > 25:
                roe_score = 90
            elif roe > 20:
                roe_score = 80
            elif roe > 15:
                roe_score = 70
            elif roe > 10:
                roe_score = 60
            elif roe > 5:
                roe_score = 50
            elif roe > 0:
                roe_score = 40
            else:
                roe_score = 20
            scores.append(roe_score)
            weights.append(0.15)

        # Debt/Equity scoring (weight: 15%)
        if data.debt_to_equity is not None:
            de = data.debt_to_equity
            if de < 0.3:
                de_score = 90  # Very conservative
            elif de < 0.5:
                de_score = 80
            elif de < 1.0:
                de_score = 70
            elif de < 1.5:
                de_score = 55
            elif de < 2.0:
                de_score = 40
            else:
                de_score = 25
            scores.append(de_score)
            weights.append(0.15)

        # Revenue Growth scoring (weight: 15%)
        if data.revenue_growth is not None:
            growth = data.revenue_growth * 100  # Convert to percentage
            if growth > 30:
                growth_score = 90
            elif growth > 20:
                growth_score = 80
            elif growth > 10:
                growth_score = 70
            elif growth > 5:
                growth_score = 60
            elif growth > 0:
                growth_score = 50
            elif growth > -5:
                growth_score = 40
            else:
                growth_score = 25
            scores.append(growth_score)
            weights.append(0.15)

        # Profit Margin scoring (weight: 10%)
        if data.profit_margin is not None:
            margin = data.profit_margin * 100  # Convert to percentage
            if margin > 20:
                margin_score = 90
            elif margin > 15:
                margin_score = 80
            elif margin > 10:
                margin_score = 70
            elif margin > 5:
                margin_score = 60
            elif margin > 0:
                margin_score = 45
            else:
                margin_score = 25
            scores.append(margin_score)
            weights.append(0.10)

        # Current Ratio scoring (weight: 10%)
        if data.current_ratio is not None:
            cr = data.current_ratio
            if cr > 2.0:
                cr_score = 85  # Very liquid
            elif cr > 1.5:
                cr_score = 75
            elif cr > 1.0:
                cr_score = 60
            elif cr > 0.8:
                cr_score = 40
            else:
                cr_score = 25  # Liquidity concern
            scores.append(cr_score)
            weights.append(0.10)

        # Calculate weighted average
        if scores and weights:
            total_weight = sum(weights)
            if total_weight > 0:
                weighted_sum = sum(s * w for s, w in zip(scores, weights))
                return weighted_sum / total_weight

        # Return neutral if no data available
        return 50.0

    def passes_fundamental_filter(
        self,
        data: FundamentalData,
        max_pe: float = 50.0,
        min_roe: float = 0.05,
        max_debt_equity: float = 2.0,
    ) -> tuple[bool, str]:
        """Check if stock passes fundamental filters.

        Args:
            data: FundamentalData to check
            max_pe: Maximum P/E ratio allowed
            min_roe: Minimum ROE required
            max_debt_equity: Maximum debt/equity ratio

        Returns:
            Tuple of (passed, reason)
        """
        # P/E filter - reject extremely overvalued
        if data.pe_ratio is not None and data.pe_ratio > max_pe:
            return False, f"P/E too high ({data.pe_ratio:.1f} > {max_pe})"

        # Negative earnings filter - allow if high growth
        if data.pe_ratio is not None and data.pe_ratio < 0:
            if data.revenue_growth is None or data.revenue_growth < 0.20:
                return False, "Negative earnings without high growth"

        # ROE filter - reject very low quality
        if data.return_on_equity is not None and data.return_on_equity < min_roe:
            if data.return_on_equity < 0:
                return False, f"Negative ROE ({data.return_on_equity:.1%})"

        # Debt filter - reject highly leveraged
        if data.debt_to_equity is not None and data.debt_to_equity > max_debt_equity:
            return False, f"Debt/Equity too high ({data.debt_to_equity:.1f} > {max_debt_equity})"

        return True, "Passed"

    def get_fundamental_summary(self, data: FundamentalData) -> Dict[str, str]:
        """Get a summary of fundamental metrics for display.

        Args:
            data: FundamentalData to summarize

        Returns:
            Dict with formatted metric strings
        """
        return {
            "P/E Ratio": f"{data.pe_ratio:.1f}" if data.pe_ratio else "N/A",
            "Forward P/E": f"{data.forward_pe:.1f}" if data.forward_pe else "N/A",
            "PEG Ratio": f"{data.peg_ratio:.2f}" if data.peg_ratio else "N/A",
            "Price/Book": f"{data.price_to_book:.2f}" if data.price_to_book else "N/A",
            "ROE": f"{data.return_on_equity:.1%}" if data.return_on_equity else "N/A",
            "Profit Margin": f"{data.profit_margin:.1%}" if data.profit_margin else "N/A",
            "Revenue Growth": f"{data.revenue_growth:.1%}" if data.revenue_growth else "N/A",
            "Debt/Equity": f"{data.debt_to_equity:.2f}" if data.debt_to_equity else "N/A",
            "Current Ratio": f"{data.current_ratio:.2f}" if data.current_ratio else "N/A",
            "Fundamental Score": f"{data.fundamental_score:.0f}",
        }
