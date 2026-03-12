"""Cross-reference agent recommendations with trade outcomes.

Maps agent meta-suggestions to actionable categories by correlating
them with postmortem loss patterns and alpha decay signals. This is
the bridge between the agent evolution system and the deterministic
learning loop.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from src.learning.config_writer import PARAM_BOUNDS

logger = logging.getLogger("steex.cross_reference")

# Maps keywords in agent suggestions to tunable parameter names.
# Longer/more-specific keywords must come first so they match before
# shorter substrings (e.g. "trailing stop" before "stop").
KEYWORD_TO_PARAM = [
    ("minimum score", "manager_min_score_entry"),
    ("entry score", "manager_min_score_entry"),
    ("trailing stop", "trail_stop_10"),
    ("stop-loss", "initial_stop_pct"),
    ("hold period", "max_hold_days"),
    ("holding period", "max_hold_days"),
    ("position size", "position_size_pct"),
    ("score", "manager_min_score_entry"),
    ("threshold", "manager_min_score_entry"),
    ("stop", "initial_stop_pct"),
    ("hold", "max_hold_days"),
    ("sizing", "position_size_pct"),
    ("momentum", "weight_momentum"),
    ("insider", "weight_insider"),
    ("volume", "weight_volume"),
    ("sentiment", "weight_sentiment"),
    ("fundamental", "weight_fundamental"),
    ("options", "weight_options"),
]

# Loss patterns that correlate with specific agent suggestions
LOSS_PATTERN_KEYWORDS = {
    "whipsaw": ["stop", "trailing", "hold period", "minimum hold"],
    "dead_money": ["hold", "max hold", "exit sooner"],
    "missed_upside": ["trailing stop", "stop too tight"],
    "early_exit": ["stop", "wider stop", "trailing"],
    "late_exit": ["exit signal", "momentum reversal"],
}


class CrossReferencer:
    """Cross-references agent recommendations with trade data."""

    def cross_reference(
        self,
        postmortem_result: Optional[Dict[str, Any]],
        alpha_decay_result: Optional[Dict[str, Any]],
        pending_recommendations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Cross-reference agent insights with trade outcomes.

        Args:
            postmortem_result: Output from run_postmortem tool
            alpha_decay_result: Output from check_alpha_decay tool
            pending_recommendations: Output from get_pending_recommendations tool

        Returns:
            Structured report with categorized insights and correlations
        """
        categorized = self._categorize_recommendations(pending_recommendations)
        correlations = self._correlate_with_trades(
            postmortem_result, alpha_decay_result, pending_recommendations
        )

        return {
            "total_recommendations": len(pending_recommendations),
            "categories": {
                "param_relevant": categorized["param_relevant"],
                "prompt_relevant": categorized["prompt_relevant"],
                "tool_relevant": categorized["tool_relevant"],
                "process_relevant": categorized["process_relevant"],
            },
            "correlations": correlations,
            "summary": self._build_summary(categorized, correlations),
        }

    def _categorize_recommendations(
        self, recommendations: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Categorize recommendations by type."""
        result: Dict[str, List[Dict[str, Any]]] = {
            "param_relevant": [],
            "prompt_relevant": [],
            "tool_relevant": [],
            "process_relevant": [],
        }

        for rec in recommendations:
            agent = rec.get("agent", "unknown")

            for suggestion in rec.get("prompt_suggestions", []):
                matched_param = self._match_to_param(suggestion)
                if matched_param:
                    bounds = PARAM_BOUNDS.get(matched_param)
                    bounds_dict = None
                    if bounds:
                        bounds_dict = {"min": bounds[0], "max": bounds[1], "max_delta": bounds[2]}
                    result["param_relevant"].append({
                        "agent": agent,
                        "suggestion": suggestion,
                        "mapped_param": matched_param,
                        "param_bounds": bounds_dict,
                    })
                else:
                    result["prompt_relevant"].append({
                        "agent": agent,
                        "suggestion": suggestion,
                    })

            for suggestion in rec.get("tool_suggestions", []):
                result["tool_relevant"].append({
                    "agent": agent,
                    "suggestion": suggestion,
                    "action": "flag_for_human",
                })

            for suggestion in rec.get("process_suggestions", []):
                result["process_relevant"].append({
                    "agent": agent,
                    "suggestion": suggestion,
                    "action": "flag_for_human",
                })

        return result

    def _match_to_param(self, suggestion: str) -> Optional[str]:
        """Try to map a suggestion to a tunable parameter."""
        lower = suggestion.lower()
        for keyword, param in KEYWORD_TO_PARAM:
            if keyword in lower:
                return param
        return None

    def _correlate_with_trades(
        self,
        postmortem_result: Optional[Dict[str, Any]],
        alpha_decay_result: Optional[Dict[str, Any]],
        recommendations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Find correlations between trade loss patterns and agent suggestions."""
        correlations = []

        all_suggestions = []
        for rec in recommendations:
            for s in rec.get("prompt_suggestions", []):
                all_suggestions.append((rec.get("agent", "unknown"), s))
            for s in rec.get("process_suggestions", []):
                all_suggestions.append((rec.get("agent", "unknown"), s))

        if not all_suggestions:
            return correlations

        # Correlate loss patterns with agent suggestions
        if postmortem_result:
            loss_breakdown = postmortem_result.get("loss_breakdown", {})
            for loss_type, count in loss_breakdown.items():
                if count == 0:
                    continue

                keywords = LOSS_PATTERN_KEYWORDS.get(loss_type, [])
                for agent, suggestion in all_suggestions:
                    lower = suggestion.lower()
                    for keyword in keywords:
                        if keyword in lower:
                            correlations.append({
                                "loss_type": loss_type,
                                "loss_count": count,
                                "agent": agent,
                                "suggestion": suggestion,
                                "correlation": f"{agent} identified '{keyword}' which aligns with {count} '{loss_type}' losses",
                            })
                            break

        # Correlate degrading signals with agent observations
        if alpha_decay_result:
            degrading = alpha_decay_result.get("degrading", [])
            for signal in degrading:
                for agent, suggestion in all_suggestions:
                    if signal.replace("_score", "") in suggestion.lower():
                        correlations.append({
                            "loss_type": "alpha_decay",
                            "signal": signal,
                            "agent": agent,
                            "suggestion": suggestion,
                            "correlation": f"{agent} observation aligns with {signal} degradation",
                        })

        return correlations

    def _build_summary(
        self,
        categorized: Dict[str, List],
        correlations: List[Dict],
    ) -> str:
        """Build a human-readable summary of findings."""
        parts = []

        param_count = len(categorized["param_relevant"])
        prompt_count = len(categorized["prompt_relevant"])
        tool_count = len(categorized["tool_relevant"])
        process_count = len(categorized["process_relevant"])

        parts.append(
            f"Categorized recommendations: {param_count} parameter-relevant, "
            f"{prompt_count} prompt-relevant, {tool_count} tool-relevant, "
            f"{process_count} process-relevant."
        )

        if correlations:
            parts.append(
                f"Found {len(correlations)} correlation(s) between agent "
                f"suggestions and trade outcomes."
            )

        if tool_count + process_count > 0:
            parts.append(
                f"{tool_count + process_count} suggestion(s) flagged for human review "
                f"(tool/process changes)."
            )

        return " ".join(parts)
