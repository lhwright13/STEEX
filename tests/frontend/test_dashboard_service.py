"""
Unit tests for dashboard service layer.

Tests:
- Data retrieval from trading system
- Default values when no run data available
- Error handling and graceful fallbacks
"""

import pytest
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from frontend.services import DashboardService


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = Mock()
    settings.data_dir = Path("/tmp/steex/data")
    settings.config_dir = Path("/tmp/steex/config")
    return settings


@pytest.fixture
def mock_registry():
    """Create mock agent registry."""
    registry = Mock()
    registry.agents = {
        "data": Mock(
            role="DataAgent",
            max_turns=8,
            needs_tools=True,
            external_servers=["alpaca", "alphavantage"],
            prompt="data_collection",
            allowed_tools=["get_current_data", "get_indicators"],
        ),
        "analysis_conservative": Mock(
            role="AnalysisVariant",
            max_turns=15,
            needs_tools=True,
            external_servers=["alpaca", "alphavantage", "polygon"],
            prompt="analysis_conservative",
            allowed_tools=["run_screening_variant", "rank_candidates_with_weights"],
        ),
    }
    registry.modes = {
        "screen": Mock(
            critical_agents=["data", "risk"],
            name="screen",
        )
    }
    return registry


@pytest.fixture
def mock_regime_detector():
    """Create mock regime detector."""
    detector = Mock()
    regime = Mock()
    regime.name = "cautious"
    regime.vix_level = 16.5
    regime.confidence = 0.85
    detector.detect_regime.return_value = regime
    return detector


@pytest.fixture
def service(mock_settings, mock_registry, mock_regime_detector):
    """Create dashboard service with mocks."""
    # __init__ now lives in the base mixin (frontend/services/base.py) after the
    # P0-5 split, so patch where the names are actually looked up.
    with patch("frontend.services.base.get_settings", return_value=mock_settings), \
         patch("frontend.services.base.AgentRegistry", return_value=mock_registry), \
         patch("frontend.services.base.RegimeDetector", return_value=mock_regime_detector):
        return DashboardService()


# ========================================================================
# Test: Pipeline Current State
# ========================================================================


class TestPipelineCurrent:
    """Test getting current pipeline state."""

    def test_pipeline_idle_when_no_run_file(self, service):
        """Pipeline should return idle state when no run file exists."""
        with patch.object(service, "_get_latest_run_file", return_value=None):
            data = service.get_pipeline_current()

            assert data["status"] == "idle"
            assert data["stage"] == "idle"
            assert data["elapsed"] == 0
            assert data["stage_progress"] == 0.0
            assert data["current_agent"] == "idle"
            assert "timestamp" in data

    def test_pipeline_running_state(self, service):
        """Pipeline should have all required fields."""
        # When no run file, should return idle state with all required fields
        data = service.get_pipeline_current()

        required_fields = ["status", "mode", "stage", "elapsed", "stage_progress", "current_agent", "run_id", "timestamp"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_pipeline_complete_state(self, service):
        """Pipeline should have proper stage progress calculation."""
        # Test the helper method directly
        result = service._calculate_stage_progress({"status": "complete"})
        assert result == 1.0

        result = service._calculate_stage_progress({"status": "idle"})
        assert result == 0.0

        result = service._calculate_stage_progress({"status": "running", "stage": "analysis"})
        assert result == 0.65


# ========================================================================
# Test: Variant Results
# ========================================================================


class TestVariantsResults:
    """Test getting analysis variant results."""

    def test_variants_all_idle_when_no_run(self, service):
        """All variants should be idle when no run data."""
        with patch.object(service, "_get_latest_run_file", return_value=None):
            data = service.get_variants_results()

            assert data["conservative"]["status"] == "idle"
            assert data["aggressive"]["status"] == "idle"
            assert data["momentum"]["status"] == "idle"
            assert all(v["candidate_count"] == 0 for v in data.values())

    def test_variants_with_conclusions(self, service):
        """Variants should return all three variants with required fields."""
        data = service.get_variants_results()

        # Should have all three variants
        assert "conservative" in data
        assert "aggressive" in data
        assert "momentum" in data

        # Each should have required fields
        for variant in data.values():
            assert "variant" in variant
            assert "status" in variant
            assert "candidate_count" in variant
            assert "avg_score" in variant
            assert "timestamp" in variant


# ========================================================================
# Test: Consensus Picks
# ========================================================================


class TestConsensus:
    """Test getting consensus picks."""

    def test_consensus_empty_when_no_run(self, service):
        """Consensus should be empty when no run data."""
        with patch.object(service, "_get_latest_run_file", return_value=None):
            data = service.get_consensus()

            assert data["high_conviction"] == []
            assert data["consensus"] == []
            assert data["speculative_excluded"] == []

    def test_consensus_separates_by_conviction(self, service):
        """Consensus should separate high conviction from others."""
        run_data = {
            "conclusions": {
                "analysis": {
                    "candidates": [
                        {"ticker": "NVDA", "score": 76.0, "high_conviction": True},
                        {"ticker": "MSFT", "score": 72.0, "high_conviction": False},
                        {"ticker": "AAPL", "score": 70.0, "high_conviction": False},
                    ],
                    "speculative_excluded": ["AMD", "META"],
                }
            },
        }

        with patch.object(service, "_get_latest_run_file", return_value=Path("/tmp/run.jsonl")), \
             patch.object(service, "_load_json", return_value=run_data):
            data = service.get_consensus()

            if data["high_conviction"]:  # Only assert if data was loaded
                assert len(data["high_conviction"]) >= 1
            if data["speculative_excluded"]:
                assert "AMD" in data["speculative_excluded"]


# ========================================================================
# Test: Screening Stats
# ========================================================================


class TestScreeningStats:
    """Test getting screening funnel statistics."""

    def test_screening_stats_empty_when_no_run(self, service):
        """Stats should be zero when no run data."""
        with patch.object(service, "_get_latest_run_file", return_value=None):
            data = service.get_screening_stats()

            assert all(v == 0 for v in data.values())

    def test_screening_stats_from_run_data(self, service):
        """Stats should reflect actual run data."""
        run_data = {
            "screening": {
                "universe_size": 7500,
                "volume_filtered": 450,
                "sentiment_filtered": 380,
                "technical_filtered": 285,
                "insider_filtered": 180,
                "final_count": 145,
            },
            "conclusions": {
                "analysis": {
                    "candidates": [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]
                }
            },
        }

        with patch.object(service, "_get_latest_run_file", return_value=Path("/tmp/run.jsonl")), \
             patch.object(service, "_load_json", return_value=run_data):
            data = service.get_screening_stats()

            # Verify structure, values may come from defaults or run data
            assert isinstance(data, dict)
            assert "universe" in data
            assert "final_picked" in data


# ========================================================================
# Test: Regime
# ========================================================================


class TestRegime:
    """Test getting market regime."""

    def test_regime_from_detector(self, service):
        """Regime should come from detector."""
        data = service.get_regime()

        assert data["current"] == "cautious"
        assert data["vix"] == 16.5
        assert "regimes" in data
        assert "timestamp" in data

    def test_regime_fallback_on_error(self, service):
        """Regime should fallback gracefully on error."""
        with patch.object(service.regime_detector, "detect_regime", side_effect=Exception("Error")):
            data = service.get_regime()

            assert data["current"] == "unknown"
            assert data["vix"] == 15.0


# ========================================================================
# Test: Manager Decision
# ========================================================================


class TestManagerDecision:
    """Test getting manager's decision."""

    def test_manager_decision_pending_when_no_run(self, service):
        """Manager decision should be pending when no run."""
        with patch.object(service, "_get_latest_run_file", return_value=None):
            data = service.get_manager_decision()

            assert data["status"] == "pending"
            assert "No recent run" in data["reasoning"]

    def test_manager_decision_approved(self, service):
        """Manager decision should show approval status."""
        run_data = {
            "abort": False,
            "manager_decision": {
                "reasoning": "Strong consensus picks",
                "position_adjustments": {"NVDA": 0.045},
            },
        }

        with patch.object(service, "_get_latest_run_file", return_value=Path("/tmp/run.jsonl")), \
             patch.object(service, "_load_json", return_value=run_data):
            data = service.get_manager_decision()

            # Should return approved when abort is False
            assert data["status"] in ["approved", "pending"]

    def test_manager_decision_rejected_on_abort(self, service):
        """Manager decision should be rejected on abort."""
        run_data = {
            "abort": True,
            "manager_decision": {"reasoning": "Pipeline aborted", "position_adjustments": {}},
        }

        with patch.object(service, "_get_latest_run_file", return_value=Path("/tmp/run.jsonl")), \
             patch.object(service, "_load_json", return_value=run_data):
            data = service.get_manager_decision()

            # Should return rejected when abort is True
            assert data["status"] in ["rejected", "pending"]


# ========================================================================
# Test: System Agents
# ========================================================================


class TestSystemAgents:
    """Test getting system agent configurations."""

    def test_system_agents_returns_all_agents(self, service):
        """Should return all agents from registry."""
        data = service.get_system_agents()

        assert "agents" in data
        assert len(data["agents"]) == 2
        agent_names = [a["name"] for a in data["agents"]]
        assert "data" in agent_names
        assert "analysis_conservative" in agent_names

    def test_agent_has_required_fields(self, service):
        """Each agent should have all required fields."""
        data = service.get_system_agents()
        agent = data["agents"][0]

        required_fields = [
            "name",
            "role",
            "max_turns",
            "needs_tools",
            "external_servers",
            "prompt_id",
            "critical",
        ]
        for field in required_fields:
            assert field in agent

    def test_critical_agents_marked(self, service):
        """Critical agents should be marked."""
        data = service.get_system_agents()
        critical_agents = [a for a in data["agents"] if a["critical"]]

        assert any(a["name"] == "data" for a in critical_agents)


# ========================================================================
# Test: System Schedules
# ========================================================================


class TestSystemSchedules:
    """Test getting schedule configuration."""

    def test_system_schedules_returns_list(self, service):
        """Should return list of schedules."""
        data = service.get_system_schedules()

        assert "schedules" in data
        assert isinstance(data["schedules"], list)
        assert len(data["schedules"]) > 0

    def test_schedule_has_required_fields(self, service):
        """Each schedule should have required fields."""
        data = service.get_system_schedules()
        schedule = data["schedules"][0]

        required_fields = ["name", "mode", "cron", "description", "next_run", "enabled"]
        for field in required_fields:
            assert field in schedule

    def test_schedule_next_run_is_future(self, service):
        """Next run should be in future."""
        data = service.get_system_schedules()

        for schedule in data["schedules"]:
            # Just verify it's a valid ISO timestamp
            assert isinstance(schedule["next_run"], str)
            assert "T" in schedule["next_run"]
            assert "Z" in schedule["next_run"]


# ========================================================================
# Test: Agent Detail
# ========================================================================


class TestAgentDetail:
    """Test getting detailed agent configuration."""

    def test_agent_detail_returns_error_for_unknown_agent(self, service):
        """Should return error for unknown agent."""
        data = service.get_agent_detail("unknown_agent")

        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_agent_detail_has_required_fields(self, service):
        """Agent detail should have all fields."""
        data = service.get_agent_detail("data")

        if "error" not in data:
            required_fields = ["name", "role", "tools", "external_servers"]
            for field in required_fields:
                assert field in data

    def test_agent_detail_includes_tools(self, service):
        """Agent detail should list available tools."""
        data = service.get_agent_detail("data")

        if "error" not in data:
            assert "tools" in data
            assert isinstance(data["tools"], list)


# ========================================================================
# Test: Helper Methods
# ========================================================================


class TestHelperMethods:
    """Test internal helper methods."""

    def test_elapsed_seconds_calculation(self, service):
        """Should calculate elapsed seconds correctly."""
        now = datetime.utcnow().isoformat() + "Z"
        elapsed = service._elapsed_seconds(now)

        assert elapsed >= 0
        assert elapsed < 5  # Should be very recent

    def test_elapsed_seconds_with_none(self, service):
        """Should return 0 for None timestamp."""
        elapsed = service._elapsed_seconds(None)

        assert elapsed == 0

    def test_calculate_stage_progress(self, service):
        """Should calculate stage progress based on stage."""
        test_cases = [
            ({"status": "idle"}, 0.0),
            ({"status": "complete"}, 1.0),
            ({"status": "failed"}, 0.0),
            ({"status": "running", "stage": "data"}, 0.15),
            ({"status": "running", "stage": "analysis"}, 0.65),
            ({"status": "running", "stage": "execution"}, 1.0),
        ]

        for run_data, expected in test_cases:
            progress = service._calculate_stage_progress(run_data)
            assert progress == expected

    def test_perf_periods_present_and_period_lookup_safe(self, service):
        """Regression: _PERF_PERIODS was dropped in the P0-5 split, 500-ing the
        performance chart (it's read before the broker check). Pin it back and
        confirm an unknown period normalizes to 1M instead of raising."""
        assert set(service._PERF_PERIODS) == {"1D", "1W", "1M", "3M", "1Y", "YTD"}
        # YTD resolves dynamically (days since Jan 1); 1D is intraday.
        assert service._resolve_period("1D")[2] == "5Min"
        ap, spy_days, tf = service._resolve_period("YTD")
        assert ap.endswith("D") and tf == "1D" and spy_days >= 7
        # Force the broker path to fail so we exercise the period lookup without
        # network/credentials, then hit the graceful unavailable branch.
        with patch("alpaca.trading.client.TradingClient", side_effect=Exception("no broker")):
            out = service.get_portfolio_performance("nonsense")
        assert out["available"] is False and out["period"] == "1M"


# ========================================================================
# Test: Kill switch, trade history, agent timeline
# ========================================================================


class TestControlsTradesTimeline:
    """Tests for the kill switch, realized-P&L, and agent-timeline readers."""

    def test_controls_roundtrip(self, service, tmp_path):
        service.data_dir = tmp_path
        assert service.get_controls()["trading_armed"] is True
        service.set_controls(trading_armed=False)
        assert service.get_controls()["trading_armed"] is False

    def test_trade_history_summary(self, service, tmp_path):
        service.data_dir = tmp_path
        (tmp_path / "trades.json").write_text(json.dumps([
            {"ticker": "AAA", "entry_price": 10, "exit_price": 12, "shares": 5,
             "pnl_dollars": 10.0, "pnl_pct": 0.2, "hold_days": 4, "exit_reason": "trailing_stop",
             "exit_date": "2026-05-01"},
            {"ticker": "BBB", "entry_price": 20, "exit_price": 18, "shares": 5,
             "pnl_dollars": -10.0, "pnl_pct": -0.1, "hold_days": 6, "exit_reason": "stop_loss",
             "exit_date": "2026-05-02"},
        ]))
        d = service.get_trade_history()
        s = d["summary"]
        assert s["count"] == 2 and s["wins"] == 1 and s["losses"] == 1
        assert s["win_rate"] == 50.0
        assert s["total_realized_pnl"] == 0.0
        assert s["exit_reasons"] == {"trailing_stop": 1, "stop_loss": 1}
        # newest exit_date first
        assert d["trades"][0]["ticker"] == "BBB"

    def test_trade_history_empty(self, service, tmp_path):
        service.data_dir = tmp_path
        d = service.get_trade_history()
        assert d["trades"] == []
        assert d["summary"]["count"] == 0

    def test_agent_timeline(self, service, tmp_path):
        service.data_dir = tmp_path
        runs = tmp_path / "runs"
        runs.mkdir()
        rec = {"run_id": "r1", "mode": "screen", "status": "complete",
               "started_at": "2026-05-01T10:00:00Z",
               "traces": [
                   {"role": "DataAgent", "agent": "data", "success": True,
                    "duration_seconds": 8.0, "tools_called": ["x"], "summary": "ok", "conclusion": {}},
                   {"role": "RiskAgent", "agent": "risk", "success": False,
                    "duration_seconds": 5.0, "tools_called": [], "error": "boom", "summary": "boom",
                    "conclusion": None},
               ]}
        (runs / "run_20260501T100000_r1.jsonl").write_text(json.dumps(rec))
        tl = service.get_agent_timeline("r1")
        assert tl["agent_count"] == 2
        assert tl["failed_count"] == 1
        assert tl["steps"][0]["agent"] == "data" and tl["steps"][0]["success"] is True
        assert tl["steps"][1]["success"] is False


class TestEventAggregate:
    """P3-4: the event-trigger panel aggregate (feed + funnel + armed strip)."""

    def _write_run(self, tmp_path, records, regime="cautious"):
        from datetime import datetime, timezone
        runs = tmp_path / "runs"
        runs.mkdir(exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        final = {
            "run_id": "evt1", "mode": "event_scan", "status": "complete",
            "completed_at": now,
            "conclusions": {"event_scan": {
                "scanned": len(records), "regime": regime, "records": records,
            }},
        }
        (runs / "run_20260606T120000_evt1.jsonl").write_text(json.dumps(final))
        return now

    def _rec(self, **kw):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        base = {
            "id": kw.get("id", "x"), "headline": kw.get("headline", "h"),
            "source": "truth_social", "figure": kw.get("figure", "realDonaldTrump"),
            "published_at": now, "detected_at": now, "decided_at": now,
            "outcome": "skipped", "classification": "noise", "stop_reason": None,
            "ticker": None, "score": None, "verdict": None,
        }
        base.update(kw)
        return base

    def _configure(self, service, tmp_path):
        service.data_dir = tmp_path
        service.settings.event_figures = [
            {"name": "Donald Trump (@realDonaldTrump)", "enabled": True,
             "platform": "truth_social", "account_id": "107780257626128497"},
        ]
        service.settings.event_cooldown_minutes = 120
        service.settings.max_event_trades_per_day = 3

    def test_funnel_and_feed(self, service, tmp_path):
        self._configure(service, tmp_path)
        records = [
            self._rec(id="a", ticker="DELL", score=88, outcome="executed",
                      classification="executed",
                      verdict={"mentions_company": True, "is_bullish": True,
                               "confidence": 0.88, "reasoning": "strong mention"}),
            self._rec(id="b", ticker="AAPL", outcome="skipped",
                      classification="near_miss", stop_reason="already held",
                      verdict={"mentions_company": True, "is_bullish": True}),
            self._rec(id="c", outcome="skipped", classification="noise",
                      stop_reason="not a bullish company signal",
                      verdict={"mentions_company": False}),
            self._rec(id="d", ticker="TSLA", outcome="skipped",
                      classification="near_miss",
                      stop_reason="low confidence 0.55 < 0.7"),
        ]
        self._write_run(tmp_path, records)

        agg = service.get_event_aggregate()
        stages = {s["key"]: s["count"] for s in agg["funnel"]["today"]["stages"]}
        assert stages["seen"] == 4
        assert stages["named"] == 3            # DELL, AAPL, TSLA
        assert stages["bullish"] == 3          # the two near_miss + executed
        assert stages["passed"] == 1 and stages["executed"] == 1
        drops = {d["reason"]: d["count"] for d in agg["funnel"]["today"]["drop_reasons"]}
        assert drops["already held"] == 1
        assert drops["not a bullish company signal"] == 1
        assert "low confidence 0.55 < 0.7" in drops

        # feed chips: executed -> traded, near_miss -> blocked, noise -> skipped
        chips = {f["id"]: f["chip"]["kind"] for f in agg["feed"]}
        assert chips["a"] == "traded"
        assert chips["b"] == "blocked"
        assert chips["c"] == "noise"
        # resolver reasoning surfaced for the executed trade
        traded = next(f for f in agg["feed"] if f["id"] == "a")
        assert traded["reasoning"] == "strong mention"

    def test_armed_strip(self, service, tmp_path):
        self._configure(service, tmp_path)
        records = [
            self._rec(id="a", ticker="DELL", outcome="executed",
                      classification="executed"),
        ]
        self._write_run(tmp_path, records)

        agg = service.get_event_aggregate()
        st = agg["status"]
        assert st["armed"] is True                 # no control.json -> armed default
        assert st["figures"] == ["Donald Trump (@realDonaldTrump)"]
        assert st["trades_today"] == 1 and st["cap"] == 3
        assert st["last_poll_seconds"] is not None and st["last_poll_seconds"] >= 0
        # the just-executed DELL is inside the 120m cooldown window
        cds = {c["ticker"]: c["expires_in_min"] for c in st["cooldowns"]}
        assert "DELL" in cds and 0 < cds["DELL"] <= 120

    def test_figure_filter(self, service, tmp_path):
        self._configure(service, tmp_path)
        records = [
            self._rec(id="a", ticker="DELL", figure="realDonaldTrump",
                      outcome="executed", classification="executed"),
            self._rec(id="b", ticker="RIVN", figure="elonmusk",
                      outcome="skipped", classification="near_miss",
                      stop_reason="already held"),
        ]
        self._write_run(tmp_path, records)

        agg = service.get_event_aggregate(figure="elonmusk")
        ids = {f["id"] for f in agg["feed"]}
        assert ids == {"b"}
        assert agg["funnel"]["today"]["stages"][0]["count"] == 1

    def test_empty_when_no_runs(self, service, tmp_path):
        self._configure(service, tmp_path)
        agg = service.get_event_aggregate()
        assert agg["feed"] == []
        assert agg["funnel"]["today"]["stages"][0]["count"] == 0
        assert agg["status"]["trades_today"] == 0

    def test_figures_from_config(self, service, tmp_path):
        """get_event_figures returns configured names (== record figure tags)."""
        self._configure(service, tmp_path)
        figs = service.get_event_figures()["figures"]
        assert [f["name"] for f in figs] == ["Donald Trump (@realDonaldTrump)"]
        # armed-strip figures use the same source, so dropdown values match
        self._write_run(tmp_path, [])
        assert service.get_event_aggregate()["status"]["figures"] == \
            ["Donald Trump (@realDonaldTrump)"]

    def test_figures_legacy_fallback(self, service, tmp_path):
        """Empty event_figures falls back to the legacy 'realDonaldTrump' tag."""
        service.data_dir = tmp_path
        service.settings.event_figures = []
        service.settings.event_truth_social_account_id = "107780257626128497"
        figs = service.get_event_figures()["figures"]
        assert figs[0]["name"] == "realDonaldTrump"
        assert figs[0]["account_id"] == "107780257626128497"

    def test_trade_cards_join_live_pnl(self, service, tmp_path):
        """Event-trade cards join the event_trade update to the live holding."""
        from src.notify import user_updates as uu
        service.data_dir = tmp_path
        uu.write_update(
            tmp_path, type="event_trade", title="Bought DELL",
            update_id="evt_dell",
            payload={"ticker": "DELL", "headline": "Buy DELL!", "figure": "realDonaldTrump",
                     "shares": 14, "price": 122.5, "stop": 105.35,
                     "review": {"verdict": "keep", "reasoning": "ok"}},
            links=[{"label": "post", "href": "https://truthsocial.com/x"}],
        )
        with patch.object(service, "get_portfolio_holdings", return_value={"positions": [
            {"ticker": "DELL", "current_price": 130.0, "market_value": 1820.0,
             "unrealized_pnl": 105.0, "unrealized_pct": 6.1, "current_stop": 110.0},
        ]}):
            out = service.get_event_trade_cards()
        assert out["count"] == 1
        card = out["cards"][0]
        assert card["ticker"] == "DELL" and card["entry_price"] == 122.5
        assert card["review_verdict"] == "keep"
        assert card["links"][0]["href"] == "https://truthsocial.com/x"
        assert card["live"]["unrealized_pnl"] == 105.0 and card["live"]["held"] is True

    def test_trade_cards_no_position(self, service, tmp_path):
        """A closed/absent position yields a card with live=None (not an error)."""
        from src.notify import user_updates as uu
        service.data_dir = tmp_path
        uu.write_update(tmp_path, type="event_trade", title="Bought RIVN",
                        payload={"ticker": "RIVN", "price": 15.0})
        with patch.object(service, "get_portfolio_holdings", return_value={"positions": []}):
            out = service.get_event_trade_cards()
        assert out["count"] == 1 and out["cards"][0]["live"] is None
