// components/help-popover.js — P3-11 reusable "?" explainer.
// Any element `<button class="help-q" data-help="explanation text">?</button>`
// shows its data-help text in a small popover on click. Self-registers a single
// delegated handler on import; works on both the dashboard and system pages.
let pop = null;

function ensure() {
  if (pop) return pop;
  pop = document.createElement("div");
  pop.className = "help-pop";
  pop.style.display = "none";
  document.body.appendChild(pop);
  document.addEventListener("click", (e) => {
    if (pop.style.display === "none") return;
    if (!pop.contains(e.target) && !e.target.closest(".help-q")) hide();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  window.addEventListener("resize", hide);
  return pop;
}

function hide() { if (pop) pop.style.display = "none"; }

function show(btn) {
  const p = ensure();
  // textContent — the help copy is plain text, never raw HTML.
  p.textContent = btn.getAttribute("data-help") || "";
  p.style.display = "block";
  const r = btn.getBoundingClientRect();
  const top = r.bottom + window.scrollY + 6;
  let left = r.left + window.scrollX;
  // keep within the viewport
  const maxLeft = window.scrollX + document.documentElement.clientWidth - p.offsetWidth - 12;
  if (left > maxLeft) left = Math.max(window.scrollX + 8, maxLeft);
  p.style.top = `${top}px`;
  p.style.left = `${left}px`;
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".help-q");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  if (pop && pop.style.display === "block" && pop._owner === btn) { hide(); return; }
  show(btn);
  if (pop) pop._owner = btn;
});
