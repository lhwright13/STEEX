import os
import secrets

from flask import Flask
from markupsafe import Markup
from pathlib import Path


def _duration_human(seconds):
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _ts_short(ts):
    """2026-02-25T19:08:08.152645 -> Feb 25, 19:08"""
    if not ts or len(ts) < 16:
        return ts or "-"
    months = [
        "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    try:
        month = months[int(ts[5:7])]
        day = int(ts[8:10])
        time = ts[11:16]
        return f"{month} {day}, {time}"
    except (ValueError, IndexError):
        return ts[:16].replace("T", " ")


def _ts_time(ts):
    """Extract HH:MM:SS from ISO timestamp."""
    if not ts or len(ts) < 19:
        return ts or ""
    return ts[11:19]


def _mode_icon(mode):
    icons = {
        "heartbeat": Markup('<span class="mode-icon" title="Heartbeat">&#9829;</span>'),
        "screen": Markup('<span class="mode-icon" title="Screen">&#9788;</span>'),
        "enter": Markup('<span class="mode-icon" title="Enter">&#9654;</span>'),
        "monitor": Markup('<span class="mode-icon" title="Monitor">&#9673;</span>'),
        "stop_sync": Markup('<span class="mode-icon" title="Stop Sync">&#9632;</span>'),
        "post_market": Markup('<span class="mode-icon" title="Post-market">&#9790;</span>'),
        "learning": Markup('<span class="mode-icon" title="Learning">&#9881;</span>'),
        "pre_market": Markup('<span class="mode-icon" title="Pre-market">&#9788;</span>'),
    }
    return icons.get(mode, "")


def _status_class(status):
    return {"success": "ok", "failed": "warn", "running": "running"}.get(status, "neutral")


def create_app():
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

    app.jinja_env.filters["duration"] = _duration_human
    app.jinja_env.filters["ts"] = _ts_short
    app.jinja_env.filters["ts_time"] = _ts_time
    app.jinja_env.globals["mode_icon"] = _mode_icon
    app.jinja_env.globals["status_class"] = _status_class

    from dashboard.routes.index import bp as index_bp
    from dashboard.routes.runs import bp as runs_bp
    from dashboard.routes.decisions import bp as decisions_bp
    from dashboard.routes.screening import bp as screening_bp
    from dashboard.routes.risk import bp as risk_bp
    from dashboard.routes.status import bp as status_bp
    from dashboard.routes.api import bp as api_bp

    app.register_blueprint(index_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(decisions_bp)
    app.register_blueprint(screening_bp)
    app.register_blueprint(risk_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(api_bp)

    return app
