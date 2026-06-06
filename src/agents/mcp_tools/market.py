"""Market MCP tools — split from mcp_server.py (P0-2). Names/signatures unchanged."""
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
def sync_broker() -> str:
    """Sync positions and account data from the Alpaca broker.

    Always call this first before any other tools. The broker is the source
    of truth for positions and account value.

    Returns account summary: position count, equity, cash.
    """
    mgr = _state.init_manager()
    mgr._sync_broker()
    count = mgr.position_manager.get_position_count()
    equity = mgr._get_portfolio_value()
    cash = mgr._get_cash()
    return _safe_json({
        "position_count": count,
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "max_positions": mgr.settings.max_positions,
        "broker_connected": mgr.broker is not None,
    })

@mcp.tool()
def prefetch_data() -> str:
    """Warm data caches for the full stock universe.

    Fetches prices, earnings, sentiment, fundamentals, and options data
    concurrently into the L1/L2 cache. Subsequent tool calls will hit
    warm cache instead of making individual API calls.

    Call this before run_screening for best performance.
    """
    mgr = _state.init_manager()
    try:
        from src.data.prefetch import DataPrefetcher
        from src.data.universe import Universe

        universe = Universe()
        tickers = universe.get_sp500()
        prefetcher = DataPrefetcher(
            settings=mgr.settings,
            price_provider=mgr.price_provider,
            universe=universe,
        )
        report = prefetcher.prefetch_all(tickers)
        return _safe_json({
            "tickers_count": len(tickers),
            "prices_fetched": report.prices_fetched,
            "earnings_fetched": report.earnings_fetched,
            "sentiment_fetched": report.sentiment_fetched,
            "fundamentals_fetched": report.fundamentals_fetched,
            "duration_seconds": round(report.duration_seconds, 1),
            "errors": report.errors[:5],
        })
    except Exception as e:
        return _safe_json({"error": str(e)})

@mcp.tool()
def refresh_data() -> str:
    """Fetch fresh insider filings and VIX data.

    Returns health status for each data source: insider transactions,
    VIX level, and price API.
    """
    mgr = _state.init_manager()
    status = mgr.refresh_data()
    return _safe_json(status)

@mcp.tool()
def check_data_health() -> str:
    """Quick health check on data sources without fetching new data.

    Checks insider cache freshness, VIX availability, and price API.
    Returns whether all sources are healthy and any issues found.
    """
    mgr = _state.init_manager()
    health = mgr.check_data_health()
    return _safe_json(health)

@mcp.tool()
def get_regime() -> str:
    """Detect current market regime using multi-factor analysis.

    Analyzes VIX, yield curve, market breadth, and dollar strength
    to classify the regime as: risk_on, cautious, risk_off, or crisis.

    Returns regime name, confidence, VIX level, yield curve status,
    sizing multiplier, and whether new entries are allowed.
    """
    mgr = _state.init_manager()
    _state.regime = mgr.get_regime()
    return _safe_json(_state.regime)

@mcp.tool()
def get_account() -> str:
    """Get broker account information.

    Returns portfolio equity, available cash, and buying power.
    """
    mgr = _state.init_manager()
    equity = mgr._get_portfolio_value()
    cash = mgr._get_cash()
    return _safe_json({
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "broker_connected": mgr.broker is not None,
    })

@mcp.tool()
def get_current_weights() -> str:
    """Get current scoring weights from config.

    Returns the current weight values for each signal factor
    (momentum, insider, volume, sentiment, fundamental, options)
    and the minimum entry score threshold.
    """
    mgr = _state.init_manager()
    s = mgr.settings
    return _safe_json({
        "weight_momentum": s.weight_momentum,
        "weight_insider": s.weight_insider,
        "weight_volume": s.weight_volume,
        "weight_sentiment": s.weight_sentiment,
        "weight_fundamental": s.weight_fundamental,
        "weight_options": s.weight_options,
        "manager_min_score_entry": s.manager_min_score_entry,
        "initial_stop_pct": s.initial_stop_pct,
        "max_hold_days": s.max_hold_days,
    })

__all__ = ['sync_broker', 'prefetch_data', 'refresh_data', 'check_data_health', 'get_regime', 'get_account', 'get_current_weights']
