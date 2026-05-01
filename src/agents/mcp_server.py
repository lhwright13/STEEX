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

# av-mcp installs a conflicting top-level `src/` into site-packages and
# Python's PathFinder wins over the editable-install finder.  Inserting the
# project root first ensures STEEX's src/ is found before site-packages.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

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
def get_order_status(order_id: str) -> str:
    """Fetch current status and fill details for a specific order.

    Use this to confirm fills after execute_entries, execute_exits, or
    place_paper_order. More reliable than get_positions for verifying a
    specific order, since position tracking may not update immediately.

    Args:
        order_id: The order UUID returned by a previous order tool call.
    """
    mgr = _init_manager()

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


# ---- Test / Paper-only Direct Order Tool ---------------------------------

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
    mgr = _init_manager()

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

    if _dry_run:
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


# ---- Granular Learning Tools ----------------------------------------------


@mcp.tool()
def check_alpha_decay() -> str:
    """Check per-signal health for alpha decay.

    Analyzes rolling hit rates for each scoring signal (momentum,
    insider, volume, sentiment, fundamental, options) and flags
    degrading signals where hit rate has dropped significantly.

    Returns signal health for each factor, list of degrading signals,
    watch list, and recommendations.
    """
    mgr = _init_manager()
    try:
        from src.research.alpha_monitor import AlphaDecayMonitor
        monitor = AlphaDecayMonitor(settings=mgr.settings)
        report = monitor.generate_report()
        return _safe_json(report)
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def run_signal_research() -> str:
    """Run signal hypothesis testing and weight optimization.

    Generates a historical feature matrix, tests each signal's
    predictive power via t-tests and information coefficients,
    identifies redundant signal pairs, and optimizes weights.

    This is a read-only analysis. Use propose_config_changes
    to actually propose weight updates, then validate_oos to
    test them, and apply_config_changes to write them.

    Returns hypotheses, redundant pairs, recommended weights,
    and feature count.
    """
    mgr = _init_manager()
    try:
        from src.learning.loop import LearningLoop
        loop = LearningLoop(settings=mgr.settings)
        result = loop._run_signal_research()
        if result is None:
            return _safe_json({"error": "Signal research returned no results"})
        return _safe_json(result)
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def validate_oos(proposed_weights: str) -> str:
    """Run walk-forward out-of-sample validation on proposed weights.

    Executes a 2-fold walk-forward backtest with the proposed weights
    and checks that average OOS Sharpe > 0 and win_rate > 50%.

    Args:
        proposed_weights: JSON string mapping signal names to weights,
            e.g. '{"weight_momentum": 0.30, "weight_insider": 0.25}'

    Returns validation result: passed, sharpe, win_rate, fold details.
    """
    mgr = _init_manager()
    try:
        weights = json.loads(proposed_weights)
    except json.JSONDecodeError:
        return _safe_json({"error": "Invalid JSON for proposed_weights"})

    try:
        from src.learning.loop import LearningLoop
        loop = LearningLoop(settings=mgr.settings)
        result = loop._run_oos_validation(weights)
        if result is None:
            return _safe_json({"error": "OOS validation returned no results"})
        return _safe_json(result)
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def propose_config_changes(changes: str, reason: str) -> str:
    """Validate and bound-check proposed config changes.

    Checks each parameter against PARAM_BOUNDS (min, max, max delta
    per cycle), clamps values to safe ranges, and normalizes weights
    to sum to 1.0.

    This does NOT apply changes. Call apply_config_changes to write.

    Args:
        changes: JSON string mapping parameter names to proposed values,
            e.g. '{"weight_momentum": 0.35, "weight_insider": 0.20}'
        reason: Human-readable reason for the changes

    Returns validated proposal with clamped values and any warnings.
    """
    try:
        change_dict = json.loads(changes)
    except json.JSONDecodeError:
        return _safe_json({"error": "Invalid JSON for changes"})

    try:
        from src.learning.config_writer import ConfigWriter
        writer = ConfigWriter()
        result = writer.propose_changes(change_dict, source="learning_agent", reason=reason)
        return _safe_json(result)
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def apply_config_changes(validated_proposal: str, reason: str) -> str:
    """Apply validated config changes to config.yaml.

    Writes bounds-checked changes to config.yaml with a full audit
    trail. Refuses to run during market hours (9:30-16:00 ET).

    Args:
        validated_proposal: JSON string - the output from propose_config_changes
        reason: Human-readable reason for applying

    Returns: applied changes and audit entry.
    """
    if _dry_run:
        return _safe_json({"applied": False, "reason": "Dry run mode - changes not applied"})

    try:
        from src.learning.loop import _is_market_hours
        if _is_market_hours():
            return _safe_json({"applied": False, "reason": "Cannot apply during market hours"})
    except ImportError:
        logger.warning("Could not import _is_market_hours - blocking config changes as safety precaution")
        return _safe_json({"applied": False, "reason": "Market hours check unavailable - refusing to apply"})

    try:
        proposal = json.loads(validated_proposal)
    except json.JSONDecodeError:
        return _safe_json({"error": "Invalid JSON for validated_proposal"})

    try:
        from src.learning.config_writer import ConfigWriter
        writer = ConfigWriter()
        result = writer.apply_changes(proposal, source="learning_agent", reason=reason)
        return _safe_json(result)
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def get_learning_journal(limit: int = 20) -> str:
    """Get recent learning journal entries.

    Returns timestamped log of learning actions: postmortem analyses,
    alpha decay checks, signal research, weight recommendations,
    OOS validations, and config changes.

    Args:
        limit: Maximum number of entries to return (default 20)
    """
    try:
        from src.learning.journal import LearningJournal
        journal = LearningJournal()
        entries = journal.get_journal(limit=limit)
        return _safe_json({"entries": entries, "count": len(entries)})
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def get_learning_gaps() -> str:
    """Get open knowledge gaps flagged by the learning system.

    Returns gaps requiring human review: missing data, degrading
    signals, parameter drift, new regimes, implementation needs.
    """
    try:
        from src.learning.journal import LearningJournal
        journal = LearningJournal()
        gaps = journal.get_gaps(include_resolved=False)
        return _safe_json({"gaps": gaps, "count": len(gaps)})
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def get_config_change_history(limit: int = 20) -> str:
    """Get audit trail of config parameter changes.

    Returns recent config changes with before/after values,
    source, reason, and timestamps.

    Args:
        limit: Maximum number of entries to return (default 20)
    """
    try:
        from src.learning.config_writer import ConfigWriter
        writer = ConfigWriter()
        history = writer.get_history(limit=limit)
        return _safe_json({"history": history, "count": len(history)})
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def get_current_weights() -> str:
    """Get current scoring weights from config.

    Returns the current weight values for each signal factor
    (momentum, insider, volume, sentiment, fundamental, options)
    and the minimum entry score threshold.
    """
    mgr = _init_manager()
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


@mcp.tool()
def get_pending_recommendations() -> str:
    """Get pending agent meta-recommendations that haven't been applied.

    Returns accumulated prompt, tool, and process suggestions from
    agent self-improvement metadata across recent sessions.
    """
    try:
        from src.agents.evolution import PromptEvolver
        evolver = PromptEvolver()
        pending = evolver.get_pending_recommendations()
        return _safe_json({"recommendations": pending, "count": len(pending)})
    except Exception as e:
        return _safe_json({"error": str(e)})


@mcp.tool()
def cross_reference_findings(
    postmortem_result: str = "{}",
    alpha_decay_result: str = "{}",
) -> str:
    """Cross-reference agent recommendations with trade outcomes.

    Maps pending agent suggestions to categories: parameter-relevant,
    prompt-relevant, tool-relevant (flagged for human), and
    process-relevant (flagged for human). Correlates trade loss
    patterns with agent observations.

    Args:
        postmortem_result: JSON string from run_postmortem output
        alpha_decay_result: JSON string from check_alpha_decay output

    Returns categorized insights and correlations.
    """
    try:
        pm = json.loads(postmortem_result)
    except json.JSONDecodeError:
        pm = {}

    try:
        ad = json.loads(alpha_decay_result)
    except json.JSONDecodeError:
        ad = {}

    try:
        from src.agents.evolution import PromptEvolver
        from src.learning.cross_reference import CrossReferencer

        evolver = PromptEvolver()
        pending = evolver.get_pending_recommendations()

        xref = CrossReferencer()
        result = xref.cross_reference(pm, ad, pending)
        return _safe_json(result)
    except Exception as e:
        return _safe_json({"error": str(e)})


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
