# STEEX Scripts

Operational scripts for running, testing, and monitoring the STEEX trading system.

## Quick Start

```bash
# Start the dashboard (HTTP API + web interface)
bash scripts/run_dashboard.sh

# Run the screening pipeline
python run_manager.py screen --agent

# Run the learning pipeline
python run_manager.py learning --agent --paper
```

## Available Scripts

### Dashboard & Monitoring

#### `run_dashboard.sh`
Start the Flask dashboard server on localhost:5000.

```bash
bash scripts/run_dashboard.sh                    # localhost:5000
bash scripts/run_dashboard.sh --port 8080        # custom port
bash scripts/run_dashboard.sh --host 0.0.0.0     # bind all interfaces
```

Serves:
- `/` - Main trading dashboard (pipeline status, consensus picks, variants)
- `/system` - Agent transparency (execution graph, schedules, configurations)
- `/api/v1/*` - REST API endpoints for live data

#### `verify_dashboard.sh`
Quick verification that dashboard is fully implemented and tested.

```bash
bash scripts/verify_dashboard.sh
```

Checks:
- All 58 dashboard tests passing
- HTML button IDs and event wiring
- API endpoints functional

### Testing & Quality

#### `test_tier_1_2.sh`
Comprehensive test suite for Tier 1 + Tier 2 trading upgrades.

```bash
bash scripts/test_tier_1_2.sh                    # Standard output
bash scripts/test_tier_1_2.sh --verbose          # Detailed test output
bash scripts/test_tier_1_2.sh --coverage         # With coverage report
```

Tests:
- **13 integration tests** - Multi-agent orchestration, parallel variants, regime adaptation
- **23 learning tests** - Prompt evolution, cross-reference, postmortem analysis
- **58 dashboard tests** - API endpoints, HTML wiring, service layer

Total: **94 tests**, ~3 minutes runtime

### Pipeline Execution

#### `run_manager.py`
Main entry point for trading system execution. Supports multiple modes.

```bash
# Screening mode (find candidates)
python run_manager.py screen --agent --paper

# Entry mode (confirm positions)
python run_manager.py enter --agent --paper

# Monitor mode (real-time risk checking)
python run_manager.py monitor --agent --paper

# Learning mode (prompt evolution)
python run_manager.py learning --agent --paper
```

Flags:
- `--agent` - Use AI agents (requires Claude API key)
- `--paper` - Paper trading (no real executions)
- `--mock` - Use mock data for testing

### Data & Analysis

#### `health_check.py`
System health and dependency check.

```bash
python scripts/health_check.py
```

Verifies:
- API connectivity (Alpaca, Alpha Vantage, Polygon)
- Database state
- Configuration validity
- Agent registry status

#### `ingest_run.py`
Ingest and parse a pipeline run from disk.

```bash
python scripts/ingest_run.py data/runs/run_20260524_143200.jsonl
```

Useful for:
- Analyzing completed runs
- Debugging agent outputs
- Auditing trade decisions

#### `analyze_holdings.py`
Analyze current portfolio holdings.

```bash
python scripts/analyze_holdings.py
```

Shows:
- Current positions
- Unrealized P&L
- Sector concentration
- Risk metrics

#### `market_gate.py`
Check market regime and entry gates.

```bash
python scripts/market_gate.py
```

Outputs:
- Current market regime (risk_on, cautious, risk_off, crisis)
- VIX level and trend
- Entry gate status

### Learning Pipeline

#### `run_learning.py`
Execute the learning pipeline (prompt evolution).

```bash
python scripts/run_learning.py --agent --num-trades 20
```

Stages:
1. **Signal research** - Analyze which signals work
2. **OOS validation** - Test signals on historical data
3. **Postmortem** - Analyze real trade outcomes
4. **Alpha decay** - Detect degrading signals
5. **Evolution** - Generate prompt improvements

## Workflow Examples

### Daily Screening Run
```bash
# Start dashboard
bash scripts/run_dashboard.sh &

# Run screening pipeline
python run_manager.py screen --agent --paper

# Check results at http://localhost:5000
```

### Testing New Variant
```bash
# Verify existing tests pass
bash scripts/verify_dashboard.sh

# Run full test suite
bash scripts/test_tier_1_2.sh

# If passing, proceed with changes
```

### Prompt Evolution
```bash
# Check system health first
python scripts/health_check.py

# Run learning pipeline
python scripts/run_learning.py --agent --num-trades 30

# View results in /api/v1/system/agents (preprompt updated)
```

### Troubleshooting
```bash
# Check what's available
python scripts/health_check.py

# Inspect a completed run
python scripts/ingest_run.py data/runs/run_latest.jsonl

# See current market conditions
python scripts/market_gate.py

# Analyze portfolio
python scripts/analyze_holdings.py
```

## File Organization

```
scripts/
├── README.md                    # This file
├── __init__.py                  # Python package marker
├── run_dashboard.sh            # Start web dashboard
├── verify_dashboard.sh         # Quick dashboard check
├── test_tier_1_2.sh           # Comprehensive test suite
├── run_manager.py             # Main pipeline executor
├── run_learning.py            # Learning mode runner
├── health_check.py            # System health check
├── ingest_run.py              # Parse completed runs
├── analyze_holdings.py        # Portfolio analysis
└── market_gate.py             # Market regime check
```

## Dependencies

All scripts require:
- `venv/` - Python virtual environment with dependencies installed
- `config/config.yaml` - Configuration file with API keys
- `src/` - Core STEEX package

Dashboard additionally requires:
- `frontend/` - Flask app and templates
- `data/runs/` - Run history directory

## Troubleshooting

**Dashboard won't start:**
```bash
# Check Flask path
venv/bin/python -c "from frontend.app import create_app; print('OK')"
```

**Tests fail:**
```bash
# Run just dashboard tests quickly
python -m pytest tests/frontend/ -v
```

**API endpoints returning errors:**
```bash
# Check service connectivity
python scripts/health_check.py

# View recent run data
python scripts/ingest_run.py
```

## Support

For issues, check:
1. `scripts/health_check.py` - System health
2. `pytest tests/frontend/ -v` - Dashboard tests
3. `data/logs/` - Application logs
