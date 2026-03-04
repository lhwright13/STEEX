import subprocess
from datetime import datetime
from pathlib import Path

from flask import Blueprint, render_template
from dashboard.db import DashboardDB
from dashboard.utils import load_heartbeat

bp = Blueprint("status", __name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SCHEDULER_CONFIG = PROJECT_DIR / "scheduler" / "config.yaml"


def _load_schedule():
    """Read the schedule from scheduler/config.yaml."""
    try:
        import yaml
        with open(SCHEDULER_CONFIG) as f:
            cfg = yaml.safe_load(f)
        modes = cfg.get("modes", {})
        schedule = {}
        for key, val in modes.items():
            if not val.get("enabled"):
                continue
            cron = val.get("schedule", "")
            manager_mode = val.get("mode_name", key)
            schedule[key] = {
                "cron": cron,
                "time": _cron_to_human(cron),
                "manager_mode": manager_mode,
                "enabled": True,
            }
        return schedule
    except Exception:
        return {}


def _cron_to_human(cron):
    """Convert a cron expression to a human-readable time string.

    Cron times are in system local time (PST). Convert to ET for display
    since trading hours are conventionally shown in ET.
    PST = ET - 3 hours.
    """
    try:
        parts = cron.split()
        minute = int(parts[0])
        hour = int(parts[1])
        dow = parts[4] if len(parts) > 4 else "*"
        # Convert PST to ET (+3 hours)
        et_hour = hour + 3
        if et_hour >= 24:
            et_hour -= 24
        period = "AM" if et_hour < 12 else "PM"
        display_hour = et_hour if et_hour <= 12 else et_hour - 12
        if display_hour == 0:
            display_hour = 12
        time_str = f"{display_hour}:{minute:02d} {period} ET"
        day_map = {
            "1-5": "weekdays",
            "0": "Sun",
            "5": "Fri",
            "6": "Sat",
            "*": "daily",
        }
        days = day_map.get(dow, dow)
        return f"{time_str}, {days}"
    except (ValueError, IndexError):
        return cron


@bp.route("/status")
def current():
    db = DashboardDB()
    running = db.get_running_run()
    latest = db.get_latest_run()
    recent_runs = db.get_runs(limit=10)

    total = db.get_run_count()
    successes = db.get_run_count(status="success")
    failures = db.get_run_count(status="failed")

    cron_ok = _check_cron()
    schedule = _load_schedule()
    heartbeat = load_heartbeat()

    return render_template(
        "status/current.html",
        running=running,
        latest=latest,
        recent_runs=recent_runs,
        schedule=schedule,
        total=total,
        successes=successes,
        failures=failures,
        cron_ok=cron_ok,
        heartbeat=heartbeat,
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
