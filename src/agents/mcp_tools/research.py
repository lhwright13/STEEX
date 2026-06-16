"""Research MCP tools — split from mcp_server.py (P0-2). Names/signatures unchanged."""
import json  # noqa: F401
import logging
from dataclasses import asdict  # noqa: F401
from datetime import datetime, timedelta  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Tuple  # noqa: F401
from src.research.alpha_monitor import AlphaDecayMonitor  # noqa: F401

from .server import mcp
from . import _state
from ._util import _safe_json

logger = logging.getLogger("steex.mcp")

@mcp.tool()
def get_signal_confidence() -> str:
    """Get confidence scores and recommended weights for each signal.

    Reads AlphaDecayMonitor for per-signal rolling win rates.
    Reads recommended weights from the learning journal when present.
    Returns overall confidence and per-signal status.
    """
    mgr = _state.init_manager()
    settings = mgr.settings

    result = {
        "overall_win_rate": None,
        "degrading_signals": [],
        "watch_list": [],
        "recommended_weights": {
            "weight_momentum": settings.weight_momentum,
            "weight_insider": settings.weight_insider,
            "weight_volume": settings.weight_volume,
            "weight_sentiment": settings.weight_sentiment,
            "weight_fundamental": settings.weight_fundamental,
            "weight_options": settings.weight_options,
        },
    }

    try:
        monitor = AlphaDecayMonitor(settings.data_dir)
        report = monitor.generate_report()
        result["overall_win_rate"] = report.get("overall_win_rate")
        result["degrading_signals"] = report.get("degrading_signals", [])
        result["watch_list"] = report.get("watch_list", [])
    except Exception as e:
        logger.warning("Could not load AlphaDecayMonitor: %s", e)

    return _safe_json(result)

@mcp.tool()
def run_postmortem() -> str:
    """Analyze recent trades for patterns and recommendations.

    Reviews trades from the past 90 days, categorizes losses,
    calculates score correlation with outcomes, and generates
    actionable recommendations.
    """
    mgr = _state.init_manager()

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
            "analysis_failures": report.analysis_failures,
        })
    except Exception as e:
        return _safe_json({"error": str(e)})

@mcp.tool()
def run_learning_loop() -> str:
    """Run the deterministic self-learning cycle (observe-only).

    Chains: PostMortem -> Alpha Decay -> Gap identification over real
    completed trades. This fallback does not write config; parameter tuning
    is the learning agent's job, bounded by ConfigWriter guardrails.

    Respects dry-run mode.
    """
    mgr = _state.init_manager()
    result = mgr.run_learning(dry_run=_state.dry_run)
    if result is None:
        return _safe_json({"error": "Learning disabled in config"})
    return _safe_json(result)

@mcp.tool()
def check_alpha_decay() -> str:
    """Check per-signal health for alpha decay.

    Analyzes rolling hit rates for each scoring signal (momentum,
    insider, volume, sentiment, fundamental, options) and flags
    degrading signals where hit rate has dropped significantly.

    Returns signal health for each factor, list of degrading signals,
    watch list, and recommendations.
    """
    mgr = _state.init_manager()
    try:
        from src.research.alpha_monitor import AlphaDecayMonitor
        monitor = AlphaDecayMonitor(settings=mgr.settings)
        report = monitor.generate_report()
        return _safe_json(report)
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
    if _state.dry_run:
        return _safe_json({"applied": False, "reason": "Dry run mode - changes not applied"})

    try:
        from src.learning.config_writer import _is_market_hours
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
    alpha decay checks, weight recommendations, and config changes.

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

@mcp.tool()
def generate_report(mode: str = "screen") -> str:
    """Generate and save a structured daily report.

    Compiles all data from the current run: data health, regime,
    portfolio, exits, entries, screening, risk alerts, and performance.

    Args:
        mode: The operating mode (screen, enter, monitor, post_market)
    """
    mgr = _state.init_manager()
    report = mgr.generate_daily_report(mode)
    filepath = mgr.save_report(report)
    return _safe_json({
        "saved": True,
        "path": str(filepath),
        "mode": mode,
        "risk_alerts": report.get("risk_alerts", []),
        "performance": report.get("performance", {}),
    })

__all__ = ['get_signal_confidence', 'run_postmortem', 'run_learning_loop', 'check_alpha_decay', 'propose_config_changes', 'apply_config_changes', 'get_learning_journal', 'get_learning_gaps', 'get_config_change_history', 'get_pending_recommendations', 'cross_reference_findings', 'generate_report']
