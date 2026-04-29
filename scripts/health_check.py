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
