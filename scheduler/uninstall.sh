#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# STEEX Scheduler - uninstall.sh
# Removes STEEX cron entries, preserves everything else.
# ---------------------------------------------------------------------------

MARKER_START="# STEEX-SCHEDULER-START"
MARKER_END="# STEEX-SCHEDULER-END"

EXISTING=$(crontab -l 2>/dev/null || true)

if [ -z "$EXISTING" ]; then
    echo "No crontab entries found. Nothing to remove."
    exit 0
fi

if ! echo "$EXISTING" | grep -q "$MARKER_START"; then
    echo "No STEEX scheduler entries found in crontab. Nothing to remove."
    exit 0
fi

# Strip the marker block
CLEANED=$(echo "$EXISTING" | sed "/$MARKER_START/,/$MARKER_END/d")

# Trim leading/trailing blank lines
CLEANED=$(echo "$CLEANED" | sed '/./,$!d' | sed -e :a -e '/^$/{ $d; N; ba; }')

if [ -z "$CLEANED" ]; then
    crontab -r 2>/dev/null || true
    echo "STEEX scheduler entries removed. Crontab is now empty."
else
    echo "$CLEANED" | crontab -
    echo "STEEX scheduler entries removed. Remaining crontab:"
    echo ""
    crontab -l
fi
