"""Trading MCP tools — split from mcp_server.py (P0-2). Names/signatures unchanged."""
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
def generate_buy_list() -> str:
    """Generate buy candidates from ranked stocks and current regime.

    Applies position capacity checks, minimum score filter, cooling-off
    periods, sector concentration limits, and volatility-adjusted sizing.

    Must call rank_candidates and get_regime first.
    """
    mgr = _state.init_manager()

    if _state.ranked is None:
        return _safe_json({"error": "Call rank_candidates first"})
    if _state.regime is None:
        return _safe_json({"error": "Call get_regime first"})

    _state.buy_list = mgr.generate_buy_list(_state.ranked, _state.regime)
    return _safe_json({"buy_list": _state.buy_list, "count": len(_state.buy_list)})

@mcp.tool()
def generate_sell_list() -> str:
    """Generate sell list from exit signals.

    Must call get_exit_signals first.
    """
    mgr = _state.init_manager()

    if _state.exit_signals is None:
        return _safe_json({"error": "Call get_exit_signals first"})

    _state.sell_list = mgr.generate_sell_list(_state.exit_signals)
    return _safe_json({"sell_list": _state.sell_list, "count": len(_state.sell_list)})

@mcp.tool()
def size_buy_list() -> str:
    """Populate price/shares/cost/stop for any unsized entries in the buy list.

    The screen-phase buy list (loaded via load_screen_results) only carries
    ticker/score/reasons — sizing is deferred to execution time so prices are
    fresh. This tool fills price/shares/cost/stop for every entry whose
    price is null, using the current quote, the saved regime's sizing
    multiplier, and the broker's portfolio value/cash.

    Skips (and removes) any entry that fails sizing (no quote, fractional
    shares, or insufficient cash). Returns a summary of what was sized
    vs skipped. Safe to call repeatedly.
    """
    mgr = _state.init_manager()

    if _state.buy_list is None:
        return _safe_json({"error": "Call load_screen_results first"})

    sized, skipped = _size_unsized_entries(mgr, _state.buy_list)
    _state.buy_list = sized
    return _safe_json({
        "sized": [e["ticker"] for e in sized],
        "skipped": skipped,
        "count": len(sized),
    })

def _size_unsized_entries(mgr, entries: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Fill price/shares/cost/stop for any entry whose price is null.

    Returns (sized, skipped). Shared by size_buy_list and execute_entries so a
    skipped size_buy_list step can't silently zero out execution — execute_entries
    sizes on demand and drops only the entries that genuinely can't be sized.
    Idempotent: already-sized entries pass through unchanged.
    """
    # Fast path: nothing to size -> skip the broker portfolio/cash calls entirely.
    if all(
        e.get("price") is not None and e.get("shares") is not None for e in entries
    ):
        return list(entries), []

    # Merge defaults so a partial regime (e.g. {"name": ...} from the screen
    # save node) can't drop sizing_multiplier/entries_allowed and crash sizing.
    regime = {"sizing_multiplier": 1.0, "entries_allowed": True, **(_state.regime or {})}
    portfolio_value = mgr._get_portfolio_value()
    cash = mgr._get_cash()

    # Keep a cash reserve: never deploy below equity * min_cash_reserve_pct.
    # With aggressive sizing (6% x up to 1.25 regime mult x 15 names) this is the
    # guard that stops us over-committing buying power into bounced orders.
    reserve = portfolio_value * mgr.settings.min_cash_reserve_pct
    cash = max(0.0, cash - reserve)

    sized: List[Dict] = []
    skipped: List[Dict] = []

    for entry in entries:
        ticker = entry.get("ticker")
        if entry.get("price") is not None and entry.get("shares") is not None:
            sized.append(entry)
            continue

        price = mgr.price_provider.get_latest_price(ticker)
        if price is None:
            skipped.append({"ticker": ticker, "reason": "no quote"})
            continue

        size_pct = mgr._calculate_position_size_pct(ticker, regime)
        target_value = portfolio_value * size_pct
        whole_shares = int(target_value / price)

        # B4 (agent-mode path): notional/fractional buy when integer shares
        # would under-deploy — rounds to zero, or an expensive name where the
        # rounding drag is worst. Mirrors QuantManager.generate_buy_list.
        use_notional = getattr(mgr.settings, "notional_orders_enabled", False) and (
            whole_shares < 1 or price >= getattr(mgr.settings, "notional_min_price", 500.0)
        )
        if use_notional:
            cost = round(target_value, 2)
            if cost > cash:
                skipped.append({"ticker": ticker, "reason": f"cost ${cost} > cash ${cash:.0f}"})
                continue
            shares = round(target_value / price, 4)
            notional = cost
        else:
            if whole_shares < 1:
                skipped.append({"ticker": ticker, "reason": "size < 1 share"})
                continue
            cost = round(price * whole_shares, 2)
            if cost > cash:
                skipped.append({"ticker": ticker, "reason": f"cost ${cost} > cash ${cash:.0f}"})
                continue
            shares = whole_shares
            notional = None

        stop_price = round(price * (1 - mgr.settings.initial_stop_pct), 2)
        entry.update({
            "price": price,
            "shares": shares,
            "cost": cost,
            "notional": notional,
            "stop": stop_price,
            "size_pct": round(size_pct * 100, 1),
        })
        sized.append(entry)
        cash -= cost

    return sized, skipped

@mcp.tool()
def execute_entries() -> str:
    """Execute buy orders for the generated buy list.

    Places market orders via Alpaca and server-side GTC stop orders
    as crash-proof safety nets. Respects dry-run mode.

    Must call generate_buy_list (or size_buy_list after load_screen_results)
    first so each entry has price/shares/stop populated.
    """
    mgr = _state.init_manager()

    if _state.buy_list is None:
        return _safe_json({"error": "Call generate_buy_list or size_buy_list first"})

    # Size on demand rather than hard-failing the whole batch: if the agent
    # skipped size_buy_list, every entry would be unsized and execution would
    # silently place zero orders. Size any unsized entries now and drop only the
    # ones that genuinely can't be sized (no quote / <1 share / over cash).
    sized, skipped = _size_unsized_entries(mgr, _state.buy_list)
    _state.buy_list = sized
    if not sized:
        return _safe_json({
            "error": "No entries could be sized for execution.",
            "skipped": skipped,
            "executed": [],
            "count": 0,
            "dry_run": _state.dry_run,
        })

    executed = mgr.execute_entries(
        _state.buy_list,
        dry_run=_state.dry_run,
        auto_confirm=True,
    )
    return _safe_json({
        "executed": executed,
        "count": len(executed),
        "skipped": skipped,
        "dry_run": _state.dry_run,
    })

@mcp.tool()
def execute_exits() -> str:
    """Execute sell orders for positions with exit signals.

    Immediate exits (stop-loss, VIX spike) auto-fire. Others are
    recommendations. Cancels server-side stops before managed sells.
    Respects dry-run mode.

    Must call generate_sell_list first.
    """
    mgr = _state.init_manager()

    if _state.sell_list is None:
        return _safe_json({"error": "Call generate_sell_list first"})

    executed = mgr.execute_exits(_state.sell_list, dry_run=_state.dry_run)
    return _safe_json({
        "executed": executed,
        "count": len(executed),
        "dry_run": _state.dry_run,
    })

@mcp.tool()
def get_order_status(order_id: str) -> str:
    """Fetch current status and fill details for a specific order.

    Use this to confirm fills after execute_entries, execute_exits, or
    place_paper_order. More reliable than get_positions for verifying a
    specific order, since position tracking may not update immediately.

    Args:
        order_id: The order UUID returned by a previous order tool call.
    """
    mgr = _state.init_manager()

    if mgr.broker is None:
        return _safe_json({"error": "broker not initialized"})

    client = getattr(mgr.broker, "client", None)
    if client is None:
        return _safe_json({"error": "broker does not expose a queryable client"})

    try:
        order = client.get_order_by_id(order_id)
    except Exception as e:
        return _safe_json({"error": f"get_order_by_id failed: {e}", "order_id": order_id})

    filled_qty = float(order.filled_qty) if order.filled_qty else 0.0
    filled_price = float(order.filled_avg_price) if order.filled_avg_price else 0.0

    return _safe_json({
        "order_id": order_id,
        "symbol": str(order.symbol),
        "side": str(order.side.value) if hasattr(order.side, "value") else str(order.side),
        "qty": float(order.qty) if order.qty else 0.0,
        "status": str(order.status.value) if hasattr(order.status, "value") else str(order.status),
        "filled_qty": filled_qty,
        "filled_avg_price": filled_price,
        "submitted_at": str(order.submitted_at) if order.submitted_at else None,
        "filled_at": str(order.filled_at) if order.filled_at else None,
    })

PAPER_ORDER_MAX_USD = 1000.0

@mcp.tool()
def place_paper_order(ticker: str, dollar_amount: float, side: str) -> str:
    """Place a single paper-mode market order for a specific ticker.

    Constrained entry point for the test_roundtrip mode. Bypasses the
    screener pipeline. Hard-gated to paper mode and capped at $1000 per
    call so it cannot be misused. Skips server-side stop placement -
    intended for short-lived test roundtrips, not production positions.

    Args:
        ticker: Stock symbol e.g. "AAPL".
        dollar_amount: Notional size in USD; converted to whole shares
            using latest price. Must be <= $1000.
        side: "buy" or "sell".
    """
    mgr = _state.init_manager()

    if mgr.broker is None:
        return _safe_json({"error": "broker not initialized"})
    if not getattr(mgr.broker, "paper", False):
        return _safe_json({"error": "place_paper_order is paper-mode only"})
    if side not in ("buy", "sell"):
        return _safe_json({"error": f"side must be 'buy' or 'sell', got '{side}'"})
    if dollar_amount <= 0 or dollar_amount > PAPER_ORDER_MAX_USD:
        return _safe_json({"error": f"dollar_amount must be in (0, {PAPER_ORDER_MAX_USD}]"})

    price = mgr.price_provider.get_latest_price(ticker)
    if price is None or price <= 0:
        return _safe_json({"error": f"no latest price for {ticker}"})

    shares = int(dollar_amount // price)
    if shares < 1:
        return _safe_json({
            "error": f"minimum ${price:.2f} required for 1 share of {ticker} at ${price:.2f}",
            "ticker": ticker,
            "latest_price": price,
            "minimum_dollar_amount": price,
        })

    if _state.dry_run:
        return _safe_json({
            "dry_run": True,
            "ticker": ticker,
            "side": side,
            "shares": shares,
            "intended_price": price,
        })

    if side == "buy":
        result = mgr.broker.buy_market(ticker, shares)
    else:
        result = mgr.broker.sell_market(ticker, shares)

    return _safe_json({
        "ticker": ticker,
        "side": side,
        "shares": shares,
        "intended_price": price,
        "filled_price": result.filled_price,
        "filled_qty": result.filled_qty,
        "order_id": result.order_id,
        "status": result.status,
        "error": result.error,
    })

__all__ = ['generate_buy_list', 'generate_sell_list', 'size_buy_list', 'execute_entries', 'execute_exits', 'get_order_status', 'place_paper_order']
