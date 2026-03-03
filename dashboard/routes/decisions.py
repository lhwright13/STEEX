from flask import Blueprint, render_template, request
from dashboard.db import DashboardDB
from dashboard.utils import parse_json_field

bp = Blueprint("decisions", __name__)


@bp.route("/decisions")
def decision_list():
    db = DashboardDB()
    ticker = request.args.get("ticker")
    decisions = db.get_decisions(ticker=ticker, limit=100)
    tickers = db.get_distinct_tickers()

    for d in decisions:
        parse_json_field(d, "reasons", "reasons_list")

    return render_template(
        "decisions/list.html",
        decisions=decisions,
        tickers=tickers,
        selected_ticker=ticker,
    )
