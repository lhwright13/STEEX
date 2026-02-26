import json
from flask import Blueprint, render_template
from dashboard.db import DashboardDB

bp = Blueprint("risk", __name__)


@bp.route("/risk")
def regime():
    db = DashboardDB()
    history = db.get_regime_history(limit=50)
    latest = db.get_latest_regime()

    for r in history:
        if r.get("risk_alerts"):
            try:
                r["alerts_list"] = json.loads(r["risk_alerts"])
            except (json.JSONDecodeError, TypeError):
                r["alerts_list"] = []
        else:
            r["alerts_list"] = []

    return render_template(
        "risk/regime.html",
        history=history,
        latest=latest,
    )
