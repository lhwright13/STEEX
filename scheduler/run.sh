#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# STEEX Scheduler - run.sh
# Usage: scheduler/run.sh <mode>
#   mode: heartbeat_morning | screen | enter | monitor_midday |
#         monitor_afternoon | stop_sync | post_market | learning |
#         heartbeat_weekend | pre_market | monitor
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$SCRIPT_DIR/config.yaml"
LOG_DIR="$SCRIPT_DIR/logs"
LOCK_DIR="$SCRIPT_DIR/locks"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

# Ensure Homebrew and common tool paths are available (cron has minimal PATH)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Source profile for API keys (Alpaca, Finnhub, etc.)
# shellcheck disable=SC1090
[ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" 2>/dev/null
[ -f "$HOME/.zprofile" ] && source "$HOME/.zprofile" 2>/dev/null

mkdir -p "$LOG_DIR" "$LOCK_DIR"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

yaml_get() {
    # Read a dot-separated key path from the scheduler config.
    # Falls back to defaults.* if modes.<mode>.* is not set.
    local key="$1"
    "$VENV_PYTHON" -c "
import yaml, sys
with open('$CONFIG') as f:
    cfg = yaml.safe_load(f)
keys = '$key'.split('.')
node = cfg
for k in keys:
    if isinstance(node, dict) and k in node:
        node = node[k]
    else:
        node = None
        break
if node is None:
    print('')
else:
    print(node)
"
}

mode_get() {
    # Get a mode-specific value, falling back to defaults.
    local mode="$1"
    local key="$2"
    local val
    val="$(yaml_get "modes.${mode}.${key}")"
    if [ -z "$val" ]; then
        val="$(yaml_get "defaults.${key}")"
    fi
    echo "$val"
}

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------

MODE="${1:-}"
if [ -z "$MODE" ]; then
    echo "Usage: scheduler/run.sh <mode>"
    exit 1
fi

# Verify mode exists in config (or is a known alias)
MODE_EXISTS=$("$VENV_PYTHON" -c "
import yaml
with open('$CONFIG') as f:
    cfg = yaml.safe_load(f)
modes = list(cfg.get('modes', {}).keys())
# Also accept legacy aliases
aliases = {'pre_market': True, 'monitor': True, 'heartbeat': True}
print('$MODE' in modes or '$MODE' in aliases)
")

if [ "$MODE_EXISTS" != "True" ]; then
    echo "Error: unknown mode '$MODE'"
    echo "  Check scheduler/config.yaml for available modes"
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve mode_name override (e.g. heartbeat_morning -> heartbeat for manager)
# ---------------------------------------------------------------------------

# The config mode key (for reading config values)
CONFIG_MODE="$MODE"

# The run_manager.py mode (may differ via mode_name override)
MANAGER_MODE="$(yaml_get "modes.${MODE}.mode_name")"
if [ -z "$MANAGER_MODE" ]; then
    MANAGER_MODE="$MODE"
fi

# ---------------------------------------------------------------------------
# Check if mode is enabled
# ---------------------------------------------------------------------------

ENABLED="$(yaml_get "modes.${CONFIG_MODE}.enabled")"
if [ "$ENABLED" != "True" ] && [ "$ENABLED" != "true" ]; then
    echo "Mode '$MODE' is disabled in config. Skipping."
    exit 0
fi

# ---------------------------------------------------------------------------
# Market gate - check if we should run based on market calendar
# ---------------------------------------------------------------------------

GATE_SCRIPT="$PROJECT_DIR/scripts/market_gate.py"
if [ -f "$GATE_SCRIPT" ]; then
    GATE_OUTPUT=$("$VENV_PYTHON" "$GATE_SCRIPT" "$MANAGER_MODE" 2>/dev/null) || true
    if [ -n "$GATE_OUTPUT" ]; then
        SHOULD_RUN=$(echo "$GATE_OUTPUT" | "$VENV_PYTHON" -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('should_run', True))
except Exception:
    print(True)
")
        if [ "$SHOULD_RUN" = "False" ]; then
            GATE_REASON=$(echo "$GATE_OUTPUT" | "$VENV_PYTHON" -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('reason', 'gate blocked'))
except Exception:
    print('gate blocked')
")
            echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Skipping $MODE: $GATE_REASON"
            exit 0
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Lockfile - prevent overlapping runs of the same manager mode
# ---------------------------------------------------------------------------

LOCKFILE="$LOCK_DIR/${MANAGER_MODE}.lock"

if [ -f "$LOCKFILE" ]; then
    LOCK_PID=$(cat "$LOCKFILE" 2>/dev/null || true)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Skipping $MODE: previous run (PID $LOCK_PID) still active"
        exit 0
    else
        # Stale lock, remove it
        rm -f "$LOCKFILE"
    fi
fi

echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

# ---------------------------------------------------------------------------
# Read settings (mode-specific with fallback to defaults)
# ---------------------------------------------------------------------------

PAPER="$(mode_get "$CONFIG_MODE" "paper")"
DRY_RUN="$(mode_get "$CONFIG_MODE" "dry_run")"
AUTO_CONFIRM="$(mode_get "$CONFIG_MODE" "auto_confirm")"
AGENT_MODE="$(mode_get "$CONFIG_MODE" "agent")"
MAX_LOG_DAYS="$(yaml_get "max_log_days")"

# ---------------------------------------------------------------------------
# Build run_manager.py flags from config
# ---------------------------------------------------------------------------

RUN_FLAGS=""
if [ "$DRY_RUN" = "True" ] || [ "$DRY_RUN" = "true" ]; then
    RUN_FLAGS="$RUN_FLAGS --dry-run"
fi
if [ "$AUTO_CONFIRM" = "True" ] || [ "$AUTO_CONFIRM" = "true" ]; then
    RUN_FLAGS="$RUN_FLAGS --yes"
fi
if [ "$AGENT_MODE" = "True" ] || [ "$AGENT_MODE" = "true" ]; then
    RUN_FLAGS="$RUN_FLAGS --agent"
fi
if [ "$PAPER" = "True" ] || [ "$PAPER" = "true" ]; then
    RUN_FLAGS="$RUN_FLAGS --paper"
else
    RUN_FLAGS="$RUN_FLAGS --live"
fi

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
LOGFILE="$LOG_DIR/${CONFIG_MODE}_$(date '+%Y%m%d_%H%M%S').log"
RUN_ID="${CONFIG_MODE}_$(date '+%Y%m%d_%H%M%S')"

echo "[$TIMESTAMP] Starting $MODE run (dry_run=$DRY_RUN, paper=$PAPER)"
echo "[$TIMESTAMP] Log: $LOGFILE"
echo "[$TIMESTAMP] Run ID: $RUN_ID"

cd "$PROJECT_DIR"

# Dashboard run records are written directly by the agent orchestrator
# (src/agents/run_log.py -> data/runs/*.jsonl), read by frontend/services.py.

# Route to the correct Python command based on mode
case "$MANAGER_MODE" in
    heartbeat)
        "$VENV_PYTHON" scripts/health_check.py 2>&1 | tee "$LOGFILE"
        ;;
    learning)
        LEARNING_FLAGS="--verbose"
        if [ "$DRY_RUN" = "True" ] || [ "$DRY_RUN" = "true" ]; then LEARNING_FLAGS="$LEARNING_FLAGS --dry-run"; fi
        "$VENV_PYTHON" scripts/run_learning.py $LEARNING_FLAGS 2>&1 | tee "$LOGFILE"
        ;;
    *)
        "$VENV_PYTHON" scripts/run_manager.py "$MANAGER_MODE" $RUN_FLAGS 2>&1 | tee "$LOGFILE"
        ;;
esac

EXIT_CODE=${PIPESTATUS[0]}
echo "[$TIMESTAMP] $MODE completed with exit code $EXIT_CODE"

# ---------------------------------------------------------------------------
# Prune old logs
# ---------------------------------------------------------------------------

if [ -n "$MAX_LOG_DAYS" ] && [ "$MAX_LOG_DAYS" -gt 0 ] 2>/dev/null; then
    find "$LOG_DIR" -name "*.log" -mtime +"$MAX_LOG_DAYS" -delete 2>/dev/null || true
fi

# The per-mode cron_*.log files are appended to on every run (crontab `>>`),
# so -mtime never ages them out and they grow without bound (cron_learning.log
# once hit 300MB+). Cap each at ~5MB by keeping only the last 5000 lines.
find "$LOG_DIR" -name "cron_*.log" -size +5M 2>/dev/null | while read -r f; do
    tail -n 5000 "$f" > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f"
done

exit "$EXIT_CODE"
