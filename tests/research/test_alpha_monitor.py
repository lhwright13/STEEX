"""Tests for AlphaDecayMonitor."""

from unittest.mock import MagicMock

import pytest

from src.research.alpha_monitor import AlphaDecayMonitor


def _make_trade(pnl_pct):
    trade = MagicMock()
    trade.pnl_pct = pnl_pct
    trade.score = 60.0
    return trade


@pytest.fixture
def monitor(test_settings):
    tracker = MagicMock()
    tracker.get_all_trades.return_value = []
    return AlphaDecayMonitor(settings=test_settings, trade_tracker=tracker)


class TestGetSignalHitRate:
    def test_all_winners(self, monitor):
        trades = [_make_trade(0.10), _make_trade(0.05), _make_trade(0.02)]
        rate = monitor._get_signal_hit_rate(trades, "momentum")
        assert rate == pytest.approx(1.0)

    def test_all_losers(self, monitor):
        trades = [_make_trade(-0.10), _make_trade(-0.05)]
        rate = monitor._get_signal_hit_rate(trades, "momentum")
        assert rate == pytest.approx(0.0)

    def test_mixed(self, monitor):
        trades = [_make_trade(0.10), _make_trade(-0.05), _make_trade(0.03), _make_trade(-0.02)]
        rate = monitor._get_signal_hit_rate(trades, "momentum")
        assert rate == pytest.approx(0.5)

    def test_empty_trades(self, monitor):
        rate = monitor._get_signal_hit_rate([], "momentum")
        assert rate is None


class TestCheckSignalHealth:
    def test_healthy_with_no_trades(self, monitor):
        health = monitor.check_signal_health("momentum")
        assert health.alert_level == "healthy"
        assert health.current_hit_rate == 0.0
        assert health.trend == "stable"

    def test_degrading_signal(self, monitor):
        # Baseline: 70% win rate over 100 trades
        all_trades = [_make_trade(0.05)] * 70 + [_make_trade(-0.05)] * 30
        # Recent window: mostly losers
        all_trades += [_make_trade(-0.05)] * 25 + [_make_trade(0.05)] * 5

        monitor.trade_tracker.get_all_trades.return_value = all_trades
        monitor.settings.alpha_monitor_window = 30

        health = monitor.check_signal_health("momentum")
        assert health.trend == "degrading"
        assert health.alert_level == "degrading"

    def test_improving_signal(self, monitor):
        # Baseline: 40% win rate over 100 trades
        all_trades = [_make_trade(0.05)] * 40 + [_make_trade(-0.05)] * 60
        # Recent window: mostly winners
        all_trades += [_make_trade(0.05)] * 25 + [_make_trade(-0.05)] * 5

        monitor.trade_tracker.get_all_trades.return_value = all_trades
        monitor.settings.alpha_monitor_window = 30

        health = monitor.check_signal_health("momentum")
        assert health.trend == "improving"
        assert health.alert_level == "healthy"
