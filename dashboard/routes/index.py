from flask import Blueprint, render_template
from dashboard.db import DashboardDB

bp = Blueprint("index", __name__)


@bp.route("/")
def overview():
    db = DashboardDB()
    latest_run = db.get_latest_run()
    running = db.get_running_run()
    regime = db.get_latest_regime()
    recent_decisions = db.get_decisions(limit=5)
    recent_runs = db.get_runs(limit=5)
    total_runs = db.get_run_count()
    success_runs = db.get_run_count(status="success")
    failed_runs = db.get_run_count(status="failed")

    return render_template(
        "index.html",
        latest_run=latest_run,
        running=running,
        regime=regime,
        recent_decisions=recent_decisions,
        recent_runs=recent_runs,
        total_runs=total_runs,
        success_runs=success_runs,
        failed_runs=failed_runs,
    )
