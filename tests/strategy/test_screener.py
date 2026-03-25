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
        result_tickers, sentiment_data = screener.stage_4_sentiment_filter(tickers)

        # Should pass all through
        assert result_tickers == tickers
        assert isinstance(sentiment_data, dict)

    # Integration tests would require mocking multiple components
    # or using real API calls which are slow


class TestEarningsBlackoutFilter:
    """Tests for earnings blackout filtering in stage 1."""

    def test_stock_in_earnings_blackout_excluded(self, test_settings):
        """Stock with earnings within blackout window should be excluded."""
        from unittest.mock import MagicMock
        from datetime import timedelta

        screener = StockScreener(settings=test_settings)

        # Mock universe filter_by_price_volume to pass all tickers through
        screener.universe.filter_by_price_volume = MagicMock(
            return_value=["AAPL", "MSFT", "GOOGL"]
        )

        # Mock earnings calendar: MSFT has earnings in 3 days (within 5-day blackout)
        reference_date = datetime(2026, 3, 24)

        def mock_has_earnings_soon(ticker, days=5, reference_date=None):
            return ticker == "MSFT"

        screener.earnings.has_earnings_soon = mock_has_earnings_soon
        screener.earnings.filter_earnings_blackout = MagicMock(
            return_value=["AAPL", "GOOGL"]
        )

        result = screener.stage_1_universe_filter(
            tickers=["AAPL", "MSFT", "GOOGL"],
            reference_date=reference_date,
        )

        assert "MSFT" not in result
        assert "AAPL" in result
        assert "GOOGL" in result

    def test_no_earnings_all_pass(self, test_settings):
        """When no stocks have upcoming earnings, all should pass."""
        from unittest.mock import MagicMock

        screener = StockScreener(settings=test_settings)

        screener.universe.filter_by_price_volume = MagicMock(
            return_value=["AAPL", "MSFT"]
        )
        screener.earnings.filter_earnings_blackout = MagicMock(
            return_value=["AAPL", "MSFT"]
        )

        result = screener.stage_1_universe_filter(
            tickers=["AAPL", "MSFT"],
        )
        assert result == ["AAPL", "MSFT"]


class TestOverextensionFilter:
    """Tests for overextension percentile filter in stage 2."""

    def test_overextended_stock_filtered(self, test_settings, mock_price_provider):
        """Stock at 95th+ percentile should be filtered when overextension is enabled."""
        from unittest.mock import MagicMock

        test_settings.overextension_filter_enabled = True
        test_settings.overextension_percentile = 0.95

        screener = StockScreener(
            settings=test_settings,
            price_provider=mock_price_provider,
        )

        # Mock momentum: AAPL at 96th percentile (overextended), MSFT at 70th
        screener.momentum.get_momentum_percentiles = MagicMock(return_value={
            "AAPL": {"momentum": 0.40, "percentile": 0.96},
            "MSFT": {"momentum": 0.20, "percentile": 0.70},
        })
        screener.momentum.get_momentum_batch = MagicMock(return_value={
            "AAPL": 0.05,
            "MSFT": 0.03,
        })
        screener.technical.check_trend_alignment = MagicMock(return_value={
            "above_short_ma": True,
            "above_long_ma": True,
            "aligned": True,
        })

        passed, _ = screener.stage_2_momentum_filter(["AAPL", "MSFT"])

        assert "AAPL" not in passed
        assert "MSFT" in passed

    def test_overextension_filter_disabled_allows_high_percentile(
        self, test_settings, mock_price_provider
    ):
        """When overextension filter is disabled, high percentile stocks pass."""
        from unittest.mock import MagicMock

        test_settings.overextension_filter_enabled = False

        screener = StockScreener(
            settings=test_settings,
            price_provider=mock_price_provider,
        )

        screener.momentum.get_momentum_percentiles = MagicMock(return_value={
            "AAPL": {"momentum": 0.40, "percentile": 0.99},
        })
        screener.momentum.get_momentum_batch = MagicMock(return_value={
            "AAPL": 0.05,
        })
        screener.technical.check_trend_alignment = MagicMock(return_value={
            "above_short_ma": True,
            "above_long_ma": True,
            "aligned": True,
        })

        passed, _ = screener.stage_2_momentum_filter(["AAPL"])
        assert "AAPL" in passed
