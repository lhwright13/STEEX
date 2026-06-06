// widgets/event-trigger.js — P3-4 event-trigger panel.
// Three views over the /api/v1/events/aggregate roll-up (P1-5 records):
//   1. armed strip   — ARMED/DISARMED · figure · trades today X/cap · last poll · cooldowns
//   2. funnel        — seen → named → bullish → passed guardrails → executed (today) + drop reasons
//   3. watching feed — recent posts with verdict chips, clickable to a detail modal
import { register } from "../core/scheduler.js";
import { fetchJSON } from "../core/fetch.js";
import { escapeHtml, attr } from "../core/dom.js";
import { timeAgo, timeOfDay } from "../core/format.js";

const ID = "event-trigger";

// P3-5: the aggregate URL carries the dropdown-selected figure (null = All).
let selectedFigure = null;
const aggUrl = () =>
  "/api/v1/events/aggregate?limit=30" +
  (selectedFigure ? `&figure=${encodeURIComponent(selectedFigure)}` : "");

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

// P4-1: near-misses — bullish posts blocked by a guardrail (the tuning gold).
function nearMisses(list) {
  const items = list || [];
  if (!items.length) {
    return `<div class="et-section">Near-misses <span class="et-dim">(bullish, blocked)</span></div>` +
      '<div class="et-empty">None — no bullish post has been gated out yet.</div>';
  }
  const rows = items.map((m) => {
    const conf = m.confidence != null ? Math.round(m.confidence * 100) + "%"
      : (m.score != null ? m.score : "—");
    return (
      `<div class="et-nm">` +
        `<span class="et-nm-tk">${escapeHtml(m.ticker || "—")}</span>` +
        `<span class="et-nm-head">${escapeHtml(m.headline || "")}</span>` +
        `<span class="et-nm-conf">${escapeHtml(conf)}</span>` +
        `<span class="et-nm-block">${escapeHtml(m.guardrail || "blocked")}</span>` +
      `</div>`
    );
  }).join("");
  return `<div class="et-section">Near-misses <span class="et-dim">(bullish, blocked)</span></div>` +
    `<div class="et-nms">${rows}</div>`;
}

// P4-2: reaction latency — post -> detected -> decided.
function latency(l) {
  if (!l || !l.count) {
    return `<div class="et-section">Reaction latency</div>` +
      '<div class="et-empty">No timed events yet.</div>';
  }
  const stat = (label, v) =>
    `<div class="et-cell"><div class="et-k">${label}</div><div class="et-v">${v == null ? "—" : v + "s"}</div></div>`;
  return (
    `<div class="et-section">Reaction latency <span class="et-dim">(post → trade decision)</span></div>` +
    `<div class="et-strip et-lat">` +
      stat("Median total", l.median_total_s) +
      stat("p90 total", l.p90_total_s) +
      stat("Median detect", l.median_detect_s) +
      `<div class="et-cell"><div class="et-k">Events</div><div class="et-v">${l.count}</div></div>` +
    `</div>`
  );
}

// P4-3: read-only event-trigger config with a single "?" explainer.
function configBlock(cfg) {
  const params = (cfg && cfg.params) || [];
  if (!params.length) return "";
  const help = params.map((p) => `${p.label}: ${p.help}`).join("\n\n");
  const cells = params.map((p) =>
    `<div class="et-cell"><div class="et-k">${escapeHtml(p.label)}</div>` +
    `<div class="et-v">${escapeHtml(String(p.value))}</div></div>`
  ).join("");
  return (
    `<div class="et-section">Event config <span class="et-dim">(read-only · edit via Claude Code agent)</span>` +
      ` <button class="help-q" data-help="${escapeHtml(help)}">?</button></div>` +
    `<div class="et-strip">${cells}</div>`
  );
}

function renderBody(data, body) {
  if (!body) return;
  const feed = (data && data.feed) || [];
  const feedHtml = feed.length
    ? feed.map(feedRow).join("")
    : '<div class="et-empty">No posts seen yet — waiting on the next scan.</div>';
  body.innerHTML =
    armedStrip(data && data.status) +
    funnel(data && data.funnel) +
    `<div class="et-section">Watching feed</div>` +
    `<div class="et-feed">${feedHtml}</div>` +
    nearMisses(data && data.near_misses) +
    latency(data && data.latency) +
    configBlock(data && data.config);
  body.querySelectorAll("[data-feed-idx]").forEach((el) =>
    el.addEventListener("click", () => openFeedDetail(feed[Number(el.dataset.feedIdx)]))
  );
}

// scheduler tick → render into the stable body (the dropdown header persists).
function update(data, root) {
  renderBody(data, root.querySelector(".et-body"));
}

async function loadFigures(sel) {
  try {
    const data = await fetchJSON("/api/v1/events/figures");
    const figs = (data && data.figures) || [];
    if (!figs.length) return;
    sel.innerHTML =
      '<option value="">All figures</option>' +
      figs.map((f) =>
        `<option value="${attr(f.name)}">${escapeHtml(f.name)}${f.enabled ? "" : " (off)"}</option>`
      ).join("");
  } catch (e) {
    console.error("[event-trigger] figures", e);
  }
}

function mount(root) {
  root.innerHTML =
    '<div class="et-toolbar"><label class="et-toollabel" for="et-figure">Figure</label>' +
    '<select id="et-figure" class="et-select"><option value="">All figures</option></select></div>' +
    '<div class="et-body"><div class="et-empty">Loading event activity…</div></div>';
  const sel = root.querySelector("#et-figure");
  loadFigures(sel);
  sel.addEventListener("change", async () => {
    selectedFigure = sel.value || null;
    try {
      renderBody(await fetchJSON(aggUrl()), root.querySelector(".et-body"));
    } catch (e) {
      console.error("[event-trigger] filter", e);
    }
  });
}

register({ id: ID, endpoint: aggUrl, update, cadence: 15000, mount });
