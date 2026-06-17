"""Portfolio equity curve vs S&P 500, with alpha (shared core).

Extracted from the dashboard's HoldingsMixin so non-frontend callers (the
end-of-day recap in particular) can compute the same authoritative
portfolio-vs-SPY performance without importing the Flask layer. The dashboard
mixin now delegates here; behaviour is unchanged.

Pulls the broker's daily portfolio history (the authoritative equity curve),
fetches SPY closes over the same window, rebases both to 0% at the first common
date, and computes alpha = portfolio% - SPY% at each point.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger("steex.performance")

# period -> (Alpaca PortfolioHistory period, SPY lookback days, timeframe).
# Alpaca uses "1A" for one year. "1D" is intraday (portfolio only — a single
# daily SPY close would render as a misleading flat benchmark). YTD is dynamic.
PERF_PERIODS = {
    "1D": ("1D", 2, "5Min"),
    "1W": ("1W", 10, "1D"),
    "1M": ("1M", 35, "1D"),
    "3M": ("3M", 100, "1D"),
    "1Y": ("1A", 370, "1D"),
    "YTD": (None, None, "1D"),
}


def resolve_period(period: str):
    """(alpaca_period, spy_days, timeframe) for a range; YTD is dynamic."""
    if period == "YTD":
        from datetime import date
        today = datetime.now(timezone.utc).date()
        n = max(2, (today - date(today.year, 1, 1)).days)
        return (f"{n}D", n + 5, "1D")
    return PERF_PERIODS[period]


def portfolio_performance(period: str = "1M") -> Dict[str, Any]:
    """Portfolio equity curve vs S&P 500, with alpha, rebased to % return.

    Returns {available: False, reason: ...} when the broker or price data can't
    be reached so callers can show an empty state instead of raising.
    """
    period = period if period in PERF_PERIODS else "1M"
    alpaca_period, spy_days, timeframe = resolve_period(period)
    intraday = timeframe != "1D"

    # --- Portfolio equity curve from the broker -------------------------
    # points: ordered [(label, day_iso, equity)]. label is the x-axis tick
    # (HH:MM intraday, else the date); day_iso aligns to daily SPY bars.
    points = []
    try:
        import os
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
        tc = TradingClient(
            os.environ["ALPACA_API_KEY"],
            os.environ["ALPACA_SECRET_KEY"],
            paper=paper,
        )
        hist = tc.get_portfolio_history(
            GetPortfolioHistoryRequest(period=alpaca_period, timeframe=timeframe)
        )
        seen_day = {}
        for ts, eq in zip(hist.timestamp or [], hist.equity or []):
            if not eq:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            day_iso = dt.date().isoformat()
            if intraday:
                points.append((dt.strftime("%H:%M"), day_iso, float(eq)))
            else:
                seen_day[day_iso] = float(eq)
        if not intraday:
            points = [(d, d, seen_day[d]) for d in sorted(seen_day)]
    except Exception as e:
        logger.debug("Portfolio history unavailable: %s", e)
        return {"available": False, "period": period, "reason": "broker unavailable"}

    if len(points) < 2:
        return {"available": False, "period": period, "reason": "insufficient history"}

    # --- SPY closes (daily) — skipped for intraday ----------------------
    spy_by_date = {}
    if not intraday:
        try:
            from src.data.price import PriceProvider
            df = PriceProvider().get_ohlcv("SPY", days=spy_days)
            if df is not None and "Close" in df.columns:
                for idx, row in df.iterrows():
                    d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                    spy_by_date[d] = float(row["Close"])
        except Exception as e:
            logger.debug("SPY history unavailable: %s", e)

    # Align SPY to each point's day, carrying the last known close forward.
    spy_sorted = sorted(spy_by_date.keys())
    spy_aligned = []
    last = None
    si = 0
    for _label, day_iso, _eq in points:
        while si < len(spy_sorted) and spy_sorted[si] <= day_iso:
            last = spy_by_date[spy_sorted[si]]
            si += 1
        spy_aligned.append(last)

    base_eq = points[0][2]
    base_spy = spy_aligned[0]

    series = []
    for (label, _day, eq), spy_close in zip(points, spy_aligned):
        port_pct = round(100 * (eq / base_eq - 1), 2)
        spy_pct = alpha = None
        if base_spy and spy_close is not None:
            spy_pct = round(100 * (spy_close / base_spy - 1), 2)
            alpha = round(port_pct - spy_pct, 2)
        series.append({
            "date": label,
            "equity": round(eq, 2),
            "portfolio_pct": port_pct,
            "spy_pct": spy_pct,
            "alpha_pct": alpha,
        })

    last_pt = series[-1]
    return {
        "available": True,
        "period": period,
        "intraday": intraday,
        "series": series,
        "summary": {
            "portfolio_return_pct": last_pt["portfolio_pct"],
            "spy_return_pct": last_pt["spy_pct"],
            "alpha_pct": last_pt["alpha_pct"],
            "start_equity": round(base_eq, 2),
            "end_equity": round(points[-1][2], 2),
            "spy_available": base_spy is not None,
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
