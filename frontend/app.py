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
    # API Endpoints
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

    @app.route("/api/v1/portfolio/performance")
    def portfolio_performance():
        """Portfolio equity curve vs S&P 500 with alpha, rebased to %."""
        from flask import request
        period = request.args.get("period", "1M")
        try:
            service = get_dashboard_service()
            data = service.get_portfolio_performance(period)
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching performance: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/events/recent")
    def events_recent():
        """Recent event-trigger trades and review verdicts."""
        try:
            service = get_dashboard_service()
            data = service.get_event_activity(limit=10)
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching event activity: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/events/trade-cards")
    def events_trade_cards():
        """Event-trade cards (P3-6): fired event trades + live P&L + review."""
        from flask import request
        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        day = request.args.get("day")
        try:
            service = get_dashboard_service()
            return jsonify(service.get_event_trade_cards(limit=limit, day=day))
        except Exception as e:
            logger.error(f"Error building event trade cards: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/events/figures")
    def events_figures():
        """Watched figures for the P3-5 dropdown (names match record tags)."""
        try:
            service = get_dashboard_service()
            return jsonify(service.get_event_figures())
        except Exception as e:
            logger.error(f"Error fetching event figures: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/events/aggregate")
    def events_aggregate():
        """Event-trigger panel (P3-4): Watching feed + funnel + armed strip.

        Optional ?figure=<name> filters every view to one figure (P3-5).
        """
        from flask import request
        figure = request.args.get("figure") or None
        try:
            limit = int(request.args.get("limit", 30))
        except (TypeError, ValueError):
            limit = 30
        try:
            service = get_dashboard_service()
            return jsonify(service.get_event_aggregate(figure=figure, limit=limit))
        except Exception as e:
            logger.error(f"Error building event aggregate: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/user_updates")
    def user_updates():
        """Today's Events feed — the user_updates stream (P0-3), newest-first.

        The same records delivered to the user via Telegram back this panel.
        """
        from flask import request
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        types_arg = request.args.get("types")
        types = [t for t in types_arg.split(",") if t] if types_arg else None
        day = request.args.get("day")
        try:
            service = get_dashboard_service()
            return jsonify(service.get_user_updates(limit=limit, types=types, day=day))
        except Exception as e:
            logger.error(f"Error fetching user updates: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/user_updates/<update_id>")
    def user_update_detail(update_id):
        """A single user_update by id — the clickable detail view."""
        try:
            service = get_dashboard_service()
            rec = service.get_user_update(update_id)
            if rec is None:
                return jsonify({"error": "not found"}), 404
            return jsonify(rec)
        except Exception as e:
            logger.error(f"Error fetching user update {update_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/system/workflow-graph")
    @app.route("/api/v1/system/workflow-graph/<mode>")
    def workflow_graph(mode=None):
        """Workflow topology derived from the live compiled LangGraph (P0-4).

        No mode -> every mode's graph; a mode -> just that one.
        """
        try:
            service = get_dashboard_service()
            return jsonify(service.get_workflow_topology(mode))
        except Exception as e:
            logger.error(f"Error building workflow topology: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/portfolio/holdings")
    def portfolio_holdings():
        """Get current open positions and portfolio summary."""
        try:
            service = get_dashboard_service()
            data = service.get_portfolio_holdings()
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error fetching holdings: {e}")
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

    @app.route("/api/v1/control", methods=["GET"])
    def get_control():
        """Current kill-switch state."""
        try:
            return jsonify(get_dashboard_service().get_controls())
        except Exception as e:
            logger.error(f"Error reading controls: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/control", methods=["POST"])
    def set_control():
        """Update kill-switch flags. Body: {trading_armed?: bool, event_armed?: bool}."""
        from flask import request
        try:
            body = request.get_json(silent=True) or {}
            data = get_dashboard_service().set_controls(
                trading_armed=body.get("trading_armed"),
                event_armed=body.get("event_armed"),
            )
            return jsonify(data)
        except Exception as e:
            logger.error(f"Error setting controls: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/trades/history")
    def trades_history():
        """Closed trades and realized-P&L summary."""
        try:
            return jsonify(get_dashboard_service().get_trade_history(limit=50))
        except Exception as e:
            logger.error(f"Error fetching trade history: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/agents/timeline")
    @app.route("/api/v1/agents/timeline/<run_id>")
    def agents_timeline(run_id=None):
        """Per-run multi-agent execution timeline."""
        try:
            return jsonify(get_dashboard_service().get_agent_timeline(run_id))
        except Exception as e:
            logger.error(f"Error fetching agent timeline: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/v1/pipeline/cancel", methods=["POST"])
    def pipeline_cancel():
        """Disarm trading (kill switch) — halts all real entries on next attempt."""
        try:
            data = get_dashboard_service().set_controls(trading_armed=False)
            return jsonify({"status": "disarmed", "controls": data,
                            "message": "Trading DISARMED — no entries will execute."})
        except Exception as e:
            logger.error(f"Error disarming: {e}")
            return jsonify({"error": str(e)}), 500

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
        """Disarm trading via the kill switch (cron keeps running but places no orders)."""
        try:
            data = get_dashboard_service().set_controls(trading_armed=False)
            return jsonify({"status": "disarmed", "controls": data,
                            "message": "Trading disarmed. Scheduled runs still execute but place no orders."})
        except Exception as e:
            logger.error(f"Error pausing: {e}")
            return jsonify({"error": str(e)}), 500

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
