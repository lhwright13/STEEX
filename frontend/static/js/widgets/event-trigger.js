// widgets/event-trigger.js — P3-4 event-trigger panel.
// Three views over the /api/v1/events/aggregate roll-up (P1-5 records):
//   1. armed strip   — ARMED/DISARMED · figure · trades today X/cap · last poll · cooldowns
//   2. funnel        — seen → named → bullish → passed guardrails → executed (today) + drop reasons
//   3. watching feed — recent posts with verdict chips, clickable to a detail modal
import { register } from "../core/scheduler.js";
import { escapeHtml } from "../core/dom.js";
import { timeAgo, timeOfDay } from "../core/format.js";

const ID = "event-trigger";
const ENDPOINT = "/api/v1/events/aggregate?limit=30";

function armedStrip(s) {
  if (!s) return "";
  const on = s.armed;
  const badge = `<span class="et-badge ${on ? "et-on" : "et-off"}">${on ? "ARMED" : "DISARMED"}</span>`;
  const poll = s.last_poll_seconds == null
    ? "no scans yet"
    : `last poll ${s.last_poll_seconds}s ago`;
  const cds = (s.cooldowns || []).map((c) =>
    `${escapeHtml(c.ticker)} ${c.expires_in_min}m`).join(", ") || "none";
  const figs = (s.figures || []).map(escapeHtml).join(", ") || "—";
  const cell = (label, val) =>
    `<div class="et-cell"><div class="et-k">${label}</div><div class="et-v">${val}</div></div>`;
  return (
    `<div class="et-strip">` +
      `<div class="et-cell"><div class="et-k">Status</div><div class="et-v">${badge}</div></div>` +
      cell("Watching", figs) +
      cell("Trades today", `${s.trades_today || 0} / ${s.cap || 0}`) +
      cell("Heartbeat", escapeHtml(poll)) +
      cell("Regime", escapeHtml(s.regime || "—")) +
      cell("Cooldowns", cds) +
    `</div>`
  );
}

function funnel(f) {
  const today = (f && f.today) || { stages: [], drop_reasons: [] };
  const stages = today.stages || [];
  const top = stages.length ? stages[0].count || 0 : 0;
  const bars = stages.map((st) => {
    const pct = top > 0 ? Math.round(((st.count || 0) / top) * 100) : 0;
    return (
      `<div class="et-fstage">` +
        `<div class="et-flabel">${escapeHtml(st.label)} <b>${st.count || 0}</b></div>` +
        `<div class="et-ftrack"><div class="et-fbar" style="width:${pct}%"></div></div>` +
      `</div>`
    );
  }).join("");
  const drops = (today.drop_reasons || []).map((d) =>
    `<span class="et-drop">${escapeHtml(d.reason)} <b>${d.count}</b></span>`).join("");
  return (
    `<div class="et-funnel">` +
      `<div class="et-section">Why no trade? <span class="et-dim">(today)</span></div>` +
      bars +
      (drops ? `<div class="et-drops">${drops}</div>` : "") +
    `</div>`
  );
}

function chipHtml(chip) {
  const kind = (chip && chip.kind) || "noise";
  const text = (chip && chip.text) || "skipped";
  return `<span class="et-chip et-${kind}">${escapeHtml(text)}</span>`;
}

function feedRow(item, idx) {
  const when = item.published_at ? timeAgo(item.published_at) : "";
  return (
    `<button class="et-row" data-feed-idx="${idx}">` +
      chipHtml(item.chip) +
      `<span class="et-headline">${escapeHtml(item.headline || "(no text)")}</span>` +
      `<span class="et-when">${escapeHtml(when)}</span>` +
    `</button>`
  );
}

async function openFeedDetail(item) {
  const { openModal } = await import("../components/modal.js");
  const rows = [];
  const add = (k, v) => { if (v != null && v !== "") rows.push(
    `<div class="md-row"><span class="md-k">${escapeHtml(k)}</span>` +
    `<span class="md-v">${escapeHtml(v)}</span></div>`); };
  add("Ticker", item.ticker);
  if (item.confidence != null) add("Confidence", Math.round(item.confidence * 100) + "%");
  else if (item.score != null) add("Score", item.score);
  add("Company", item.company);
  add("Outcome", item.classification === "executed" ? "executed" : item.classification);
  add("Guardrail", item.stop_reason);
  add("Figure", item.figure);
  add("Detected", item.decided_at ? timeOfDay(item.decided_at) : null);

  openModal(
    `<div class="md-head">${chipHtml(item.chip)}` +
      `<span><div class="md-title">${escapeHtml(item.ticker || "Post")}</div>` +
      `<div class="md-time">${escapeHtml(item.published_at ? timeOfDay(item.published_at) : "")}</div></span></div>` +
    `<blockquote class="md-post">${escapeHtml(item.headline || "")}</blockquote>` +
    (item.reasoning ? `<p class="md-summary"><b>Resolver:</b> ${escapeHtml(item.reasoning)}</p>` : "") +
    (rows.length ? `<div class="md-grid">${rows.join("")}</div>` : "")
  );
}

function update(data, root) {
  const feed = (data && data.feed) || [];
  const feedHtml = feed.length
    ? feed.map(feedRow).join("")
    : '<div class="et-empty">No posts seen yet — waiting on the next scan.</div>';
  root.innerHTML =
    armedStrip(data && data.status) +
    funnel(data && data.funnel) +
    `<div class="et-section">Watching feed</div>` +
    `<div class="et-feed">${feedHtml}</div>`;
  root.querySelectorAll("[data-feed-idx]").forEach((el) =>
    el.addEventListener("click", () => openFeedDetail(feed[Number(el.dataset.feedIdx)]))
  );
}

register({ id: ID, endpoint: ENDPOINT, update, cadence: 15000 });
