"""Render the end-of-day performance chart PNG for Telegram.

Two-panel figure from the same `src/portfolio/performance.py` data the recap
text uses (single source of truth for the numbers):
  * top    — 1M portfolio vs SPY (% return, rebased), alpha shaded between
  * bottom — today's intraday equity curve

`render_perf_chart(perf_1m, perf_1d)` is a PURE function over already-fetched
perf dicts, so tests never touch the network. `build_eod_chart(settings)` is
the convenience wrapper that fetches + renders. Headless-safe (Agg backend);
matplotlib is imported lazily so importing this module costs nothing when the
chart is disabled.
"""
from __future__ import annotations

import io
import logging
from typing import Dict, Optional

logger = logging.getLogger("steex.perf_chart")

_STYLE = {
    "bg": "#0f1419", "panel": "#151b23", "grid": "#26303b",
    "text": "#d7dee7", "portfolio": "#4da3ff", "spy": "#8a93a0",
    "alpha_pos": "#2ecc71", "alpha_neg": "#e74c3c",
}


def render_perf_chart(perf_1m: Optional[Dict], perf_1d: Optional[Dict]) -> Optional[bytes]:
    """Render the chart from perf dicts; returns PNG bytes or None if there is
    nothing plottable. Never raises."""
    try:
        m_series = (perf_1m or {}).get("series") or []
        d_series = (perf_1d or {}).get("series") or []
        if len(m_series) < 2 and len(d_series) < 2:
            logger.info("perf chart: no plottable series")
            return None

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        n_panels = (1 if len(m_series) >= 2 else 0) + (1 if len(d_series) >= 2 else 0)
        fig, axes = plt.subplots(
            n_panels, 1, figsize=(8, 3.2 * n_panels), dpi=130,
            gridspec_kw={"hspace": 0.45} if n_panels > 1 else {},
        )
        if n_panels == 1:
            axes = [axes]
        fig.patch.set_facecolor(_STYLE["bg"])
        ax_i = 0

        # ---- Top: 1M portfolio vs SPY -----------------------------------
        if len(m_series) >= 2:
            ax = axes[ax_i]; ax_i += 1
            xs = list(range(len(m_series)))
            port = [p.get("portfolio_pct") for p in m_series]
            spy = [p.get("spy_pct") for p in m_series]
            ax.plot(xs, port, color=_STYLE["portfolio"], lw=2.0, label="Portfolio")
            if any(v is not None for v in spy):
                spy_f = [v if v is not None else float("nan") for v in spy]
                ax.plot(xs, spy_f, color=_STYLE["spy"], lw=1.4, ls="--", label="SPY")
                ax.fill_between(
                    xs, port, spy_f,
                    where=[(p is not None and s is not None and p >= s)
                           for p, s in zip(port, spy)],
                    color=_STYLE["alpha_pos"], alpha=0.15, interpolate=True)
                ax.fill_between(
                    xs, port, spy_f,
                    where=[(p is not None and s is not None and p < s)
                           for p, s in zip(port, spy)],
                    color=_STYLE["alpha_neg"], alpha=0.15, interpolate=True)
            summ = (perf_1m or {}).get("summary") or {}
            bits = []
            if summ.get("portfolio_return_pct") is not None:
                bits.append(f"Portfolio {summ['portfolio_return_pct']:+.2f}%")
            if summ.get("spy_return_pct") is not None:
                bits.append(f"SPY {summ['spy_return_pct']:+.2f}%")
            if summ.get("alpha_pct") is not None:
                bits.append(f"α {summ['alpha_pct']:+.2f}%")
            ax.set_title("1 Month — " + " · ".join(bits) if bits else "1 Month",
                         color=_STYLE["text"], fontsize=10, loc="left")
            ticks = xs[:: max(1, len(xs) // 6)]
            ax.set_xticks(ticks)
            ax.set_xticklabels([m_series[i]["date"][5:] for i in ticks], fontsize=7)
            ax.legend(loc="upper left", fontsize=7, framealpha=0.2,
                      labelcolor=_STYLE["text"])

        # ---- Bottom: today's intraday equity -----------------------------
        if len(d_series) >= 2:
            ax = axes[ax_i]
            xs = list(range(len(d_series)))
            eq = [p.get("equity") for p in d_series]
            ax.plot(xs, eq, color=_STYLE["portfolio"], lw=1.6)
            summ = (perf_1d or {}).get("summary") or {}
            start, end = summ.get("start_equity"), summ.get("end_equity")
            if start:
                ax.axhline(start, color=_STYLE["spy"], lw=0.8, ls=":")
            title = "Today"
            if start and end is not None:
                chg = end - start
                pct = summ.get("portfolio_return_pct")
                title = (f"Today — ${end:,.0f}  ({chg:+,.0f}"
                         + (f", {pct:+.2f}%" if pct is not None else "") + ")")
            ax.set_title(title, color=_STYLE["text"], fontsize=10, loc="left")
            ticks = xs[:: max(1, len(xs) // 6)]
            ax.set_xticks(ticks)
            ax.set_xticklabels([d_series[i]["date"] for i in ticks], fontsize=7)

        for ax in axes:
            ax.set_facecolor(_STYLE["panel"])
            ax.grid(color=_STYLE["grid"], lw=0.5, alpha=0.6)
            for spine in ax.spines.values():
                spine.set_color(_STYLE["grid"])
            ax.tick_params(colors=_STYLE["text"], labelsize=7)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        logger.error("perf chart render failed: %s", e)
        return None


def build_eod_chart(settings=None) -> Optional[bytes]:
    """Fetch performance data and render the EOD chart PNG (or None)."""
    try:
        from src.portfolio.performance import portfolio_performance
        perf_1m = portfolio_performance("1M")
        perf_1d = portfolio_performance("1D")
        return render_perf_chart(
            perf_1m if perf_1m.get("available") else None,
            perf_1d if perf_1d.get("available") else None,
        )
    except Exception as e:
        logger.error("perf chart build failed: %s", e)
        return None
