"""Screen MCP tools — split from mcp_server.py (P0-2). Names/signatures unchanged."""
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

VARIANT_PARAMS = {
    "conservative": {
        "momentum_min_return": 0.10,
        "sentiment_min_score": 50.0,
        "fundamental_max_pe": 30.0,
        "fundamental_min_roe": 0.10,
    },
    "aggressive": {
        "momentum_min_return": 0.02,
        "sentiment_min_score": 28.0,
        "fundamental_max_pe": 80.0,
        "fundamental_min_roe": 0.0,
    },
    "momentum": {
        "momentum_min_return": 0.08,
        "sentiment_min_score": 35.0,
        "fundamental_enabled": False,
    },
}

REGIME_PARAMS = {
    "risk_on": {
        "rationale": "Aggressive: momentum-led, catch early breakouts",
        "weight_momentum": 0.45,
        "weight_fundamental": 0.04,
        "momentum_min_return": 0.01,
    },
    "cautious": {
        "rationale": "Aggressive: still momentum-tilted, mild quality screen",
        "weight_insider": 0.20,
        "weight_momentum": 0.35,
        "sentiment_min_score": 35.0,
    },
    "risk_off": {
        "rationale": "Less defensive: keep some momentum exposure",
        "weight_insider": 0.30,
        "weight_momentum": 0.25,
        "sentiment_min_score": 45.0,
        "fundamental_min_roe": 0.08,
    },
    "crisis": {
        "rationale": "Very high bars, insider weight maximum (entries frozen by regime)",
        "weight_insider": 0.40,
        "weight_momentum": 0.10,
        "sentiment_min_score": 60.0,
        "fundamental_min_roe": 0.15,
    },
}

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
    mgr = _state.init_manager()
    _state.pipeline_result = mgr.run_screening()

    candidates = []
    for c in _state.pipeline_result.final_candidates:
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
        "universe_size": _state.pipeline_result.universe_size,
        "stage_1_passed": _state.pipeline_result.stage_1_passed,
        "stage_2_passed": _state.pipeline_result.stage_2_passed,
        "stage_3_passed": _state.pipeline_result.stage_3_passed,
        "stage_4_passed": _state.pipeline_result.stage_4_passed,
        "stage_5_passed": _state.pipeline_result.stage_5_passed,
        "final_candidates": len(_state.pipeline_result.final_candidates),
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
    mgr = _state.init_manager()

    if _state.pipeline_result is None:
        return _safe_json({"error": "Call run_screening first"})

    _state.ranked = mgr.rank_candidates(_state.pipeline_result)

    result = []
    for r in _state.ranked:
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
    mgr = _state.init_manager()

    if _state.ranked is None:
        return _safe_json({"error": "Call rank_candidates first"})

    if mgr.portfolio_constructor is None:
        return _safe_json({"error": "Portfolio constructor not available"})

    try:
        proposal = mgr.portfolio_constructor.select_portfolio(
            _state.ranked,
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

@mcp.tool()
def run_screening_variant(variant: str) -> str:
    """Run screening with a specific variant's parameter preset.

    Variants: conservative (high bars), aggressive (low bars), momentum (no fundamentals).
    Temporarily overrides settings, runs the 5-stage pipeline, then restores.

    Must be called before rank_candidates or get_unusual_options_activity.
    """
    mgr = _state.init_manager()

    if variant not in VARIANT_PARAMS:
        return _safe_json({
            "error": f"Unknown variant: {variant}. Options: {list(VARIANT_PARAMS.keys())}"
        })

    params = VARIANT_PARAMS[variant]
    original = {}

    try:
        for key, value in params.items():
            if hasattr(mgr.settings, key):
                original[key] = getattr(mgr.settings, key)
                setattr(mgr.settings, key, value)

        _state.pipeline_result = mgr.run_screening()

        candidates = []
        for c in _state.pipeline_result.final_candidates:
            candidates.append({
                "ticker": c.ticker,
                "momentum_6m": round(c.momentum_6m, 3) if c.momentum_6m else None,
                "insider_score": round(c.insider_score, 1),
                "sentiment_score": round(c.sentiment_score, 1) if c.sentiment_score else None,
                "fundamental_score": round(c.fundamental_score, 1) if c.fundamental_score else None,
                "volume_surge": round(c.volume_surge, 1) if c.volume_surge else None,
            })

        return _safe_json({
            "variant": variant,
            "params_applied": params,
            "universe_size": _state.pipeline_result.universe_size,
            "stage_1_passed": _state.pipeline_result.stage_1_passed,
            "stage_2_passed": _state.pipeline_result.stage_2_passed,
            "stage_3_passed": _state.pipeline_result.stage_3_passed,
            "stage_4_passed": _state.pipeline_result.stage_4_passed,
            "stage_5_passed": _state.pipeline_result.stage_5_passed,
            "final_candidates": len(_state.pipeline_result.final_candidates),
            "candidates": candidates,
        })
    finally:
        for key, value in original.items():
            if hasattr(mgr.settings, key):
                setattr(mgr.settings, key, value)

@mcp.tool()
def rank_candidates_with_weights(
    weight_momentum: Optional[float] = None,
    weight_insider: Optional[float] = None,
    weight_volume: Optional[float] = None,
    weight_sentiment: Optional[float] = None,
    weight_fundamental: Optional[float] = None,
    weight_options: Optional[float] = None,
) -> str:
    """Rank candidates with custom scoring weights.

    All weights are optional. Temporarily overrides weight settings,
    calls the ranker, then restores original weights.

    Must call run_screening or run_screening_variant first.
    """
    mgr = _state.init_manager()

    if _state.pipeline_result is None:
        return _safe_json({"error": "Call run_screening or run_screening_variant first"})

    original = {}
    weights = {
        "weight_momentum": weight_momentum,
        "weight_insider": weight_insider,
        "weight_volume": weight_volume,
        "weight_sentiment": weight_sentiment,
        "weight_fundamental": weight_fundamental,
        "weight_options": weight_options,
    }

    try:
        for key, value in weights.items():
            if value is not None and hasattr(mgr.settings, key):
                original[key] = getattr(mgr.settings, key)
                setattr(mgr.settings, key, value)

        _state.ranked = mgr.rank_candidates(_state.pipeline_result)

        result = []
        for r in _state.ranked:
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

        return _safe_json({
            "weights_applied": {k: v for k, v in weights.items() if v is not None},
            "ranked": result,
            "count": len(result),
        })
    finally:
        for key, value in original.items():
            if hasattr(mgr.settings, key):
                setattr(mgr.settings, key, value)

@mcp.tool()
def get_regime_screening_params() -> str:
    """Get screening parameter overrides based on current regime.

    Reads the cached regime (populated by get_regime tool).
    Returns regime-specific weight and threshold adjustments.
    """
    if _state.regime is None:
        return _safe_json({
            "error": "Call get_regime first to populate regime data"
        })

    # Manager.get_regime() returns dict with "name"/"confidence" keys
    # (legacy code here previously looked for "regime_name"/"regime_confidence").
    regime_name = _state.regime.get("name") or _state.regime.get("regime_name", "unknown")
    if regime_name not in REGIME_PARAMS:
        return _safe_json({
            "error": f"Unknown regime: {regime_name}",
            "available_regimes": list(REGIME_PARAMS.keys()),
        })

    params = REGIME_PARAMS[regime_name]
    return _safe_json({
        "regime": regime_name,
        "regime_confidence": _state.regime.get("confidence", _state.regime.get("regime_confidence", 0)),
        "rationale": params.get("rationale", ""),
        "param_overrides": {k: v for k, v in params.items() if k != "rationale"},
    })

@mcp.tool()
def get_unusual_options_activity(min_call_volume_ratio: float = 1.5) -> str:
    """Get stocks with unusual call/put imbalance indicating bullish activity.

    Requires run_screening or run_screening_variant to have been called.
    Filters for stocks with call-heavy options (unusual_call_activity=True
    and put_call_ratio < 0.9), sorted by options_score.

    Args:
        min_call_volume_ratio: Minimum call-to-put volume ratio (default 1.5)

    Returns list of unusual call activity stocks with options metrics.
    """
    if _state.pipeline_result is None:
        return _safe_json({
            "error": "Call run_screening or run_screening_variant first"
        })

    unusual = []
    for c in _state.pipeline_result.final_candidates:
        has_unusual = getattr(c, "unusual_call_activity", False)
        put_call_ratio = getattr(c, "put_call_ratio", 1.0)
        options_score = getattr(c, "options_score", 0.0)

        if has_unusual and put_call_ratio < 0.9:
            unusual.append({
                "ticker": c.ticker,
                "put_call_ratio": round(put_call_ratio, 2),
                "unusual_call_activity": True,
                "options_score": round(options_score, 1),
            })

    unusual.sort(key=lambda x: x["options_score"], reverse=True)

    return _safe_json({
        "unusual_call_activity": unusual,
        "count": len(unusual),
        "filter_ratio": min_call_volume_ratio,
    })

@mcp.tool()
def load_screen_results() -> str:
    """Load saved screen results from the screen phase.

    Used by the enter mode to pick up buy candidates generated during
    the earlier screen phase. Validates that results are fresh (< 2 hours).
    """
    mgr = _state.init_manager()
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

    # Accept either key: the LangGraph save node (make_save_screen_node) writes
    # candidates under "entries", while the MCP/orchestrator savers use
    # "buy_list". Reading only one silently dropped every enter-phase buy.
    _state.buy_list = screen_data.get("buy_list") or screen_data.get("entries") or []
    _state.regime = screen_data.get("regime", {}) or _state.regime
    return _safe_json({
        "buy_list": _state.buy_list,
        "count": len(_state.buy_list),
        "timestamp": screen_data["timestamp"],
        "age_hours": round(age_hours, 1),
        "regime": _state.regime,
    })

@mcp.tool()
def save_screen_results() -> str:
    """Save current buy list and regime to screen results file.

    Called at the end of the screen phase so the enter phase can pick
    up the candidates later.
    """
    mgr = _state.init_manager()

    screen_dir = Path(mgr.settings.data_dir) / "screen_results"
    screen_dir.mkdir(parents=True, exist_ok=True)

    screen_data = {
        "timestamp": datetime.now().isoformat(),
        "regime": _state.regime or {},
        "buy_list": _state.buy_list or [],
        "ranked_count": len(_state.ranked) if _state.ranked else 0,
    }

    screen_path = screen_dir / "latest.json"
    with open(screen_path, "w") as f:
        json.dump(screen_data, f, indent=2, default=str)

    return _safe_json({
        "saved": True,
        "path": str(screen_path),
        "candidates": len(_state.buy_list) if _state.buy_list else 0,
    })

__all__ = ['run_screening', 'rank_candidates', 'construct_portfolio', 'run_screening_variant', 'rank_candidates_with_weights', 'get_regime_screening_params', 'get_unusual_options_activity', 'load_screen_results', 'save_screen_results']
