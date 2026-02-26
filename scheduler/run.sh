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
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node/" 2>/dev/null | sort -V | tail -1)/bin:$PATH" 2>/dev/null

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

MODEL="$(mode_get "$CONFIG_MODE" "model")"
MAX_BUDGET="$(mode_get "$CONFIG_MODE" "max_budget_usd")"
ALLOWED_TOOLS="$(mode_get "$CONFIG_MODE" "allowed_tools")"
PAPER="$(mode_get "$CONFIG_MODE" "paper")"
DRY_RUN="$(mode_get "$CONFIG_MODE" "dry_run")"
AUTO_CONFIRM="$(mode_get "$CONFIG_MODE" "auto_confirm")"
OUTPUT_FORMAT="$(mode_get "$CONFIG_MODE" "output_format")"
NO_SESSION="$(mode_get "$CONFIG_MODE" "no_session_persistence")"
PROMPT_FILE="$(yaml_get "modes.${CONFIG_MODE}.prompt_file")"
MAX_LOG_DAYS="$(yaml_get "max_log_days")"

if [ -z "$PROMPT_FILE" ] || [ ! -f "$PROJECT_DIR/$PROMPT_FILE" ]; then
    echo "Error: prompt file not found: $PROJECT_DIR/$PROMPT_FILE"
    exit 1
fi

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
if [ "$PAPER" = "True" ] || [ "$PAPER" = "true" ]; then
    RUN_FLAGS="$RUN_FLAGS --paper"
else
    RUN_FLAGS="$RUN_FLAGS --live"
fi

# ---------------------------------------------------------------------------
# Load and prepare prompt
# ---------------------------------------------------------------------------

PROMPT="$(cat "$PROJECT_DIR/$PROMPT_FILE")"

# Inject runtime flags into the {{FLAGS}} placeholder
PROMPT="${PROMPT//\{\{FLAGS\}\}/$RUN_FLAGS}"

# Append runtime metadata
TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
PROMPT="$PROMPT

---
Runtime metadata (injected by scheduler):
- Mode: $MANAGER_MODE
- Config mode: $CONFIG_MODE
- Timestamp: $TIMESTAMP
- Dry run: $DRY_RUN
- Paper trading: $PAPER
- Working directory: $PROJECT_DIR"

# Append system prompt for safety guardrails
SYSTEM_APPEND="You are running in automated scheduler mode. Do NOT modify any source code files. Do NOT create or delete files outside of data/. Only read code, run the pipeline, and report results. Working directory: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# Build claude command
# ---------------------------------------------------------------------------

LOGFILE="$LOG_DIR/${CONFIG_MODE}_$(date '+%Y%m%d_%H%M%S').log"

CMD=(
    claude
    -p "$PROMPT"
    --model "$MODEL"
    --max-budget-usd "$MAX_BUDGET"
    --allowedTools "$ALLOWED_TOOLS"
    --output-format "$OUTPUT_FORMAT"
    --append-system-prompt "$SYSTEM_APPEND"
)

if [ "$NO_SESSION" = "True" ] || [ "$NO_SESSION" = "true" ]; then
    CMD+=(--no-session-persistence)
fi

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

RUN_ID="${CONFIG_MODE}_$(date '+%Y%m%d_%H%M%S')"

echo "[$TIMESTAMP] Starting $MODE run (dry_run=$DRY_RUN, paper=$PAPER)"
echo "[$TIMESTAMP] Log: $LOGFILE"
echo "[$TIMESTAMP] Run ID: $RUN_ID"

cd "$PROJECT_DIR"

# Record run start in dashboard DB
INGEST_FLAGS=""
if [ "$DRY_RUN" = "True" ] || [ "$DRY_RUN" = "true" ]; then INGEST_FLAGS="$INGEST_FLAGS --dry-run"; fi
if [ "$PAPER" = "True" ] || [ "$PAPER" = "true" ]; then INGEST_FLAGS="$INGEST_FLAGS --paper"; fi
"$VENV_PYTHON" scripts/ingest_run.py --start --run-id "$RUN_ID" --mode "$MANAGER_MODE" --log-path "$LOGFILE" $INGEST_FLAGS || true

# Unset CLAUDECODE to allow scheduler to invoke claude -p
# (Claude Code sets this to prevent accidental nesting; cron is intentional)
unset CLAUDECODE

"${CMD[@]}" 2>&1 | tee "$LOGFILE"

EXIT_CODE=${PIPESTATUS[0]}
echo "[$TIMESTAMP] $MODE completed with exit code $EXIT_CODE"

# Record run finish in dashboard DB
"$VENV_PYTHON" scripts/ingest_run.py --finish --run-id "$RUN_ID" --exit-code "$EXIT_CODE" || true

# ---------------------------------------------------------------------------
# Prune old logs
# ---------------------------------------------------------------------------

if [ -n "$MAX_LOG_DAYS" ] && [ "$MAX_LOG_DAYS" -gt 0 ] 2>/dev/null; then
    find "$LOG_DIR" -name "*.log" -mtime +"$MAX_LOG_DAYS" -delete 2>/dev/null || true
fi

exit "$EXIT_CODE"
