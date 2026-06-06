#!/usr/bin/env bash
# Quick verification of dashboard implementation
# Run this to verify all dashboard tests pass

set -euo pipefail
cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Dashboard Implementation Verification${NC}"
echo ""

# Run only dashboard tests (much faster than full suite). Use the venv python
# (bare `python` may lack pytest) and trust pytest's own exit code rather than a
# hardcoded pass count that drifts as tests are added.
PYTEST="venv/bin/python -m pytest"
[ -x "venv/bin/python" ] || PYTEST="python3 -m pytest"
echo "Running dashboard tests..."
if $PYTEST tests/frontend/ -q; then
    echo -e "${GREEN}✓ Dashboard tests passing${NC}"
else
    echo -e "${RED}✗ Dashboard tests failed${NC}"
    exit 1
fi

echo ""
echo "Implementation summary:"
echo -e "  ${GREEN}✓${NC} frontend/services/: domain-split DashboardService behind a facade (P0-5)"
echo -e "  ${GREEN}✓${NC} frontend/static/js/: native ES-module widgets on one scheduler (P3-1)"
echo -e "  ${GREEN}✓${NC} frontend/app.py: REST API under /api/v1/*"
echo -e "  ${GREEN}✓${NC} tests/frontend/: green (count tracked by pytest, above)"
echo ""
echo -e "${GREEN}✓ Dashboard ready to test${NC}"
echo ""
echo "To start: ./start_dash.sh"
