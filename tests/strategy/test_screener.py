"""Tests for stock screener."""

from datetime import datetime

import pytest

from src.strategy.screener import ScreeningResult, StockScreener


class TestScreeningResult:
    """Tests for ScreeningResult dataclass."""

    def test_create_result(self):
        """Test creating a screening result."""
        result = ScreeningResult(ticker="AAPL")

        assert result.ticker == "AAPL"
        assert result.passed_stages == []
        assert result.failed_stage is None
        assert result.momentum_6m is None

    def test_result_with_data(self):
        """Test result with populated data."""
        result = ScreeningResult(
            ticker="AAPL",
            passed_stages=["stage_1", "stage_2"],
            momentum_6m=0.15,
            momentum_1m=0.03,
            above_ma_50=True,
            above_ma_200=True,
            insider_score=75,
            insider_buyers=3,
        )

        assert len(result.passed_stages) == 2
        assert result.momentum_6m == 0.15
        assert result.insider_score == 75


class TestStockScreener:
    """Tests for StockScreener class."""

    def test_init(self, test_settings):
        """Test screener initialization."""
        screener = StockScreener(settings=test_settings)
        assert screener.settings == test_settings

    def test_stage_4_passthrough(self, test_settings):
        """Test that stage 4 (sentiment) passes all tickers through."""
        screener = StockScreener(settings=test_settings)

        tickers = ["AAPL", "MSFT", "GOOGL"]
        result = screener.stage_4_sentiment_filter(tickers)

        # Should pass all through (it's a stub)
        assert result == tickers

    # Integration tests would require mocking multiple components
    # or using real API calls which are slow
