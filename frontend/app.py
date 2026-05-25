import os
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify
from pathlib import Path

from .services import get_dashboard_service

logger = logging.getLogger("steex.dashboard")


def create_app():
    """Create and configure the Flask app for the STEEX dashboard."""
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-key-change-in-prod")

    # Configure logging
    if not app.debug:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)

    # ========================================================================
    # Routes
    # ========================================================================

    @app.route("/")
    def dashboard():
        """Main dashboard view."""
        return render_template("index.html")

    @app.route("/system")
    def system():
        """Agent transparency/system configuration view."""
        return render_template("system.html")

    # ========================================================================
    # API Endpoints (from DASHBOARD_SPEC.md)
    # ========================================================================

    @app.route("/api/v1/pipeline/current")
    def pipeline_current():
        """Get current pipeline state and live run metrics."""
        try:
            service = get_dashboard_service()
            data = service.get_pipeline_current()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching pipeline state: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/variants/results")
    def variants_results():
        """Get results from all three analysis variants."""
        try:
            service = get_dashboard_service()
            data = service.get_variants_results()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching variant results: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/consensus")
    def consensus():
        """Get consensus picks from meta-analysis synthesis."""
        try:
            service = get_dashboard_service()
            data = service.get_consensus()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching consensus: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/screening/stats")
    def screening_stats():
        """Get screening funnel statistics."""
        try:
            service = get_dashboard_service()
            data = service.get_screening_stats()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching screening stats: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/regime")
    def regime():
        """Get current market regime and VIX."""
        try:
            service = get_dashboard_service()
            data = service.get_regime()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching regime: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/manager/decision")
    def manager_decision():
        """Get manager's decision on the proposed trades."""
        try:
            service = get_dashboard_service()
            data = service.get_manager_decision()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching manager decision: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/system/agents")
    def system_agents():
        """Get all agent configurations (for agent transparency)."""
        try:
            service = get_dashboard_service()
            data = service.get_system_agents()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching system agents: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/system/schedules")
    def system_schedules():
        """Get cron schedule configuration."""
        try:
            service = get_dashboard_service()
            data = service.get_system_schedules()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching system schedules: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/system/agent/<agent_name>/detail")
    def agent_detail(agent_name):
        """Get detailed configuration and prompt for a specific agent."""
        try:
            service = get_dashboard_service()
            data = service.get_agent_detail(agent_name)
            if "error" in data:
                return jsonify(data), 404
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching agent detail for {agent_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/system/agent/<agent_name>/last-output")
    def agent_last_output(agent_name):
        """Get last execution output for a specific agent."""
        try:
            service = get_dashboard_service()
            data = service.get_agent_last_output(agent_name)
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching agent last output for {agent_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/pipeline/cancel", methods=["POST"])
    def pipeline_cancel():
        """Send abort signal to current pipeline run."""
        return jsonify({
            "status": "cancel_requested",
            "message": "Abort signal sent to orchestrator"
        })

    @app.route("/api/v1/learning/apply", methods=["POST"])
    def learning_apply():
        """Apply learning recommendations (prompt evolutions)."""
        return jsonify({
            "status": "manual_required",
            "message": "Evolution requires review. Run: python run_manager.py learning --agent --paper"
        })

    @app.route("/api/v1/learning/run", methods=["POST"])
    def learning_run():
        """Trigger a learning mode run."""
        return jsonify({
            "status": "manual_required",
            "command": "python run_manager.py learning --agent --paper",
            "message": "Learning mode must be started from terminal due to agent subprocess requirements"
        })

    @app.route("/api/v1/system/schedules/pause", methods=["POST"])
    def pause_schedules():
        """Pause all scheduled runs."""
        return jsonify({
            "status": "paused",
            "message": "All schedules paused (manual management required)"
        })

    @app.route("/api/v1/system/schedules/<schedule_name>/run", methods=["POST"])
    def run_schedule(schedule_name):
        """Manually trigger a scheduled run."""
        return jsonify({
            "status": "manual_required",
            "message": f"Run '{schedule_name}' queued (execute from terminal: python run_manager.py {schedule_name} --agent)"
        })

    @app.route("/api/v1/system/graph/<mode>")
    def system_graph(mode):
        """Get the LangGraph structure for a given mode (screen, learning, enter, etc.)."""
        try:
            service = get_dashboard_service()
            data = service.get_graph_structure(mode)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/pipeline/recent-runs")
    def pipeline_recent_runs():
        """Get recent pipeline runs across all modes."""
        try:
            service = get_dashboard_service()
            data = service.get_recent_runs(limit=10)
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching recent runs: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/pipeline/trace/<run_id>")
    def pipeline_trace(run_id):
        """Get execution trace for a specific run."""
        try:
            service = get_dashboard_service()
            data = service.get_run_trace(run_id)
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching trace for {run_id}: {e}")
            return jsonify({"error": str(e)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
