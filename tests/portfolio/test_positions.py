"""Tests for Position and PositionManager."""

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.portfolio.positions import Position, PositionManager


@dataclass
class FakeBrokerPosition:
    """Module-level broker-position stand-in for the sync-integrity tests."""
    ticker: str
    avg_price: float
    qty: float


class TestPosition:
    def test_calculate_pnl_profit(self):
        pos = Position(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1).isoformat(),
            entry_price=100.0,
            shares=10,
            cost_basis=1000.0,
            high_since_entry=110.0,
            current_stop=90.0,
            score=75.0,
        )
        pnl = pos.calculate_pnl(110.0)
        assert pnl["pnl_dollars"] == pytest.approx(100.0)
        assert pnl["pnl_pct"] == pytest.approx(0.10)
        assert pnl["current_value"] == pytest.approx(1100.0)

    def test_calculate_pnl_loss(self):
        pos = Position(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1).isoformat(),
            entry_price=100.0,
            shares=10,
            cost_basis=1000.0,
            high_since_entry=100.0,
            current_stop=90.0,
            score=75.0,
        )
        pnl = pos.calculate_pnl(95.0)
        assert pnl["pnl_dollars"] == pytest.approx(-50.0)
        assert pnl["pnl_pct"] == pytest.approx(-0.05)

    def test_update_high_new_high(self):
        pos = Position(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1).isoformat(),
            entry_price=100.0,
            shares=10,
            cost_basis=1000.0,
            high_since_entry=105.0,
            current_stop=90.0,
            score=75.0,
        )
        assert pos.update_high(110.0) is True
        assert pos.high_since_entry == 110.0

    def test_update_high_no_change(self):
        pos = Position(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1).isoformat(),
            entry_price=100.0,
            shares=10,
            cost_basis=1000.0,
            high_since_entry=105.0,
            current_stop=90.0,
            score=75.0,
        )
        assert pos.update_high(103.0) is False
        assert pos.high_since_entry == 105.0

    def test_entry_datetime_property(self):
        pos = Position(
            ticker="AAPL",
            entry_date="2024-06-15T10:30:00",
            entry_price=100.0,
            shares=10,
            cost_basis=1000.0,
            high_since_entry=100.0,
            current_stop=90.0,
            score=75.0,
        )
        assert pos.entry_datetime == datetime(2024, 6, 15, 10, 30, 0)


class TestPositionManager:
    def test_add_and_get_position(self, test_settings, tmp_path):
        test_settings.data_dir = str(tmp_path)
        test_settings.positions_file = "positions.json"
        pm = PositionManager(settings=test_settings, positions_file=tmp_path / "positions.json")

        pos = pm.add_position("AAPL", entry_price=100.0, shares=10, score=75.0)
        assert pos.ticker == "AAPL"
        assert pm.has_position("AAPL")
        assert pm.get_position_count() == 1

    def test_sync_from_broker_adds_missing(self, test_settings, tmp_path):
        test_settings.data_dir = str(tmp_path)
        test_settings.positions_file = "positions.json"
        pm = PositionManager(settings=test_settings, positions_file=tmp_path / "positions.json")

        @dataclass
        class FakeBrokerPosition:
            ticker: str
            avg_price: float
            qty: float

        broker = MagicMock()
        broker.get_positions.return_value = [
            FakeBrokerPosition(ticker="AAPL", avg_price=150.0, qty=20),
            FakeBrokerPosition(ticker="MSFT", avg_price=300.0, qty=5),
        ]

        result = pm.sync_from_broker(broker)
        assert set(result["added"]) == {"AAPL", "MSFT"}
        assert result["removed"] == []
        assert result["total"] == 2
        assert pm.has_position("AAPL")
        assert pm.has_position("MSFT")

    def test_sync_from_broker_removes_stale(self, test_settings, tmp_path):
        """A stale local ticker is removed when the broker read is HEALTHY
        (returns other positions but not this one)."""
        test_settings.data_dir = str(tmp_path)
        test_settings.positions_file = "positions.json"
        pm = PositionManager(settings=test_settings, positions_file=tmp_path / "positions.json")
        pm.add_position("OLD_STOCK", entry_price=50.0, shares=10)
        pm.add_position("AAPL", entry_price=150.0, shares=10)

        broker = MagicMock()
        broker.get_positions.return_value = [
            FakeBrokerPosition(ticker="AAPL", avg_price=150.0, qty=10),
        ]

        result = pm.sync_from_broker(broker)
        assert "OLD_STOCK" in result["removed"]
        assert not pm.has_position("OLD_STOCK")

    def test_sync_empty_broker_read_is_treated_as_transient(self, test_settings, tmp_path):
        """07-08 incident: an EMPTY broker read while positions are held must
        NOT delete the book (it fabricated a full set of phantom exits)."""
        test_settings.data_dir = str(tmp_path)
        test_settings.positions_file = "positions.json"
        pm = PositionManager(settings=test_settings, positions_file=tmp_path / "positions.json")
        pm.add_position("AAPL", entry_price=150.0, shares=10)

        broker = MagicMock()
        broker.get_positions.return_value = []

        result = pm.sync_from_broker(broker)
        assert result["removed"] == []
        assert result.get("suspect_read") is True
        assert pm.has_position("AAPL")  # book untouched

    def test_sync_never_adopts_short_positions(self, test_settings, tmp_path):
        """07-07 incident: a negative-qty broker row (account went short via a
        double-sell race) must never become a local 'position'."""
        test_settings.data_dir = str(tmp_path)
        test_settings.positions_file = "positions.json"
        pm = PositionManager(settings=test_settings, positions_file=tmp_path / "positions.json")

        broker = MagicMock()
        broker.get_positions.return_value = [
            FakeBrokerPosition(ticker="JBL", avg_price=325.0, qty=-9),
            FakeBrokerPosition(ticker="AAPL", avg_price=150.0, qty=10),
        ]

        result = pm.sync_from_broker(broker)
        assert result["added"] == ["AAPL"]
        assert not pm.has_position("JBL")

    def test_sync_from_broker_updates_shares(self, test_settings, tmp_path):
        test_settings.data_dir = str(tmp_path)
        test_settings.positions_file = "positions.json"
        pm = PositionManager(settings=test_settings, positions_file=tmp_path / "positions.json")
        pm.add_position("AAPL", entry_price=150.0, shares=10)

        @dataclass
        class FakeBrokerPosition:
            ticker: str
            avg_price: float
            qty: float

        broker = MagicMock()
        broker.get_positions.return_value = [
            FakeBrokerPosition(ticker="AAPL", avg_price=150.0, qty=20),
        ]

        pm.sync_from_broker(broker)
        assert pm.get_position("AAPL").shares == 20
