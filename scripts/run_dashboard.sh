#!/usr/bin/env bash
# Start the STEEX Flask dashboard.
#
# Usage:
#   scripts/run_dashboard.sh                  # localhost:5000
#   scripts/run_dashboard.sh --port 8080      # custom port
#   scripts/run_dashboard.sh --host 0.0.0.0   # bind all interfaces
set -euo pipefail

cd "$(dirname "$0")/.."

exec venv/bin/python -m flask --app frontend.app:create_app run "$@"
