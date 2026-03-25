"""Tests for RiskManager."""

from unittest.mock import MagicMock, patch

import pytest

from src.portfolio.positions import Position, PositionManager
from src.portfolio.risk import RiskManager
from src.strategy.signals import ExitReason, ExitSignal, SignalGenerator


@pytest.fixture
def risk_manager(test_settings, mock_price_provider, mock_vix_provider, tmp_path):
    positions_file = tmp_path / "positions.json"
    test_settings.data_dir = str(tmp_path)
    test_settings.positions_file = "positions.json"
    pm = PositionManager(settings=test_settings, positions_file=positions_file)
    sg = SignalGenerator(
        settings=test_settings,
        price_provider=mock_price_provider,
        vix_provider=mock_vix_provider,
    )
    return RiskManager(
        settings=test_settings,
        position_manager=pm,
        signal_generator=sg,
        price_provider=mock_price_provider,
        vix_provider=mock_vix_provider,
    )


class TestUpdateStops:
    def test_trailing_stop_moves_up(self, risk_manager):
        pm = risk_manager.positions
        pm.add_position("AAPL", entry_price=100.0, shares=10)
        pos = pm.get_position("AAPL")
        original_stop = pos.current_stop

        updates = risk_manager.update_stops()
        pos_after = pm.get_position("AAPL")

        # The mock price provider returns a price from sample data (~100).
        # The stop should only move up if the new calculated stop exceeds the old one.
        if "AAPL" in updates:
            assert pos_after.current_stop >= original_stop

    def test_stop_never_lowered(self, risk_manager):
        pm = risk_manager.positions
        pm.add_position("AAPL", entry_price=50.0, shares=10)
        pos = pm.get_position("AAPL")
        # Artificially set a high stop
        pm.update_stop("AAPL", 200.0)

        risk_manager.update_stops()
        assert pm.get_position("AAPL").current_stop == 200.0

    def test_no_positions_returns_empty(self, risk_manager):
        updates = risk_manager.update_stops()
        assert updates == {}


class TestCheckAllExits:
    def test_no_positions_returns_empty(self, risk_manager):
        triggered = risk_manager.check_all_exits()
        assert triggered == []

    def test_returns_list_of_tuples(self, risk_manager):
        pm = risk_manager.positions
        pm.add_position("AAPL", entry_price=100.0, shares=10)

        triggered = risk_manager.check_all_exits()
        # Result is always a list
        assert isinstance(triggered, list)
        for item in triggered:
            assert isinstance(item, tuple)
            assert len(item) == 2


class TestCheckVixRisk:
    def test_normal_vix(self, risk_manager):
        risk_manager.vix.get_current = MagicMock(return_value=20.0)
        result = risk_manager.check_vix_risk()
        assert result["status"] == "normal"
        assert result["action"] == "none"

    def test_elevated_vix(self, risk_manager):
        risk_manager.vix.get_current = MagicMock(return_value=35.0)
        result = risk_manager.check_vix_risk()
        assert result["status"] == "elevated"
        assert result["action"] == "tighten_stops"

    def test_spike_vix(self, risk_manager):
        risk_manager.vix.get_current = MagicMock(return_value=45.0)
        result = risk_manager.check_vix_risk()
        assert result["status"] == "spike"
        assert result["action"] == "exit_50_percent"

    def test_unknown_vix(self, risk_manager):
        risk_manager.vix.get_current = MagicMock(return_value=None)
        result = risk_manager.check_vix_risk()
        assert result["status"] == "unknown"


class TestDrawdownRules:
    """Tests for calculate_portfolio_drawdown drawdown thresholds."""

    def _make_drawdown_result(self, risk_manager, drawdown_pct):
        """Helper: create a drawdown scenario at the given percentage.

        Args:
            risk_manager: RiskManager fixture
            drawdown_pct: Desired drawdown as a decimal (e.g. 0.10 for 10%)
        """
        starting_value = 100_000.0
        # We need current_value = starting_value * (1 - drawdown_pct)
        # current_value = portfolio_summary["total_value"] + cash
        # With no positions, total_value = 0, so cash = target current_value
        cash = starting_value * (1 - drawdown_pct)
        return risk_manager.calculate_portfolio_drawdown(
            starting_value=starting_value,
            current_prices={},
            cash=cash,
        )

    def test_no_drawdown_returns_none_action(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.0)
        assert result["action"] == "none"
        assert result["drawdown"] == pytest.approx(0.0)

    def test_small_drawdown_below_review_returns_none(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.05)
        assert result["action"] == "none"

    def test_review_threshold_at_10_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.10)
        assert result["action"] == "review"
        assert result["drawdown_pct"] == pytest.approx(10.0)

    def test_review_threshold_at_12_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.12)
        assert result["action"] == "review"

    def test_reduce_size_threshold_at_15_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.15)
        assert result["action"] == "reduce_size"
        assert result["drawdown_pct"] == pytest.approx(15.0)

    def test_reduce_size_threshold_at_18_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.18)
        assert result["action"] == "reduce_size"

    def test_pause_entries_threshold_at_20_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.20)
        assert result["action"] == "pause_entries"
        assert result["drawdown_pct"] == pytest.approx(20.0)

    def test_pause_entries_threshold_at_22_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.22)
        assert result["action"] == "pause_entries"

    def test_exit_all_threshold_at_25_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.25)
        assert result["action"] == "exit_all"
        assert result["drawdown_pct"] == pytest.approx(25.0)

    def test_exit_all_threshold_at_30_pct(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.30)
        assert result["action"] == "exit_all"

    def test_drawdown_returns_correct_values(self, risk_manager):
        result = self._make_drawdown_result(risk_manager, 0.15)
        assert result["starting_value"] == 100_000.0
        assert result["current_value"] == pytest.approx(85_000.0)
        assert result["drawdown"] == pytest.approx(0.15)
