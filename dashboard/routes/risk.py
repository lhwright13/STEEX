from flask import Blueprint, render_template
from dashboard.db import DashboardDB
from dashboard.utils import parse_json_field

bp = Blueprint("risk", __name__)


@bp.route("/risk")
def regime():
    db = DashboardDB()
    history = db.get_regime_history(limit=50)
    latest = db.get_latest_regime()

    for r in history:
        parse_json_field(r, "risk_alerts", "alerts_list")

    return render_template(
        "risk/regime.html",
        history=history,
        latest=latest,
    )
