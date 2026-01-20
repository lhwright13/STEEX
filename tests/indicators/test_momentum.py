"""Tests for momentum indicators."""

import pandas as pd
import pytest

from src.indicators.momentum import MomentumCalculator


class TestMomentumCalculator:
    """Tests for MomentumCalculator class."""

    def test_calculate_return(self, sample_ohlcv_data):
        """Test return calculation."""
        calc = MomentumCalculator()
        prices = sample_ohlcv_data["Close"]

        # Test 21-day return
        ret = calc.calculate_return(prices, 21)
        assert ret is not None

        # Manual calculation for verification
        expected = (prices.iloc[-1] - prices.iloc[-22]) / prices.iloc[-22]
        assert abs(ret - expected) < 0.0001

    def test_calculate_return_insufficient_data(self, sample_ohlcv_data):
        """Test return calculation with insufficient data."""
        calc = MomentumCalculator()
        prices = sample_ohlcv_data["Close"].head(10)

        ret = calc.calculate_return(prices, 21)
        assert ret is None

    def test_calculate_percentile_rank(self):
        """Test percentile ranking."""
        calc = MomentumCalculator()

        values = {
            "A": 0.10,
            "B": 0.20,
            "C": 0.15,
            "D": 0.05,
            "E": 0.25,
        }

        ranks = calc.calculate_percentile_rank(values)

        # D should be lowest, E should be highest
        assert ranks["D"] == 0.2  # 1/5
        assert ranks["E"] == 1.0  # 5/5
        assert ranks["A"] == 0.4  # 2/5

    def test_calculate_percentile_rank_empty(self):
        """Test percentile ranking with empty dict."""
        calc = MomentumCalculator()
        ranks = calc.calculate_percentile_rank({})
        assert ranks == {}

    def test_get_momentum_with_mock(self, mock_price_provider, sample_ohlcv_data):
        """Test momentum calculation with mock provider."""
        calc = MomentumCalculator(price_provider=mock_price_provider)

        momentum = calc.get_momentum("AAPL", lookback_days=21)
        assert momentum is not None

    def test_filter_by_momentum(self, mock_price_provider):
        """Test momentum filtering."""
        calc = MomentumCalculator(price_provider=mock_price_provider)

        tickers = ["AAPL", "MSFT", "GOOGL"]
        passed, data = calc.filter_by_momentum(
            tickers,
            min_return=0.0,  # Low threshold for test
            lookback_days=21,
        )

        # Should have data for all tickers
        assert len(data) == 3

    def test_momentum_score(self, mock_price_provider):
        """Test momentum score calculation."""
        calc = MomentumCalculator(price_provider=mock_price_provider)

        score = calc.calculate_momentum_score(
            "AAPL",
            momentum_6m=0.20,  # 20% return
            momentum_1m=0.05,  # 5% return
        )

        # Score should be positive for positive momentum
        assert score > 0
        # 6m: 0.20 * 140 = 28 (capped)
        # 1m: 0.05 * 300 = 15
        assert score <= 100
