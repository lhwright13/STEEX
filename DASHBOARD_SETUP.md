# STEEX Dashboard Setup

## Overview

The new STEEX Dashboard provides real-time visibility into:
- **Live Pipeline** - Current screening run, variant progress, consensus synthesis
- **Variant Results** - Candidate counts and avg scores from conservative/aggressive/momentum variants
- **Consensus Picks** - High-conviction, consensus, and speculative candidates
- **System Transparency** - Agent configuration, cron schedules, MCP tools, external servers

## File Structure

```
frontend/
├── app.py                 # Flask app with all routes and API endpoints
├── templates/
│   ├── index.html        # Main dashboard (Live Pipeline, Candidates, System Status)
│   └── system.html       # Agent transparency (Graph, Agents, Schedules, Tools)
└── static/
    └── css/
        └── steex.css     # Shared styles (old-school, utility-first aesthetic)
```

## Running the Dashboard

```bash
# Simple: use the launcher script
python run_dashboard.py

# Or: customize host/port
python run_dashboard.py --host 127.0.0.1 --port 8080 --debug

# Or: direct Flask
FLASK_APP=frontend.app FLASK_ENV=development flask run
```

Dashboard available at: **http://localhost:5000**

## API Endpoints

All endpoints return JSON with live data:

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Main dashboard view |
| `GET /system` | Agent transparency view |
| `GET /api/v1/pipeline/current` | Current run status & stage |
| `GET /api/v1/variants/results` | Results from 3 analysis variants |
| `GET /api/v1/consensus` | Consensus picks (high/medium conviction) |
| `GET /api/v1/screening/stats` | Screening funnel stats |
| `GET /api/v1/regime` | Current market regime & VIX |
| `GET /api/v1/manager/decision` | Manager's trade approval |
| `GET /api/v1/system/agents` | All agent configs & status |
| `GET /api/v1/system/schedules` | Cron schedule config |
| `GET /api/v1/system/agent/{name}/detail` | Agent's preprompt, tools, servers |

## Next Steps

### 1. Connect to Live Data (Immediate)

Currently, all API endpoints return mock data (hardcoded). Wire them to real data sources:

**Option A: Server-render (Jinja)**
- Keep HTML static
- Update Flask routes to query actual system state
- Render template with `render_template("index.html", live_mode="screen", candidates=[...])`
- Simplest, no JS changes

**Option B: Client-fetch (JS)**
- Keep HTML static
- Create `frontend/static/js/refresh.js` that polls endpoints
- `fetch('/api/v1/pipeline/current')` → innerHTML live updates
- Better for real-time dashboards with auto-refresh

**Example: One Connected Card**

Here's how to wire the "Live Pipeline" card (Option B):

```javascript
// frontend/static/js/refresh.js
async function updatePipeline() {
    const res = await fetch('/api/v1/pipeline/current');
    const data = await res.json();
    document.querySelector('[data-live-stage]').textContent = data.stage;
    document.querySelector('[data-live-agent]').textContent = data.current_agent;
    document.querySelector('[data-live-progress]').style.width = (data.stage_progress * 100) + '%';
}
setInterval(updatePipeline, 2000);  // Update every 2 sec
```

Then in HTML add `data-live-*` attributes to cells:
```html
<td data-live-stage>analysis</td>
<td data-live-agent>analysis_aggressive</td>
```

And include in `<body>`:
```html
<script src="/static/js/refresh.js"></script>
```

### 2. Integrate with Orchestrator (Phase 2)

Connect dashboard to live agent execution:
- Query `orchestrator._run_pipeline()` state in real-time
- Push variant conclusions as they complete (WebSocket or polling)
- Display agent trace logs (reasoning steps)
- Show MCP tool call results

### 3. Add Cron Monitoring (Phase 3)

- Wire schedules from `src/scheduler/` or APScheduler
- Show upcoming/running/completed jobs
- Allow manual run trigger from dashboard
- Log execution history

## Notes

- **Mock data**: All current endpoint responses are hardcoded for UI testing. Replace with real queries to QuantManager, orchestrator state, and agent registry.
- **CSS**: Uses embedded styles in index.html + external steex.css for system.html. No build step needed.
- **No frontend tooling**: Plain HTML/CSS/JS (fetch API). No React, Vue, Webpack, etc.
- **Old-school aesthetic**: Monospace fonts, black/white, minimal color. Data-first, no skeuomorphism.

## Troubleshooting

**Import error on `frontend.app`:**
```bash
# Make sure venv is activated and STEEX is in PYTHONPATH
source venv/bin/activate
export PYTHONPATH=/Users/lhwri/Desktop/STEEX:$PYTHONPATH
python run_dashboard.py
```

**CSS not loading (404):**
- Check that Flask is serving from `frontend/static/css/steex.css`
- Verify Flask's `static_folder` path in app.py

**Hardcoded data not updating:**
- API endpoints return mock data on purpose (for UI development)
- Replace endpoint handlers in `app.py` with real queries to your trading system

