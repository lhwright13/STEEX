"""Tests for technical indicators."""

import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import TechnicalIndicators


class TestTechnicalIndicators:
    """Tests for TechnicalIndicators class."""

    def test_moving_average(self, sample_ohlcv_data):
        """Test simple moving average calculation."""
        tech = TechnicalIndicators()
        prices = sample_ohlcv_data["Close"]

        ma = tech.moving_average(prices, 20)

        # First 19 values should be NaN
        assert ma.iloc[:19].isna().all()
        # After that should have values
        assert not ma.iloc[19:].isna().any()

        # MA should be close to mean of window
        window = prices.iloc[230:250]
        expected = window.mean()
        actual = ma.iloc[-1]
        assert abs(actual - expected) < 0.01

    def test_exponential_moving_average(self, sample_ohlcv_data):
        """Test EMA calculation."""
        tech = TechnicalIndicators()
        prices = sample_ohlcv_data["Close"]

        ema = tech.exponential_moving_average(prices, 20)

        # EMA should not have NaN after warmup
        assert not ema.iloc[20:].isna().any()

    def test_is_above_ma(self, mock_price_provider, sample_ohlcv_data):
        """Test price vs MA comparison."""
        tech = TechnicalIndicators(price_provider=mock_price_provider)

        # The mock returns the same data, so we know the price
        current_price = sample_ohlcv_data["Close"].iloc[-1]
        ma_50 = sample_ohlcv_data["Close"].tail(50).mean()

        result = tech.is_above_ma("AAPL", 50)
        expected = current_price > ma_50

        assert result == expected

    def test_check_trend_alignment(self, mock_price_provider):
        """Test trend alignment check."""
        tech = TechnicalIndicators(price_provider=mock_price_provider)

        alignment = tech.check_trend_alignment("AAPL", short_ma=50, long_ma=200)

        assert "above_short_ma" in alignment
        assert "above_long_ma" in alignment
        assert "aligned" in alignment
        assert alignment["aligned"] == (
            alignment["above_short_ma"] and alignment["above_long_ma"]
        )

    def test_volume_surge(self, mock_price_provider, sample_ohlcv_data):
        """Test volume surge calculation."""
        tech = TechnicalIndicators(price_provider=mock_price_provider)

        surge = tech.get_volume_surge("AAPL", lookback_days=20)
        assert surge is not None
        assert surge > 0

    def test_rsi_calculation(self, sample_ohlcv_data):
        """Test RSI calculation."""
        tech = TechnicalIndicators()
        prices = sample_ohlcv_data["Close"]

        rsi = tech.calculate_rsi(prices, period=14)

        # RSI should be between 0 and 100
        valid_rsi = rsi.dropna()
        assert (valid_rsi >= 0).all()
        assert (valid_rsi <= 100).all()

    def test_get_rsi(self, mock_price_provider):
        """Test getting RSI for ticker."""
        tech = TechnicalIndicators(price_provider=mock_price_provider)

        rsi = tech.get_rsi("AAPL", period=14)
        assert rsi is not None
        assert 0 <= rsi <= 100
