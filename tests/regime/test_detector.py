"""Tests for RegimeDetector."""

from unittest.mock import MagicMock

import pytest

from src.regime.detector import RegimeDetector


@pytest.fixture
def detector(test_settings, mock_vix_provider):
    yield_provider = MagicMock()
    breadth_provider = MagicMock()
    dollar_provider = MagicMock()
    return RegimeDetector(
        settings=test_settings,
        vix_provider=mock_vix_provider,
        yield_provider=yield_provider,
        breadth_provider=breadth_provider,
        dollar_provider=dollar_provider,
    )


class TestVixRiskScore:
    def test_low_vix(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=12.0)
        score, level = detector._vix_risk_score()
        assert score < 20
        assert level == 12.0

    def test_moderate_vix(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=20.0)
        score, level = detector._vix_risk_score()
        assert 20 <= score <= 40

    def test_high_vix(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=30.0)
        score, level = detector._vix_risk_score()
        assert 40 <= score <= 70

    def test_crisis_vix(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=50.0)
        score, level = detector._vix_risk_score()
        assert score >= 70

    def test_none_vix_returns_neutral(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=None)
        score, level = detector._vix_risk_score()
        assert score == 50.0
        assert level is None


class TestYieldRiskScore:
    def test_healthy_spread(self, detector):
        detector.yield_provider.get_current_spread.return_value = 1.5
        detector.yield_provider.get_yield_curve_status.return_value = "normal"
        score, spread, status = detector._yield_risk_score()
        assert score <= 15
        assert status == "normal"

    def test_inverted_curve(self, detector):
        detector.yield_provider.get_current_spread.return_value = -0.5
        detector.yield_provider.get_yield_curve_status.return_value = "inverted"
        score, spread, status = detector._yield_risk_score()
        assert score >= 70
        assert status == "inverted"

    def test_none_spread_returns_neutral(self, detector):
        detector.yield_provider.get_current_spread.return_value = None
        detector.yield_provider.get_yield_curve_status.return_value = "unknown"
        score, spread, status = detector._yield_risk_score()
        assert score == 50.0


class TestCompositeRegime:
    def test_risk_on(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=12.0)
        detector.yield_provider.get_current_spread.return_value = 1.5
        detector.yield_provider.get_yield_curve_status.return_value = "normal"
        detector.breadth_provider.get_breadth_score.return_value = 80.0
        detector.dollar_provider.get_dollar_trend.return_value = "weakening"
        detector.get_sector_rotation = MagicMock(return_value="cyclical")

        regime = detector.detect_regime()
        assert regime.name == "risk_on"
        assert regime.entries_allowed is True
        # Aggressive profile levers up in risk_on.
        assert regime.sizing_multiplier == 1.25

    def test_crisis(self, detector):
        detector.vix_provider.get_current = MagicMock(return_value=50.0)
        detector.yield_provider.get_current_spread.return_value = -0.5
        detector.yield_provider.get_yield_curve_status.return_value = "inverted"
        detector.breadth_provider.get_breadth_score.return_value = 10.0
        detector.dollar_provider.get_dollar_trend.return_value = "strengthening"
        detector.get_sector_rotation = MagicMock(return_value = "defensive")

        regime = detector.detect_regime()
        assert regime.name == "crisis"
        assert regime.entries_allowed is False
        assert regime.sizing_multiplier == 0.0
