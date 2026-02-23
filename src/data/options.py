"""Options intelligence provider using Yahoo Finance data."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import yfinance as yf

from .base import DataProvider


@dataclass
class OptionsData:
    """Options analysis data for a stock."""

    ticker: str
    # Put/Call Ratios
    put_call_oi_ratio: Optional[float] = None  # Open Interest ratio
    put_call_volume_ratio: Optional[float] = None  # Volume ratio

    # Implied Volatility
    avg_call_iv: Optional[float] = None  # Average call IV
    avg_put_iv: Optional[float] = None  # Average put IV
    iv_skew: Optional[float] = None  # Put IV - Call IV (positive = fear)

    # Max Pain and Price Context
    max_pain: Optional[float] = None  # Strike with most OI
    current_price: Optional[float] = None
    max_pain_distance: Optional[float] = None  # % from current price

    # Open Interest Analysis
    total_call_oi: int = 0
    total_put_oi: int = 0
    total_call_volume: int = 0
    total_put_volume: int = 0

    # Unusual Activity Detection
    unusual_call_activity: bool = False  # High volume vs OI
    unusual_put_activity: bool = False

    # Expiration used
    expiration_date: Optional[str] = None
    days_to_expiry: Optional[int] = None

    # Scoring
    options_score: float = 50.0  # 0-100 bullish/bearish score
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class OptionsProvider(DataProvider):
    """Provides options intelligence data using Yahoo Finance.

    Yahoo Finance provides free options chain data including:
    - Strike prices and expirations
    - Open interest and volume
    - Implied volatility
    """

    default_ttl = 4 * 3600  # 4 hours

    # Cache TTL in seconds (1 hour for options data)
    CACHE_TTL = 3600

    def __init__(self, cache_enabled: bool = True):
        """Initialize options provider.

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

    def fetch(self, ticker: str) -> OptionsData:
        """Fetch options data for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            OptionsData with options metrics
        """
        return self.get_options_sentiment(ticker)

    def get_options_sentiment(self, ticker: str) -> OptionsData:
        """Get options sentiment analysis for a ticker.

        Analyzes the nearest expiry options chain to gauge
        market sentiment through put/call ratios and IV.

        Args:
            ticker: Stock ticker symbol

        Returns:
            OptionsData with sentiment score and metrics
        """
        cache_key = f"options:{ticker}"

        # Check cache
        if self._is_cache_valid(cache_key):
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached

        try:
            stock = yf.Ticker(ticker)

            # Get available expiration dates
            expirations = stock.options
            if not expirations:
                return OptionsData(ticker=ticker, options_score=50.0)

            # Use nearest expiry (most liquid, best sentiment indicator)
            nearest_expiry = expirations[0]

            # Get options chain
            options = stock.option_chain(nearest_expiry)
            calls = options.calls
            puts = options.puts

            if calls.empty or puts.empty:
                return OptionsData(ticker=ticker, options_score=50.0)

            # Get current stock price
            history = stock.history(period="1d")
            current_price = history["Close"].iloc[-1] if not history.empty else None

            # Calculate put/call ratios
            total_call_oi = calls["openInterest"].sum()
            total_put_oi = puts["openInterest"].sum()
            total_call_volume = calls["volume"].sum()
            total_put_volume = puts["volume"].sum()

            pc_oi_ratio = None
            if total_call_oi > 0:
                pc_oi_ratio = total_put_oi / total_call_oi

            pc_volume_ratio = None
            if total_call_volume > 0:
                pc_volume_ratio = total_put_volume / total_call_volume

            # Calculate average IV for ATM options (near current price)
            avg_call_iv = None
            avg_put_iv = None
            iv_skew = None

            if current_price is not None:
                # Filter to ATM options (+/- 10% of current price)
                price_low = current_price * 0.9
                price_high = current_price * 1.1

                atm_calls = calls[
                    (calls["strike"] >= price_low) & (calls["strike"] <= price_high)
                ]
                atm_puts = puts[
                    (puts["strike"] >= price_low) & (puts["strike"] <= price_high)
                ]

                if (
                    not atm_calls.empty
                    and "impliedVolatility" in atm_calls.columns
                ):
                    avg_call_iv = atm_calls["impliedVolatility"].mean()

                if (
                    not atm_puts.empty
                    and "impliedVolatility" in atm_puts.columns
                ):
                    avg_put_iv = atm_puts["impliedVolatility"].mean()

                if avg_call_iv is not None and avg_put_iv is not None:
                    iv_skew = avg_put_iv - avg_call_iv

            # Calculate max pain (strike with most total OI)
            max_pain = self._calculate_max_pain(calls, puts)
            max_pain_distance = None
            if max_pain is not None and current_price is not None:
                max_pain_distance = (max_pain - current_price) / current_price

            # Detect unusual activity (volume >> open interest)
            unusual_call = False
            unusual_put = False

            # Check for unusual call activity
            if total_call_oi > 0:
                call_vol_oi_ratio = total_call_volume / total_call_oi
                if call_vol_oi_ratio > 0.5:  # Volume > 50% of OI is unusual
                    unusual_call = True

            # Check for unusual put activity
            if total_put_oi > 0:
                put_vol_oi_ratio = total_put_volume / total_put_oi
                if put_vol_oi_ratio > 0.5:
                    unusual_put = True

            # Calculate days to expiry
            days_to_expiry = None
            try:
                exp_date = datetime.strptime(nearest_expiry, "%Y-%m-%d")
                days_to_expiry = (exp_date - datetime.now()).days
            except ValueError:
                pass

            # Create data object
            data = OptionsData(
                ticker=ticker,
                put_call_oi_ratio=pc_oi_ratio,
                put_call_volume_ratio=pc_volume_ratio,
                avg_call_iv=avg_call_iv,
                avg_put_iv=avg_put_iv,
                iv_skew=iv_skew,
                max_pain=max_pain,
                current_price=current_price,
                max_pain_distance=max_pain_distance,
                total_call_oi=int(total_call_oi) if not np.isnan(total_call_oi) else 0,
                total_put_oi=int(total_put_oi) if not np.isnan(total_put_oi) else 0,
                total_call_volume=int(total_call_volume) if not np.isnan(total_call_volume) else 0,
                total_put_volume=int(total_put_volume) if not np.isnan(total_put_volume) else 0,
                unusual_call_activity=unusual_call,
                unusual_put_activity=unusual_put,
                expiration_date=nearest_expiry,
                days_to_expiry=days_to_expiry,
            )

            # Calculate composite score
            data.options_score = self._calculate_score(data)

            self._set_cache_with_timestamp(cache_key, data)
            return data

        except Exception:
            # Return neutral data on error
            return OptionsData(ticker=ticker, options_score=50.0)

    def get_options_batch(
        self,
        tickers: List[str],
    ) -> Dict[str, OptionsData]:
        """Get options data for multiple tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker to OptionsData
        """
        results = {}
        for ticker in tickers:
            results[ticker] = self.get_options_sentiment(ticker)
        return results

    def _calculate_max_pain(self, calls, puts) -> Optional[float]:
        """Calculate max pain price (strike where options expire worthless).

        Max pain is the strike price where the total value of
        expiring options is minimized (most OI expires worthless).

        Args:
            calls: DataFrame of call options
            puts: DataFrame of put options

        Returns:
            Max pain strike price or None
        """
        try:
            # Get all unique strikes
            all_strikes = set(calls["strike"].tolist() + puts["strike"].tolist())

            if not all_strikes:
                return None

            # Calculate total pain at each strike
            max_pain_strike = None
            min_pain_value = float("inf")

            for strike in all_strikes:
                pain = 0

                # Pain from calls (ITM if stock > strike)
                for _, call in calls.iterrows():
                    if call["strike"] < strike:
                        # Call is ITM at this price
                        pain += call["openInterest"] * (strike - call["strike"])

                # Pain from puts (ITM if stock < strike)
                for _, put in puts.iterrows():
                    if put["strike"] > strike:
                        # Put is ITM at this price
                        pain += put["openInterest"] * (put["strike"] - strike)

                if pain < min_pain_value:
                    min_pain_value = pain
                    max_pain_strike = strike

            return max_pain_strike

        except Exception:
            return None

    def _calculate_score(self, data: OptionsData) -> float:
        """Calculate composite options sentiment score (0-100).

        Scoring logic:
        - Put/Call < 0.7 = bullish (calls dominate)
        - Put/Call 0.7-1.0 = neutral
        - Put/Call > 1.0 = bearish (puts dominate)
        - High call volume with low IV = very bullish
        - Unusual put buying = warning sign

        Args:
            data: OptionsData to score

        Returns:
            Composite score from 0 to 100 (higher = more bullish)
        """
        scores = []
        weights = []

        # Put/Call OI Ratio scoring (weight: 40%)
        if data.put_call_oi_ratio is not None:
            pc = data.put_call_oi_ratio
            if pc < 0.5:
                pc_score = 85  # Very bullish
            elif pc < 0.7:
                pc_score = 70  # Bullish
            elif pc < 0.85:
                pc_score = 60  # Slightly bullish
            elif pc < 1.0:
                pc_score = 50  # Neutral
            elif pc < 1.2:
                pc_score = 40  # Slightly bearish
            elif pc < 1.5:
                pc_score = 30  # Bearish
            else:
                pc_score = 20  # Very bearish
            scores.append(pc_score)
            weights.append(0.40)

        # Put/Call Volume Ratio (more real-time) (weight: 25%)
        if data.put_call_volume_ratio is not None:
            pcv = data.put_call_volume_ratio
            if pcv < 0.5:
                pcv_score = 85
            elif pcv < 0.7:
                pcv_score = 70
            elif pcv < 1.0:
                pcv_score = 55
            elif pcv < 1.3:
                pcv_score = 40
            else:
                pcv_score = 25
            scores.append(pcv_score)
            weights.append(0.25)

        # IV Skew scoring (weight: 20%)
        # Positive skew (put IV > call IV) = fear/bearish
        if data.iv_skew is not None:
            skew = data.iv_skew
            if skew < -0.1:
                skew_score = 75  # Call IV higher - bullish speculation
            elif skew < 0:
                skew_score = 60
            elif skew < 0.1:
                skew_score = 50  # Neutral
            elif skew < 0.2:
                skew_score = 40  # Some fear
            else:
                skew_score = 30  # High fear
            scores.append(skew_score)
            weights.append(0.20)

        # Unusual Activity scoring (weight: 15%)
        if data.unusual_call_activity and not data.unusual_put_activity:
            activity_score = 75  # Bullish unusual activity
        elif data.unusual_put_activity and not data.unusual_call_activity:
            activity_score = 30  # Bearish unusual activity
        elif data.unusual_call_activity and data.unusual_put_activity:
            activity_score = 50  # Mixed signals
        else:
            activity_score = 50  # Normal activity
        scores.append(activity_score)
        weights.append(0.15)

        # Calculate weighted average
        if scores and weights:
            total_weight = sum(weights)
            if total_weight > 0:
                weighted_sum = sum(s * w for s, w in zip(scores, weights))
                return weighted_sum / total_weight

        # Return neutral if no data available
        return 50.0

    def get_options_summary(self, data: OptionsData) -> Dict[str, str]:
        """Get a summary of options metrics for display.

        Args:
            data: OptionsData to summarize

        Returns:
            Dict with formatted metric strings
        """
        sentiment = "Neutral"
        if data.options_score >= 65:
            sentiment = "Bullish"
        elif data.options_score >= 55:
            sentiment = "Slightly Bullish"
        elif data.options_score <= 35:
            sentiment = "Bearish"
        elif data.options_score <= 45:
            sentiment = "Slightly Bearish"

        return {
            "Put/Call OI": f"{data.put_call_oi_ratio:.2f}" if data.put_call_oi_ratio else "N/A",
            "Put/Call Vol": f"{data.put_call_volume_ratio:.2f}" if data.put_call_volume_ratio else "N/A",
            "Avg Call IV": f"{data.avg_call_iv:.1%}" if data.avg_call_iv else "N/A",
            "Avg Put IV": f"{data.avg_put_iv:.1%}" if data.avg_put_iv else "N/A",
            "IV Skew": f"{data.iv_skew:.3f}" if data.iv_skew else "N/A",
            "Max Pain": f"${data.max_pain:.2f}" if data.max_pain else "N/A",
            "Distance to Max Pain": f"{data.max_pain_distance:.1%}" if data.max_pain_distance else "N/A",
            "Unusual Call Activity": "Yes" if data.unusual_call_activity else "No",
            "Unusual Put Activity": "Yes" if data.unusual_put_activity else "No",
            "Options Score": f"{data.options_score:.0f}",
            "Sentiment": sentiment,
            "Expiration": data.expiration_date or "N/A",
        }
