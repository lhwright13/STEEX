# Dashboard Hardcoded Demo Data - Complete Removal

## Summary
All hardcoded demo data has been removed from the dashboard. The system now reads entirely from live API endpoints that fetch real data from configuration files and execution history.

## What Was Removed

### Hardcoded Dates (2026-05-24 timestamps)
- ❌ `<span>Last run: 2026-05-24 10:17:50</span>` → ✓ Dynamic from API
- ❌ Recent Runs table dates (5 rows of hardcoded dates) → ✓ From `/api/v1/pipeline/recent-runs`
- ❌ "Today's Schedule" header date `2026-05-24` → ✓ Updated every refresh
- ❌ Schedule header time display `14:32:15` → ✓ Current time

### Hardcoded Execution Trace Data
- ❌ Demo run ID `c4d2a1b9` → ✓ From latest run file
- ❌ Timestamp `2026-05-24 10:15:42` → ✓ From run data
- ❌ Agent name `analysis_conservative` → ✓ From run data
- ❌ Demo ticker symbols (AAPL, JPM, KO, PG, MRK) → ✓ From actual tool outputs
- ❌ Demo tool execution times → ✓ From actual trace data
- ❌ Demo function parameters/outputs → ✓ From real trace data

### Hardcoded Agent Data (Previously removed)
- ❌ 200+ lines of AGENTS constant → ✓ Populated from `/api/v1/system/agents`
- ❌ Success rates (99%, 98%, etc.) → ✓ Calculated from 10 recent runs
- ❌ Agent prompts → ✓ Loaded from prompt files
- ❌ Tools lists → ✓ From agent configuration
- ❌ MCP server lists → ✓ From external_servers field

## New API Endpoints

**Live Run Data:**
- `GET /api/v1/pipeline/recent-runs` - Returns recent execution history (10 most recent runs)
- `GET /api/v1/pipeline/trace/<run_id>` - Returns execution trace for a specific run

**Existing Endpoints (Enhanced):**
- `GET /api/v1/system/agents` - Now returns real stats from run history
- `GET /api/v1/system/schedules` - Now uses actual mode configurations
- `GET /api/v1/system/agent/<name>/detail` - Configuration with prompt loaded from files
- `GET /api/v1/system/agent/<name>/last-output` - Actual execution output
- `GET /api/v1/system/graph/<mode>` - Real LangGraph structure

## Data Sources

All dashboard data now comes from one of these sources:

1. **Configuration Files:**
   - `config/agents.yaml` - Agent specifications
   - `config/config.yaml` - System configuration

2. **Run History:**
   - `/data/runs/*.jsonl` - Recent execution data
   - Analyzed to compute stats, traces, and outcomes

3. **Prompt Files:**
   - `src/agents/prompts/*.py` - Agent system prompts

4. **Live System State:**
   - `RegimeDetector` - Real-time market regime
   - Current time for schedule displays

## Verification

### Testing
- All 58 dashboard tests passing ✓
- Service methods tested with empty and valid data ✓
- API endpoints verified returning correct data ✓

### Data Flow
1. Page loads with placeholder "Loading..." state
2. `system-live.js` fetches all data from APIs
3. UI populated with real system data
4. Refresh every 10 seconds keeps data current
5. If no run data exists, shows empty/disabled state (not demo data)

## What Happens When There's No Run Data

Instead of showing demo/placeholder values, the system shows:
- Empty lists with message "No data available"
- Disabled state indicators
- "—" (dash) for missing values
- Graceful degradation

This is appropriate for a fresh system that hasn't executed yet, and matches the actual system state.

## Files Modified

**Backend:**
- `frontend/services.py` - Service layer for dashboard data
- `frontend/app.py` - API endpoints

**Frontend:**
- `frontend/templates/system.html` - Removed hardcoded AGENTS constant, templates for dynamic data
- `frontend/static/js/system-live.js` - Live data fetching and DOM updates
- `frontend/static/js/refresh.js` - Dashboard polling (previously created)

## Testing the Changes

When run data exists:
```bash
# Recent runs table populated with real data
curl http://localhost:5000/api/v1/pipeline/recent-runs

# Execution trace from latest run
curl http://localhost:5000/api/v1/pipeline/trace/<latest-run-id>

# Agent statistics from run history
curl http://localhost:5000/api/v1/system/agents
```

When no run data exists:
- API returns empty arrays or default values
- UI shows "No data available" or "Loading..." states
- No demo/placeholder content is shown

## Backwards Compatibility

The `AGENTS` global object is populated dynamically from the API for backwards compatibility with existing JavaScript functions (`buildAgentsGrid()`, `renderDetail()`, `selectAgent()`).

## Known Limitations

None - all hardcoded demo data has been replaced with live data sources.
