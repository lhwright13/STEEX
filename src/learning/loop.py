"""Learning loop coordinator.

Chains PostMortem, AlphaDecay, SignalResearch, and WalkForward validation
into a single automated learning cycle. Runs weekly after market close,
validates proposed changes via out-of-sample backtest, and safely writes
validated changes to config with a full audit trail.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config.settings import Settings, get_settings
from .config_writer import ConfigWriter, WEIGHT_KEYS
from .journal import LearningJournal

logger = logging.getLogger(__name__)


def _is_market_hours() -> bool:
    """Check if current time is during US market hours (9:30-16:00 ET weekdays)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


class LearningLoop:
    """Coordinates the full learning cycle.

    Phases:
        1. PostMortem - analyze recent trades
        2. Alpha Decay - check signal health
        3. Signal Research (conditional) - optimize weights if signals degrading
        4. OOS Validation (conditional) - walk-forward validation of proposed changes
        5. Apply (conditional) - write validated changes to config
        6. Gap Identification - flag knowledge gaps for user review
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        config_writer: Optional[ConfigWriter] = None,
        journal: Optional[LearningJournal] = None,
    ):
        self.settings = settings or get_settings()
        self.config_writer = config_writer or ConfigWriter()
        self.journal = journal or LearningJournal()

    def run(
        self,
        dry_run: bool = False,
        force_research: bool = False,
        phases: Optional[List[str]] = None,
    ) -> Dict:
        """Execute the full learning cycle.

        Args:
            dry_run: If True, propose but do not apply changes
            force_research: If True, run signal research even if no degradation
            phases: Optional list of specific phases to run (default: all)

        Returns:
            Dict with results from each phase
        """
        if not dry_run and _is_market_hours():
            msg = "Refusing to apply config changes during market hours"
            logger.warning(msg)
            return {"error": msg, "market_hours": True}

        all_phases = [
            "postmortem", "alpha_decay", "signal_research",
            "oos_validation", "apply", "gaps",
        ]
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
        needs_research = force_research
        if "alpha_decay" in run_phases:
            decay_report = self._run_alpha_decay()
            results["alpha_decay"] = decay_report
            results["phases_run"].append("alpha_decay")

            if decay_report:
                degrading = decay_report.get("degrading", [])
                score_corr = (
                    pm_report.get("score_correlation", 1.0) if pm_report else 1.0
                )
                if degrading or score_corr < 0.10:
                    needs_research = True
                    logger.info(
                        "Research triggered: degrading=%s, score_corr=%.2f",
                        degrading, score_corr,
                    )

        # Phase 3: Signal Research (conditional)
        research_report = None
        recommended_weights = None
        if "signal_research" in run_phases and needs_research:
            research_report = self._run_signal_research()
            results["signal_research"] = research_report
            results["phases_run"].append("signal_research")

            if research_report:
                recommended_weights = research_report.get("recommended_weights")

        # Phase 4: OOS Validation (conditional)
        validation_result = None
        validated_proposal = None
        if (
            "oos_validation" in run_phases
            and recommended_weights
        ):
            validation_result = self._run_oos_validation(recommended_weights)
            results["oos_validation"] = validation_result
            results["phases_run"].append("oos_validation")

            if validation_result and validation_result.get("passed"):
                weight_changes = {
                    k: v for k, v in recommended_weights.items()
                    if k in WEIGHT_KEYS
                }
                if weight_changes:
                    validated_proposal = self.config_writer.propose_changes(
                        weight_changes,
                        source="signal_research",
                        reason=(
                            f"OOS validated: Sharpe={validation_result.get('sharpe', 0):.2f}, "
                            f"win_rate={validation_result.get('win_rate', 0):.1%}"
                        ),
                    )

        # Phase 5: Apply (conditional)
        if "apply" in run_phases and validated_proposal:
            if dry_run:
                results["apply"] = {
                    "dry_run": True,
                    "would_apply": validated_proposal,
                }
                self.journal.log_action(
                    "weight_recommendation",
                    "Dry run - proposed weight changes not applied",
                    details=validated_proposal,
                )
            else:
                apply_result = self.config_writer.apply_changes(
                    validated_proposal,
                    source="learning_loop",
                    reason=validated_proposal.get("reason", "OOS validated weight update"),
                )
                results["apply"] = apply_result
                self.journal.log_action(
                    "config_change",
                    f"Applied {apply_result.get('count', 0)} parameter changes",
                    details=apply_result,
                )
            results["phases_run"].append("apply")

        # Phase 6: Gap Identification
        if "gaps" in run_phases:
            gaps = self._identify_gaps(pm_report, decay_report, research_report)
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

    def _run_signal_research(self) -> Optional[Dict]:
        """Phase 3: Run signal research and weight optimization."""
        try:
            from ..backtest.walkforward import WalkForwardBacktester
            from ..research.signal_tester import SignalResearcher
            from ..data.universe import Universe

            lookback_months = getattr(
                self.settings, "learning_feature_lookback_months", 6
            )
            end = datetime.now()
            start = end - timedelta(days=lookback_months * 30)

            logger.info(
                "Generating feature matrix from %s to %s",
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
            )

            backtester = WalkForwardBacktester(settings=self.settings)
            universe = Universe().get_sp500()
            price_cache = backtester.prefetch_data(universe, start, end)

            _, features = backtester.generate_historical_signals(
                start=start, end=end,
                price_cache=price_cache, universe=universe,
            )

            if not features:
                self.journal.log_action(
                    "signal_research",
                    "No features generated - insufficient data",
                )
                return {"error": "No features generated"}

            researcher = SignalResearcher(settings=self.settings)
            report = researcher.run_full_analysis(features, price_cache=price_cache)

            result = {
                "hypotheses": [
                    {
                        "signal": h.signal_name,
                        "ic": round(h.information_coefficient, 4),
                        "p_value": round(h.p_value, 4),
                        "significant": h.is_significant,
                        "win_rate": round(h.win_rate_when_strong, 4),
                    }
                    for h in report.hypotheses
                ],
                "redundant_pairs": report.redundant_pairs,
                "recommended_weights": report.recommended_weights,
                "feature_count": len(features),
            }

            if report.recommended_weights:
                self.journal.save_weight_recommendations(
                    report.recommended_weights,
                    source="signal_research",
                )

            self.journal.log_action(
                "signal_research",
                f"Analyzed {len(features)} observations, "
                f"recommended weights: {report.recommended_weights}",
                details=result,
            )

            return result

        except Exception as e:
            logger.error("Signal Research failed: %s", e)
            return {"error": str(e)}

    def _run_oos_validation(self, proposed_weights: Dict[str, float]) -> Optional[Dict]:
        """Phase 4: Walk-forward out-of-sample validation.

        Runs a 2-fold walk-forward backtest with proposed weights and checks
        that OOS Sharpe > 0 and win_rate > 50%.
        """
        try:
            from ..backtest.walkforward import (
                WalkForwardBacktester,
                WalkForwardConfig,
            )

            train_months = getattr(
                self.settings, "learning_validation_train_months", 3
            )
            test_months = getattr(
                self.settings, "learning_validation_test_months", 1
            )
            min_sharpe = getattr(self.settings, "learning_oos_min_sharpe", 0.0)
            min_win_rate = getattr(self.settings, "learning_oos_min_win_rate", 0.50)

            # Build 2-fold config
            now = datetime.now()
            folds = []
            for i in range(2):
                test_end = now - timedelta(days=i * test_months * 30)
                test_start = test_end - timedelta(days=test_months * 30)
                train_end = test_start - timedelta(days=1)
                train_start = train_end - timedelta(days=train_months * 30)

                folds.append(WalkForwardConfig(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                ))

            folds.reverse()

            # Create settings with proposed weights
            overrides = {}
            for k, v in proposed_weights.items():
                if k in WEIGHT_KEYS:
                    overrides[k] = v

            override_settings = Settings(**overrides)
            backtester = WalkForwardBacktester(settings=override_settings)

            logger.info("Running OOS validation with 2 folds")
            fold_results = backtester.run_walk_forward(folds=folds)

            if not fold_results:
                return {"passed": False, "reason": "No fold results"}

            # Aggregate OOS metrics
            oos_sharpes = []
            oos_win_rates = []

            for fr in fold_results:
                metrics = fr.out_of_sample.metrics
                oos_sharpes.append(metrics.get("sharpe_ratio", 0))
                oos_win_rates.append(metrics.get("win_rate", 0))

            avg_sharpe = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0
            avg_win_rate = sum(oos_win_rates) / len(oos_win_rates) if oos_win_rates else 0
            passed = avg_sharpe > min_sharpe and avg_win_rate > min_win_rate

            result = {
                "passed": passed,
                "sharpe": avg_sharpe,
                "win_rate": avg_win_rate,
                "min_sharpe": min_sharpe,
                "min_win_rate": min_win_rate,
                "folds": len(fold_results),
                "fold_details": [
                    {
                        "oos_sharpe": fr.out_of_sample.metrics.get("sharpe_ratio", 0),
                        "oos_win_rate": fr.out_of_sample.metrics.get("win_rate", 0),
                        "oos_trades": len(fr.out_of_sample.trades),
                    }
                    for fr in fold_results
                ],
            }

            self.journal.log_action(
                "oos_validation",
                f"OOS validation {'PASSED' if passed else 'FAILED'}: "
                f"Sharpe={avg_sharpe:.2f}, win_rate={avg_win_rate:.1%}",
                details=result,
            )

            return result

        except Exception as e:
            logger.error("OOS Validation failed: %s", e)
            return {"error": str(e), "passed": False}

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

