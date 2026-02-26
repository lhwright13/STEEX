#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# STEEX Scheduler - run.sh
# Usage: scheduler/run.sh <mode>
#   mode: pre_market | monitor | post_market
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$SCRIPT_DIR/config.yaml"
LOG_DIR="$SCRIPT_DIR/logs"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

# Source profile for API keys (Alpaca, Finnhub, etc.)
# shellcheck disable=SC1090
[ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" 2>/dev/null
[ -f "$HOME/.zprofile" ] && source "$HOME/.zprofile" 2>/dev/null

mkdir -p "$LOG_DIR"

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
    echo "  mode: pre_market | monitor | post_market"
    exit 1
fi

case "$MODE" in
    pre_market|monitor|post_market) ;;
    *)
        echo "Error: unknown mode '$MODE'"
        echo "  Valid modes: pre_market | monitor | post_market"
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Check if mode is enabled
# ---------------------------------------------------------------------------

ENABLED="$(yaml_get "modes.${MODE}.enabled")"
if [ "$ENABLED" != "True" ] && [ "$ENABLED" != "true" ]; then
    echo "Mode '$MODE' is disabled in config. Skipping."
    exit 0
fi

# ---------------------------------------------------------------------------
# Read settings (mode-specific with fallback to defaults)
# ---------------------------------------------------------------------------

MODEL="$(mode_get "$MODE" "model")"
MAX_BUDGET="$(mode_get "$MODE" "max_budget_usd")"
ALLOWED_TOOLS="$(mode_get "$MODE" "allowed_tools")"
PAPER="$(mode_get "$MODE" "paper")"
DRY_RUN="$(mode_get "$MODE" "dry_run")"
AUTO_CONFIRM="$(mode_get "$MODE" "auto_confirm")"
OUTPUT_FORMAT="$(mode_get "$MODE" "output_format")"
NO_SESSION="$(mode_get "$MODE" "no_session_persistence")"
PROMPT_FILE="$(yaml_get "modes.${MODE}.prompt_file")"
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
- Mode: $MODE
- Timestamp: $TIMESTAMP
- Dry run: $DRY_RUN
- Paper trading: $PAPER
- Working directory: $PROJECT_DIR"

# Append system prompt for safety guardrails
SYSTEM_APPEND="You are running in automated scheduler mode. Do NOT modify any source code files. Do NOT create or delete files outside of data/. Only read code, run the pipeline, and report results. Working directory: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# Build claude command
# ---------------------------------------------------------------------------

LOGFILE="$LOG_DIR/${MODE}_$(date '+%Y%m%d_%H%M%S').log"

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

RUN_ID="${MODE}_$(date '+%Y%m%d_%H%M%S')"

echo "[$TIMESTAMP] Starting $MODE run (dry_run=$DRY_RUN, paper=$PAPER)"
echo "[$TIMESTAMP] Log: $LOGFILE"
echo "[$TIMESTAMP] Run ID: $RUN_ID"

cd "$PROJECT_DIR"

# Record run start in dashboard DB
INGEST_FLAGS=""
if [ "$DRY_RUN" = "True" ] || [ "$DRY_RUN" = "true" ]; then INGEST_FLAGS="$INGEST_FLAGS --dry-run"; fi
if [ "$PAPER" = "True" ] || [ "$PAPER" = "true" ]; then INGEST_FLAGS="$INGEST_FLAGS --paper"; fi
"$VENV_PYTHON" scripts/ingest_run.py --start --run-id "$RUN_ID" --mode "$MODE" --log-path "$LOGFILE" $INGEST_FLAGS || true

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
