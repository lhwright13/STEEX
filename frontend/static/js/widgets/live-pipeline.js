// widgets/live-pipeline.js — P3-2 Live Pipeline.
// Shows whether a run is active right now and the per-agent lanes (status +
// tools called, from H4 trace telemetry) of the run in flight — or the most
// recent run that produced traces, flagged as such. Cron one-shots persist no
// mid-run progress, so this reflects real run state, not a fabricated stream.
import { register } from "../core/scheduler.js";
import { escapeHtml } from "../core/dom.js";
import { timeAgo } from "../core/format.js";

const ID = "live-pipeline";
const ENDPOINT = "/api/v1/pipeline/live";

const DOT = { ok: "lp-ok", failed: "lp-failed", running: "lp-running" };

function laneHtml(l) {
  const tools = (l.tools_called || []).slice(0, 8)
    .map((t) => `<span class="lp-tool">${escapeHtml(t)}</span>`).join("");
  const more = l.tool_count > 8 ? `<span class="lp-tool lp-more">+${l.tool_count - 8}</span>` : "";
  const dur = l.duration_seconds != null ? `${Math.round(l.duration_seconds)}s` : "";
  return (
    `<div class="lp-lane">` +
      `<span class="lp-dot ${DOT[l.status] || "lp-ok"}"></span>` +
      `<span class="lp-agent">${escapeHtml(l.agent || "—")}</span>` +
      `<span class="lp-tools">${tools}${more}${(l.tool_count ? "" : '<span class="lp-none">no tools</span>')}</span>` +
      `<span class="lp-dur">${escapeHtml(dur)}</span>` +
    `</div>`
  );
}

function update(data, root) {
  if (!data || data.error) {
    root.innerHTML = '<div class="lp-empty">Pipeline state unavailable.</div>';
    return;
  }
  const active = data.active;
  const lanes = data.lanes || [];
  const banner = active
    ? `<span class="lp-live"><span class="lp-pulse"></span>LIVE</span> ` +
      `<b>${escapeHtml(data.mode || "")}</b> running · ${data.elapsed || 0}s` +
      (data.current_agent ? ` · ${escapeHtml(data.current_agent)}` : "")
    : `<span class="lp-idle">IDLE</span> no active run` +
      (lanes.length ? ` · showing last <b>${escapeHtml(data.mode || "")}</b> run` : "");

  if (!lanes.length) {
    root.innerHTML = `<div class="lp-banner">${banner}</div>` +
      '<div class="lp-empty">No agent runs recorded yet.</div>';
    return;
  }
  root.innerHTML =
    `<div class="lp-banner">${banner}` +
      (data.source === "last_run" ? ' <span class="lp-tag">last run</span>' : "") +
    `</div>` +
    `<div class="lp-lanes">${lanes.map(laneHtml).join("")}</div>`;
}

register({ id: ID, endpoint: ENDPOINT, update, cadence: 5000 });
