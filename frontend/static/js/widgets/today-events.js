// widgets/today-events.js — P3-3 "Today's Events" feed.
// Renders the user_updates stream (P0-3) — the SAME records sent to the user via
// Telegram — newest-first, each row clickable to a detail modal. One data source
// backs both the notification and this panel (no divergent copy).
import { register } from "../core/scheduler.js";
import { fetchJSON } from "../core/fetch.js";
import { escapeHtml, attr } from "../core/dom.js";
import { timeOfDay } from "../core/format.js";

const ID = "today-events";
const ENDPOINT = "/api/v1/user_updates?limit=50";

const TYPE_META = {
  buy:         { icon: "▲", cls: "te-buy",    label: "Buy" },
  sell:        { icon: "▼", cls: "te-sell",   label: "Sell" },
  event_trade: { icon: "⚡", cls: "te-event",  label: "Event" },
  big_move:    { icon: "◆", cls: "te-move",   label: "Move" },
  system:      { icon: "●", cls: "te-system", label: "System" },
};

function rowHtml(u) {
  const meta = TYPE_META[u.type] || TYPE_META.system;
  return (
    `<button class="te-row ${meta.cls}" data-update-id="${attr(u.id)}">` +
      `<span class="te-icon" title="${attr(meta.label)}">${meta.icon}</span>` +
      `<span class="te-main">` +
        `<span class="te-title">${escapeHtml(u.title)}</span>` +
        `<span class="te-summary">${escapeHtml(u.summary)}</span>` +
      `</span>` +
      `<span class="te-time">${escapeHtml(timeOfDay(u.ts))}</span>` +
    `</button>`
  );
}

function detailHtml(u) {
  const p = u.payload || {};
  const meta = TYPE_META[u.type] || TYPE_META.system;
  const rows = [];
  const add = (k, v) => { if (v != null && v !== "") rows.push(
    `<div class="md-row"><span class="md-k">${escapeHtml(k)}</span>` +
    `<span class="md-v">${escapeHtml(v)}</span></div>`); };

  add("Ticker", p.ticker);
  add("Shares", p.shares);
  if (p.price != null) add("Price", "$" + p.price);
  if (p.stop != null) add("Stop", "$" + p.stop);
  add("Source", p.figure || p.source);
  if (p.review) add("Review", `${p.review.verdict || ""} — ${p.review.reasoning || ""}`.trim().replace(/^— /, ""));

  const post = p.headline
    ? `<blockquote class="md-post">${escapeHtml(p.headline)}</blockquote>` : "";
  const links = (u.links || []).map((l) =>
    `<a class="md-link" href="${attr(l.href)}" target="_blank" rel="noopener">${escapeHtml(l.label || l.href)}</a>`
  ).join("");

  return (
    `<div class="md-head"><span class="te-icon ${meta.cls}">${meta.icon}</span>` +
      `<span><div class="md-title">${escapeHtml(u.title)}</div>` +
      `<div class="md-time">${escapeHtml(timeOfDay(u.ts))}</div></span></div>` +
    post +
    `<p class="md-summary">${escapeHtml(u.summary)}</p>` +
    (rows.length ? `<div class="md-grid">${rows.join("")}</div>` : "") +
    (links ? `<div class="md-links">${links}</div>` : "")
  );
}

async function openDetail(id) {
  const { openModal } = await import("../components/modal.js");
  try {
    const u = await fetchJSON(`/api/v1/user_updates/${encodeURIComponent(id)}`);
    if (!u || u.error) { openModal('<p class="md-summary">Update not found.</p>'); return; }
    openModal(detailHtml(u));
  } catch (e) {
    openModal(`<p class="md-summary">Failed to load: ${escapeHtml(e.message)}</p>`);
  }
}

function update(data, root) {
  const updates = (data && data.updates) || [];
  if (!updates.length) {
    root.innerHTML = '<div class="te-empty">No events today — the system is quiet.</div>';
    return;
  }
  root.innerHTML = updates.map(rowHtml).join("");
  root.querySelectorAll("[data-update-id]").forEach((el) =>
    el.addEventListener("click", () => openDetail(el.dataset.updateId))
  );
}

register({ id: ID, endpoint: ENDPOINT, update, cadence: 15000 });
