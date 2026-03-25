"""Tests for MCP server tools — verify each tool wraps QuantManager correctly.

These tests mock the QuantManager and verify that each MCP tool:
1. Calls the correct manager methods
2. Formats the output as valid JSON
3. Handles error paths gracefully
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# We can't import mcp_server directly (it runs argparse and inits globals).
# Instead, patch the module-level globals and test each tool function.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_mcp_globals():
    """Reset MCP server globals between tests."""
    import src.agents.mcp_server as mcp_mod
    mcp_mod._manager = None
    mcp_mod._settings = None
    mcp_mod._pipeline_result = None
    mcp_mod._ranked = None
    mcp_mod._exit_signals = None
    mcp_mod._regime = None
    mcp_mod._buy_list = None
    mcp_mod._sell_list = None
    mcp_mod._dry_run = False
    yield
    mcp_mod._manager = None
    mcp_mod._settings = None
    mcp_mod._pipeline_result = None
    mcp_mod._ranked = None
    mcp_mod._exit_signals = None
    mcp_mod._regime = None
    mcp_mod._buy_list = None
    mcp_mod._sell_list = None
    mcp_mod._dry_run = False


@pytest.fixture
def mock_manager():
    """A MagicMock standing in for QuantManager."""
    mgr = MagicMock()
    mgr.settings = MagicMock()
    mgr.settings.max_positions = 10
    mgr.settings.daily_picks = 2
    mgr.settings.portfolio_max_pairwise_corr = 0.70
    mgr.settings.data_dir = "/tmp/steex_test"
    mgr.settings.postmortem_lookback_days = 90
    mgr.settings.weight_momentum = 0.30
    mgr.settings.weight_insider = 0.25
    mgr.settings.weight_volume = 0.15
    mgr.settings.weight_sentiment = 0.15
    mgr.settings.weight_fundamental = 0.10
    mgr.settings.weight_options = 0.05
    mgr.settings.manager_min_score_entry = 60
    mgr.settings.initial_stop_pct = 0.10
    mgr.settings.max_hold_days = 30
    mgr.broker = MagicMock()
    mgr.position_manager = MagicMock()
    mgr.price_provider = MagicMock()
    mgr.trade_tracker = MagicMock()
    mgr.portfolio_constructor = MagicMock()
    mgr.postmortem_analyzer = MagicMock()
    return mgr


@pytest.fixture
def inject_manager(mock_manager):
    """Inject the mock manager into the MCP module globals."""
    import src.agents.mcp_server as mcp_mod
    mcp_mod._manager = mock_manager
    mcp_mod._settings = mock_manager.settings
    return mock_manager


def _parse(result: str) -> dict:
    """Parse a tool result JSON string."""
    return json.loads(result)


# ---------------------------------------------------------------------------
# C1: sync_broker
# ---------------------------------------------------------------------------

class TestSyncBroker:
    def test_sync_returns_account_summary(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mgr = inject_manager
        mgr.position_manager.get_position_count.return_value = 3
        mgr._get_portfolio_value.return_value = 50000.0
        mgr._get_cash.return_value = 20000.0

        result = _parse(mcp_mod.sync_broker())

        assert result["position_count"] == 3
        assert result["equity"] == 50000.0
        assert result["cash"] == 20000.0
        assert result["max_positions"] == 10
        assert result["broker_connected"] is True
        mgr._sync_broker.assert_called_once()


# ---------------------------------------------------------------------------
# C2: Screening pipeline (run_screening -> rank_candidates -> construct_portfolio)
# ---------------------------------------------------------------------------

class TestScreeningPipeline:
    def _make_pipeline_result(self):
        from src.strategy.screener import ScreeningPipelineResult, ScreeningResult
        candidate = ScreeningResult(
            ticker="AAPL",
            passed_stages=["universe", "momentum", "insider", "sentiment", "fundamental"],
            momentum_6m=0.15,
            insider_buyers=4,
            insider_score=80.0,
            sentiment_score=72.0,
            fundamental_score=65.0,
            volume_surge=1.5,
            sector="Technology",
        )
        return ScreeningPipelineResult(
            date=datetime.now(),
            universe_size=500,
            stage_1_passed=450,
            stage_2_passed=120,
            stage_3_passed=15,
            stage_4_passed=15,
            stage_5_passed=12,
            final_candidates=[candidate],
        )

    def _make_ranked(self):
        from src.strategy.ranking import RankedStock
        from src.strategy.screener import ScreeningResult
        sr = ScreeningResult(ticker="AAPL")
        return [RankedStock(
            ticker="AAPL",
            composite_score=78.5,
            momentum_score=85.0,
            insider_score=80.0,
            volume_score=60.0,
            sentiment_score=72.0,
            fundamental_score=65.0,
            options_score=50.0,
            pysr_score=0.0,
            rank=1,
            screening_result=sr,
        )]

    def test_run_screening(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        pipeline = self._make_pipeline_result()
        inject_manager.run_screening.return_value = pipeline

        result = _parse(mcp_mod.run_screening())

        assert result["universe_size"] == 500
        assert result["stage_2_passed"] == 120
        assert result["stage_3_passed"] == 15
        assert result["final_candidates"] == 1
        assert result["candidates"][0]["ticker"] == "AAPL"
        assert result["candidates"][0]["insider_buyers"] == 4
        inject_manager.run_screening.assert_called_once()

    def test_rank_candidates_requires_screening(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.rank_candidates())
        assert "error" in result

    def test_rank_candidates(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mcp_mod._pipeline_result = self._make_pipeline_result()
        ranked = self._make_ranked()
        inject_manager.rank_candidates.return_value = ranked

        result = _parse(mcp_mod.rank_candidates())

        assert result["count"] == 1
        assert result["ranked"][0]["ticker"] == "AAPL"
        assert result["ranked"][0]["composite_score"] == 78.5
        assert result["ranked"][0]["rank"] == 1

    def test_construct_portfolio_requires_ranking(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.construct_portfolio())
        assert "error" in result

    def test_construct_portfolio(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        ranked = self._make_ranked()
        mcp_mod._ranked = ranked

        proposal = MagicMock()
        proposal.selected = []
        proposal.rejected = []
        proposal.sector_exposure = {"Technology": 0.5}
        proposal.diversification_ratio = 0.85
        inject_manager.portfolio_constructor.select_portfolio.return_value = proposal

        result = _parse(mcp_mod.construct_portfolio())

        assert result["diversification_ratio"] == 0.85
        assert "Technology" in result["sector_exposure"]


# ---------------------------------------------------------------------------
# C3: execute_entries / execute_exits
# ---------------------------------------------------------------------------

class TestExecution:
    def test_execute_entries_requires_buy_list(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.execute_entries())
        assert "error" in result

    def test_execute_entries(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mcp_mod._buy_list = [{"ticker": "AAPL", "qty": 10, "price": 150.0}]
        inject_manager.execute_entries.return_value = [
            {"ticker": "AAPL", "status": "filled", "qty": 10}
        ]

        result = _parse(mcp_mod.execute_entries())

        assert result["count"] == 1
        assert result["dry_run"] is False
        inject_manager.execute_entries.assert_called_once()

    def test_execute_entries_dry_run(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mcp_mod._buy_list = [{"ticker": "AAPL", "qty": 10}]
        mcp_mod._dry_run = True
        inject_manager.execute_entries.return_value = []

        result = _parse(mcp_mod.execute_entries())
        assert result["dry_run"] is True

    def test_execute_exits_requires_sell_list(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.execute_exits())
        assert "error" in result

    def test_execute_exits(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mcp_mod._sell_list = [{"ticker": "AAPL", "qty": 10, "reason": "stop_loss"}]
        inject_manager.execute_exits.return_value = [
            {"ticker": "AAPL", "status": "filled", "qty": 10}
        ]

        result = _parse(mcp_mod.execute_exits())

        assert result["count"] == 1
        assert result["dry_run"] is False


# ---------------------------------------------------------------------------
# C4: get_exit_signals
# ---------------------------------------------------------------------------

class TestExitSignals:
    def test_get_exit_signals_empty(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.get_exit_signals.return_value = []

        result = _parse(mcp_mod.get_exit_signals())
        assert result["total"] == 0
        assert result["exit_signals"] == []

    def test_get_exit_signals_with_signals(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        from src.portfolio.positions import Position
        from src.strategy.signals import ExitReason, ExitSignal

        pos = Position(
            ticker="AAPL",
            entry_date="2026-01-15",
            entry_price=150.0,
            shares=10,
            cost_basis=1500.0,
            high_since_entry=160.0,
            current_stop=135.0,
            score=75.0,
        )
        signal = ExitSignal(
            ticker="AAPL",
            signal_date=datetime.now(),
            reason=ExitReason.STOP_LOSS,
            recommended_exit=134.0,
            current_price=134.0,
            gain_pct=-0.107,
            urgency="immediate",
        )
        inject_manager.get_exit_signals.return_value = [(pos, [signal])]

        result = _parse(mcp_mod.get_exit_signals())

        assert result["total"] == 1
        sig_data = result["exit_signals"][0]
        assert sig_data["ticker"] == "AAPL"
        assert sig_data["entry_price"] == 150.0
        assert sig_data["signals"][0]["reason"] == "stop_loss"
        assert sig_data["signals"][0]["urgency"] == "immediate"


# ---------------------------------------------------------------------------
# C5: save_screen_results / load_screen_results round-trip
# ---------------------------------------------------------------------------

class TestScreenResultsRoundTrip:
    def test_save_and_load(self, inject_manager, tmp_path):
        import src.agents.mcp_server as mcp_mod
        inject_manager.settings.data_dir = str(tmp_path)
        mcp_mod._regime = {"regime": "risk_on", "confidence": 0.85}
        mcp_mod._buy_list = [{"ticker": "AAPL", "qty": 10}]
        mcp_mod._ranked = [MagicMock()]

        # Save
        save_result = _parse(mcp_mod.save_screen_results())
        assert save_result["saved"] is True
        assert save_result["candidates"] == 1

        # Reset buy_list to verify load restores it
        mcp_mod._buy_list = None

        # Load
        load_result = _parse(mcp_mod.load_screen_results())
        assert load_result["count"] == 1
        assert load_result["buy_list"][0]["ticker"] == "AAPL"
        assert load_result["age_hours"] < 0.1  # just saved

    def test_load_missing_file(self, inject_manager, tmp_path):
        import src.agents.mcp_server as mcp_mod
        inject_manager.settings.data_dir = str(tmp_path)

        result = _parse(mcp_mod.load_screen_results())
        assert "error" in result

    def test_load_stale_results(self, inject_manager, tmp_path):
        import src.agents.mcp_server as mcp_mod
        inject_manager.settings.data_dir = str(tmp_path)

        # Write a stale file (3 hours ago)
        screen_dir = tmp_path / "screen_results"
        screen_dir.mkdir()
        stale_ts = (datetime.now() - timedelta(hours=3)).isoformat()
        data = {"timestamp": stale_ts, "buy_list": [], "regime": {}, "ranked_count": 0}
        (screen_dir / "latest.json").write_text(json.dumps(data))

        result = _parse(mcp_mod.load_screen_results())
        assert "error" in result
        assert "stale" in result["error"].lower()


# ---------------------------------------------------------------------------
# C6: Learning loop
# ---------------------------------------------------------------------------

class TestLearningLoop:
    def test_run_learning_loop(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.run_learning.return_value = {
            "postmortem": {"trades_analyzed": 25},
            "alpha_decay": {"degrading": []},
            "changes_applied": False,
        }

        result = _parse(mcp_mod.run_learning_loop())
        assert "postmortem" in result
        inject_manager.run_learning.assert_called_once_with(dry_run=False)

    def test_run_learning_loop_disabled(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.run_learning.return_value = None

        result = _parse(mcp_mod.run_learning_loop())
        assert "error" in result

    def test_run_learning_loop_dry_run(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mcp_mod._dry_run = True
        inject_manager.run_learning.return_value = {"dry_run": True}

        _parse(mcp_mod.run_learning_loop())
        inject_manager.run_learning.assert_called_once_with(dry_run=True)


# ---------------------------------------------------------------------------
# C7: Config changes (propose + apply)
# ---------------------------------------------------------------------------

class TestConfigChanges:
    def test_propose_config_changes(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        with patch("src.learning.config_writer.ConfigWriter") as MockWriter:
            writer = MockWriter.return_value
            writer.propose_changes.return_value = {
                "validated": True,
                "changes": {"weight_momentum": 0.35},
                "warnings": [],
            }
            result = _parse(mcp_mod.propose_config_changes(
                '{"weight_momentum": 0.35}',
                "testing weight adjustment",
            ))
            assert result["validated"] is True
            writer.propose_changes.assert_called_once()

    def test_propose_config_invalid_json(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.propose_config_changes("not json", "test"))
        assert "error" in result

    def test_apply_config_dry_run(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        mcp_mod._dry_run = True

        result = _parse(mcp_mod.apply_config_changes('{"weight_momentum": 0.35}', "test"))
        assert result["applied"] is False
        assert "dry run" in result["reason"].lower()

    def test_apply_config_blocks_market_hours(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        with patch("src.learning.loop._is_market_hours", return_value=True):
            result = _parse(mcp_mod.apply_config_changes('{"weight_momentum": 0.35}', "test"))
            assert result["applied"] is False
            assert "market hours" in result["reason"].lower()

    def test_apply_config_invalid_json(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        with patch("src.learning.loop._is_market_hours", return_value=False):
            result = _parse(mcp_mod.apply_config_changes("bad json", "test"))
            assert "error" in result


# ---------------------------------------------------------------------------
# Additional tool tests: get_positions, get_account, get_regime, get_weights
# ---------------------------------------------------------------------------

class TestUtilityTools:
    def test_get_positions(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        from src.portfolio.positions import Position

        pos = Position(
            ticker="AAPL",
            entry_date="2026-03-10",
            entry_price=150.0,
            shares=10,
            cost_basis=1500.0,
            high_since_entry=160.0,
            current_stop=135.0,
            score=75.0,
        )
        inject_manager.position_manager.get_all_positions.return_value = [pos]
        inject_manager.price_provider.get_latest_price.return_value = 155.0

        result = _parse(mcp_mod.get_positions())

        assert result["count"] == 1
        assert result["positions"][0]["ticker"] == "AAPL"
        assert result["positions"][0]["current_price"] == 155.0
        assert result["positions"][0]["pnl_pct"] is not None

    def test_get_account(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager._get_portfolio_value.return_value = 50000.0
        inject_manager._get_cash.return_value = 20000.0

        result = _parse(mcp_mod.get_account())

        assert result["equity"] == 50000.0
        assert result["cash"] == 20000.0
        assert result["broker_connected"] is True

    def test_get_regime(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.get_regime.return_value = {
            "regime": "risk_on",
            "confidence": 0.85,
            "vix": 18.5,
            "sizing_multiplier": 1.0,
            "entries_allowed": True,
        }

        result = _parse(mcp_mod.get_regime())
        assert result["regime"] == "risk_on"
        assert result["entries_allowed"] is True

    def test_get_current_weights(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.get_current_weights())

        assert result["weight_momentum"] == 0.30
        assert result["weight_insider"] == 0.25
        assert result["weight_volume"] == 0.15
        total = sum(result[k] for k in result if k.startswith("weight_"))
        assert abs(total - 1.0) < 0.01

    def test_get_trade_history(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.trade_tracker.calculate_metrics.return_value = {
            "total_trades": 10,
            "win_rate": 0.60,
            "profit_factor": 1.8,
            "avg_pnl_pct": 0.05,
        }
        inject_manager.trade_tracker.get_all_trades.return_value = []

        result = _parse(mcp_mod.get_trade_history())
        assert result["total_trades"] == 10
        assert result["win_rate"] == 60.0
        assert result["profit_factor"] == 1.8

    def test_assess_portfolio_risk(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.assess_portfolio_risk.return_value = {
            "position_count": 5,
            "total_value": 25000.0,
            "drawdown_pct": -3.5,
            "vix_risk": "normal",
            "immediate_exits": 0,
        }

        result = _parse(mcp_mod.assess_portfolio_risk())
        assert result["position_count"] == 5
        assert result["vix_risk"] == "normal"

    def test_generate_report(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        inject_manager.generate_daily_report.return_value = {
            "risk_alerts": ["VIX elevated"],
            "performance": {"daily_pnl": 250.0},
        }
        inject_manager.save_report.return_value = Path("/tmp/report.json")

        result = _parse(mcp_mod.generate_report("screen"))
        assert result["saved"] is True
        assert result["mode"] == "screen"

    def test_generate_buy_list_requires_ranked_and_regime(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.generate_buy_list())
        assert "error" in result

    def test_generate_sell_list_requires_exit_signals(self, inject_manager):
        import src.agents.mcp_server as mcp_mod
        result = _parse(mcp_mod.generate_sell_list())
        assert "error" in result
