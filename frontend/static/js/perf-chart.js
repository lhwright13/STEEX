/* Portfolio vs S&P 500 performance chart.
 *
 * Zero-dependency inline SVG (matches the dashboard's hand-drawn SVG style).
 * Plots two cumulative-return lines (portfolio solid black, S&P dashed gray)
 * rebased to 0%, shades the gap between them as alpha (green when the
 * portfolio leads, red when it trails), and wires a period toggle.
 */
(function () {
  "use strict";

  const HOST = "perf-chart-host";
  let _period = "1M";
  let _timer = null;

  const NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, text) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }

  function fmtPct(v) {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
  }

  function colorClass(v) {
    if (v == null) return "";
    return v >= 0 ? "ok" : "bad";
  }

  function setStat(id, v) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = fmtPct(v);
    node.classList.remove("ok", "bad");
    const c = colorClass(v);
    if (c) node.classList.add(c);
  }

  function render(data) {
    const host = document.getElementById(HOST);
    if (!host) return;

    if (!data || !data.available || !data.series || data.series.length < 2) {
      host.innerHTML =
        '<div style="text-align:center;padding:40px;color:#999;">' +
        "No performance history available (broker offline or insufficient data)." +
        "</div>";
      setStat("perf-port", null);
      setStat("perf-spy", null);
      setStat("perf-alpha", null);
      return;
    }

    const s = data.summary || {};
    setStat("perf-port", s.portfolio_return_pct);
    setStat("perf-spy", s.spy_return_pct);
    setStat("perf-alpha", s.alpha_pct);

    const series = data.series;
    const hasSpy = s.spy_available;

    // --- geometry -------------------------------------------------------
    const W = 920, H = 320;
    const m = { top: 16, right: 16, bottom: 28, left: 48 };
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;

    const port = series.map(d => d.portfolio_pct);
    const spy = series.map(d => d.spy_pct).filter(v => v != null);
    let lo = Math.min(0, ...port, ...spy);
    let hi = Math.max(0, ...port, ...spy);
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;

    const n = series.length;
    const x = i => m.left + (n === 1 ? 0 : (i / (n - 1)) * iw);
    const y = v => m.top + ih - ((v - lo) / (hi - lo)) * ih;

    const svg = el("svg", {
      viewBox: `0 0 ${W} ${H}`,
      width: "100%",
      preserveAspectRatio: "xMidYMid meet",
      role: "img",
      "aria-label": "Portfolio performance versus S&P 500",
      style: "font-family:var(--mono);font-size:11px;",
    });

    // --- horizontal gridlines + y labels --------------------------------
    const ticks = 5;
    for (let t = 0; t <= ticks; t++) {
      const val = lo + (t / ticks) * (hi - lo);
      const yy = y(val);
      svg.appendChild(el("line", {
        x1: m.left, y1: yy, x2: W - m.right, y2: yy,
        stroke: "var(--grid)", "stroke-width": 1,
      }));
      svg.appendChild(el("text", {
        x: m.left - 6, y: yy + 3, "text-anchor": "end", fill: "var(--muted)",
      }, val.toFixed(1) + "%"));
    }

    // --- zero baseline (emphasised) -------------------------------------
    const y0 = y(0);
    svg.appendChild(el("line", {
      x1: m.left, y1: y0, x2: W - m.right, y2: y0,
      stroke: "#000", "stroke-width": 1, "stroke-dasharray": "2 2", opacity: 0.5,
    }));

    // --- x labels (first, middle, last) ---------------------------------
    [0, Math.floor((n - 1) / 2), n - 1].forEach((i, k) => {
      svg.appendChild(el("text", {
        x: x(i), y: H - 8,
        "text-anchor": k === 0 ? "start" : k === 2 ? "end" : "middle",
        fill: "var(--muted)",
      }, series[i].date.slice(5)));
    });

    // --- alpha shading between the two lines (only where SPY exists) -----
    if (hasSpy) {
      // Build contiguous segments split by alpha sign so each fills cleanly.
      let seg = [];
      let segSign = null;
      const flush = () => {
        if (seg.length < 2) { seg = []; return; }
        const top = seg.map(i => `${x(i)},${y(series[i].portfolio_pct)}`);
        const bot = seg.slice().reverse().map(i => `${x(i)},${y(series[i].spy_pct)}`);
        svg.appendChild(el("polygon", {
          points: top.concat(bot).join(" "),
          fill: segSign >= 0 ? "rgba(0,119,42,0.18)" : "rgba(184,0,0,0.16)",
          stroke: "none",
        }));
        // keep the last point so adjacent fills meet with no gap
        seg = [seg[seg.length - 1]];
      };
      for (let i = 0; i < n; i++) {
        if (series[i].spy_pct == null || series[i].alpha_pct == null) { flush(); seg = []; segSign = null; continue; }
        const sign = series[i].alpha_pct >= 0 ? 1 : -1;
        if (segSign === null) segSign = sign;
        if (sign !== segSign) { flush(); segSign = sign; }
        seg.push(i);
      }
      flush();
    }

    // --- S&P line (dashed gray) -----------------------------------------
    if (hasSpy) {
      const pts = [];
      series.forEach((d, i) => {
        if (d.spy_pct != null) pts.push(`${x(i)},${y(d.spy_pct)}`);
      });
      svg.appendChild(el("polyline", {
        points: pts.join(" "), fill: "none", stroke: "#888",
        "stroke-width": 1.5, "stroke-dasharray": "5 4",
      }));
    }

    // --- portfolio line (solid black) -----------------------------------
    const portPts = series.map((d, i) => `${x(i)},${y(d.portfolio_pct)}`).join(" ");
    svg.appendChild(el("polyline", {
      points: portPts, fill: "none", stroke: "#000", "stroke-width": 2,
    }));

    // --- end-point markers ----------------------------------------------
    svg.appendChild(el("circle", {
      cx: x(n - 1), cy: y(port[n - 1]), r: 3, fill: "#000",
    }));
    if (hasSpy && series[n - 1].spy_pct != null) {
      svg.appendChild(el("circle", {
        cx: x(n - 1), cy: y(series[n - 1].spy_pct), r: 3, fill: "#888",
      }));
    }

    host.innerHTML = "";
    host.appendChild(svg);
  }

  async function load() {
    try {
      const r = await fetch(`/api/v1/portfolio/performance?period=${_period}`);
      render(await r.json());
    } catch (e) {
      console.error("perf fetch failed", e);
      render(null);
    }
  }

  function setPeriod(p) {
    _period = p;
    document.querySelectorAll("#perf-period-tabs .tab").forEach(t =>
      t.classList.toggle("on", t.dataset.perfPeriod === p));
    load();
  }

  function init() {
    document.querySelectorAll("#perf-period-tabs .tab").forEach(t => {
      t.addEventListener("click", () => setPeriod(t.dataset.perfPeriod));
    });
    load();
    // Refresh on the same cadence as the rest of the dashboard.
    _timer = setInterval(load, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
