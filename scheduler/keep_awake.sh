#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# STEEX Keep-Awake - prevents macOS from sleeping during market hours.
#
# Cron fires this at 3:55 AM PST (before the first trading job at 4:00 AM).
# It runs caffeinate in the background for the duration of the trading day,
# then exits. The caffeinate process keeps the Mac awake until post_market
# finishes (~1:45 PM PST / 4:45 PM ET).
#
# caffeinate flags:
#   -d  prevent display sleep
#   -i  prevent idle sleep
#   -m  prevent disk sleep
#   -s  prevent system sleep (on AC power)
# ---------------------------------------------------------------------------

LOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/locks"
PIDFILE="$LOCK_DIR/caffeinate.pid"

mkdir -p "$LOCK_DIR"

# Kill any leftover caffeinate from a previous day
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
fi

# Keep awake for 10.5 hours (3:55 AM to ~2:25 PM PST / 5:25 PM ET).
# This covers all trading modes through post_market (1:30 PM PST).
SECONDS_AWAKE=37800

caffeinate -dims -t "$SECONDS_AWAKE" &
CAFE_PID=$!
echo "$CAFE_PID" > "$PIDFILE"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] caffeinate started (PID $CAFE_PID, ${SECONDS_AWAKE}s)"