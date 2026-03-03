from flask import Blueprint, jsonify, render_template
from dashboard.db import DashboardDB
from dashboard.utils import load_heartbeat

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/status")
def status():
    db = DashboardDB()
    running = db.get_running_run()
    latest = db.get_latest_run()
    regime = db.get_latest_regime()
    heartbeat = load_heartbeat()

    return jsonify({
        "running": running,
        "latest": latest,
        "regime": {
            "name": regime["regime_name"] if regime else None,
            "vix": regime["vix_level"] if regime else None,
        } if regime else None,
        "heartbeat": {
            "overall": heartbeat.get("overall"),
            "timestamp": heartbeat.get("timestamp"),
            "positions": heartbeat.get("checks", {}).get("positions", {}).get("broker_count"),
            "stops": heartbeat.get("checks", {}).get("stops", {}).get("active_stops"),
            "equity": heartbeat.get("checks", {}).get("api", {}).get("equity"),
        } if heartbeat else None,
    })


@bp.route("/partials/run-status")
def partial_run_status():
    db = DashboardDB()
    running = db.get_running_run()
    latest = db.get_latest_run()
    return render_template("partials/run_status.html", running=running, latest=latest)


@bp.route("/partials/latest-run")
def partial_latest_run():
    db = DashboardDB()
    latest = db.get_latest_run()
    regime = db.get_latest_regime()
    recent_decisions = db.get_decisions(limit=10)
    return render_template(
        "partials/latest_run.html",
        latest_run=latest,
        regime=regime,
        recent_decisions=recent_decisions,
    )
