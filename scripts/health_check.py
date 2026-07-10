#!/usr/bin/env python3
"""STEEX heartbeat / health check.

Checks:
1. Alpaca API connectivity (get_account)
2. Market calendar for today
3. Position count (local vs broker)
4. Active server-side stop orders (one per position expected)
5. Last successful run timestamp
6. Writes data/heartbeat.json with results
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Ensure project root is on sys.path so this script runs via direct invocation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings


def run_health_check():
    settings = get_settings()
    checks = {}
    overall = "OK"

    # 1. Alpaca API connectivity
    try:
        from src.broker.alpaca import AlpacaBroker

        paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
        broker = AlpacaBroker(paper=paper)
        account = broker.get_account()
        checks["api"] = {
            "status": "OK",
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
        }
    except Exception as e:
        checks["api"] = {"status": "CRITICAL", "error": str(e)}
        overall = "CRITICAL"
        broker = None

    # 2. Market calendar for today
    if broker:
        try:
            today = date.today().isoformat()
            calendar = broker.get_calendar(today, today)
            clock = broker.get_clock()
            is_market_day = len(calendar) > 0
            checks["market"] = {
                "status": "OK",
                "is_market_day": is_market_day,
                "is_open": clock["is_open"],
                "next_open": clock["next_open"],
                "next_close": clock["next_close"],
            }
            if calendar:
                checks["market"]["today_close"] = calendar[0].get("close")
        except Exception as e:
            checks["market"] = {"status": "WARNING", "error": str(e)}
            if overall == "OK":
                overall = "WARNING"

    # 3. Position count comparison
    if broker:
        try:
            broker_positions = broker.get_positions()
            broker_tickers = {p.ticker for p in broker_positions}

            from src.portfolio.positions import PositionManager
            pm = PositionManager(settings)
            local_positions = pm.get_all_positions()
            local_tickers = {p.ticker for p in local_positions}

            missing_local = broker_tickers - local_tickers
            stale_local = local_tickers - broker_tickers

            pos_status = "OK"
            if missing_local or stale_local:
                pos_status = "WARNING"
                if overall == "OK":
                    overall = "WARNING"

            checks["positions"] = {
                "status": pos_status,
                "broker_count": len(broker_tickers),
                "local_count": len(local_tickers),
                "missing_local": list(missing_local),
                "stale_local": list(stale_local),
            }
        except Exception as e:
            checks["positions"] = {"status": "WARNING", "error": str(e)}
            if overall == "OK":
                overall = "WARNING"

    # 4. Server-side stop orders
    if broker:
        try:
            stops = broker.get_all_stop_orders()
            stop_tickers = {s["ticker"] for s in stops}
            position_tickers = {p.ticker for p in broker_positions} if broker_positions else set()
            missing_stops = position_tickers - stop_tickers
            orphan_stops = stop_tickers - position_tickers

            stop_status = "OK"
            if missing_stops:
                stop_status = "WARNING"
                if overall == "OK":
                    overall = "WARNING"

            checks["stops"] = {
                "status": stop_status,
                "active_stops": len(stops),
                "positions_with_stops": len(stop_tickers & position_tickers),
                "missing_stops": list(missing_stops),
                "orphan_stops": list(orphan_stops),
                "details": stops,
            }
        except Exception as e:
            checks["stops"] = {"status": "WARNING", "error": str(e)}
            if overall == "OK":
                overall = "WARNING"

    # 5. Last successful run
    report_dir = Path(settings.manager_report_dir)
    latest_report = report_dir / "latest.json"
    if latest_report.exists():
        try:
            with open(latest_report) as f:
                report = json.load(f)
            last_ts = report.get("timestamp", "unknown")
            checks["last_run"] = {
                "status": "OK",
                "timestamp": last_ts,
                "mode": report.get("mode", "unknown"),
            }
        except Exception as e:
            checks["last_run"] = {"status": "WARNING", "error": str(e)}
    else:
        checks["last_run"] = {"status": "WARNING", "error": "no report found"}
        if overall == "OK":
            overall = "WARNING"

    # 6. Data integrity invariants (07-07/07-08 incident: phantom trades,
    #    negative shares, score-0 fabricated positions). Catches corruption in
    #    hours instead of days.
    integrity = {"status": "OK", "violations": []}
    try:
        trades_path = Path(settings.data_dir) / "trades.json"
        pos_path = Path(settings.data_dir) / "positions.json"
        trades = json.loads(trades_path.read_text()) if trades_path.exists() else []
        positions = json.loads(pos_path.read_text()) if pos_path.exists() else {}

        neg_trades = [t["ticker"] for t in trades if (t.get("shares") or 0) <= 0]
        if neg_trades:
            integrity["violations"].append(f"negative/zero-share trades: {neg_trades}")

        neg_pos = [k for k, v in positions.items() if (v.get("shares") or 0) <= 0]
        if neg_pos:
            integrity["violations"].append(f"negative/zero-share positions: {neg_pos}")

        # An "exited" ticker that is still in the live book = phantom exit.
        held = set(positions.keys())
        today_iso = date.today().isoformat()
        phantom = sorted({
            t["ticker"] for t in trades
            if t["ticker"] in held and str(t.get("exit_date", ""))[:10] == today_iso
        })
        if phantom:
            integrity["violations"].append(f"exit recorded today for still-held: {phantom}")

        from collections import Counter
        dups = [k for k, c in Counter(
            (t["ticker"], str(t.get("exit_date", ""))[:10]) for t in trades
        ).items() if c > 1]
        if dups:
            integrity["violations"].append(f"duplicate exit rows: {dups}")

        if checks.get("positions", {}).get("status") == "OK" and broker:
            score0 = [k for k, v in positions.items()
                      if not v.get("score") and str(v.get("entry_date", ""))[:10] == today_iso]
            if score0:
                integrity["violations"].append(f"score-0 positions created today (sync fabrication?): {score0}")

        if integrity["violations"]:
            integrity["status"] = "WARNING"
            if overall == "OK":
                overall = "WARNING"
    except Exception as e:
        integrity = {"status": "WARNING", "error": str(e)}
        if overall == "OK":
            overall = "WARNING"
    checks["integrity"] = integrity

    # Alert the operator on ANY non-OK heartbeat — both July incidents ran for
    # days with the heartbeat warning silently into a JSON file nobody reads.
    # Idempotent per day via the user_updates id.
    if overall != "OK":
        try:
            from src.notify.event_summary import summarize_and_notify
            issues = [f"{n}: {c.get('status')}" for n, c in checks.items()
                      if c.get("status") not in (None, "OK")]
            detail = "; ".join(issues) + (
                " | " + "; ".join(integrity.get("violations", [])) if integrity.get("violations") else ""
            )
            summarize_and_notify(
                {
                    "id": f"heartbeat_{overall.lower()}_{date.today().isoformat()}",
                    "type": "system",
                    "title": f"🩺 Heartbeat {overall}",
                    "context": {"checks": issues, "violations": integrity.get("violations", [])},
                },
                settings=settings,
                summarizer=lambda _e: (
                    f"Heartbeat is {overall}: {detail}. See data/heartbeat.json on the mini."
                ),
            )
        except Exception as e:
            print(f"  (heartbeat alert failed: {e})")

    # Write heartbeat file
    result = {
        "timestamp": datetime.now().isoformat(),
        "overall": overall,
        "checks": checks,
    }

    heartbeat_path = Path(settings.data_dir) / "heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    with open(heartbeat_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Print summary
    print(f"Health: {overall}")
    for name, check in checks.items():
        status = check.get("status", "UNKNOWN")
        print(f"  {name}: {status}")
        if status != "OK":
            for k, v in check.items():
                if k != "status" and v:
                    print(f"    {k}: {v}")

    print(f"\nSaved to {heartbeat_path}")
    return result


if __name__ == "__main__":
    run_health_check()
