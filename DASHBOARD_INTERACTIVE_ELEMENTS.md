# Dashboard Interactive Elements Report

## Overview

The new dashboard HTML files contain **35 interactive elements** (buttons, tabs, clickable elements). Currently, **0 are fully functional** - they all need JavaScript handlers or server endpoints to work.

This document catalogues each interactive element, what it's supposed to do, and whether we should keep or remove it.

---

## STEEX Dashboard (index.html)

### 1. Refresh Controls

**Current Status:** NO HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Manual | Button | Switch to manual refresh mode | ✅ KEEP - needs simple JS toggle |
| Auto 5s | Button | Auto-refresh every 5 seconds | ✅ KEEP - needs `setInterval` in JS |
| Pause | Button | Pause live updates | ✅ KEEP - needs pause flag in JS |

**What needs to happen:**
- Create `frontend/static/js/refresh.js` with interval manager
- Toggle between manual/auto modes
- Store refresh state in client-side flag

**Implementation notes:**
```javascript
let autoRefresh = false;
let refreshInterval = null;

function startAutoRefresh() {
  autoRefresh = true;
  refreshInterval = setInterval(updateAllCards, 5000);
}

function stopAutoRefresh() {
  autoRefresh = false;
  if (refreshInterval) clearInterval(refreshInterval);
}
```

---

### 2. Pipeline Control

**Current Status:** NO HANDLER

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Cancel run | Button | Abort current pipeline execution | ⚠️  KEEP FOR NOW - requires endpoint `POST /api/v1/pipeline/cancel` |

**What needs to happen:**
- Create API endpoint to cancel running pipeline
- Requires integration with Orchestrator to send abort signal
- Should show confirmation dialog before cancelling

**Implementation notes:**
- This is more complex - needs to integrate with agent execution
- Maybe defer until later phase

---

### 3. Time Filter Buttons

**Current Status:** NO HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| All | Button | Show all historical picks | 🗑️  **RECOMMEND REMOVE** - no historical data in current spec |
| Today | Button | Show only today's picks | 🗑️  **RECOMMEND REMOVE** - no time filtering in current spec |
| Export CSV | Button | Export candidate list to CSV | ⚠️  KEEP IF TIME - requires CSV generation |

**Why remove "All" and "Today":**
- API endpoints don't support time-based filtering
- Dashboard currently shows latest run only
- Can add later when history is available

**CSV Export implementation (if kept):**
```javascript
function exportCSV() {
  // Gather consensus picks from DOM
  // Convert to CSV format
  // Trigger download
}
```

---

### 4. Learning Suggestions Panel

**Current Status:** NO HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Apply Recommendations | Button | Apply prompt evolution suggestions | ⚠️  KEEP - needs `POST /api/v1/learning/apply` |
| Run Learning Mode | Button | Manually trigger learning pipeline | ⚠️  KEEP - needs `POST /api/v1/learning/run` |
| Dismiss | Button | Hide the suggestions panel | ✅ KEEP - simple JS hide |

**What needs to happen:**
- Dismiss button: just `display: none` on panel
- Apply/Run buttons: call learning API endpoints (may not exist yet)
- Loading indicator while processing

**Implementation notes:**
- Check if `/api/v1/learning/*` endpoints exist
- If not, these buttons should probably be removed or disabled

---

### 5. Settings/Configuration Panel

**Current Status:** NO HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Edit | Button | Edit settings | 🗑️  **RECOMMEND REMOVE** - no settings form in spec |
| Load Preset | Button | Load preset configuration | 🗑️  **RECOMMEND REMOVE** - not in current scope |
| Save As New | Button | Save current config as preset | 🗑️  **RECOMMEND REMOVE** - not in current scope |
| Diff vs Previous | Button | Compare to previous config | 🗑️  **RECOMMEND REMOVE** - not in current scope |

**Why remove:**
- These require a full settings management system
- Not included in DASHBOARD_SPEC.md
- Would clutter the dashboard with incomplete functionality

---

## STEEX System (system.html)

### 1. View Tabs

**Current Status:** NEEDS JS HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Graph | Tab | Show agent execution graph | ✅ KEEP - tab switching is simple JS |
| Schedules | Tab | Show cron schedules | ✅ KEEP - requires JS show/hide of view divs |
| Agents | Tab | Show agent list and details | ✅ KEEP - populates from `/api/v1/system/agents` |
| Tools | Tab | Show MCP tool permissions matrix | ✅ KEEP - populates from agent configs |
| Workflows | Tab | Show execution flow across modes | ⚠️  KEEP FOR NOW - needs workflow data structure |

**What needs to happen:**
```html
<!-- Current HTML uses data-view="name" attributes -->
<div class="v on" data-view="graph">Graph</div>

<!-- JavaScript needs to handle clicks -->
<script>
document.querySelectorAll('[data-view]').forEach(tab => {
  tab.addEventListener('click', () => {
    // Hide all views
    document.querySelectorAll('[data-view]').forEach(v => v.classList.remove('on'));
    // Show this view
    document.querySelector(`[data-view="${tab.dataset.view}"]`).classList.add('on');
  });
});
</script>
```

**Status:** Easy to implement - just CSS class toggles

---

### 2. Agent Graph - Click Handlers

**Current Status:** NEEDS JS HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Click any agent node | Click | Show agent detail modal | ✅ KEEP - needs detail modal JS |

**Example agent nodes in SVG:**
- DataAgent
- RiskAgent  
- analysis_conservative
- analysis_aggressive
- analysis_momentum
- MetaAnalysisAgent
- ManagerAgent
- ExecutionAgent

**What needs to happen:**
```javascript
document.querySelectorAll('[data-agent]').forEach(node => {
  node.addEventListener('click', (e) => {
    const agentName = e.currentTarget.dataset.agent;
    fetch(`/api/v1/system/agent/${agentName}/detail`)
      .then(r => r.json())
      .then(data => showAgentModal(data));
  });
});

function showAgentModal(agentData) {
  // Show modal with:
  // - agent name, role, status
  // - full preprompt text
  // - list of tools with descriptions
  // - external servers
  // - last run info
  // - buttons: View Prompt, Last Output, Test Run, etc.
}
```

**Status:** Moderate complexity - needs modal and data fetch

---

### 3. Schedule Controls

**Current Status:** NO HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| Pause Schedule | Button | Disable a cron schedule | ⚠️  KEEP IF SCHEDULER INTEGRATED - needs `POST /api/v1/schedule/{name}/pause` |
| Run Now | Button | Manually trigger a scheduled run | ⚠️  KEEP IF SCHEDULER INTEGRATED - needs `POST /api/v1/schedule/{name}/run` |

**What needs to happen:**
- Requires APScheduler or similar integration
- Need endpoints to pause/enable schedules
- Need endpoint to trigger manual runs
- Should show confirmation + loading indicator

**Status:** Depends on scheduler integration (may not exist yet)

---

### 4. Agent Detail Modal - Buttons

**Current Status:** NO HANDLERS

| Element | Type | Purpose | Recommendation |
|---------|------|---------|-----------------|
| View Full Prompt | Button | Show complete agent prompt | ⚠️  KEEP - expand prompt text in modal |
| View Last Output | Button | Show agent's last execution output | ⚠️  KEEP IF LOGS AVAILABLE - needs `GET /api/v1/agent/{name}/last-output` |
| Download Output JSON | Button | Download execution result as JSON | ⚠️  KEEP - simple file download |
| Test Run | Button | Run agent in dry-run mode | 🗑️  **RECOMMEND REMOVE** - complex, needs special mode |
| Rerun Agent | Button | Re-execute agent with same inputs | 🗑️  **RECOMMEND REMOVE** - complex, limited use case |

**Implement these:**
- View Full Prompt: Just expand/collapse in modal
- View Last Output: Fetch from `/api/v1/agent/{name}/last-output` (may need to create)
- Download JSON: JavaScript download trigger

**Remove these:**
- Test Run / Rerun Agent: Too complex for now, can add later

---

## Summary Table

### By Status

| Status | Count | Examples |
|--------|-------|----------|
| ✅ KEEP - Easy JS | 8 | Manual, Auto 5s, Pause, Tab switching, Dismiss |
| ✅ KEEP - Needs API | 6 | Agent clicks, Schedule controls, Apply Recommendations |
| ⚠️  KEEP IF | 4 | Cancel run, Export CSV, Test/Rerun (complex) |
| 🗑️  REMOVE | 17 | Edit, Load Preset, Save As, All/Today filters, Test Run, Rerun |

---

## Implementation Priority

### Phase 1: Core (MVP)
```
Priority: HIGH - Do first

1. Tab switching (Graph/Schedules/Agents/Tools/Workflows)
   - Pure CSS/JS, no backend needed
   - Time: 30 min

2. Manual/Auto/Pause refresh buttons
   - Connect to API polling
   - Time: 1 hour

3. Agent node click → detail modal
   - Fetch from /api/v1/system/agent/{name}/detail
   - Time: 1.5 hours

4. Dismiss button for suggestions
   - Simple CSS hide
   - Time: 5 min
```

### Phase 2: Enhanced (After MVP)
```
Priority: MEDIUM - Do if time permits

1. Schedule Pause/Run buttons
   - Requires scheduler integration first
   - Time: 1 hour (after scheduler ready)

2. View Prompt/Output/Download
   - Requires additional endpoints
   - Time: 1 hour

3. Cancel run button
   - Requires orchestrator integration
   - Time: 1 hour
```

### Phase 3: Nice-to-have (Later)
```
Priority: LOW - Defer

1. Export CSV
2. Workflow comparison view
3. Advanced features (Test Run, Rerun Agent, etc.)
```

---

## Buttons to Remove

I recommend removing these elements since they require functionality not yet planned:

```
index.html:
- [ ] Edit (settings management not in scope)
- [ ] Load Preset (configuration system not in scope)
- [ ] Save As New (configuration system not in scope)
- [ ] Diff vs Previous (requires version tracking)
- [ ] All (time filtering not implemented)
- [ ] Today (time filtering not implemented)
- [ ] Export CSV (can defer, not critical)

system.html:
- [ ] Test Run (requires dry-run mode)
- [ ] Rerun Agent (complex, limited use)
```

---

## Next Steps

**Option A: Keep everything as-is**
- Implement handlers for all 35 elements
- Estimated time: 8-10 hours
- Result: Fully featured dashboard

**Option B: Implement Phase 1 only** (RECOMMENDED)
- Implement tabs, refresh, agent clicks, dismiss
- Remove low-priority buttons
- Estimated time: 3-4 hours
- Result: Functional MVP dashboard

**Option C: Remove all incomplete buttons**
- Delete buttons with no implementation
- Keep view-switching and navigation
- Estimated time: 30 min deletions + 3 hours implementation
- Result: Clean, minimal, 100% functional dashboard

---

## Questions for User

Which approach do you prefer?

1. **Keep all buttons** - I'll implement handlers for everything
2. **Phase 1 only (recommended)** - I'll implement core features and remove incomplete buttons
3. **Remove incomplete elements** - Start with a clean, minimal dashboard
4. **Custom** - You tell me which buttons to keep/remove

Let me know, and I'll proceed with implementation!
