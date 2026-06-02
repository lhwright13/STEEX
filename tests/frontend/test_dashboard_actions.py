"""
Unit tests for dashboard action endpoints.

Tests:
- Pipeline cancel endpoint (POST)
- Learning apply and run endpoints (POST)
- Agent last-output endpoint (GET)
- Response format and error handling
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
# Test: Pipeline Cancel Endpoint
# ========================================================================


class TestCancelPipelineEndpoint:
    """Test pipeline cancel POST endpoint."""

    def test_cancel_returns_json(self, client):
        """Cancel endpoint should return JSON response."""
        response = client.post("/api/v1/pipeline/cancel")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "status" in data
        # Cancel now triggers the kill switch (disarm trading).
        assert data["status"] == "disarmed"

    def test_cancel_has_message(self, client):
        """Cancel response should include message."""
        response = client.post("/api/v1/pipeline/cancel")

        data = json.loads(response.data)
        assert "message" in data
        assert "disarm" in data["message"].lower()

    def test_cancel_method_post_only(self, client):
        """Cancel endpoint should only accept POST."""
        response = client.get("/api/v1/pipeline/cancel")
        assert response.status_code == 405  # Method not allowed


# ========================================================================
# Test: Learning Apply Endpoint
# ========================================================================


class TestLearningApplyEndpoint:
    """Test learning apply POST endpoint."""

    def test_apply_returns_json(self, client):
        """Apply endpoint should return JSON response."""
        response = client.post("/api/v1/learning/apply")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "status" in data

    def test_apply_returns_message(self, client):
        """Apply response should include message."""
        response = client.post("/api/v1/learning/apply")

        data = json.loads(response.data)
        assert "message" in data
        assert "Evolution" in data["message"] or "evolution" in data["message"].lower()

    def test_apply_method_post_only(self, client):
        """Apply endpoint should only accept POST."""
        response = client.get("/api/v1/learning/apply")
        assert response.status_code == 405


# ========================================================================
# Test: Learning Run Endpoint
# ========================================================================


class TestLearningRunEndpoint:
    """Test learning run POST endpoint."""

    def test_run_returns_json(self, client):
        """Run endpoint should return JSON response."""
        response = client.post("/api/v1/learning/run")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "status" in data

    def test_run_returns_command(self, client):
        """Run response should include command field."""
        response = client.post("/api/v1/learning/run")

        data = json.loads(response.data)
        assert "command" in data
        assert "python" in data["command"].lower()
        assert "learning" in data["command"].lower()

    def test_run_method_post_only(self, client):
        """Run endpoint should only accept POST."""
        response = client.get("/api/v1/learning/run")
        assert response.status_code == 405


# ========================================================================
# Test: Agent Last-Output Endpoint
# ========================================================================


class TestAgentLastOutputEndpoint:
    """Test agent last-output GET endpoint."""

    @patch("frontend.app.get_dashboard_service")
    def test_last_output_returns_json(self, mock_get_service, client):
        """Last-output endpoint should return JSON."""
        mock_service = Mock()
        mock_service.get_agent_last_output.return_value = {
            "agent": "data",
            "output": {"status": "complete"},
            "timestamp": "2026-05-24T14:30:00Z",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/agent/data/last-output")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["agent"] == "data"
        assert "output" in data

    @patch("frontend.app.get_dashboard_service")
    def test_last_output_no_run_data(self, mock_get_service, client):
        """Last-output should return gracefully when no data."""
        mock_service = Mock()
        mock_service.get_agent_last_output.return_value = {
            "agent": "data",
            "output": None,
            "message": "No run data available",
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/agent/data/last-output")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["output"] is None
        assert "message" in data

    @patch("frontend.app.get_dashboard_service")
    def test_last_output_handles_error(self, mock_get_service, client):
        """Last-output should handle service errors."""
        mock_service = Mock()
        mock_service.get_agent_last_output.side_effect = Exception("Service error")
        mock_get_service.return_value = mock_service

        response = client.get("/api/v1/system/agent/data/last-output")

        assert response.status_code == 500
        data = json.loads(response.data)
        assert "error" in data


# ========================================================================
# Test: JSON Response Format
# ========================================================================


class TestScheduleActionEndpoints:
    """Test schedule control endpoints."""

    def test_pause_schedules_returns_json(self, client):
        """Pause schedules endpoint should return JSON."""
        response = client.post("/api/v1/system/schedules/pause")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "status" in data
        assert "message" in data

    def test_pause_schedules_status(self, client):
        """Pause schedules should return paused status."""
        response = client.post("/api/v1/system/schedules/pause")

        data = json.loads(response.data)
        # Pause now disarms trading via the kill switch.
        assert data["status"] == "disarmed"

    def test_run_schedule_returns_json(self, client):
        """Run schedule endpoint should return JSON."""
        response = client.post("/api/v1/system/schedules/screen/run")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "status" in data
        assert "message" in data

    def test_run_schedule_includes_schedule_name(self, client):
        """Run schedule message should include schedule name."""
        response = client.post("/api/v1/system/schedules/monitor/run")

        data = json.loads(response.data)
        assert "monitor" in data["message"].lower()

    def test_run_schedule_method_post_only(self, client):
        """Run schedule endpoint should only accept POST."""
        response = client.get("/api/v1/system/schedules/screen/run")
        assert response.status_code == 405


class TestActionEndpointResponses:
    """Test that action endpoints return valid JSON."""

    @patch("frontend.app.get_dashboard_service")
    def test_all_action_endpoints_return_valid_json(self, mock_get_service, client):
        """All action endpoints should return valid JSON."""
        mock_service = Mock()
        mock_service.get_agent_last_output.return_value = {"agent": "data"}
        mock_service.set_controls.return_value = {"trading_armed": False, "event_armed": True}
        mock_get_service.return_value = mock_service

        endpoints = [
            ("POST", "/api/v1/pipeline/cancel"),
            ("POST", "/api/v1/learning/apply"),
            ("POST", "/api/v1/learning/run"),
            ("GET", "/api/v1/system/agent/data/last-output"),
            ("POST", "/api/v1/system/schedules/pause"),
            ("POST", "/api/v1/system/schedules/screen/run"),
        ]

        for method, endpoint in endpoints:
            if method == "POST":
                response = client.post(endpoint)
            else:
                response = client.get(endpoint)

            assert response.status_code == 200
            try:
                json.loads(response.data)
            except json.JSONDecodeError:
                pytest.fail(f"Endpoint {endpoint} did not return valid JSON")
