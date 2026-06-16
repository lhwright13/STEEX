"""Learning loop coordinator.

Deterministic fallback for the learning cycle: runs PostMortem and AlphaDecay
over real completed trades, then flags knowledge gaps for human review. It is
observe-only by design - it does not self-tune config. Parameter changes are
the agent's job (propose_config_changes / apply_config_changes), bounded by the
deterministic guardrails. Runs weekly after market close.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config.settings import Settings, get_settings
from .journal import LearningJournal

logger = logging.getLogger(__name__)


class LearningLoop:
    """Coordinates the deterministic learning cycle.

    Phases:
        1. PostMortem - analyze recent completed trades
        2. Alpha Decay - check per-signal health
        3. Gap Identification - flag knowledge gaps for user review

    Observe-only: this fallback never writes config. Weight/parameter tuning
    is handled by the learning agent within the deterministic guardrails.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        journal: Optional[LearningJournal] = None,
    ):
        self.settings = settings or get_settings()
        self.journal = journal or LearningJournal()

    def run(
        self,
        dry_run: bool = False,
        phases: Optional[List[str]] = None,
    ) -> Dict:
        """Execute the deterministic learning cycle (observe-only).

        Args:
            dry_run: Retained for interface compatibility; this loop never
                writes config, so it has no behavioral effect here.
            phases: Optional list of specific phases to run (default: all)

        Returns:
            Dict with results from each phase
        """
        all_phases = ["postmortem", "alpha_decay", "gaps"]
        run_phases = set(phases) if phases else set(all_phases)

        results = {
            "timestamp": datetime.now().isoformat(),
            "dry_run": dry_run,
            "phases_run": [],
        }

        # Phase 1: PostMortem
        pm_report = None
        if "postmortem" in run_phases:
            pm_report = self._run_postmortem()
            results["postmortem"] = pm_report
            results["phases_run"].append("postmortem")

        # Phase 2: Alpha Decay
        decay_report = None
        if "alpha_decay" in run_phases:
            decay_report = self._run_alpha_decay()
            results["alpha_decay"] = decay_report
            results["phases_run"].append("alpha_decay")

        # Phase 3: Gap Identification
        if "gaps" in run_phases:
            gaps = self._identify_gaps(pm_report, decay_report, None)
            results["gaps"] = gaps
            results["phases_run"].append("gaps")

        return results

    def _run_postmortem(self) -> Optional[Dict]:
        """Phase 1: Run post-mortem analysis on recent trades."""
        try:
            from ..portfolio.postmortem import PostMortemAnalyzer

            lookback = self.settings.postmortem_lookback_days
            analyzer = PostMortemAnalyzer(settings=self.settings)
            start = datetime.now() - timedelta(days=lookback)
            report = analyzer.generate_report(start, datetime.now())

            min_trades = getattr(
                self.settings, "learning_min_trades_for_analysis", 15
            )

            result = {
                "trades_analyzed": report.trades_analyzed,
                "loss_breakdown": report.loss_breakdown,
                "score_correlation": report.score_correlation,
                "avg_missed_upside": report.avg_missed_upside,
                "recommendations": report.recommendations,
                "patterns": report.patterns,
                "sufficient_data": report.trades_analyzed >= min_trades,
            }

            self.journal.log_action(
                "postmortem_analysis",
                f"Analyzed {report.trades_analyzed} trades, "
                f"score_corr={report.score_correlation:.2f}",
                details=result,
            )

            return result

        except Exception as e:
            logger.error("PostMortem failed: %s", e)
            return {"error": str(e)}

    def _run_alpha_decay(self) -> Optional[Dict]:
        """Phase 2: Check signal health for alpha decay."""
        try:
            from ..research.alpha_monitor import AlphaDecayMonitor

            monitor = AlphaDecayMonitor(settings=self.settings)
            report = monitor.generate_report()

            self.journal.log_action(
                "alpha_decay_check",
                f"Checked {len(report.get('signals', []))} signals, "
                f"degrading: {report.get('degrading', [])}",
                details=report,
            )

            return report

        except Exception as e:
            logger.error("Alpha Decay check failed: %s", e)
            return {"error": str(e)}

    def _identify_gaps(
        self,
        pm_report: Optional[Dict],
        decay_report: Optional[Dict],
        research_report: Optional[Dict],
    ) -> List[Dict]:
        """Phase 6: Scan all results for knowledge gaps.

        Flags gaps for user review when the system encounters situations
        it cannot resolve automatically.
        """
        gaps = []

        # Check for insufficient trade data
        min_trades = getattr(
            self.settings, "learning_min_trades_for_analysis", 15
        )
        if pm_report and not pm_report.get("sufficient_data", True):
            gap = self.journal.flag_gap(
                "missing_data",
                f"Only {pm_report.get('trades_analyzed', 0)} trades available "
                f"(need {min_trades} for reliable analysis)",
                severity="medium",
            )
            gaps.append(gap)

        # Check for degrading signals with no research fix
        if decay_report and decay_report.get("degrading"):
            for signal in decay_report["degrading"]:
                if research_report and research_report.get("error"):
                    gap = self.journal.flag_gap(
                        "degrading_signal",
                        f"Signal '{signal}' is degrading but research failed: "
                        f"{research_report.get('error')}",
                        context={"signal": signal},
                        severity="high",
                    )
                    gaps.append(gap)

        # Check for dominant loss categories in postmortem
        if pm_report and pm_report.get("loss_breakdown"):
            breakdown = pm_report["loss_breakdown"]
            total_losses = sum(breakdown.values())
            if total_losses > 0:
                for cat, count in breakdown.items():
                    pct = count / total_losses
                    if pct > 0.5 and total_losses >= 5:
                        gap = self.journal.flag_gap(
                            "parameter_drift",
                            f"Dominant loss category: {cat} ({pct:.0%} of losses). "
                            f"Manual review recommended.",
                            context={"category": cat, "count": count, "pct": pct},
                            severity="high",
                        )
                        gaps.append(gap)

        # Check for failed OOS validation (weights proposed but didn't validate)
        if research_report and research_report.get("recommended_weights"):
            # This is informational - just log it
            pass

        # Check for postmortem errors
        if pm_report and pm_report.get("error"):
            gap = self.journal.flag_gap(
                "implementation_needed",
                f"PostMortem analysis failed: {pm_report['error']}",
                severity="medium",
            )
            gaps.append(gap)

        return gaps

