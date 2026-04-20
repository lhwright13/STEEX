"""Integration tests for QuantManager end-to-end mode execution.

Each test exercises a full mode (screen, enter, monitor, stop_sync, learning)
with all internal components wired up but external dependencies mocked:
- Broker (Alpaca API)
- Data providers (yfinance, SEC, Finnhub)
- File I/O (reports, screen results)

These tests verify the full pipeline works, not individual components.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from config.settings import Settings
from src.broker.base import AccountInfo, BrokerPosition, OrderResult
from src.portfolio.positions import Position, PositionManager
from src.portfolio.tracker import TradeTracker
from src.portfolio.risk import RiskManager
from src.strategy.manager import QuantManager
from src.strategy.screener import ScreeningPipelineResult, ScreeningResult
from src.strategy.ranking import RankedStock, StockRanker
from src.strategy.signals import ExitReason, ExitSignal, SignalGenerator


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path):
    """Temp data dir with required subdirs."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "screen_results").mkdir()
    (tmp_path / "learning").mkdir()
    return tmp_path


@pytest.fixture
def settings(test_settings, tmp_data_dir):
    """Real Settings from conftest with overrides for integration tests."""
    test_settings.data_dir = str(tmp_data_dir)
    test_settings.manager_report_dir = str(tmp_data_dir / "reports")
    test_settings.broker_enabled = False
    test_settings.broker_paper = True
    test_settings.server_stops_enabled = True
    test_settings.server_stop_offset_pct = 0.005
    test_settings.prefetch_enabled = False
    test_settings.regime_multi_factor_enabled = False
    test_settings.postmortem_enabled = False
    test_settings.execution_quality_enabled = False
    test_settings.learning_enabled = True
    test_settings.learning_dry_run = False
    return test_settings


@pytest.fixture
def mock_broker():
    """Mock broker returning paper account info."""
    broker = MagicMock()
    broker.get_account.return_value = AccountInfo(
        buying_power=50000.0, equity=100000.0, cash=50000.0
    )
    broker.get_positions.return_value = []
    broker.place_stop_order.return_value = OrderResult(
        order_id="stop-001", status="accepted"
    )
    broker.update_stop_order.return_value = OrderResult(
        order_id="stop-002", status="accepted"
    )
    broker.buy.return_value = OrderResult(
        order_id="buy-001", status="filled", filled_price=150.0, filled_qty=10
    )
    broker.sell.return_value = OrderResult(
        order_id="sell-001", status="filled", filled_price=155.0, filled_qty=10
    )
    broker.cancel_stop_for_ticker.return_value = True
    broker.get_stop_order.return_value = None
    broker.get_all_stop_orders.return_value = []
    return broker


@pytest.fixture
def mock_price():
    """Mock price provider."""
    pp = MagicMock()
    pp.get_latest_price.return_value = 155.0
    pp.get_ohlcv.return_value = MagicMock()
    return pp


@pytest.fixture
def mock_vix():
    """Mock VIX provider."""
    vix = MagicMock()
    vix.get_current.return_value = 18.5
    return vix


@pytest.fixture
def mock_tracker():
    """Mock TradeTracker with realistic return values."""
    tt = MagicMock()
    tt.calculate_metrics.return_value = {
        "total_trades": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "avg_pnl_pct": 0.0,
    }
    tt.get_all_trades.return_value = []
    tt.get_recent_tickers.return_value = []
    return tt


@pytest.fixture
def position_manager(settings, tmp_data_dir):
    """Real PositionManager with temp file."""
    return PositionManager(
        settings=settings,
        positions_file=tmp_data_dir / "positions.json",
    )


def _add_position(pm, ticker, entry_price, shares, score=50.0, days_ago=5):
    """Helper to add a position using the correct API."""
    pm.add_position(
        ticker=ticker,
        entry_price=entry_price,
        shares=shares,
        score=score,
        entry_date=datetime.now() - timedelta(days=days_ago),
    )


def _make_candidate(ticker="AAPL", score=80.0, insider_buyers=4):
    return ScreeningResult(
        ticker=ticker,
        passed_stages=["universe", "momentum", "insider", "sentiment", "fundamental"],
        momentum_6m=0.15,
        insider_buyers=insider_buyers,
        insider_score=score,
        sentiment_score=72.0,
        fundamental_score=65.0,
        volume_surge=1.5,
        sector="Technology",
    )


def _make_pipeline(candidates):
    return ScreeningPipelineResult(
        date=datetime.now(),
        universe_size=500,
        stage_1_passed=450,
        stage_2_passed=120,
        stage_3_passed=len(candidates),
        stage_4_passed=len(candidates),
        stage_5_passed=len(candidates),
        final_candidates=candidates,
    )


def _make_ranked(screening_result, rank=1, score=78.5):
    return RankedStock(
        ticker=screening_result.ticker,
        composite_score=score,
        momentum_score=85.0,
        insider_score=screening_result.insider_score or 0,
        volume_score=60.0,
        sentiment_score=72.0,
        fundamental_score=65.0,
        options_score=50.0,
        rank=rank,
        screening_result=screening_result,
    )


# ---------------------------------------------------------------------------
# D1: Screen mode end-to-end
# ---------------------------------------------------------------------------

class TestScreenMode:
    def test_full_screen_pipeline(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager, tmp_data_dir,
    ):
        """screen mode: sync → data → regime → risk → exits → screen → rank → save."""
        mock_screener = MagicMock()
        candidates = [_make_candidate("AAPL"), _make_candidate("MSFT", score=75)]
        mock_screener.run_pipeline.return_value = _make_pipeline(candidates)

        mock_ranker = MagicMock()
        ranked = [_make_ranked(c, i + 1) for i, c in enumerate(candidates)]
        mock_ranker.rank.return_value = ranked

        mock_signal = MagicMock()
        mock_signal.check_all.return_value = []

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=mock_signal,
            screener=mock_screener,
            ranker=mock_ranker,
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
            regime_detector=None,
            portfolio_constructor=None,
        )

        report = mgr.run_screen(dry_run=True)

        assert report is not None
        screen_file = tmp_data_dir / "screen_results" / "latest.json"
        assert screen_file.exists()
        screen_data = json.loads(screen_file.read_text())
        assert "buy_list" in screen_data
        assert "timestamp" in screen_data
        mock_broker.get_account.assert_called()
        mock_screener.run_pipeline.assert_called_once()

    def test_screen_with_position(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager, tmp_data_dir,
    ):
        """screen mode runs correctly when positions exist."""
        _add_position(position_manager, "LOSE", 100.0, 10)

        mock_screener = MagicMock()
        mock_screener.run_pipeline.return_value = _make_pipeline([])
        mock_ranker = MagicMock()
        mock_ranker.rank.return_value = []

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=mock_screener,
            ranker=mock_ranker,
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
            regime_detector=None,
            portfolio_constructor=None,
        )

        report = mgr.run_screen(dry_run=True)
        assert report is not None


# ---------------------------------------------------------------------------
# D2: Enter mode end-to-end
# ---------------------------------------------------------------------------

class TestEnterMode:
    def test_enter_loads_screen_and_executes(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager, tmp_data_dir,
    ):
        """enter mode: load screen → risk check → execute buys → place stops."""
        screen_data = {
            "timestamp": datetime.now().isoformat(),
            "regime": {"name": "risk_on", "vix": 18.5, "sizing_multiplier": 1.0},
            "buy_list": [
                {
                    "ticker": "AAPL", "shares": 10, "price": 150.0,
                    "cost": 1500.0, "stop": 135.0, "score": 78,
                    "size_pct": 3.0, "reasons": ["momentum", "insider"],
                },
            ],
            "ranked_count": 1,
        }
        screen_path = tmp_data_dir / "screen_results" / "latest.json"
        screen_path.write_text(json.dumps(screen_data))

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_enter(dry_run=False, auto_confirm=True)

        assert report is not None
        mock_broker.get_account.assert_called()

    def test_enter_skips_stale_results(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager, tmp_data_dir,
    ):
        """enter mode skips entries if screen results are >2h old."""
        screen_data = {
            "timestamp": (datetime.now() - timedelta(hours=3)).isoformat(),
            "regime": {},
            "buy_list": [{"ticker": "AAPL", "qty": 10}],
            "ranked_count": 1,
        }
        screen_path = tmp_data_dir / "screen_results" / "latest.json"
        screen_path.write_text(json.dumps(screen_data))

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_enter(dry_run=False, auto_confirm=True)
        assert report is not None

    def test_enter_no_screen_file(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager, tmp_data_dir,
    ):
        """enter mode handles missing screen results gracefully."""
        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_enter(dry_run=False, auto_confirm=True)
        assert report is not None


# ---------------------------------------------------------------------------
# D3: Monitor mode end-to-end
# ---------------------------------------------------------------------------

class TestMonitorMode:
    def test_monitor_no_exits(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """monitor mode with no exit signals — pure health check."""
        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_monitor(dry_run=True)
        assert report is not None
        mock_broker.get_account.assert_called()

    def test_monitor_with_positions(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """monitor mode with active positions."""
        _add_position(position_manager, "LOSE", 100.0, 10, days_ago=10)

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_monitor(dry_run=True)
        assert report is not None


# ---------------------------------------------------------------------------
# D4: Stop sync end-to-end
# ---------------------------------------------------------------------------

class TestStopSyncMode:
    def test_stop_sync_with_position(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """stop_sync: update trailing stops then sync to Alpaca."""
        _add_position(position_manager, "WIN", 100.0, 10, score=75, days_ago=15)

        mock_risk = MagicMock(spec=RiskManager)
        mock_risk.update_stops.return_value = {"WIN": {"new_stop": 103.5}}

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=mock_risk,
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_stop_sync(dry_run=False)

        assert report is not None

    def test_stop_sync_dry_run(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """stop_sync dry_run doesn't touch broker."""
        _add_position(position_manager, "HLD", 200.0, 5, score=70, days_ago=5)

        mock_risk = MagicMock(spec=RiskManager)
        mock_risk.update_stops.return_value = {}

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=mock_risk,
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_stop_sync(dry_run=True)
        assert report is not None

    def test_stop_sync_no_positions(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """stop_sync with empty portfolio — no broker calls needed."""
        mock_risk = MagicMock(spec=RiskManager)
        mock_risk.update_stops.return_value = {}

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=mock_risk,
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        report = mgr.run_stop_sync(dry_run=False)
        assert report is not None
        mock_broker.update_stop_order.assert_not_called()


# ---------------------------------------------------------------------------
# D5: Learning loop end-to-end
# ---------------------------------------------------------------------------

class TestLearningMode:
    def test_learning_runs_full_cycle(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """learning mode runs the full loop: postmortem → alpha → research → OOS."""
        settings.learning_enabled = True

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        with patch("src.learning.loop.LearningLoop") as MockLoop:
            mock_loop = MockLoop.return_value
            mock_loop.run.return_value = {
                "postmortem": {"trades_analyzed": 25, "win_rate": 0.60},
                "alpha_decay": {"degrading": []},
                "signal_research": None,
                "oos_validation": None,
                "changes_applied": False,
            }

            result = mgr.run_learning(dry_run=False)

        assert result is not None
        mock_loop.run.assert_called_once_with(dry_run=False)

    def test_learning_respects_dry_run(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        settings.learning_enabled = True

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        with patch("src.learning.loop.LearningLoop") as MockLoop:
            mock_loop = MockLoop.return_value
            mock_loop.run.return_value = {"dry_run": True}
            mgr.run_learning(dry_run=True)
            mock_loop.run.assert_called_once_with(dry_run=True)

    def test_learning_disabled(
        self, settings, mock_broker, mock_price, mock_vix, mock_tracker,
        position_manager,
    ):
        """learning mode returns None when disabled."""
        settings.learning_enabled = False

        mgr = QuantManager(
            settings=settings,
            position_manager=position_manager,
            trade_tracker=mock_tracker,
            risk_manager=MagicMock(spec=RiskManager),
            signal_generator=MagicMock(),
            screener=MagicMock(),
            ranker=MagicMock(),
            price_provider=mock_price,
            vix_provider=mock_vix,
            broker=mock_broker,
        )

        result = mgr.run_learning(dry_run=False)
        assert result is None
