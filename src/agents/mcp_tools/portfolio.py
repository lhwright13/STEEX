"""Portfolio MCP tools — split from mcp_server.py (P0-2). Names/signatures unchanged."""
import json  # noqa: F401
import logging
from dataclasses import asdict  # noqa: F401
from datetime import datetime, timedelta  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Tuple  # noqa: F401

from .server import mcp
from . import _state
from ._util import _safe_json

logger = logging.getLogger("steex.mcp")

@mcp.tool()
def assess_portfolio_risk() -> str:
    """Full portfolio risk assessment.

    Updates trailing stops, syncs server-side stops, checks VIX risk,
    calculates drawdown, and identifies exit signals.

    Returns position count, total value, P&L, drawdown status,
    VIX risk, and number of immediate exits needed.
    """
    mgr = _state.init_manager()
    summary = mgr.assess_portfolio_risk()
    return _safe_json(summary)

@mcp.tool()
def get_exit_signals() -> str:
    """Check all positions for exit conditions.

    Evaluates stop-loss, trailing stop, max hold time, VIX spike,
    and dead money signals for every open position.

    Returns list of positions with exit signals, grouped by urgency
    (immediate, end_of_day, next_session).
    """
    mgr = _state.init_manager()
    exits = mgr.get_exit_signals()
    _state.exit_signals = exits

    result = []
    for position, signals in exits:
        result.append({
            "ticker": position.ticker,
            "entry_price": position.entry_price,
            "shares": position.shares,
            "current_stop": position.current_stop,
            "signals": [
                {
                    "reason": s.reason.value,
                    "urgency": s.urgency,
                    "current_price": s.current_price,
                    "gain_pct": round(s.gain_pct * 100, 1),
                }
                for s in signals
            ],
        })
    return _safe_json({"exit_signals": result, "total": len(result)})

@mcp.tool()
def get_positions() -> str:
    """Get all current positions with P&L details.

    Returns each position's ticker, entry price, current price,
    P&L percentage, days held, and current stop level.
    """
    mgr = _state.init_manager()
    positions = mgr.position_manager.get_all_positions()
    result = []
    for pos in positions:
        price = mgr.price_provider.get_latest_price(pos.ticker)
        pnl = pos.calculate_pnl(price) if price else {}
        result.append({
            "ticker": pos.ticker,
            "entry_date": pos.entry_date,
            "entry_price": pos.entry_price,
            "shares": pos.shares,
            "current_price": price,
            "pnl_pct": round(pnl.get("pnl_pct", 0) * 100, 1) if pnl else None,
            "pnl_dollars": round(pnl.get("pnl_dollars", 0), 2) if pnl else None,
            "days_held": pnl.get("days_held") if pnl else None,
            "current_stop": pos.current_stop,
            "score": pos.score,
        })
    return _safe_json({"positions": result, "count": len(result)})

@mcp.tool()
def get_trade_history() -> str:
    """Get recent trade history with performance metrics.

    Returns completed trades and aggregate metrics: win rate,
    profit factor, average P&L.
    """
    mgr = _state.init_manager()
    metrics = mgr.trade_tracker.calculate_metrics()
    trades = mgr.trade_tracker.get_all_trades()

    recent = []
    for t in trades[-20:]:
        recent.append({
            "ticker": t.ticker,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "pnl_pct": round(t.pnl_pct * 100, 1),
            "pnl_dollars": round(t.pnl_dollars, 2),
            "exit_reason": t.exit_reason,
            "hold_days": t.hold_days,
        })

    return _safe_json({
        "total_trades": metrics["total_trades"],
        "win_rate": round(metrics["win_rate"] * 100, 1),
        "profit_factor": round(metrics["profit_factor"], 2),
        "avg_pnl_pct": round(metrics["avg_pnl_pct"] * 100, 1),
        "recent_trades": recent,
    })

__all__ = ['assess_portfolio_risk', 'get_exit_signals', 'get_positions', 'get_trade_history']
