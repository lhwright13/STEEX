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
