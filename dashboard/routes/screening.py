from flask import Blueprint, render_template
from dashboard.db import DashboardDB

bp = Blueprint("screening", __name__)


@bp.route("/screening")
def funnel():
    db = DashboardDB()
    history = db.get_screening_history(limit=20)
    latest = history[0] if history else None

    return render_template(
        "screening/funnel.html",
        history=history,
        latest=latest,
    )
