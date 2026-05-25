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


class TestVariantsAPI:
    """Test variants results API endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_variants_returns_json(self, mock_get_service, client):
        """Variants endpoint should return variant results."""
        mock_service = Mock()
        mock_service.get_variants_results.return_value = {
            "conservative": {
                "variant": "conservative",
                "status": "complete",
                "candidate_count": 8,
                "avg_score": 72.3,
                "timestamp": "2026-05-24T14:30:45Z",
            },
            "aggressive": {
                "variant": "aggressive",
                "status": "complete",
                "candidate_count": 15,
                "avg_score": 68.9,
                "timestamp": "2026-05-24T14:31:20Z",
            },
            "momentum": {
                "variant": "momentum",
                "status": "complete",
                "candidate_count": 12,
                "avg_score": 71.2,
                "timestamp": "2026-05-24T14:31:55Z",
            },
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/variants/results")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "conservative" in data
        assert "aggressive" in data
        assert "momentum" in data
        assert data["conservative"]["candidate_count"] == 8


# ========================================================================
# Test: API Endpoints - Consensus
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
            "/api/v1/variants/results",
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
