"""
Unit tests for Flask dashboard application.

Tests:
- All API endpoints return correct JSON structure
- Error handling (500 errors, missing data)
- Route accessibility (200 status codes)
- CORS and header handling
"""

import pytest
import json
from unittest.mock import patch, Mock

from frontend.app import create_app


@pytest.fixture
def app():
    """Create test Flask app."""
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


# ========================================================================
# Test: HTML Routes
# ========================================================================


class TestHTMLRoutes:
    """Test main HTML page routes."""

    def test_dashboard_route_returns_200(self, client):
        """Dashboard route should return 200."""
        response = client.get("/")
        assert response.status_code == 200
        assert b"STEEX" in response.data

    def test_system_route_returns_200(self, client):
        """System transparency route should return 200."""
        response = client.get("/system")
        assert response.status_code == 200
        assert b"System" in response.data or b"STEEX" in response.data


# ========================================================================
# Test: API Endpoints - Pipeline
# ========================================================================


class TestPipelineAPI:
    """Test pipeline API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_pipeline_current_returns_json(self, mock_get_service, client):
        """Pipeline endpoint should return valid JSON."""
        mock_service = Mock()
        mock_service.get_pipeline_current.return_value = {
            "status": "running",
            "mode": "screen",
            "stage": "analysis",
            "elapsed": 45,
            "stage_progress": 0.65,
            "current_agent": "analysis_aggressive",
            "run_id": "run_123",
            "timestamp": "2026-05-24T14:32:15Z",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/pipeline/current")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "running"
        assert data["stage"] == "analysis"
        assert "timestamp" in data

    @patch("frontend.app.get_dashboard_service")
    def test_pipeline_current_handles_error(self, mock_get_service, client):
        """Pipeline endpoint should handle errors gracefully."""
        mock_service = Mock()
        mock_service.get_pipeline_current.side_effect = Exception("Database error")
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/pipeline/current")

        assert response.status_code == 500
        data = json.loads(response.data)
        assert "error" in data


# ========================================================================
# Test: API Endpoints - Variants
# ========================================================================



class TestConsensusAPI:
    """Test consensus picks API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_consensus_returns_high_and_medium_conviction(self, mock_get_service, client):
        """Consensus endpoint should return pick categories."""
        mock_service = Mock()
        mock_service.get_consensus.return_value = {
            "high_conviction": [
                {"ticker": "NVDA", "score": 76.8, "variants_agreeing": 3},
            ],
            "consensus": [
                {"ticker": "AAPL", "score": 71.5, "variants_agreeing": 2},
            ],
            "speculative_excluded": ["AMD", "META"],
            "timestamp": "2026-05-24T14:31:55Z",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/consensus")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "high_conviction" in data
        assert "consensus" in data
        assert "speculative_excluded" in data
        assert len(data["high_conviction"]) == 1
        assert data["high_conviction"][0]["ticker"] == "NVDA"


# ========================================================================
# Test: API Endpoints - Screening
# ========================================================================


class TestScreeningAPI:
    """Test screening stats API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_screening_returns_funnel_stats(self, mock_get_service, client):
        """Screening endpoint should return funnel statistics."""
        mock_service = Mock()
        mock_service.get_screening_stats.return_value = {
            "universe": 7500,
            "passed_volume": 450,
            "passed_sentiment": 380,
            "passed_technical": 285,
            "passed_insider": 180,
            "final_screened": 145,
            "final_picked": 35,
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/screening/stats")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["universe"] == 7500
        assert data["final_picked"] == 35


# ========================================================================
# Test: API Endpoints - Regime
# ========================================================================


class TestRegimeAPI:
    """Test market regime API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_regime_returns_current_and_probabilities(self, mock_get_service, client):
        """Regime endpoint should return current regime and probabilities."""
        mock_service = Mock()
        mock_service.get_regime.return_value = {
            "current": "cautious",
            "vix": 16.8,
            "trend": "up",
            "change": 1.2,
            "timestamp": "2026-05-24T14:30:00Z",
            "regimes": {
                "risk_on": {"probability": 0.15},
                "cautious": {"probability": 0.60},
                "risk_off": {"probability": 0.20},
                "crisis": {"probability": 0.05},
            },
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/regime")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["current"] == "cautious"
        assert data["vix"] == 16.8
        assert "regimes" in data


# ========================================================================
# Test: API Endpoints - Manager Decision
# ========================================================================


class TestManagerDecisionAPI:
    """Test manager decision API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_manager_decision_returns_approval_status(self, mock_get_service, client):
        """Manager decision endpoint should return approval/rejection."""
        mock_service = Mock()
        mock_service.get_manager_decision.return_value = {
            "status": "approved",
            "reasoning": "Strong consensus picks",
            "adjustments": {"NVDA": 0.045, "MSFT": 0.042},
            "timestamp": "2026-05-24T14:31:58Z",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/manager/decision")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "approved"
        assert "reasoning" in data


# ========================================================================
# Test: API Endpoints - System Agents
# ========================================================================


class TestSystemAgentsAPI:
    """Test system agents API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_system_agents_returns_list(self, mock_get_service, client):
        """System agents endpoint should return list of agents."""
        mock_service = Mock()
        mock_service.get_system_agents.return_value = {
            "agents": [
                {
                    "name": "data",
                    "role": "DataAgent",
                    "status": "ready",
                    "max_turns": 8,
                    "needs_tools": True,
                    "external_servers": ["alpaca"],
                    "prompt_id": "data_collection",
                    "critical": True,
                },
                {
                    "name": "analysis_conservative",
                    "role": "AnalysisVariant",
                    "status": "ready",
                    "max_turns": 15,
                    "needs_tools": True,
                    "external_servers": ["alpaca", "alphavantage"],
                    "prompt_id": "analysis_conservative",
                    "critical": False,
                },
            ]
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/agents")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "agents" in data
        assert len(data["agents"]) == 2


# ========================================================================
# Test: API Endpoints - System Health (WP8)
# ========================================================================


class TestSystemHealthAPI:
    """Test system health API endpoint (integrity + quarantine)."""

    @patch("frontend.app.get_dashboard_service")
    def test_system_health_returns_json(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_system_health.return_value = {
            "integrity": {"status": "OK", "violations": [], "error": None},
            "overall": "OK",
            "heartbeat_at": "2026-07-09T20:00:00",
            "quarantine": {"count": 1, "rows": [
                {"ticker": "BK", "exit_date": "2026-05-21", "reason": "no matching filled broker sell order"},
            ]},
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/health")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["integrity"]["status"] == "OK"
        assert data["quarantine"]["count"] == 1
        assert data["quarantine"]["rows"][0]["ticker"] == "BK"

    @patch("frontend.app.get_dashboard_service")
    def test_system_health_error_returns_500(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_system_health.side_effect = Exception("boom")
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/health")

        assert response.status_code == 500
        assert "error" in json.loads(response.data)


# ========================================================================
# Test: API Endpoints - System Schedules
# ========================================================================


class TestSystemSchedulesAPI:
    """Test system schedules API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_system_schedules_returns_list(self, mock_get_service, client):
        """System schedules endpoint should return list of schedules."""
        mock_service = Mock()
        mock_service.get_system_schedules.return_value = {
            "schedules": [
                {
                    "name": "screen_morning",
                    "mode": "screen",
                    "cron": "0 9 * * 1-5",
                    "description": "Weekday screening at 9 AM",
                    "next_run": "2026-05-27T09:00:00Z",
                    "enabled": True,
                },
                {
                    "name": "risk_monitor",
                    "mode": "monitor",
                    "cron": "*/30 * * * *",
                    "description": "Risk check every 30 min",
                    "next_run": "2026-05-24T14:30:00Z",
                    "enabled": True,
                },
            ]
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/schedules")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "schedules" in data
        assert len(data["schedules"]) == 2


# ========================================================================
# Test: API Endpoints - Agent Detail
# ========================================================================


class TestAgentDetailAPI:
    """Test agent detail API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_agent_detail_returns_config(self, mock_get_service, client):
        """Agent detail endpoint should return configuration."""
        mock_service = Mock()
        mock_service.get_agent_detail.return_value = {
            "name": "data",
            "role": "DataAgent",
            "status": "ready",
            "preprompt": "You are the data collection agent...",
            "tools": [
                {"name": "get_current_data", "description": "Fetch data"},
            ],
            "external_servers": ["alpaca"],
            "last_run": "2026-05-24T14:15:30Z",
            "success_rate": 0.98,
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/agent/data/detail")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["name"] == "data"
        assert "tools" in data

    @patch("frontend.app.get_dashboard_service")
    def test_agent_detail_returns_404_for_unknown_agent(self, mock_get_service, client):
        """Agent detail should return 404 for unknown agent."""
        mock_service = Mock()
        mock_service.get_agent_detail.return_value = {"error": "Agent not found"}
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/agent/unknown/detail")

        assert response.status_code == 404


class TestNaNSafeJSON:
    """A NaN/Infinity in any response must serialize to null (valid JSON), so a
    price-feed outage can never break a dashboard widget."""

    @patch("frontend.app.get_dashboard_service")
    def test_nan_becomes_null(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_portfolio_holdings.return_value = {
            "positions": [{"ticker": "X", "current_price": float("nan"),
                           "market_value": float("inf")}],
            "summary": {"equity": float("nan")},
        }
        mock_get_service.return_value = mock_service
        r = client.get("/api/v1/portfolio/holdings")
        assert r.status_code == 200
        assert b"NaN" not in r.data and b"Infinity" not in r.data
        data = json.loads(r.data)  # would raise if NaN leaked
        assert data["positions"][0]["current_price"] is None
        assert data["positions"][0]["market_value"] is None
        assert data["summary"]["equity"] is None


class TestSignalHealthAPI:
    """P4-4 signal-health endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_signal_health_returns_json(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_signal_health.return_value = {
            "available": True, "signals": [{"signal": "momentum_score"}],
            "overall_recent_win_rate": 0.6,
        }
        mock_get_service.return_value = mock_service
        r = client.get("/api/v1/signals/health")
        assert r.status_code == 200
        assert json.loads(r.data)["signals"][0]["signal"] == "momentum_score"


class TestEventAggregateAPI:
    """Test the P3-4 event-trigger panel endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_aggregate_returns_views(self, mock_get_service, client):
        """Aggregate endpoint returns feed/funnel/status and forwards params."""
        mock_service = Mock()
        mock_service.get_event_aggregate.return_value = {
            "feed": [], "funnel": {"today": {"stages": []}}, "status": {"armed": True},
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/events/aggregate?figure=elonmusk&limit=10")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "feed" in data and "funnel" in data and "status" in data
        _, kwargs = mock_service.get_event_aggregate.call_args
        assert kwargs["figure"] == "elonmusk" and kwargs["limit"] == 10

    @patch("frontend.app.get_dashboard_service")
    def test_trade_cards_endpoint(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_event_trade_cards.return_value = {
            "cards": [{"ticker": "DELL", "live": {"unrealized_pnl": 105.0}}], "count": 1,
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/events/trade-cards?limit=20")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["cards"][0]["ticker"] == "DELL"
        _, kwargs = mock_service.get_event_trade_cards.call_args
        assert kwargs["limit"] == 20

    @patch("frontend.app.get_dashboard_service")
    def test_figures_endpoint(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_event_figures.return_value = {
            "figures": [{"name": "realDonaldTrump", "enabled": True}]
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/events/figures")
        assert response.status_code == 200
        assert json.loads(response.data)["figures"][0]["name"] == "realDonaldTrump"

    @patch("frontend.app.get_dashboard_service")
    def test_aggregate_handles_error(self, mock_get_service, client):
        mock_service = Mock()
        mock_service.get_event_aggregate.side_effect = Exception("boom")
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/events/aggregate")
        assert response.status_code == 500
        assert "error" in json.loads(response.data)


class TestUserUpdatesAPI:
    """Test the Today's Events (user_updates) API endpoints (P3-3)."""

    @patch("frontend.app.get_dashboard_service")
    def test_user_updates_returns_feed(self, mock_get_service, client):
        """Feed endpoint returns the user_updates stream newest-first."""
        mock_service = Mock()
        mock_service.get_user_updates.return_value = {
            "updates": [
                {"id": "u2", "type": "event_trade", "title": "Bought DELL",
                 "summary": "…", "ts": "2026-06-06T03:35:49Z"},
            ],
            "count": 1,
            "timestamp": "2026-06-06T04:00:00Z",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/user_updates?limit=50")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["count"] == 1
        assert data["updates"][0]["title"] == "Bought DELL"
        # query params parsed through to the service
        _, kwargs = mock_service.get_user_updates.call_args
        assert kwargs["limit"] == 50

    @patch("frontend.app.get_dashboard_service")
    def test_user_updates_parses_types_and_day(self, mock_get_service, client):
        """types/day query params are forwarded to the reader."""
        mock_service = Mock()
        mock_service.get_user_updates.return_value = {"updates": [], "count": 0}
        mock_get_service.return_value = mock_service

        client.get("/api/v1/user_updates?types=buy,sell&day=2026-06-06")

        _, kwargs = mock_service.get_user_updates.call_args
        assert kwargs["types"] == ["buy", "sell"]
        assert kwargs["day"] == "2026-06-06"

    @patch("frontend.app.get_dashboard_service")
    def test_user_update_detail_returns_record(self, mock_get_service, client):
        """Detail endpoint returns a single record by id."""
        mock_service = Mock()
        mock_service.get_user_update.return_value = {
            "id": "u2", "type": "event_trade", "title": "Bought DELL",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/user_updates/u2")

        assert response.status_code == 200
        assert json.loads(response.data)["id"] == "u2"

    @patch("frontend.app.get_dashboard_service")
    def test_user_update_detail_404_for_unknown(self, mock_get_service, client):
        """Unknown update id returns 404."""
        mock_service = Mock()
        mock_service.get_user_update.return_value = None
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/user_updates/nope")

        assert response.status_code == 404


# ========================================================================
# Test: JSON Response Format
# ========================================================================


class TestJSONResponseFormat:
    """Test that all API responses are valid JSON."""

    @patch("frontend.app.get_dashboard_service")
    def test_all_endpoints_return_valid_json(self, mock_get_service, client):
        """All endpoints should return valid JSON."""
        endpoints = [
            "/api/v1/pipeline/current",
            "/api/v1/consensus",
            "/api/v1/screening/stats",
            "/api/v1/regime",
            "/api/v1/manager/decision",
            "/api/v1/system/agents",
            "/api/v1/system/schedules",
        ]

        mock_service = Mock()
        mock_service.get_pipeline_current.return_value = {"status": "idle"}
        mock_service.get_variants_results.return_value = {}
        mock_service.get_consensus.return_value = {}
        mock_service.get_screening_stats.return_value = {}
        mock_service.get_regime.return_value = {}
        mock_service.get_manager_decision.return_value = {}
        mock_service.get_system_agents.return_value = {"agents": []}
        mock_service.get_system_schedules.return_value = {"schedules": []}
        mock_get_service.return_value = mock_service

        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 200
            # Should be valid JSON
            try:
                json.loads(response.data)
            except json.JSONDecodeError:
                pytest.fail(f"Endpoint {endpoint} did not return valid JSON")
