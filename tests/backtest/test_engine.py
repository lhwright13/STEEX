"""Tests for backtest engine."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine, BacktestPosition, BacktestTrade


class TestBacktestTrade:
    """Tests for BacktestTrade dataclass."""

    def test_pnl_calculation(self):
        """Test P&L calculation."""
        trade = BacktestTrade(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1),
            exit_date=datetime(2024, 1, 15),
            entry_price=100.0,
            exit_price=110.0,
            shares=10,
            score=75,
        )

        assert trade.pnl == 100.0  # (110-100) * 10
        assert trade.pnl_pct == 0.10  # 10% gain

    def test_pnl_with_loss(self):
        """Test P&L with losing trade."""
        trade = BacktestTrade(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1),
            exit_date=datetime(2024, 1, 15),
            entry_price=100.0,
            exit_price=93.0,
            shares=10,
            score=75,
        )

        assert trade.pnl == -70.0  # (93-100) * 10
        assert trade.pnl_pct == -0.07  # 7% loss

    def test_pnl_open_trade(self):
        """Test P&L for open trade."""
        trade = BacktestTrade(
            ticker="AAPL",
            entry_date=datetime(2024, 1, 1),
            exit_date=None,
            entry_price=100.0,
            exit_price=None,
            shares=10,
            score=75,
        )

        assert trade.pnl is None
        assert trade.pnl_pct is None


class TestBacktestEngine:
    """Tests for BacktestEngine class."""

    def test_init(self, test_settings):
        """Test engine initialization."""
        engine = BacktestEngine(settings=test_settings)
        assert engine.settings == test_settings

    def test_run_with_no_signals(
        self, test_settings, mock_price_provider, mock_vix_provider
    ):
        """Test running backtest with no signals."""
        engine = BacktestEngine(
            settings=test_settings,
            price_provider=mock_price_provider,
            vix_provider=mock_vix_provider,
        )

        result = engine.run(
            signals=[],
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            starting_capital=10000,
        )

        assert result.starting_capital == 10000
        assert result.ending_capital == 10000
        assert len(result.trades) == 0

    def test_run_with_signals(
        self, test_settings, mock_price_provider, mock_vix_provider
    ):
        """Test running backtest with signals."""
        engine = BacktestEngine(
            settings=test_settings,
            price_provider=mock_price_provider,
            vix_provider=mock_vix_provider,
        )

        signals = [
            {"date": datetime(2024, 1, 5), "ticker": "AAPL", "score": 75},
            {"date": datetime(2024, 1, 10), "ticker": "MSFT", "score": 70},
        ]

        result = engine.run(
            signals=signals,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            starting_capital=10000,
        )

        assert result.starting_capital == 10000
        # Should have some trades
        assert len(result.trades) >= 0
        assert not result.equity_curve.empty

    def test_result_properties(
        self, test_settings, mock_price_provider, mock_vix_provider
    ):
        """Test BacktestResult properties."""
        engine = BacktestEngine(
            settings=test_settings,
            price_provider=mock_price_provider,
            vix_provider=mock_vix_provider,
        )

        result = engine.run(
            signals=[],
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            starting_capital=10000,
        )

        assert result.total_return == 0
        assert result.total_return_pct == 0
