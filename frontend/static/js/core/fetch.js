// core/fetch.js — the single JSON fetcher (replaces the per-file try/catch
// fetchers in refresh.js / system-live.js / perf-chart.js, P-5).
export async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url}`);
  return r.json();
}
