#!/usr/bin/env bash
# Kill any running STEEX dashboard and start a fresh one.
#
# Usage:
#   ./start_dash.sh              # default port 5055
#   ./start_dash.sh 8080         # custom port
#   PORT=8080 ./start_dash.sh    # custom port via env
#
# Default is 5055 (not 5000) because macOS reserves :5000 for the AirPlay /
# Control Center receiver, which silently shadows a Flask dev server there.
#
# Logs to data/logs/dashboard.log; PID written to data/logs/dashboard.pid.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PORT="${1:-${PORT:-5055}}"
HOST="${HOST:-127.0.0.1}"
VENV_PY="$PROJECT_DIR/venv/bin/python"
LOG_DIR="$PROJECT_DIR/data/logs"
LOG_FILE="$LOG_DIR/dashboard.log"
PID_FILE="$LOG_DIR/dashboard.pid"

mkdir -p "$LOG_DIR"

if [ ! -x "$VENV_PY" ]; then
    echo "Error: venv python not found at $VENV_PY" >&2
    exit 1
fi

# --- Kill any existing dashboard ------------------------------------------
echo "Stopping any running dashboard..."

# 1) By the process signature we launch with (covers any port).
pkill -f "frontend.app.*create_app" 2>/dev/null || true

# 2) By recorded PID, if present.
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi

# 3) By whoever is bound to the target port.
if command -v lsof >/dev/null 2>&1; then
    PORT_PIDS="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
    if [ -n "$PORT_PIDS" ]; then
        echo "  freeing port $PORT (killing: $PORT_PIDS)"
        # shellcheck disable=SC2086
        kill $PORT_PIDS 2>/dev/null || true
    fi
fi

# Give sockets a moment to release.
sleep 1

# --- Start a fresh dashboard ----------------------------------------------
echo "Starting dashboard on http://$HOST:$PORT ..."

PYTHONUNBUFFERED=1 nohup "$VENV_PY" -c \
    "from frontend.app import create_app; create_app().run(host='$HOST', port=$PORT, debug=False)" \
    > "$LOG_FILE" 2>&1 &

NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# --- Wait for it to come up ------------------------------------------------
for _ in $(seq 1 15); do
    if curl -s -o /dev/null "http://$HOST:$PORT/"; then
        echo "Dashboard up (PID $NEW_PID) → http://$HOST:$PORT"
        echo "  logs: $LOG_FILE"
        exit 0
    fi
    if ! kill -0 "$NEW_PID" 2>/dev/null; then
        echo "Error: dashboard process exited. Last log lines:" >&2
        tail -n 20 "$LOG_FILE" >&2
        exit 1
    fi
    sleep 1
done

echo "Error: dashboard did not respond on http://$HOST:$PORT within 15s." >&2
tail -n 20 "$LOG_FILE" >&2
exit 1
