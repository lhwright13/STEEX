// core/scheduler.js — ONE poll scheduler + widget registry (replaces the three
// uncoordinated setInterval loops, P-4, and the window-global wiring, P-1/P-3).
//
// A widget is the P0-1 triple's JS leg:
//   register({ id, endpoint, update, cadence?, mount? })
//     id       - the partial's stable root element id (owns only that subtree)
//     endpoint - the one API it fetches (omit for static/mount-only widgets)
//     update(data, root) - render fetched data into root
//     cadence  - poll interval ms (default 5000; 0 = fetch once, no polling)
//     mount(root) - optional one-time setup (event wiring, etc.)
import { fetchJSON } from "./fetch.js";

const registered = [];

export function register(widget) {
  registered.push(widget);
  return widget;
}

export function start() {
  for (const w of registered) {
    const root = document.getElementById(w.id);
    if (!root) continue; // widget's partial not on this page — skip silently
    if (typeof w.mount === "function") {
      try { w.mount(root); } catch (e) { console.error(`[widget ${w.id}] mount`, e); }
    }
    const tick = async () => {
      try {
        const data = w.endpoint ? await fetchJSON(w.endpoint) : null;
        w.update(data, root);
      } catch (e) {
        console.error(`[widget ${w.id}]`, e);
      }
    };
    tick();
    const cadence = w.cadence == null ? 5000 : w.cadence;
    if (cadence > 0) setInterval(tick, cadence);
  }
}
