"""Technical indicators for trading strategy."""

from typing import Dict, List, Optional, Union

import pandas as pd

from ..data.price import PriceProvider


class TechnicalIndicators:
    """Calculates technical indicators for stocks."""

    def __init__(self, price_provider: Optional[PriceProvider] = None):
        """Initialize technical indicators calculator.

        Args:
            price_provider: Price data provider instance
        """
        self.price_provider = price_provider or PriceProvider()

    def moving_average(
        self,
        prices: pd.Series,
        period: int,
    ) -> pd.Series:
        """Calculate simple moving average.

        Args:
            prices: Series of prices
            period: MA period

        Returns:
            Series with moving average values
        """
        return prices.rolling(window=period).mean()

    def exponential_moving_average(
        self,
        prices: pd.Series,
        period: int,
    ) -> pd.Series:
        """Calculate exponential moving average.

        Args:
            prices: Series of prices
            period: EMA period

        Returns:
            Series with EMA values
        """
        return prices.ewm(span=period, adjust=False).mean()

    def get_ma(
        self,
        ticker: str,
        period: int,
        ma_type: str = "sma",
    ) -> Optional[float]:
        """Get current moving average value for a ticker.

        Args:
            ticker: Stock ticker symbol
            period: MA period
            ma_type: 'sma' or 'ema'

        Returns:
            Current MA value or None
        """
        # Fetch enough data for calculation
        calendar_days = int(period * 1.5) + 20
        df = self.price_provider.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < period:
            return None

        try:
            if ma_type == "ema":
                ma = self.exponential_moving_average(df["Close"], period)
            else:
                ma = self.moving_average(df["Close"], period)

            return ma.iloc[-1]
        except (IndexError, KeyError):
            return None

    def price_vs_ma(
        self,
        ticker: str,
        period: int,
        ma_type: str = "sma",
    ) -> Optional[Dict[str, Union[float, bool]]]:
        """Get price position relative to moving average.

        Args:
            ticker: Stock ticker symbol
            period: MA period
            ma_type: 'sma' or 'ema'

        Returns:
            Dict with price, ma, pct_from_ma, and above_ma
        """
        calendar_days = int(period * 1.5) + 20
        df = self.price_provider.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < period:
            return None

        try:
            price = df["Close"].iloc[-1]
            if ma_type == "ema":
                ma = self.exponential_moving_average(df["Close"], period).iloc[-1]
            else:
                ma = self.moving_average(df["Close"], period).iloc[-1]

            pct_from_ma = (price - ma) / ma if ma > 0 else 0

            return {
                "price": price,
                "ma": ma,
                "pct_from_ma": pct_from_ma,
                "above_ma": price > ma,
            }
        except (IndexError, KeyError):
            return None

    def is_above_ma(
        self,
        ticker: str,
        period: int,
        ma_type: str = "sma",
    ) -> bool:
        """Check if price is above moving average.

        Args:
            ticker: Stock ticker symbol
            period: MA period
            ma_type: 'sma' or 'ema'

        Returns:
            True if price is above MA
        """
        result = self.price_vs_ma(ticker, period, ma_type)
        if result is None:
            return False
        return result["above_ma"]

    def check_trend_alignment(
        self,
        ticker: str,
        short_ma: int = 50,
        long_ma: int = 200,
    ) -> Dict[str, bool]:
        """Check if price is above both short and long MAs.

        Args:
            ticker: Stock ticker symbol
            short_ma: Short-term MA period
            long_ma: Long-term MA period

        Returns:
            Dict with above_short_ma, above_long_ma, and aligned
        """
        above_short = self.is_above_ma(ticker, short_ma)
        above_long = self.is_above_ma(ticker, long_ma)

        return {
            "above_short_ma": above_short,
            "above_long_ma": above_long,
            "aligned": above_short and above_long,
        }

    def get_volume_surge(
        self,
        ticker: str,
        lookback_days: int = 20,
    ) -> Optional[float]:
        """Calculate volume surge ratio (current vs average).

        Args:
            ticker: Stock ticker symbol
            lookback_days: Days for average volume calculation

        Returns:
            Volume surge ratio or None
        """
        calendar_days = int(lookback_days * 1.5) + 10
        df = self.price_provider.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < lookback_days:
            return None

        try:
            current_volume = df["Volume"].iloc[-1]
            avg_volume = df["Volume"].iloc[-(lookback_days + 1) : -1].mean()

            if avg_volume > 0:
                return current_volume / avg_volume
            return None
        except (IndexError, KeyError):
            return None

    def get_volume_surge_batch(
        self,
        tickers: List[str],
        lookback_days: int = 20,
    ) -> Dict[str, float]:
        """Calculate volume surge for multiple tickers.

        Args:
            tickers: List of ticker symbols
            lookback_days: Days for average volume calculation

        Returns:
            Dict mapping ticker to volume surge ratio
        """
        results = {}
        calendar_days = int(lookback_days * 1.5) + 10
        data = self.price_provider.get_ohlcv_batch(tickers, days=calendar_days)

        for ticker, df in data.items():
            if df.empty or len(df) < lookback_days:
                continue

            try:
                vol_col = df["Volume"]
                if hasattr(vol_col.iloc[-1], 'item'):
                    current_volume = vol_col.iloc[-1].item()
                else:
                    current_volume = float(vol_col.iloc[-1])
                avg_volume = vol_col.iloc[-(lookback_days + 1) : -1].mean()
                if hasattr(avg_volume, 'item'):
                    avg_volume = avg_volume.item()
                else:
                    avg_volume = float(avg_volume)

                if avg_volume > 0:
                    results[ticker] = current_volume / avg_volume
            except (IndexError, KeyError, TypeError, AttributeError):
                continue

        return results

    def calculate_rsi(
        self,
        prices: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Calculate Relative Strength Index.

        Args:
            prices: Series of closing prices
            period: RSI period

        Returns:
            Series with RSI values
        """
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def get_rsi(
        self,
        ticker: str,
        period: int = 14,
    ) -> Optional[float]:
        """Get current RSI for a ticker.

        Args:
            ticker: Stock ticker symbol
            period: RSI period

        Returns:
            Current RSI value or None
        """
        calendar_days = period * 3
        df = self.price_provider.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < period + 1:
            return None

        try:
            rsi = self.calculate_rsi(df["Close"], period)
            return rsi.iloc[-1]
        except (IndexError, KeyError):
            return None

    def calculate_atr(
        self,
        df: pd.DataFrame,
        period: int = 20,
    ) -> pd.Series:
        """Calculate Average True Range.

        Args:
            df: DataFrame with High, Low, Close columns
            period: ATR period

        Returns:
            Series with ATR values
        """
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        # True Range = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()

        return atr

    def get_atr_percent(
        self,
        ticker: str,
        period: int = 20,
    ) -> Optional[float]:
        """Get ATR as percentage of current price.

        Args:
            ticker: Stock ticker symbol
            period: ATR period

        Returns:
            ATR as percentage of price (e.g., 0.05 = 5%)
        """
        calendar_days = int(period * 1.5) + 20
        df = self.price_provider.get_ohlcv(ticker, days=calendar_days)

        if df.empty or len(df) < period + 1:
            return None

        try:
            atr = self.calculate_atr(df, period)
            current_price = df["Close"].iloc[-1]
            atr_value = atr.iloc[-1]

            if current_price > 0:
                return atr_value / current_price
            return None
        except (IndexError, KeyError):
            return None

    def get_atr_percent_from_df(
        self,
        df: pd.DataFrame,
        period: int = 20,
    ) -> Optional[float]:
        """Get ATR percentage from pre-loaded DataFrame.

        Args:
            df: DataFrame with OHLCV data
            period: ATR period

        Returns:
            ATR as percentage of price
        """
        if df.empty or len(df) < period + 1:
            return None

        try:
            atr = self.calculate_atr(df, period)
            current_price = df["Close"].iloc[-1]
            atr_value = atr.iloc[-1]

            if current_price > 0:
                return atr_value / current_price
            return None
        except (IndexError, KeyError):
            return None
