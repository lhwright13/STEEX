#!/usr/bin/env python3
"""Ingest run data into the dashboard database.

Usage:
    # Before claude invocation - creates a 'running' row:
    python scripts/ingest_run.py --start --run-id <id> --mode <mode> [--dry-run] [--paper] [--log-path <path>]

    # After claude invocation - reads report JSON and populates all tables:
    python scripts/ingest_run.py --finish --run-id <id> --exit-code <code> [--report <path>]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dashboard.db import DashboardDB

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_REPORT = DATA_DIR / "reports" / "latest.json"


def ingest_start(db, args):
    now = datetime.now(timezone.utc).isoformat()
    db.insert_run(
        run_id=args.run_id,
        mode=args.mode,
        started_at=now,
        dry_run=1 if args.dry_run else 0,
        paper=1 if args.paper else 0,
        log_path=args.log_path,
    )
    print(f"[ingest] Started run {args.run_id} ({args.mode})")


def ingest_finish(db, args):
    run = db.get_run(args.run_id)
    if not run:
        print(f"[ingest] Warning: run {args.run_id} not found, creating it")
        db.insert_run(
            run_id=args.run_id,
            mode=args.mode or "unknown",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        run = db.get_run(args.run_id)

    report_path = args.report or str(DEFAULT_REPORT)
    report = _load_report(report_path)

    now = datetime.now(timezone.utc).isoformat()
    started = run["started_at"]
    try:
        duration = (
            datetime.fromisoformat(now) - datetime.fromisoformat(started)
        ).total_seconds()
    except (ValueError, TypeError):
        duration = None

    status = "success" if args.exit_code == 0 else "failed"
    error_msg = None if args.exit_code == 0 else f"Exit code {args.exit_code}"

    db.finish_run(
        run_id=args.run_id,
        finished_at=now,
        duration_seconds=duration,
        status=status,
        exit_code=args.exit_code,
        error_message=error_msg,
        report_path=report_path if report else None,
    )

    if report:
        _ingest_report(db, args.run_id, report)

    print(f"[ingest] Finished run {args.run_id} -> {status}")


def ingest_report_file(db, report_path):
    """Ingest a standalone report file (used by backfill)."""
    report = _load_report(report_path)
    if not report:
        return False

    timestamp = report.get("timestamp", "")
    mode = report.get("mode", "unknown")
    run_id = f"{mode}_{timestamp.replace(':', '').replace('-', '').replace('T', '_')}"

    if db.run_exists(run_id):
        return False

    log_events = report.get("log", [])
    started = log_events[0]["timestamp"] if log_events else timestamp
    finished = log_events[-1]["timestamp"] if log_events else timestamp

    try:
        duration = (
            datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        ).total_seconds()
    except (ValueError, TypeError):
        duration = None

    db.insert_run(
        run_id=run_id,
        mode=mode,
        started_at=started,
    )
    db.finish_run(
        run_id=run_id,
        finished_at=finished,
        duration_seconds=duration,
        status="success",
        exit_code=0,
        report_path=str(report_path),
    )
    _ingest_report(db, run_id, report)
    return True


def _load_report(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[ingest] Could not load report {path}: {e}")
        return None


def _ingest_report(db, run_id, report):
    timestamp = report.get("timestamp", datetime.now(timezone.utc).isoformat())

    # Events from log
    for event in report.get("log", []):
        db.insert_event(
            run_id=run_id,
            timestamp=event.get("timestamp", timestamp),
            action=event.get("action", "unknown"),
            detail=event.get("detail", ""),
            data=event.get("data"),
        )

    # Entry decisions
    for entry in report.get("entries", []):
        db.insert_decision(
            run_id=run_id,
            timestamp=timestamp,
            ticker=entry["ticker"],
            action="enter",
            price=entry.get("price"),
            shares=entry.get("shares"),
            score=entry.get("score"),
            reasons=entry.get("reasons"),
        )

    # Exit decisions
    for ex in report.get("exits", []):
        db.insert_decision(
            run_id=run_id,
            timestamp=timestamp,
            ticker=ex.get("ticker", "UNKNOWN"),
            action="exit",
            price=ex.get("price"),
            shares=ex.get("shares"),
            exit_reason=ex.get("reason"),
            pnl_dollars=ex.get("pnl_dollars"),
            pnl_pct=ex.get("pnl_pct"),
        )

    # Screening funnel
    screening = report.get("screening", {})
    if screening and screening.get("universe"):
        db.insert_screening(
            run_id=run_id,
            timestamp=timestamp,
            universe=screening.get("universe"),
            stage_1=screening.get("stage_1"),
            stage_2=screening.get("stage_2"),
            stage_3=screening.get("stage_3"),
            stage_4=screening.get("stage_4"),
            stage_5=screening.get("stage_5"),
            final=screening.get("final"),
        )

    # Regime snapshot
    regime = report.get("regime", {})
    if regime and regime.get("name"):
        db.insert_regime(
            run_id=run_id,
            timestamp=timestamp,
            regime_name=regime["name"],
            vix_level=regime.get("vix"),
            sizing_multiplier=regime.get("sizing_multiplier"),
            entries_allowed=1 if regime.get("entries_allowed") else 0,
            risk_alerts=report.get("risk_alerts"),
        )


def main():
    parser = argparse.ArgumentParser(description="Ingest run data into dashboard DB")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true", help="Record run start")
    group.add_argument("--finish", action="store_true", help="Record run finish")

    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--mode", help="Run mode (pre_market/monitor/post_market)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--paper", action="store_true", default=True)
    parser.add_argument("--log-path", help="Path to log file")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--report", help="Path to report JSON (default: latest.json)")

    args = parser.parse_args()
    db = DashboardDB()

    if args.start:
        if not args.mode:
            parser.error("--mode is required with --start")
        ingest_start(db, args)
    else:
        ingest_finish(db, args)


if __name__ == "__main__":
    main()
