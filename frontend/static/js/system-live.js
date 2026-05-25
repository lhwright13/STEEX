(function() {
  // ========================================================================
  // STEEX System Dashboard Live Data Manager
  // Fetches real agent configs, schedules, and graph structures
  // ========================================================================

  let _currentMode = 'screen';
  let _graphCache = {};
  let _agentsData = {};

  // ── API Fetchers ───────────────────────────────────────────────────────

  async function fetchAgents() {
    try {
      const r = await fetch('/api/v1/system/agents');
      const data = await r.json();
      if (data.agents) {
        // Store for later use
        _agentsData = data.agents;
      }
      return data;
    } catch (e) {
      console.error('Error fetching agents:', e);
      return null;
    }
  }

  async function fetchSchedules() {
    try {
      const r = await fetch('/api/v1/system/schedules');
      return await r.json();
    } catch (e) {
      console.error('Error fetching schedules:', e);
      return null;
    }
  }

  async function fetchGraphStructure(mode) {
    try {
      const r = await fetch(`/api/v1/system/graph/${mode}`);
      return await r.json();
    } catch (e) {
      console.error(`Error fetching graph for ${mode}:`, e);
      return null;
    }
  }

  async function fetchAgentDetail(agentName) {
    try {
      const r = await fetch(`/api/v1/system/agent/${agentName}/detail`);
      return await r.json();
    } catch (e) {
      console.error(`Error fetching agent detail for ${agentName}:`, e);
      return null;
    }
  }

  async function fetchAgentLastOutput(agentName) {
    try {
      const r = await fetch(`/api/v1/system/agent/${agentName}/last-output`);
      return await r.json();
    } catch (e) {
      console.error(`Error fetching agent output for ${agentName}:`, e);
      return null;
    }
  }

  async function fetchRecentRuns() {
    try {
      const r = await fetch('/api/v1/pipeline/recent-runs');
      return await r.json();
    } catch (e) {
      console.error('Error fetching recent runs:', e);
      return null;
    }
  }

  async function fetchRunTrace(runId) {
    try {
      const r = await fetch(`/api/v1/pipeline/trace/${runId}`);
      return await r.json();
    } catch (e) {
      console.error(`Error fetching trace for ${runId}:`, e);
      return null;
    }
  }

  // ── DOM Updaters ───────────────────────────────────────────────────────

  function q(sel) {
    return document.querySelector(sel);
  }

  function qa(sel) {
    return document.querySelectorAll(sel);
  }

  function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '—';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function formatPercent(rate) {
    if (rate === null || rate === undefined) return '—';
    return `${Math.round(rate * 100)}%`;
  }

  function updateHeaderStatus(data) {
    if (!data || !data.runs || data.runs.length === 0) return;

    const latestRun = data.runs[0];
    const statusline = q('.statusline .left');
    if (statusline) {
      const lastRunDate = new Date(latestRun.started_at);
      const lastRunStr = lastRunDate.toLocaleString();
      const spans = statusline.querySelectorAll('span');
      if (spans.length > 1) {
        spans[1].textContent = `Last run: ${lastRunStr}`;
      }
      if (spans.length > 2) {
        const nextRun = new Date(lastRunDate.getTime() + 2.5 * 60 * 60 * 1000); // 2.5 hours from last run
        const nextRunTime = nextRun.toLocaleTimeString();
        const minutesUntil = Math.max(0, Math.floor((nextRun - new Date()) / 60000));
        spans[2].innerHTML = `Next scheduled: ${nextRunTime} <b style="color:#000">(in ${minutesUntil} min)</b>`;
      }
    }
  }

  function updateScheduleHeader() {
    const now = new Date();
    const dateStr = now.toISOString().split('T')[0];
    const timeStr = now.toISOString().split('T')[1].slice(0, 8);

    // Update "Today's Schedule" header
    const scheduleHeader = qa('h2');
    for (let h of scheduleHeader) {
      if (h.textContent.includes("Today's Schedule")) {
        const sub = h.querySelector('.sub');
        if (sub) {
          sub.textContent = `${dateStr} · all modes · UTC`;
        }
        break;
      }
    }

    // Update current time display
    const tools = qa('.tools span');
    for (let t of tools) {
      if (t.textContent.includes('now:')) {
        t.textContent = `now: ${timeStr}`;
        break;
      }
    }
  }

  async function updateAgentGrid(data) {
    if (!data || !data.agents) return;

    const gridBody = q('#agent-grid tbody');
    if (!gridBody) return;

    const rows = data.agents.map(agent => {
      const statusClass = agent.status || 'ready';
      const successStr = agent.success_rate ? `${Math.round(agent.success_rate * 100)}%` : '—';
      const durationStr = agent.avg_duration || '—';
      const lastRunStr = agent.last_run_timestamp ? new Date(agent.last_run_timestamp).toLocaleString() : '—';

      return `
        <tr data-agent="${agent.name}">
          <td class="t"><b>${agent.name.replace(/_/g, ' ').toUpperCase()}</b></td>
          <td class="mute">${agent.role || '—'}</td>
          <td><span class="tag ${statusClass}">${agent.status || 'ready'}</span></td>
          <td class="num">${agent.max_turns}</td>
          <td class="num">${durationStr}</td>
          <td class="num">${successStr}</td>
          <td class="mute">${lastRunStr}</td>
        </tr>
      `;
    }).join('\n');

    gridBody.innerHTML = rows;

    // Add click handlers to select agent
    gridBody.querySelectorAll('tr').forEach(row => {
      row.addEventListener('click', () => {
        const agentName = row.dataset.agent;
        if (agentName) selectAgent(agentName);
      });
    });
  }

  async function updateSchedulesTable(data) {
    if (!data || !data.schedules) return;

    const scheduleBody = q('section:has(h2:contains("Schedules")) tbody');
    if (!scheduleBody) {
      // Try more specific selector
      const tables = qa('table.data tbody');
      if (tables.length > 0) {
        scheduleBody = tables[tables.length - 1]; // Last table is usually schedules
      }
    }

    if (!scheduleBody) return;

    const rows = data.schedules.map(schedule => {
      const nextRunDate = new Date(schedule.next_run);
      const nextRunStr = nextRunDate.toLocaleString();
      const recentRuns = schedule.recent_runs || 0;

      return `
        <tr data-schedule="${schedule.name}">
          <td class="t">${schedule.mode}</td>
          <td>${schedule.description}</td>
          <td class="mute">${schedule.cron}</td>
          <td class="num">${nextRunStr}</td>
          <td class="num">${recentRuns}</td>
          <td><span class="tag ok">${schedule.enabled ? 'enabled' : 'disabled'}</span></td>
        </tr>
      `;
    }).join('\n');

    scheduleBody.innerHTML = rows;
  }

  async function updateAgentDetail(agentName) {
    const detail = await fetchAgentDetail(agentName);
    if (!detail || detail.error) {
      console.warn(`Could not fetch detail for ${agentName}`);
      return;
    }

    // Update agent detail panel
    const detailHost = q('#agent-detail-host');
    if (!detailHost) return;

    // Build detail HTML
    const toolsList = (detail.tools || []).map(t => `<li>${t}</li>`).join('\n');
    const serversList = (detail.external_servers || []).join(', ') || '—';

    detailHost.innerHTML = `
      <div class="agent-detail">
        <header>
          <h3>${detail.name}</h3>
          <span class="tag">${detail.role || '—'}</span>
        </header>
        <div class="ad-body">
          <div class="ad-cell">
            <dt>status</dt><dd><span class="tag">${detail.status || 'ready'}</span></dd>
            <dt>max turns</dt><dd><b>${detail.max_turns || '—'}</b></dd>
            <dt>needs tools</dt><dd>${detail.needs_tools ? 'yes' : 'no'}</dd>
          </div>
          <div class="ad-cell">
            <dt>tools</dt>
            <dd>
              <ul style="margin:0;padding-left:20px;font-size:0.9em">
                ${toolsList || '<li>—</li>'}
              </ul>
            </dd>
          </div>
          <div class="ad-cell">
            <dt>external servers</dt><dd>${serversList}</dd>
            <dt>preprompt</dt><dd><button class="btn sm" id="view-prompt-btn">View Full Prompt</button></dd>
          </div>
          <div class="ad-cell full">
            <details style="cursor:pointer;user-select:none">
              <summary style="padding:8px;background:#f5f5f5;border:1px solid #ddd;border-radius:4px">
                <b>Full Preprompt</b> (${(detail.preprompt || '').length} chars)
              </summary>
              <pre style="margin:8px 0 0;padding:12px;background:#fafafa;border:1px solid #eee;overflow:auto;max-height:300px;font-size:0.85em">
${detail.preprompt || 'Prompt not found'}
              </pre>
            </details>
          </div>
        </div>
        <div class="actions" style="display:flex;gap:8px;padding:12px 0;border-top:1px solid #eee">
          <button class="btn" id="btn-view-output">View Last Output</button>
          <button class="btn" id="btn-download-config" disabled title="Use developer console">Download Config</button>
          <button class="btn" disabled title="Not available in this version">Test Run</button>
          <button class="btn" disabled title="Not available in this version">Rerun Agent</button>
        </div>
      </div>
    `;

    // Wire button handlers
    const viewOutputBtn = q('#btn-view-output');
    if (viewOutputBtn) {
      viewOutputBtn.addEventListener('click', async () => {
        const output = await fetchAgentLastOutput(agentName);
        if (output && output.conclusion) {
          alert(`Last output for ${agentName}:\n${JSON.stringify(output.conclusion, null, 2)}`);
        }
      });
    }
  }

  async function selectAgent(agentName) {
    // Update grid selection
    const agentCards = qa('[data-agent]');
    agentCards.forEach(card => {
      card.classList.toggle('on', card.dataset.agent === agentName);
    });

    // Fetch and display agent detail
    await updateAgentDetail(agentName);

    // Call existing JavaScript function if available
    if (typeof window.selectAgent === 'function' && window.selectAgent !== selectAgent) {
      window.selectAgent(agentName);
    }
  }

  async function updateGraphVisualization(mode) {
    const graph = await fetchGraphStructure(mode);
    if (!graph || graph.error) {
      console.warn(`Could not fetch graph for ${mode}`);
      return;
    }

    _currentMode = mode;
    _graphCache[mode] = graph;

    // Update graph display
    const graphContainer = q('[data-view="graph"]');
    if (!graphContainer) return;

    // For now, show graph summary
    const summary = graph.summary || {};
    const graphInfo = `
      <div style="padding:16px;background:#f5f5f5;border-radius:8px">
        <h3>${mode} Graph Structure</h3>
        <p>Nodes: ${summary.total_nodes || 0} (${summary.critical_nodes || 0} critical)</p>
        <p>Parallel agents: ${summary.parallel_nodes || 0}</p>
        <p><small>Full visualization coming soon</small></p>
      </div>
    `;

    graphContainer.innerHTML = graphInfo;
  }

  async function updateRecentRunsTable(data) {
    if (!data || !data.runs) return;

    // Find the recent runs table
    const tables = qa('table.data tbody');
    if (tables.length < 2) return;
    const runsTable = tables[1]; // Second table should be recent runs

    const rows = data.runs.slice(0, 5).map(run => {
      const startDate = new Date(run.started_at);
      const startStr = startDate.toLocaleString();
      const elapsedStr = formatDuration(run.elapsed);
      const statusTag = run.status === 'complete' ? '<span class="tag ok">OK</span>'
                      : run.status === 'running' ? '<span class="tag warn">RUNNING</span>'
                      : '<span class="tag dim">—</span>';

      return `
        <tr>
          <td class="t">${run.mode || '—'}</td>
          <td>—</td>
          <td>${startStr} ${run.elapsed ? `<span class="warn">(${Math.floor(run.elapsed/60)}m)</span>` : ''}</td>
          <td>—</td>
          <td class="num">${elapsedStr}</td>
          <td class="num">—</td>
          <td>${statusTag}</td>
          <td class="dim">—</td>
        </tr>
      `;
    }).join('\n');

    if (rows) runsTable.innerHTML = rows;
  }

  async function updateExecutionTrace(data) {
    if (!data || !data.traces || data.traces.length === 0) return;

    // Find the trace section header
    const header = qa('section.panel h2');
    let traceSection = null;
    for (let h of header) {
      if (h.textContent.includes('Tool Execution Trace')) {
        traceSection = h.closest('section.panel');
        break;
      }
    }

    if (!traceSection) return;

    const traceBody = traceSection.querySelector('.trace');
    if (!traceBody) return;

    const traceHtml = data.traces.map((t, i) => {
      const toolName = t.tool || `Tool ${i+1}`;
      const duration = t.duration ? `${t.duration.toFixed(2)}s` : '—';
      const status = t.status === 'success' ? '<span class="ok">✓</span>' : '—';
      const input = t.input ? `<span class="dim">${t.input}</span>` : '<span class="dim">(no params)</span>';
      const output = t.output ? `<span class="dim">${t.output}</span>` : '<span class="dim">—</span>';

      return `
<span class="tn">${i+1}.</span> <span class="tn">${toolName}</span> <span class="dim">${duration}</span> ${status}
   in    ${input}
   out   ${output}
      `;
    }).join('\n\n');

    traceBody.innerHTML = traceHtml;

    // Update the header
    const traceHeader = traceSection.querySelector('h2 .sub');
    if (traceHeader) {
      const dateStr = new Date(data.started_at).toLocaleString();
      traceHeader.textContent = `${data.mode || 'unknown'} · run ${data.run_id || '—'} · ${dateStr}`;
    }
  }

  async function refreshLiveData() {
    const agentsData = await fetchAgents();
    const schedulesData = await fetchSchedules();
    const recentRunsData = await fetchRecentRuns();

    if (agentsData && agentsData.agents) {
      // Populate AGENTS object from API for backward compatibility
      if (typeof window.AGENTS !== 'undefined') {
        agentsData.agents.forEach(agent => {
          window.AGENTS[agent.name] = {
            idx: agent.name,
            role: agent.role,
            critical: agent.critical,
            type: agent.critical ? 'Critical' : 'Agent',
            maxTurns: agent.max_turns,
            avgDur: agent.avg_duration || '—',
            success: agent.success_rate ? `${Math.round(agent.success_rate * 100)}%` : '—',
            lastRun: agent.last_run_timestamp ? new Date(agent.last_run_timestamp).toLocaleString() : '—',
            tools: [], // Will be fetched on demand
            mcp: agent.external_servers || [],
            prompt: 'Prompt loaded from API'
          };
        });
      }

      // Rebuild the grid
      if (typeof buildAgentsGrid === 'function') {
        buildAgentsGrid();
      } else {
        await updateAgentGrid(agentsData);
      }
    }

    if (schedulesData) await updateSchedulesTable(schedulesData);
    if (recentRunsData) {
      await updateRecentRunsTable(recentRunsData);
      updateHeaderStatus(recentRunsData);
    }

    // Update schedule header with current date/time
    updateScheduleHeader();

    // Fetch and display trace from most recent run
    if (recentRunsData && recentRunsData.runs && recentRunsData.runs.length > 0) {
      const latestRun = recentRunsData.runs[0];
      if (latestRun.run_id) {
        const traceData = await fetchRunTrace(latestRun.run_id);
        if (traceData) await updateExecutionTrace(traceData);
      }
    }

    // Also refresh graph for current mode
    await updateGraphVisualization(_currentMode);
  }

  function initViewSwitching() {
    // Wire view switching tabs
    qa('[data-view-switch]').forEach(tab => {
      tab.addEventListener('click', () => {
        const viewName = tab.dataset.viewSwitch;
        if (viewName) switchView(viewName);
      });
    });

    // Wire mode switching in graph view
    qa('[data-mode-switch]').forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.modeSwitch;
        if (mode) updateGraphVisualization(mode);
      });
    });
  }

  // ── Public API (inject into global scope if needed) ────────────────────

  window.SystemLive = {
    refreshData: refreshLiveData,
    selectAgent: selectAgent,
    switchView: switchView,
    updateGraph: updateGraphVisualization,
  };

  // ── Initialization ─────────────────────────────────────────────────────

  async function initializeLiveData() {
    // Fetch and populate agent data immediately
    await refreshLiveData();

    // Set up periodic refresh every 10 seconds
    setInterval(refreshLiveData, 10000);
  }

  // Start initialization as soon as DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initViewSwitching();
      initializeLiveData();
    });
  } else {
    // DOM is already loaded
    initViewSwitching();
    initializeLiveData();
  }
})();
