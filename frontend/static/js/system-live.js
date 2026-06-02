(function() {
  // ========================================================================
  // STEEX System Dashboard Live Data Manager
  // Fetches real agent configs, schedules, and graph structures
  // ========================================================================

  // The agent grid, detail panel, graph SVG, AGENTS map, view switching and
  // node selection are all owned by the inline script in system.html. This
  // module only handles what that script does NOT: the schedules table, the
  // statusline next-run, the recent-runs table, and the execution trace.
  const _currentMode = 'screen';

  // ── API Fetchers ───────────────────────────────────────────────────────

  async function fetchSchedules() {
    try {
      const r = await fetch('/api/v1/system/schedules');
      return await r.json();
    } catch (e) {
      console.error('Error fetching schedules:', e);
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

  async function updateHeaderStatus(data) {
    if (!data || !data.runs || data.runs.length === 0) return;

    const latestRun = data.runs[0];
    const statusline = q('.statusline .left');
    if (!statusline) return;
    // Only direct-child spans — the RUNNING span contains a nested .live-dot
    // span, so a plain querySelectorAll('span') shifts every index by one and
    // clobbers the wrong cells.
    const spans = statusline.querySelectorAll(':scope > span');

    // Last run — guard falsy/invalid timestamps. new Date(null) is the 1969
    // epoch (not NaN), so check the raw value before formatting.
    if (spans.length > 1) {
      const raw = latestRun.started_at;
      const d = raw ? new Date(raw) : null;
      spans[1].textContent = `Last run: ${d && !isNaN(d) ? d.toLocaleString() : '—'}`;
    }

    // Next scheduled — use the REAL cron for the current mode from the
    // schedules API rather than guessing "last run + 2.5h".
    if (spans.length > 2) {
      try {
        const r = await fetch('/api/v1/system/schedules');
        const sched = await r.json();
        const mode = (latestRun.mode) || _currentMode;
        const match = (sched.schedules || []).find(s => s.mode === mode || s.name === mode);
        if (match && match.next_run) {
          const next = new Date(match.next_run);
          if (!isNaN(next)) {
            const mins = Math.max(0, Math.floor((next - new Date()) / 60000));
            const when = mins < 1440
              ? `in ${mins} min`
              : `in ${Math.floor(mins / 1440)}d`;
            spans[2].innerHTML = `Next scheduled: ${next.toLocaleString()} <b style="color:#000">(${when})</b>`;
          }
        }
      } catch (e) { /* leave static text */ }
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

  function fmtTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString();
  }

  async function updateSchedulesTable(data) {
    if (!data || !data.schedules) return;

    const scheduleBody = q('#schedules-tbody');
    if (!scheduleBody) return;

    // Columns match the table header: Mode | Frequency | Next Run |
    // Last Run | Avg Dur | Success | Status | Pipeline
    const rows = data.schedules.map(schedule => {
      const avgDur = schedule.avg_duration != null ? `${schedule.avg_duration}s` : '—';
      const success = schedule.success_rate != null ? `${schedule.success_rate}%` : '—';
      const statusTag = schedule.enabled
        ? '<span class="tag ok">enabled</span>'
        : '<span class="tag">disabled</span>';

      return `
        <tr data-schedule="${schedule.name}" data-mode="${schedule.mode}">
          <td class="t">${schedule.name}</td>
          <td title="${schedule.cron}">${schedule.frequency || schedule.cron}</td>
          <td class="num">${fmtTime(schedule.next_run)}</td>
          <td class="num mute">${fmtTime(schedule.last_run)}</td>
          <td class="num">${avgDur}</td>
          <td class="num">${success}</td>
          <td>${statusTag}</td>
          <td class="mute">${schedule.mode}</td>
        </tr>
      `;
    }).join('\n');

    scheduleBody.innerHTML = rows;
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
    const schedulesData = await fetchSchedules();
    const recentRunsData = await fetchRecentRuns();

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
  }

  // ── Public API (inject into global scope if needed) ────────────────────

  window.SystemLive = {
    refreshData: refreshLiveData,
  };

  // ── Initialization ─────────────────────────────────────────────────────

  async function initializeLiveData() {
    await refreshLiveData();
    // Set up periodic refresh every 10 seconds
    setInterval(refreshLiveData, 10000);
  }

  // Start initialization as soon as DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeLiveData);
  } else {
    initializeLiveData();
  }
})();
