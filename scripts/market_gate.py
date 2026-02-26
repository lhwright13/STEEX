#!/usr/bin/env python3
"""Market calendar gate for the STEEX scheduler.

Uses Alpaca's get_clock() and get_calendar() to determine whether a
given scheduler mode should run right now.

Gate rules:
    Mode        Requires open?  Requires market day?
    ----------  --------------  --------------------
    heartbeat   No              No
    screen      No              Yes
    enter       Yes             Yes
    monitor     Yes             Yes
    stop_sync   Yes             Yes
    post_market No              Yes
    learning    No              No (Friday check only)

Output: JSON to stdout with should_run, reason, is_open, early_close.
Exit code 0 always (run.sh parses JSON). Exit code 1 on API error
(treated as "run anyway" by run.sh).
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

GATE_RULES = {
    "heartbeat":  {"requires_open": False, "requires_market_day": False},
    "screen":     {"requires_open": False, "requires_market_day": True},
    "enter":      {"requires_open": True,  "requires_market_day": True},
    "monitor":    {"requires_open": True,  "requires_market_day": True},
    "stop_sync":  {"requires_open": True,  "requires_market_day": True},
    "post_market": {"requires_open": False, "requires_market_day": True},
    "learning":   {"requires_open": False, "requires_market_day": False},
    # backward compat
    "pre_market": {"requires_open": False, "requires_market_day": True},
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "should_run": True,
            "reason": "no mode specified, defaulting to run",
        }))
        return

    mode = sys.argv[1]
    rules = GATE_RULES.get(mode)

    if rules is None:
        print(json.dumps({
            "should_run": True,
            "reason": f"unknown mode '{mode}', defaulting to run",
        }))
        return

    # If mode needs nothing, always run
    if not rules["requires_open"] and not rules["requires_market_day"]:
        print(json.dumps({
            "should_run": True,
            "reason": f"{mode} has no market requirements",
            "is_open": None,
            "early_close": None,
        }))
        return

    try:
        from src.broker.alpaca import AlpacaBroker

        paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
        broker = AlpacaBroker(paper=paper)
        clock = broker.get_clock()
        is_open = clock["is_open"]

        # Check if today is a market day
        today = date.today().isoformat()
        calendar = broker.get_calendar(today, today)
        is_market_day = len(calendar) > 0

        # Detect early close
        early_close = False
        if calendar:
            close_time = calendar[0].get("close", "16:00")
            if close_time and close_time < "16:00":
                early_close = True

    except Exception as e:
        # API error - exit code 1, run.sh treats this as "run anyway"
        print(json.dumps({
            "error": str(e),
            "should_run": True,
            "reason": f"API error, running anyway: {e}",
        }), file=sys.stderr)
        sys.exit(1)

    # Apply gate rules
    if rules["requires_market_day"] and not is_market_day:
        print(json.dumps({
            "should_run": False,
            "reason": f"not a market day (mode={mode})",
            "is_open": is_open,
            "early_close": early_close,
        }))
        return

    if rules["requires_open"] and not is_open:
        print(json.dumps({
            "should_run": False,
            "reason": f"market is closed (mode={mode})",
            "is_open": is_open,
            "early_close": early_close,
        }))
        return

    print(json.dumps({
        "should_run": True,
        "reason": "gate passed",
        "is_open": is_open,
        "early_close": early_close,
    }))


if __name__ == "__main__":
    main()
