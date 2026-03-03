from flask import Blueprint, render_template, request
from dashboard.db import DashboardDB
from dashboard.utils import parse_json_field

bp = Blueprint("runs", __name__)


@bp.route("/runs")
def run_list():
    db = DashboardDB()
    mode = request.args.get("mode")
    status = request.args.get("status")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20
    offset = (page - 1) * per_page

    runs = db.get_runs(mode=mode, status=status, limit=per_page, offset=offset)
    total = db.get_run_count(mode=mode, status=status)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "runs/list.html",
        runs=runs,
        mode=mode,
        status=status,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@bp.route("/runs/<run_id>")
def run_detail(run_id):
    db = DashboardDB()
    run = db.get_run(run_id)
    if not run:
        return render_template("runs/detail.html", run=None, run_id=run_id), 404

    events = db.get_events(run_id)
    decisions = db.get_decisions(run_id=run_id)
    screening = db.get_screening(run_id)
    regime = db.get_regime(run_id)

    # Parse JSON fields for display
    for d in decisions:
        parse_json_field(d, "reasons", "reasons_list")

    for e in events:
        parse_json_field(e, "data_json", "data_parsed", default=None)

    if regime:
        parse_json_field(regime, "risk_alerts", "alerts_list")

    return render_template(
        "runs/detail.html",
        run=run,
        run_id=run_id,
        events=events,
        decisions=decisions,
        screening=screening,
        regime=regime,
    )
