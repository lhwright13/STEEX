"""Portfolio equity curve vs S&P 500, with alpha (shared core).

Extracted from the dashboard's HoldingsMixin so non-frontend callers (the
end-of-day recap in particular) can compute the same authoritative
portfolio-vs-SPY performance without importing the Flask layer. The dashboard
mixin now delegates here; behaviour is unchanged.

Pulls the broker's daily portfolio history (the authoritative equity curve),
fetches SPY closes over the same window, rebases both to 0% at the first common
date, and computes alpha = portfolio% - SPY% at each point.

Window alignment (B5):
  * 1D ("Today") bases the day change on the broker's prior-close equity
    (``last_equity``) so it captures the overnight gap instead of starting from
    the first intraday bar; a same-day SPY quote vs SPY's prior close surfaces
    a 1-day ``spy_pct``/alpha (previously always null intraday).
  * Daily ranges (1W/1M/...) drop a dangling newer portfolio bar that has no
    matching SPY close yet, so the summary's endpoints line up on the same last
    COMMON complete trading day instead of comparing an intraday-stale daily
    portfolio bar against a carried-forward SPY close.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def _load_portfolio_history(
    alpaca_period: str, timeframe: str, intraday: bool
) -> Tuple[Optional[List[Tuple[str, str, float]]], Optional[float]]:
    """Fetch the broker equity curve and the prior-close equity.

    Returns ``(points, last_equity)`` where ``points`` is an ordered list of
    ``(label, day_iso, equity)`` (label is the x-axis tick — HH:MM intraday,
    else the date) and ``last_equity`` is the broker's prior trading-day close
    equity (Alpaca ``last_equity``), or ``None`` if unavailable. ``points`` is
    ``None`` when the broker can't be reached.
    """
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
        last_equity = None
        try:
            acct = tc.get_account()
            if getattr(acct, "last_equity", None) is not None:
                last_equity = float(acct.last_equity)
        except Exception as e:
            logger.debug("Account last_equity unavailable: %s", e)

        points: List[Tuple[str, str, float]] = []
        seen_day: Dict[str, float] = {}
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
        return points, last_equity
    except Exception as e:
        logger.debug("Portfolio history unavailable: %s", e)
        return None, None


def _load_spy_by_date(spy_days: int) -> Dict[str, float]:
    """SPY daily closes keyed by ISO date over the lookback window."""
    spy_by_date: Dict[str, float] = {}
    try:
        from src.data.price import PriceProvider
        df = PriceProvider().get_ohlcv("SPY", days=spy_days)
        if df is not None and "Close" in df.columns:
            for idx, row in df.iterrows():
                d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                spy_by_date[d] = float(row["Close"])
    except Exception as e:
        logger.debug("SPY history unavailable: %s", e)
    return spy_by_date


def _spy_latest_quote() -> Optional[float]:
    """Same-day SPY quote (for the 1D benchmark), or None."""
    try:
        from src.data.price import PriceProvider
        return PriceProvider().get_latest_price("SPY")
    except Exception as e:
        logger.debug("SPY latest quote unavailable: %s", e)
        return None


def _intraday_performance(period: str, points, last_equity) -> Dict[str, Any]:
    """1D ("Today"): rebase off the broker's prior-close equity so the overnight
    gap is included, and surface a 1-day SPY/alpha from a same-day SPY quote vs
    SPY's prior close when both are available.
    """
    # Prior-close equity is the base; fall back to the first intraday bar only
    # if the broker didn't report last_equity.
    base_eq = last_equity if (last_equity and last_equity > 0) else points[0][2]

    # SPY prior close (last daily bar strictly before today) and today's quote.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    spy_by_date = _load_spy_by_date(PERF_PERIODS["1D"][1] + 5)
    prior_dates = [d for d in sorted(spy_by_date) if d < today_iso]
    spy_prior_close = spy_by_date[prior_dates[-1]] if prior_dates else None
    spy_quote = _spy_latest_quote()

    spy_end_pct = None
    if spy_prior_close and spy_prior_close > 0 and spy_quote is not None:
        spy_end_pct = round(100 * (spy_quote / spy_prior_close - 1), 2)

    series = []
    last_port_pct = None
    for label, _day, eq in points:
        port_pct = round(100 * (eq / base_eq - 1), 2)
        last_port_pct = port_pct
        series.append({
            "date": label,
            "equity": round(eq, 2),
            "portfolio_pct": port_pct,
            # Intraday SPY isn't plotted point-by-point (single daily close);
            # only the summary carries a 1-day benchmark.
            "spy_pct": None,
            "alpha_pct": None,
        })

    alpha_end = None
    if spy_end_pct is not None and last_port_pct is not None:
        alpha_end = round(last_port_pct - spy_end_pct, 2)

    return {
        "available": True,
        "period": period,
        "intraday": True,
        "series": series,
        "summary": {
            "portfolio_return_pct": last_port_pct,
            "spy_return_pct": spy_end_pct,
            "alpha_pct": alpha_end,
            "start_equity": round(base_eq, 2),
            "end_equity": round(points[-1][2], 2),
            "spy_available": spy_end_pct is not None,
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def _daily_performance(period: str, points, spy_days: int) -> Dict[str, Any]:
    """Daily ranges: align portfolio and SPY to the same last COMMON complete
    trading day, dropping a dangling newer portfolio bar with no SPY close yet.
    """
    spy_by_date = _load_spy_by_date(spy_days)
    last_spy_date = max(spy_by_date) if spy_by_date else None

    # Drop trailing portfolio bars newer than the last available SPY close so
    # the summary endpoints compare the same trading day (avoids an
    # intraday-stale daily portfolio bar vs a carried-forward SPY close).
    if last_spy_date is not None:
        aligned = [p for p in points if p[1] <= last_spy_date]
        if len(aligned) >= 2:
            points = aligned
        # If alignment would leave <2 points, keep the raw series rather than
        # returning an empty state — better a slightly-skewed chart than none.

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
        "intraday": False,
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


def portfolio_performance(period: str = "1M") -> Dict[str, Any]:
    """Portfolio equity curve vs S&P 500, with alpha, rebased to % return.

    Returns {available: False, reason: ...} when the broker or price data can't
    be reached so callers can show an empty state instead of raising.
    """
    period = period if period in PERF_PERIODS else "1M"
    alpaca_period, spy_days, timeframe = resolve_period(period)
    intraday = timeframe != "1D"

    points, last_equity = _load_portfolio_history(alpaca_period, timeframe, intraday)
    if points is None:
        return {"available": False, "period": period, "reason": "broker unavailable"}
    if len(points) < 2:
        return {"available": False, "period": period, "reason": "insufficient history"}

    if intraday:
        return _intraday_performance(period, points, last_equity)
    return _daily_performance(period, points, spy_days)
