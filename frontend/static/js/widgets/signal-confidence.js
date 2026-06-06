// widgets/signal-confidence.js — P4-4. Makes the Signal Confidence & Alpha Decay
// block functional: live per-signal hit-rate vs baseline, decay trend, and alert
// level from AlphaDecayMonitor (/api/v1/signals/health). No placeholder data.
import { register } from "../core/scheduler.js";
import { escapeHtml } from "../core/dom.js";

const ID = "signal-confidence";
const ENDPOINT = "/api/v1/signals/health";

const TREND = { improving: "▲", degrading: "▼", stable: "—" };
const ALERT = { healthy: "sc-ok", watch: "sc-watch", degrading: "sc-bad" };

function pct(v) { return v == null ? "—" : Math.round(v * 100) + "%"; }

function row(s) {
  const trend = TREND[s.trend] || "—";
  return (
    `<div class="sc-row">` +
      `<span class="sc-name">${escapeHtml(s.signal)}</span>` +
      `<span class="sc-rate">${pct(s.current_hit_rate)} <span class="sc-base">vs ${pct(s.baseline_hit_rate)}</span></span>` +
      `<span class="sc-trend sc-${escapeHtml(s.trend)}">${trend} ${escapeHtml(s.trend)}</span>` +
      `<span class="sc-alert ${ALERT[s.alert_level] || "sc-ok"}">${escapeHtml(s.alert_level)}</span>` +
    `</div>`
  );
}

function update(data, root) {
  if (!data || data.available === false) {
    root.innerHTML = '<div class="sc-empty">No closed trades yet — per-signal health appears once trades settle.</div>';
    return;
  }
  const signals = data.signals || [];
  const recs = (data.recommendations || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
  root.innerHTML =
    `<div class="sc-head">` +
      `<span>Overall recent win rate <b>${pct(data.overall_recent_win_rate)}</b></span>` +
      `<span class="sc-dim">${data.total_trades || 0} trades · window ${data.window_size || "—"}</span>` +
    `</div>` +
    `<div class="sc-rows">${signals.map(row).join("")}</div>` +
    (recs ? `<ul class="sc-recs">${recs}</ul>` : "");
}

register({ id: ID, endpoint: ENDPOINT, update, cadence: 30000 });
