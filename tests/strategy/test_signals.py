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


class TestDeadMoneyExit:
    """Tests for dead money exit signal."""

    def test_dead_money_triggers_when_below_entry_long_enough(self, signal_gen):
        """Position below entry for longer than dead_money_days should trigger."""
        signal_gen.settings.dead_money_enabled = True
        signal_gen.settings.dead_money_days = 10
        # Price below entry
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=95.0)

        entry_date = datetime.now() - timedelta(days=21)  # ~15 trading days
        signal = signal_gen.check_dead_money("AAPL", entry_price=100.0, entry_date=entry_date)

        assert signal is not None
        assert signal.reason == ExitReason.DEAD_MONEY
        assert signal.urgency == "next_session"
        assert signal.gain_pct < 0

    def test_dead_money_no_trigger_when_above_entry(self, signal_gen):
        """Position above entry price should not trigger dead money."""
        signal_gen.settings.dead_money_enabled = True
        signal_gen.settings.dead_money_days = 10
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=105.0)

        entry_date = datetime.now() - timedelta(days=21)
        signal = signal_gen.check_dead_money("AAPL", entry_price=100.0, entry_date=entry_date)
        assert signal is None

    def test_dead_money_no_trigger_when_too_recent(self, signal_gen):
        """Position held for fewer than dead_money_days should not trigger."""
        signal_gen.settings.dead_money_enabled = True
        signal_gen.settings.dead_money_days = 10
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=95.0)

        entry_date = datetime.now() - timedelta(days=5)  # ~3-4 trading days
        signal = signal_gen.check_dead_money("AAPL", entry_price=100.0, entry_date=entry_date)
        assert signal is None

    def test_dead_money_disabled_returns_none(self, signal_gen):
        """When dead_money_enabled is False, should always return None."""
        signal_gen.settings.dead_money_enabled = False
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=95.0)

        entry_date = datetime.now() - timedelta(days=60)
        signal = signal_gen.check_dead_money("AAPL", entry_price=100.0, entry_date=entry_date)
        assert signal is None


class TestVixSpikeExit:
    """Tests for VIX spike exit signal specifics."""

    def test_vix_above_exit_level_triggers(self, signal_gen):
        """VIX above vix_exit_level should trigger immediate exit."""
        signal_gen.settings.vix_exit_level = 40
        signal_gen.vix.get_current = MagicMock(return_value=42.0)
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=110.0)

        signal = signal_gen.check_vix_exit("AAPL", entry_price=100.0)

        assert signal is not None
        assert signal.reason == ExitReason.VIX_SPIKE
        assert signal.urgency == "immediate"
        assert signal.gain_pct == pytest.approx(0.10)

    def test_vix_at_exact_exit_level_no_trigger(self, signal_gen):
        """VIX exactly at the exit level should NOT trigger (must be >)."""
        signal_gen.settings.vix_exit_level = 40
        signal_gen.vix.get_current = MagicMock(return_value=40.0)

        signal = signal_gen.check_vix_exit("AAPL", entry_price=100.0)
        assert signal is None

    def test_vix_spike_with_loss(self, signal_gen):
        """VIX spike exit should correctly report negative gain_pct."""
        signal_gen.settings.vix_exit_level = 40
        signal_gen.vix.get_current = MagicMock(return_value=50.0)
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=90.0)

        signal = signal_gen.check_vix_exit("AAPL", entry_price=100.0)

        assert signal is not None
        assert signal.gain_pct == pytest.approx(-0.10)


class TestMaxHoldExit:
    """Tests for max hold time exit signal."""

    def test_max_hold_triggers_at_threshold(self, signal_gen):
        """Position held beyond max_hold_days trading days should trigger."""
        signal_gen.settings.max_hold_days = 60
        # 90 calendar days ~ 64 trading days (90 * 5/7)
        entry_date = datetime.now() - timedelta(days=90)
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=120.0)

        signal = signal_gen.check_time_exit("AAPL", entry_price=100.0, entry_date=entry_date)

        assert signal is not None
        assert signal.reason == ExitReason.MAX_HOLD_TIME
        assert signal.urgency == "next_session"

    def test_max_hold_no_trigger_below_threshold(self, signal_gen):
        """Position held fewer than max_hold_days should not trigger."""
        signal_gen.settings.max_hold_days = 60
        # 14 calendar days ~ 10 trading days
        entry_date = datetime.now() - timedelta(days=14)

        signal = signal_gen.check_time_exit("AAPL", entry_price=100.0, entry_date=entry_date)
        assert signal is None

    def test_max_hold_reports_gain_correctly(self, signal_gen):
        """Max hold exit should correctly report the gain percentage."""
        signal_gen.settings.max_hold_days = 60
        entry_date = datetime.now() - timedelta(days=90)
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=130.0)

        signal = signal_gen.check_time_exit("AAPL", entry_price=100.0, entry_date=entry_date)

        assert signal is not None
        assert signal.gain_pct == pytest.approx(0.30)
        assert signal.current_price == 130.0

    def test_max_hold_price_unavailable(self, signal_gen):
        """Max hold should return None when price is unavailable."""
        signal_gen.settings.max_hold_days = 60
        entry_date = datetime.now() - timedelta(days=90)
        signal_gen.price_provider.get_latest_price = MagicMock(return_value=None)

        signal = signal_gen.check_time_exit("AAPL", entry_price=100.0, entry_date=entry_date)
        assert signal is None
