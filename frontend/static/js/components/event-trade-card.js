// components/event-trade-card.js — P3-6 shared event-trade card renderer.
// One fired event trade: quoted triggering post + entry + stop + live P&L +
// review verdict. Visually distinct (⚡ amber accent) from scheduled trades.
// Used by Today's Events and the event panel from one /events/trade-cards source.
import { escapeHtml, attr } from "../core/dom.js";
import { money, pct } from "../core/format.js";

const VERDICT_CLS = { keep: "etc-keep", exit: "etc-exit", tighten_stop: "etc-tighten" };

export function cardHtml(card) {
  const live = card.live;
  let pnl = '<span class="etc-pnl etc-flat">position closed</span>';
  if (live && live.unrealized_pnl != null) {
    const good = live.unrealized_pnl >= 0;
    pnl =
      `<span class="etc-pnl ${good ? "etc-up" : "etc-down"}">` +
      `${money(live.unrealized_pnl)}${live.unrealized_pct != null ? ` (${pct(live.unrealized_pct)})` : ""}` +
      `</span>`;
  }

  const cell = (label, val) =>
    `<div><span class="etc-k">${label}</span><span class="etc-v">${val}</span></div>`;
  const usd = (v) => (v == null ? "—" : "$" + Number(v).toFixed(2));
  const stop = live && live.stop != null ? live.stop : card.stop;
  const grid = [
    cell("Entry", usd(card.entry_price)),
    cell("Shares", card.shares != null ? card.shares : "—"),
    cell("Stop", usd(stop)),
    cell("Now", live ? usd(live.price) : "—"),
  ].join("");

  const verdict = card.review_verdict
    ? `<span class="etc-verdict ${VERDICT_CLS[card.review_verdict] || ""}">` +
      `${escapeHtml(card.review_verdict)}</span>`
    : "";
  const link = (card.links && card.links[0])
    ? `<a class="etc-link" href="${attr(card.links[0].href)}" target="_blank" rel="noopener">view post ↗</a>`
    : "";

  return (
    `<div class="etc-card">` +
      `<div class="etc-head">` +
        `<span class="etc-ticker">${escapeHtml(card.ticker || "—")}</span>` +
        `<span class="etc-figure">⚡ ${escapeHtml(card.figure || card.source || "event")}</span>` +
        pnl +
      `</div>` +
      (card.headline ? `<blockquote class="etc-post">${escapeHtml(card.headline)}</blockquote>` : "") +
      `<div class="etc-grid">${grid}</div>` +
      `<div class="etc-foot">${verdict}${link}</div>` +
    `</div>`
  );
}

export function renderCards(data, root) {
  const cards = (data && data.cards) || [];
  // Empty -> clear so the container collapses (.etc-wrap:empty); the feed below
  // already conveys "no events", so no redundant empty-state here.
  root.innerHTML = cards.length ? cards.map(cardHtml).join("") : "";
}
