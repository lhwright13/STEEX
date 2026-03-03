"""Tests for SignalGenerator."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.strategy.signals import ExitReason, SignalGenerator


@pytest.fixture
def signal_gen(test_settings, mock_price_provider, mock_vix_provider):
    return SignalGenerator(
        settings=test_settings,
        price_provider=mock_price_provider,
        vix_provider=mock_vix_provider,
    )


class TestCheckStopLoss:
    def test_initial_stop_triggered(self, signal_gen):
        # Mock a price well below entry
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=90.0)
        signal = signal_gen.check_stop_loss("AAPL", entry_price=100.0, current_high=100.0)
        assert signal is not None
        assert signal.reason == ExitReason.STOP_LOSS
        assert signal.urgency == "immediate"

    def test_no_stop_when_above_entry(self, signal_gen):
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=105.0)
        signal = signal_gen.check_stop_loss("AAPL", entry_price=100.0, current_high=105.0)
        assert signal is None

    def test_trailing_stop_triggered(self, signal_gen):
        # Price rose 15% then dropped 13% from high
        entry = 100.0
        high = 115.0
        current = high * 0.87  # 13% below high
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=current)
        signal = signal_gen.check_stop_loss("AAPL", entry_price=entry, current_high=high)
        assert signal is not None
        assert signal.reason == ExitReason.TRAILING_STOP

    def test_no_signal_when_price_unavailable(self, signal_gen):
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=None)
        signal = signal_gen.check_stop_loss("AAPL", entry_price=100.0, current_high=100.0)
        assert signal is None


class TestCheckVixExit:
    def test_vix_spike_triggers_exit(self, signal_gen):
        signal_gen.vix.get_current = MagicMock(return_value=45.0)
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=105.0)
        signal = signal_gen.check_vix_exit("AAPL", entry_price=100.0)
        assert signal is not None
        assert signal.reason == ExitReason.VIX_SPIKE
        assert signal.urgency == "immediate"

    def test_normal_vix_no_exit(self, signal_gen):
        signal_gen.vix.get_current = MagicMock(return_value=20.0)
        signal = signal_gen.check_vix_exit("AAPL", entry_price=100.0)
        assert signal is None

    def test_vix_unavailable_no_exit(self, signal_gen):
        signal_gen.vix.get_current = MagicMock(return_value=None)
        signal = signal_gen.check_vix_exit("AAPL", entry_price=100.0)
        assert signal is None


class TestGetAdjustedStop:
    def test_initial_stop_no_gain(self, signal_gen):
        signal_gen.vix.get_current = MagicMock(return_value=20.0)
        stop = signal_gen.get_adjusted_stop(entry_price=100.0, current_high=100.0)
        expected = 100.0 * (1 - signal_gen.settings.initial_stop_pct)
        assert stop == pytest.approx(expected)

    def test_trailing_stop_after_gain(self, signal_gen):
        signal_gen.vix.get_current = MagicMock(return_value=20.0)
        # 25% gain, should use trail_stop_20 (0.15 by default in test_settings)
        stop = signal_gen.get_adjusted_stop(entry_price=100.0, current_high=125.0)
        assert stop > 100.0  # Stop should be above entry after 25% gain

    def test_vix_tightens_stop(self, signal_gen):
        # Normal VIX
        signal_gen.vix.get_current = MagicMock(return_value=20.0)
        normal_stop = signal_gen.get_adjusted_stop(entry_price=100.0, current_high=130.0)

        # Elevated VIX
        signal_gen.vix.get_current = MagicMock(return_value=35.0)
        tight_stop = signal_gen.get_adjusted_stop(entry_price=100.0, current_high=130.0)

        # Tight stop should be closer to the high (higher value)
        assert tight_stop >= normal_stop


class TestCheckTimeExit:
    def test_max_hold_triggers(self, signal_gen):
        entry_date = datetime.now() - timedelta(days=90)
        signal = signal_gen.check_time_exit("AAPL", 100.0, entry_date)
        assert signal is not None
        assert signal.reason == ExitReason.MAX_HOLD_TIME
        assert signal.urgency == "next_session"

    def test_recent_entry_no_trigger(self, signal_gen):
        entry_date = datetime.now() - timedelta(days=5)
        signal = signal_gen.check_time_exit("AAPL", 100.0, entry_date)
        assert signal is None
