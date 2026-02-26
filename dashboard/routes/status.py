import subprocess
from datetime import datetime
from flask import Blueprint, render_template
from dashboard.db import DashboardDB

bp = Blueprint("status", __name__)

SCHEDULE = {
    "pre_market": "08:30 ET",
    "monitor": "12:00 ET",
    "post_market": "16:30 ET",
}


@bp.route("/status")
def current():
    db = DashboardDB()
    running = db.get_running_run()
    latest = db.get_latest_run()
    recent_runs = db.get_runs(limit=10)

    # Count by status
    total = db.get_run_count()
    successes = db.get_run_count(status="success")
    failures = db.get_run_count(status="failed")

    # Check cron status
    cron_ok = _check_cron()

    return render_template(
        "status/current.html",
        running=running,
        latest=latest,
        recent_runs=recent_runs,
        schedule=SCHEDULE,
        total=total,
        successes=successes,
        failures=failures,
        cron_ok=cron_ok,
        now=datetime.utcnow().isoformat(),
    )


def _check_cron():
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        return "run.sh" in result.stdout
    except Exception:
        return False
