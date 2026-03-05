#!/usr/bin/env python3
"""MCP server exposing STEEX trading tools.

Runs as a stdio server started by the claude CLI. Each tool wraps
existing QuantManager methods so Claude agents can call them during
their reasoning loop.

Usage (via claude CLI MCP config, not directly):
    venv/bin/python src/agents/mcp_server.py --paper
    venv/bin/python src/agents/mcp_server.py --paper --dry-run
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure project root is on path
_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Load .env so broker credentials are available even if parent didn't propagate them
from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path(_project_root) / ".env")

# Redirect rich console output to stderr BEFORE importing QuantManager,
# since MCP uses stdout for the JSON-RPC protocol.
from rich.console import Console  # noqa: E402

import src.strategy.manager as _mgr_mod  # noqa: E402

_mgr_mod.console = Console(stderr=True, quiet=True)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from config.settings import Settings, get_settings  # noqa: E402
from src.strategy.manager import QuantManager  # noqa: E402

logger = logging.getLogger("steex.mcp")

# ---------------------------------------------------------------------------
# Parse arguments (passed via MCP config "args" field)
# ---------------------------------------------------------------------------

_parser = argparse.ArgumentParser(description="STEEX MCP Server")
_parser.add_argument("--paper", action="store_true")
_parser.add_argument("--live", action="store_true")
_parser.add_argument("--dry-run", action="store_true")
_parser.add_argument("--no-broker", action="store_true")
_args, _ = _parser.parse_known_args()

# ---------------------------------------------------------------------------
# Initialize shared state
# ---------------------------------------------------------------------------

_settings: Optional[Settings] = None
_manager: Optional[QuantManager] = None
_dry_run: bool = _args.dry_run

# Intermediate results cached between tool calls within the same session
_pipeline_result = None
_ranked = None
_exit_signals = None
_regime = None
_buy_list = None
_sell_list = None


def _init_manager() -> QuantManager:
    """Lazy-init the QuantManager on first tool call."""
    global _settings, _manager
    if _manager is not None:
        return _manager

    _settings = get_settings()
    if _args.paper:
        _settings.broker_enabled = True
        _settings.broker_paper = True
    elif _args.live:
        _settings.broker_enabled = True
        _settings.broker_paper = False
    elif _args.no_broker:
        _settings.broker_enabled = False

    _manager = QuantManager(settings=_settings)
    return _manager


def _safe_json(obj) -> str:
    """Serialize to JSON, handling non-serializable types."""
    def default(o):
        if hasattr(o, "__dict__"):
            return {k: v for k, v in o.__dict__.items() if not k.startswith("_")}
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, indent=2, default=default)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("steex")


# ---- Utility Tools --------------------------------------------------------


@mcp.tool()
def sync_broker() -> str:
    """Sync positions and account data from the Alpaca broker.

    Always call this first before any other tools. The broker is the source
    of truth for positions and account value.

    Returns account summary: position count, equity, cash.
    """
    mgr = _init_manager()
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


# ---- Data Tools -----------------------------------------------------------


@mcp.tool()
def prefetch_data() -> str:
    """Warm data caches for the full stock universe.

    Fetches prices, earnings, sentiment, fundamentals, and options data
    concurrently into the L1/L2 cache. Subsequent tool calls will hit
    warm cache instead of making individual API calls.

    Call this before run_screening for best performance.
    """
    mgr = _init_manager()
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
    mgr = _init_manager()
    status = mgr.refresh_data()
    return _safe_json(status)


@mcp.tool()
def check_data_health() -> str:
    """Quick health check on data sources without fetching new data.

    Checks insider cache freshness, VIX availability, and price API.
    Returns whether all sources are healthy and any issues found.
    """
    mgr = _init_manager()
    health = mgr.check_data_health()
    return _safe_json(health)


# ---- Risk Tools -----------------------------------------------------------


@mcp.tool()
def get_regime() -> str:
    """Detect current market regime using multi-factor analysis.

    Analyzes VIX, yield curve, market breadth, and dollar strength
    to classify the regime as: risk_on, cautious, risk_off, or crisis.

    Returns regime name, confidence, VIX level, yield curve status,
    sizing multiplier, and whether new entries are allowed.
    """
    global _regime
    mgr = _init_manager()
    _regime = mgr.get_regime()
    return _safe_json(_regime)


@mcp.tool()
def assess_portfolio_risk() -> str:
    """Full portfolio risk assessment.

    Updates trailing stops, syncs server-side stops, checks VIX risk,
    calculates drawdown, and identifies exit signals.

    Returns position count, total value, P&L, drawdown status,
    VIX risk, and number of immediate exits needed.
    """
    mgr = _init_manager()
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
    global _exit_signals
    mgr = _init_manager()
    exits = mgr.get_exit_signals()
    _exit_signals = exits

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
    mgr = _init_manager()
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
def get_account() -> str:
    """Get broker account information.

    Returns portfolio equity, available cash, and buying power.
    """
    mgr = _init_manager()
    equity = mgr._get_portfolio_value()
    cash = mgr._get_cash()
    return _safe_json({
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "broker_connected": mgr.broker is not None,
    })


# ---- Analysis Tools -------------------------------------------------------


@mcp.tool()
def run_screening() -> str:
    """Run the full 5-stage stock screening pipeline.

    Stages:
    1. Universe filter (price, volume, history)
    2. Momentum filter (6-month return, moving averages)
    3. Insider cluster filter (3+ buyers, significant value)
    4. Sentiment filter (stock + geopolitical sentiment)
    5. Fundamental filter (P/E, ROE, debt/equity)

    Returns the screening funnel (universe -> stage counts -> final candidates)
    and the list of final candidates with their screening details.
    """
    global _pipeline_result
    mgr = _init_manager()
    _pipeline_result = mgr.run_screening()

    candidates = []
    for c in _pipeline_result.final_candidates:
        candidates.append({
            "ticker": c.ticker,
            "momentum_6m": round(c.momentum_6m, 3) if c.momentum_6m else None,
            "insider_buyers": c.insider_buyers,
            "insider_score": round(c.insider_score, 1),
            "sentiment_score": round(c.sentiment_score, 1) if c.sentiment_score else None,
            "fundamental_score": round(c.fundamental_score, 1) if c.fundamental_score else None,
            "volume_surge": round(c.volume_surge, 1) if c.volume_surge else None,
            "sector": c.sector,
        })

    return _safe_json({
        "universe_size": _pipeline_result.universe_size,
        "stage_1_passed": _pipeline_result.stage_1_passed,
        "stage_2_passed": _pipeline_result.stage_2_passed,
        "stage_3_passed": _pipeline_result.stage_3_passed,
        "stage_4_passed": _pipeline_result.stage_4_passed,
        "stage_5_passed": _pipeline_result.stage_5_passed,
        "final_candidates": len(_pipeline_result.final_candidates),
        "candidates": candidates,
    })


@mcp.tool()
def rank_candidates() -> str:
    """Rank screening candidates by weighted composite score.

    Weights: momentum (0.30), insider (0.25), volume (0.15),
    sentiment (0.15), fundamental (0.10), options (0.05).

    Must call run_screening first. Returns ranked list with
    composite scores and component breakdowns.
    """
    global _ranked
    mgr = _init_manager()

    if _pipeline_result is None:
        return _safe_json({"error": "Call run_screening first"})

    _ranked = mgr.rank_candidates(_pipeline_result)

    result = []
    for r in _ranked:
        result.append({
            "rank": r.rank,
            "ticker": r.ticker,
            "composite_score": round(r.composite_score, 1),
            "momentum_score": round(r.momentum_score, 1),
            "insider_score": round(r.insider_score, 1),
            "volume_score": round(r.volume_score, 1),
            "sentiment_score": round(r.sentiment_score, 1),
            "fundamental_score": round(r.fundamental_score, 1),
            "options_score": round(r.options_score, 1),
        })

    return _safe_json({"ranked": result, "count": len(result)})


@mcp.tool()
def construct_portfolio() -> str:
    """Apply portfolio construction with correlation and sector constraints.

    Filters ranked candidates for diversification: removes highly correlated
    pairs and enforces sector exposure limits. Uses risk parity weighting.

    Must call rank_candidates first.
    """
    mgr = _init_manager()

    if _ranked is None:
        return _safe_json({"error": "Call rank_candidates first"})

    if mgr.portfolio_constructor is None:
        return _safe_json({"error": "Portfolio constructor not available"})

    try:
        proposal = mgr.portfolio_constructor.select_portfolio(
            _ranked,
            max_picks=mgr.settings.daily_picks,
            max_correlation=mgr.settings.portfolio_max_pairwise_corr,
        )

        selected = [
            {
                "ticker": c.ranked_stock.ticker,
                "score": round(c.ranked_stock.composite_score, 1),
                "sector": c.sector,
                "correlation_to_portfolio": round(c.correlation_to_portfolio, 2),
                "suggested_weight": round(c.suggested_weight, 3),
            }
            for c in proposal.selected
        ]
        rejected = [
            {"ticker": rs.ticker, "reason": reason}
            for rs, reason in proposal.rejected
        ]

        return _safe_json({
            "selected": selected,
            "rejected": rejected,
            "sector_exposure": proposal.sector_exposure,
            "diversification_ratio": round(proposal.diversification_ratio, 2),
        })
    except Exception as e:
        return _safe_json({"error": str(e)})


# ---- Execution Tools ------------------------------------------------------


@mcp.tool()
def generate_buy_list() -> str:
    """Generate buy candidates from ranked stocks and current regime.

    Applies position capacity checks, minimum score filter, cooling-off
    periods, sector concentration limits, and volatility-adjusted sizing.

    Must call rank_candidates and get_regime first.
    """
    global _buy_list
    mgr = _init_manager()

    if _ranked is None:
        return _safe_json({"error": "Call rank_candidates first"})
    if _regime is None:
        return _safe_json({"error": "Call get_regime first"})

    _buy_list = mgr.generate_buy_list(_ranked, _regime)
    return _safe_json({"buy_list": _buy_list, "count": len(_buy_list)})


@mcp.tool()
def generate_sell_list() -> str:
    """Generate sell list from exit signals.

    Must call get_exit_signals first.
    """
    global _sell_list
    mgr = _init_manager()

    if _exit_signals is None:
        return _safe_json({"error": "Call get_exit_signals first"})

    _sell_list = mgr.generate_sell_list(_exit_signals)
    return _safe_json({"sell_list": _sell_list, "count": len(_sell_list)})


@mcp.tool()
def execute_entries() -> str:
    """Execute buy orders for the generated buy list.

    Places market orders via Alpaca and server-side GTC stop orders
    as crash-proof safety nets. Respects dry-run mode.

    Must call generate_buy_list first.
    """
    mgr = _init_manager()

    if _buy_list is None:
        return _safe_json({"error": "Call generate_buy_list first"})

    executed = mgr.execute_entries(
        _buy_list,
        dry_run=_dry_run,
        auto_confirm=True,
    )
    return _safe_json({
        "executed": executed,
        "count": len(executed),
        "dry_run": _dry_run,
    })


@mcp.tool()
def execute_exits() -> str:
    """Execute sell orders for positions with exit signals.

    Immediate exits (stop-loss, VIX spike) auto-fire. Others are
    recommendations. Cancels server-side stops before managed sells.
    Respects dry-run mode.

    Must call generate_sell_list first.
    """
    mgr = _init_manager()

    if _sell_list is None:
        return _safe_json({"error": "Call generate_sell_list first"})

    executed = mgr.execute_exits(_sell_list, dry_run=_dry_run)
    return _safe_json({
        "executed": executed,
        "count": len(executed),
        "dry_run": _dry_run,
    })


@mcp.tool()
def load_screen_results() -> str:
    """Load saved screen results from the screen phase.

    Used by the enter mode to pick up buy candidates generated during
    the earlier screen phase. Validates that results are fresh (< 2 hours).
    """
    global _buy_list
    mgr = _init_manager()
    screen_path = Path(mgr.settings.data_dir) / "screen_results" / "latest.json"

    if not screen_path.exists():
        return _safe_json({"error": "No screen results found. Run screen mode first."})

    with open(screen_path) as f:
        screen_data = json.load(f)

    screen_ts = datetime.fromisoformat(screen_data["timestamp"])
    age_hours = (datetime.now() - screen_ts).total_seconds() / 3600

    if age_hours > 2:
        return _safe_json({
            "error": f"Screen results are {age_hours:.1f}h old (stale)",
            "timestamp": screen_data["timestamp"],
        })

    _buy_list = screen_data.get("buy_list", [])
    return _safe_json({
        "buy_list": _buy_list,
        "count": len(_buy_list),
        "timestamp": screen_data["timestamp"],
        "age_hours": round(age_hours, 1),
        "regime": screen_data.get("regime", {}),
    })


@mcp.tool()
def save_screen_results() -> str:
    """Save current buy list and regime to screen results file.

    Called at the end of the screen phase so the enter phase can pick
    up the candidates later.
    """
    mgr = _init_manager()

    screen_dir = Path(mgr.settings.data_dir) / "screen_results"
    screen_dir.mkdir(parents=True, exist_ok=True)

    screen_data = {
        "timestamp": datetime.now().isoformat(),
        "regime": _regime or {},
        "buy_list": _buy_list or [],
        "ranked_count": len(_ranked) if _ranked else 0,
    }

    screen_path = screen_dir / "latest.json"
    with open(screen_path, "w") as f:
        json.dump(screen_data, f, indent=2, default=str)

    return _safe_json({
        "saved": True,
        "path": str(screen_path),
        "candidates": len(_buy_list) if _buy_list else 0,
    })


# ---- Research Tools -------------------------------------------------------


@mcp.tool()
def run_postmortem() -> str:
    """Analyze recent trades for patterns and recommendations.

    Reviews trades from the past 90 days, categorizes losses,
    calculates score correlation with outcomes, and generates
    actionable recommendations.
    """
    mgr = _init_manager()

    if mgr.postmortem_analyzer is None:
        return _safe_json({"error": "Post-mortem analyzer not available"})

    try:
        from datetime import timedelta
        start = datetime.now() - timedelta(days=mgr.settings.postmortem_lookback_days)
        report = mgr.postmortem_analyzer.generate_report(start, datetime.now())
        return _safe_json({
            "trades_analyzed": report.trades_analyzed,
            "loss_breakdown": report.loss_breakdown,
            "score_correlation": report.score_correlation,
            "avg_missed_upside": report.avg_missed_upside,
            "recommendations": report.recommendations,
        })
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def run_learning_loop() -> str:
    """Run the full self-learning cycle.

    Chains: PostMortem -> Alpha Decay -> Signal Research -> OOS Validation
    -> ConfigWriter. Validates all changes via out-of-sample backtest
    before applying. Refuses to run during market hours.

    Respects dry-run mode.
    """
    mgr = _init_manager()
    result = mgr.run_learning(dry_run=_dry_run)
    if result is None:
        return _safe_json({"error": "Learning disabled in config"})
    return _safe_json(result)


# ---- Report Tools ---------------------------------------------------------


@mcp.tool()
def generate_report(mode: str = "screen") -> str:
    """Generate and save a structured daily report.

    Compiles all data from the current run: data health, regime,
    portfolio, exits, entries, screening, risk alerts, and performance.

    Args:
        mode: The operating mode (screen, enter, monitor, post_market)
    """
    mgr = _init_manager()
    report = mgr.generate_daily_report(mode)
    filepath = mgr.save_report(report)
    return _safe_json({
        "saved": True,
        "path": str(filepath),
        "mode": mode,
        "risk_alerts": report.get("risk_alerts", []),
        "performance": report.get("performance", {}),
    })


@mcp.tool()
def get_trade_history() -> str:
    """Get recent trade history with performance metrics.

    Returns completed trades and aggregate metrics: win rate,
    profit factor, average P&L.
    """
    mgr = _init_manager()
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
