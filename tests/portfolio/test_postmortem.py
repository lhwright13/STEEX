"""Tests for PostMortemAnalyzer."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.portfolio.postmortem import PostMortemAnalyzer


@pytest.fixture
def analyzer(test_settings, mock_price_provider, mock_vix_provider):
    tracker = MagicMock()
    tracker.get_all_trades.return_value = []
    tracker.get_trades_in_range.return_value = []
    return PostMortemAnalyzer(
        settings=test_settings,
        trade_tracker=tracker,
        price_provider=mock_price_provider,
        vix_provider=mock_vix_provider,
    )


class TestGetVixAtDate:
    def test_returns_float_for_valid_date(self, analyzer, sample_vix_data):
        target = sample_vix_data.index[-10].to_pydatetime()
        analyzer.vix_provider.fetch = MagicMock(return_value=sample_vix_data)
        result = analyzer._get_vix_at_date(target)
        assert result is not None
        assert isinstance(result, float)

    def test_returns_none_for_empty_data(self, analyzer):
        analyzer.vix_provider.fetch = MagicMock(return_value=pd.DataFrame())
        result = analyzer._get_vix_at_date(datetime.now())
        assert result is None


class TestGetRegimeName:
    def test_low_vol(self, analyzer):
        assert analyzer._get_regime_name(12.0) == "low_vol"

    def test_normal(self, analyzer):
        assert analyzer._get_regime_name(20.0) == "normal"

    def test_elevated(self, analyzer):
        assert analyzer._get_regime_name(30.0) == "elevated"

    def test_crisis(self, analyzer):
        assert analyzer._get_regime_name(40.0) == "crisis"

    def test_none(self, analyzer):
        assert analyzer._get_regime_name(None) == "unknown"

    def test_boundary_15(self, analyzer):
        assert analyzer._get_regime_name(15.0) == "normal"

    def test_boundary_25(self, analyzer):
        assert analyzer._get_regime_name(25.0) == "normal"

    def test_boundary_35(self, analyzer):
        assert analyzer._get_regime_name(35.0) == "elevated"


class TestCategorizeLoss:
    def test_bad_regime(self, analyzer):
        trade = MagicMock(score=70, hold_days=10, pnl_pct=-0.08, exit_price=95.0)
        result = analyzer.categorize_loss(trade, vix=32.0, regime="elevated", price_5d=None)
        assert result == "bad_regime"

    def test_bad_signal(self, analyzer):
        trade = MagicMock(score=70, hold_days=3, pnl_pct=-0.08, exit_price=95.0)
        result = analyzer.categorize_loss(trade, vix=18.0, regime="normal", price_5d=None)
        assert result == "bad_signal"

    def test_bad_timing(self, analyzer):
        trade = MagicMock(score=50, hold_days=15, pnl_pct=-0.03, exit_price=95.0)
        result = analyzer.categorize_loss(trade, vix=18.0, regime="normal", price_5d=102.0)
        assert result == "bad_timing"

    def test_bad_luck_default(self, analyzer):
        trade = MagicMock(score=50, hold_days=15, pnl_pct=-0.03, exit_price=95.0)
        result = analyzer.categorize_loss(trade, vix=18.0, regime="normal", price_5d=94.0)
        assert result == "bad_luck"
