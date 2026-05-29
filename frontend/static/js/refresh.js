(function() {
  // ========================================================================
  // STEEX Dashboard Auto-Refresh Manager
  // State machine: manual | auto | paused
  // Fetches from all API endpoints and updates DOM elements
  // ========================================================================

  let _mode = 'auto';
  let _timer = null;
  const INTERVAL = 5000;

  // ── Helpers ────────────────────────────────────────────────────────────
  function q(sel) {
    return document.querySelector(sel);
  }

  function qa(sel) {
    return document.querySelectorAll(sel);
  }

  function set(sel, val) {
    const el = q(sel);
    if (el) el.textContent = val;
  }

  function setClass(el, cls, enabled) {
    if (enabled) {
      el.classList.add(cls);
    } else {
      el.classList.remove(cls);
    }
  }

  function setActiveBtn(activeId) {
    qa('#btn-refresh-manual, #btn-refresh-auto, #btn-refresh-pause').forEach(btn => {
      setClass(btn, 'on', btn.id === activeId);
    });
  }

  function stopTimer() {
    if (_timer) {
      clearInterval(_timer);
      _timer = null;
    }
  }

  function downloadText(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ── API Fetchers ───────────────────────────────────────────────────────

  async function fetchPipeline() {
    try {
      const r = await fetch('/api/v1/pipeline/current');
      return await r.json();
    } catch (e) {
      console.error('Error fetching pipeline:', e);
      return null;
    }
  }

  async function fetchVariants() {
    try {
      const r = await fetch('/api/v1/variants/results');
      return await r.json();
    } catch (e) {
      console.error('Error fetching variants:', e);
      return null;
    }
  }

  async function fetchConsensus() {
    try {
      const r = await fetch('/api/v1/consensus');
      return await r.json();
    } catch (e) {
      console.error('Error fetching consensus:', e);
      return null;
    }
  }

  async function fetchScreening() {
    try {
      const r = await fetch('/api/v1/screening/stats');
      return await r.json();
    } catch (e) {
      console.error('Error fetching screening:', e);
      return null;
    }
  }

  async function fetchRegime() {
    try {
      const r = await fetch('/api/v1/regime');
      return await r.json();
    } catch (e) {
      console.error('Error fetching regime:', e);
      return null;
    }
  }

  async function fetchManagerDecision() {
    try {
      const r = await fetch('/api/v1/manager/decision');
      return await r.json();
    } catch (e) {
      console.error('Error fetching manager decision:', e);
      return null;
    }
  }

  async function fetchHoldings() {
    try {
      const r = await fetch('/api/v1/portfolio/holdings');
      return await r.json();
    } catch (e) {
      console.error('Error fetching holdings:', e);
      return null;
    }
  }

  async function fetchEvents() {
    try {
      const r = await fetch('/api/v1/events/recent');
      return await r.json();
    } catch (e) {
      console.error('Error fetching events:', e);
      return null;
    }
  }

  // ── DOM Updaters ───────────────────────────────────────────────────────

  function formatTime(seconds) {
    if (!seconds) return '0:00:00';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function getStatusClass(status) {
    const mapping = {
      'idle': 'mute',
      'running': 'warn',
      'complete': 'ok',
      'failed': 'bad',
      'pending': 'mute',
      'ready': 'ok',
    };
    return mapping[status] || 'dim';
  }

  function updatePipelineDOM(data) {
    if (!data) return;

    // Update header status
    const liveSpan = q('header .statusline .left span:first-child');
    if (liveSpan) {
      const dotClass = data.status === 'running' ? 'live-dot' : '';
      liveSpan.innerHTML = `<span class="${dotClass}"></span><b>${(data.status || 'idle').toUpperCase()}</b>`;
    }

    // Update data-live attributes
    if (data.status) {
      qa('[data-live="pipeline-status"]').forEach(el => {
        el.textContent = data.status;
      });
    }
    if (data.stage) {
      qa('[data-live="pipeline-stage"]').forEach(el => {
        el.textContent = data.stage;
      });
    }
    if (data.current_agent) {
      qa('[data-live="pipeline-agent"]').forEach(el => {
        el.textContent = data.current_agent;
      });
    }
    if (data.elapsed) {
      qa('[data-live="pipeline-elapsed"]').forEach(el => {
        el.textContent = formatTime(data.elapsed);
      });
    }
    if (typeof data.stage_progress === 'number') {
      qa('[data-live="pipeline-progress"]').forEach(el => {
        el.style.width = (data.stage_progress * 100) + '%';
      });
    }
  }

  function updateVariantsDOM(data) {
    if (!data) return;
    // Update variant cards for each of the three variants
    ['conservative', 'aggressive', 'momentum'].forEach(variant => {
      const card = q(`[data-variant="${variant}"]`);
      if (!card || !data[variant]) return;

      const info = data[variant];
      // Update status tag
      const statusTag = card.querySelector('.tag');
      if (statusTag) {
        statusTag.textContent = `${info.status.toUpperCase()} ${info.candidate_count}`;
      }
      // Update stats
      const stats = card.querySelectorAll('.stats span');
      if (stats[0]) stats[0].innerHTML = `cand <b>${info.candidate_count || 0}</b>`;
      if (stats[1]) stats[1].innerHTML = `top <b>${info.avg_score ? info.avg_score.toFixed(1) : '—'}</b>`;
    });
  }

  function updateConsensusDOM(data) {
    if (!data) return;
    const tbody = q('#consensus-table-body');
    if (!tbody) return;

    const rows = [];

    // Build consensus rows from high_conviction and consensus arrays
    if (data.high_conviction && Array.isArray(data.high_conviction)) {
      data.high_conviction.forEach(pick => {
        rows.push(`
          <tr data-csv-row data-ticker="${pick.ticker}" data-score="${pick.score}" data-conviction="high" data-variants="${pick.variants_agreeing || 3}">
            <td class="t">${pick.ticker}</td>
            <td class="num">—</td>
            <td class="num">—</td>
            <td class="num">—</td>
            <td><span class="tag ok">HIGH · 3/3</span></td>
            <td>all variants agree</td>
            <td class="mute">—</td>
            <td class="num">max size</td>
          </tr>
        `);
      });
    }

    if (data.consensus && Array.isArray(data.consensus)) {
      data.consensus.forEach(pick => {
        rows.push(`
          <tr data-csv-row data-ticker="${pick.ticker}" data-score="${pick.score}" data-conviction="medium" data-variants="${pick.variants_agreeing || 2}">
            <td class="t">${pick.ticker}</td>
            <td class="num">—</td>
            <td class="num">—</td>
            <td class="num">—</td>
            <td><span class="tag warn">MED · 2/3</span></td>
            <td>${pick.variants_agreeing === 2 ? 'two agree' : 'awaiting'}</td>
            <td class="mute">—</td>
            <td class="num">std size</td>
          </tr>
        `);
      });
    }

    // Empty state
    if ((!data.high_conviction || data.high_conviction.length === 0) &&
        (!data.consensus || data.consensus.length === 0) &&
        (!data.speculative_excluded || data.speculative_excluded.length === 0)) {
      rows.push(`
        <tr>
          <td colspan="8" style="text-align:center;padding:24px;color:#999;">
            No consensus picks available. Run screening to generate candidates.
          </td>
        </tr>
      `);
    }

    tbody.innerHTML = rows.join('\n');
  }

  function updateRegimeDOM(data) {
    if (!data) return;
    const regimeLabel = data.current ? data.current.toUpperCase() : 'UNKNOWN';
    qa('[data-live="regime-label"]').forEach(el => {
      el.textContent = regimeLabel;
    });
    qa('[data-live="vix-value"]').forEach(el => {
      el.textContent = data.vix ? data.vix.toFixed(1) : '—';
    });
  }

  function updateHeaderTime() {
    const now = new Date();
    const utcString = now.toISOString().split('T')[0] + ' ' + now.toISOString().split('T')[1].slice(0, 8) + ' UTC';
    const serverTimeEl = q('header .statusline .left span:nth-child(2)');
    if (serverTimeEl) {
      serverTimeEl.textContent = 'Server time: ' + utcString;
    }

    // Update last update and next update
    const lastUpdateEl = q('header .statusline .left span:nth-child(3)');
    if (lastUpdateEl) {
      lastUpdateEl.textContent = 'Last update: ' + now.toISOString().split('T')[1].slice(0, 8);
    }

    const nextTime = new Date(now.getTime() + 5000);
    const nextUpdateEl = q('header .statusline .left span:nth-child(4)');
    if (nextUpdateEl) {
      nextUpdateEl.textContent = 'Next update: ' + nextTime.toISOString().split('T')[1].slice(0, 8);
    }
  }

  // ── Refresh All ────────────────────────────────────────────────────────

  function fmtMoney(v) {
    if (v == null) return '—';
    return '$' + Number(v).toLocaleString(undefined, {maximumFractionDigits: 0});
  }

  function updateHoldingsDOM(data) {
    if (!data) return;
    const body = document.getElementById('holdings-table-body');
    const positions = data.positions || [];
    if (body) {
      if (!positions.length) {
        body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:12px;color:#999;">No open positions.</td></tr>';
      } else {
        body.innerHTML = positions.map(p => {
          const pnl = p.unrealized_pnl;
          const pnlCls = pnl == null ? '' : (pnl >= 0 ? 'ok' : 'bad');
          const pnlStr = pnl == null ? '—'
            : `${pnl >= 0 ? '+' : ''}${fmtMoney(pnl)}${p.unrealized_pct != null ? ` (${p.unrealized_pct}%)` : ''}`;
          return `<tr>
            <td class="t"><b>${p.ticker}</b></td>
            <td class="num">${p.shares ?? '—'}</td>
            <td class="num">$${p.entry_price ?? '—'}</td>
            <td class="num">${p.current_price != null ? '$' + p.current_price : '—'}</td>
            <td class="num">${fmtMoney(p.market_value)}</td>
            <td class="num ${pnlCls}">${pnlStr}</td>
            <td class="num">${p.current_stop != null ? '$' + Number(p.current_stop).toFixed(2) : '—'}</td>
            <td class="num">${p.score ?? '—'}</td>
          </tr>`;
        }).join('');
      }
    }

    const s = data.summary || {};
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('pf-equity', fmtMoney(s.equity));
    set('pf-cash', fmtMoney(s.cash));
    set('pf-exposure', s.exposure_pct != null ? s.exposure_pct + '%' : '—');
    set('pf-count', data.count != null ? String(data.count) : '—');
    const upnl = s.total_unrealized_pnl;
    set('pf-upnl', upnl == null ? '—' : `${upnl >= 0 ? '+' : ''}${fmtMoney(upnl)}`);
    const src = document.getElementById('holdings-source');
    if (src) src.textContent = data.live ? 'live · broker' : 'cached';
  }

  function updateEventsDOM(data) {
    const body = document.getElementById('events-table-body');
    if (!body || !data) return;
    const events = data.events || [];
    if (!events.length) {
      body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:12px;color:#999;">No event-triggered trades yet.</td></tr>';
    } else {
      body.innerHTML = events.map(e => {
        const conf = e.confidence != null ? (Math.round(e.confidence * 100) + '%') : '—';
        const v = e.verdict || '—';
        const vCls = v === 'exit' ? 'bad' : (v === 'keep' ? 'ok' : '');
        const head = (e.headline || '').slice(0, 90);
        const link = e.url ? `<a href="${e.url}" target="_blank" rel="noopener">${head}</a>` : head;
        return `<tr>
          <td class="t"><b>${e.ticker || '—'}</b></td>
          <td class="num">${e.price != null ? '$' + e.price : '—'}</td>
          <td class="num">${e.shares ?? '—'}</td>
          <td class="num">${conf}</td>
          <td style="font-size:11px;">${link}</td>
          <td class="${vCls}">${v}</td>
        </tr>`;
      }).join('');
    }
    const sub = document.getElementById('events-lastscan');
    if (sub && data.last_scan) {
      const ls = data.last_scan;
      sub.textContent = `last scan: ${ls.scanned ?? 0} posts · regime ${ls.regime || '—'}`;
    }
  }

  async function refreshAll() {
    updateHeaderTime();

    const [pipeline, variants, consensus, screening, regime, decision, holdings, events] = await Promise.allSettled([
      fetchPipeline(),
      fetchVariants(),
      fetchConsensus(),
      fetchScreening(),
      fetchRegime(),
      fetchManagerDecision(),
      fetchHoldings(),
      fetchEvents(),
    ]);

    // Extract values from PromiseSettledResult
    if (pipeline.status === 'fulfilled') updatePipelineDOM(pipeline.value);
    if (variants.status === 'fulfilled') updateVariantsDOM(variants.value);
    if (consensus.status === 'fulfilled') updateConsensusDOM(consensus.value);
    if (regime.status === 'fulfilled') updateRegimeDOM(regime.value);
    if (holdings.status === 'fulfilled') updateHoldingsDOM(holdings.value);
    if (events.status === 'fulfilled') updateEventsDOM(events.value);
    // screening and decision updates would go here if DOM has those elements
  }

  // ── Refresh Mode Management ─────────────────────────────────────────────

  function setManual() {
    _mode = 'manual';
    setActiveBtn('btn-refresh-manual');
    stopTimer();
    refreshAll();
  }

  function setAuto() {
    _mode = 'auto';
    setActiveBtn('btn-refresh-auto');
    stopTimer();
    _timer = setInterval(refreshAll, INTERVAL);
    refreshAll();
  }

  function setPaused() {
    _mode = 'paused';
    setActiveBtn('btn-refresh-pause');
    stopTimer();
  }

  // ── Action Handlers ────────────────────────────────────────────────────

  async function cancelRun() {
    if (!confirm('Abort the current pipeline run?')) return;
    const btn = q('#btn-cancel-run');
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = 'Cancelling...';
    try {
      const r = await fetch('/api/v1/pipeline/cancel', { method: 'POST' });
      const d = await r.json();
      btn.textContent = d.status === 'cancel_requested' ? 'Cancelled' : origText;
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = origText;
      }, 3000);
    } catch (e) {
      console.error('Cancel failed:', e);
      btn.disabled = false;
      btn.textContent = origText;
    }
  }

  function dismissSignal() {
    const panel = q('#signal-panel');
    if (panel) panel.style.display = 'none';
  }

  async function applyRecs() {
    try {
      const r = await fetch('/api/v1/learning/apply', { method: 'POST' });
      const d = await r.json();
      alert(d.message || 'Recommendations queued');
    } catch (e) {
      console.error('Apply recs failed:', e);
      alert('Error: ' + e.message);
    }
  }

  async function runLearning() {
    try {
      const r = await fetch('/api/v1/learning/run', { method: 'POST' });
      const d = await r.json();
      alert(d.message || d.command || 'Learning mode started');
    } catch (e) {
      console.error('Run learning failed:', e);
      alert('Error: ' + e.message);
    }
  }

  function setFilterTab(which) {
    q('#btn-filter-all').classList.toggle('on', which === 'all');
    q('#btn-filter-today').classList.toggle('on', which === 'today');
    // Future: filter visible rows in executions table
  }

  function exportCSV() {
    const rows = [['Ticker', 'Score', 'Conviction', 'Variants']];
    qa('[data-csv-row]').forEach(r => {
      rows.push([
        r.dataset.ticker || '',
        r.dataset.score || '',
        r.dataset.conviction || '',
        r.dataset.variants || '',
      ]);
    });
    const csv = rows.map(r => r.map(cell => `"${cell}"`).join(',')).join('\n');
    downloadText('consensus_picks.csv', csv, 'text/csv');
  }

  function setHistoryPeriod(period) {
    qa('[data-period]').forEach(t => {
      setClass(t, 'on', t.dataset.period === period);
    });
  }

  // ── Init Buttons ───────────────────────────────────────────────────────

  function initButtons() {
    const refreshManual = q('#btn-refresh-manual');
    const refreshAuto = q('#btn-refresh-auto');
    const refreshPause = q('#btn-refresh-pause');
    const cancelBtn = q('#btn-cancel-run');
    const filterAll = q('#btn-filter-all');
    const filterToday = q('#btn-filter-today');
    const exportBtn = q('#btn-export-csv');
    const applyRecsBtn = q('#btn-apply-recs');
    const runLearningBtn = q('#btn-run-learning');
    const dismissBtn = q('#btn-dismiss-signal');

    if (refreshManual) refreshManual.addEventListener('click', setManual);
    if (refreshAuto) refreshAuto.addEventListener('click', setAuto);
    if (refreshPause) refreshPause.addEventListener('click', setPaused);
    if (cancelBtn) cancelBtn.addEventListener('click', cancelRun);
    if (filterAll) filterAll.addEventListener('click', () => setFilterTab('all'));
    if (filterToday) filterToday.addEventListener('click', () => setFilterTab('today'));
    if (exportBtn) exportBtn.addEventListener('click', exportCSV);
    if (applyRecsBtn) applyRecsBtn.addEventListener('click', applyRecs);
    if (runLearningBtn) runLearningBtn.addEventListener('click', runLearning);
    if (dismissBtn) dismissBtn.addEventListener('click', dismissSignal);

    // Session history tabs
    qa('[data-period]').forEach(tab => {
      tab.addEventListener('click', () => setHistoryPeriod(tab.dataset.period));
    });
  }

  // ── Start ──────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    initButtons();
    setAuto();
  });
})();
