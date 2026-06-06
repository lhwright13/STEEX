"""PipelineMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class PipelineMixin:
    def get_pipeline_current(self) -> Dict[str, Any]:
        """Get current pipeline state and stage."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_pipeline_state()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_pipeline_state()

        # Extract stage and progress from run data
        mode = run_data.get("mode", "screen")
        status = run_data.get("status", "idle")  # running, complete, failed
        stage = run_data.get("stage", "idle")
        elapsed = self._elapsed_seconds(run_data.get("started_at"))
        current_agent = run_data.get("current_agent")

        # Estimate stage progress (0.0 - 1.0)
        stage_progress = self._calculate_stage_progress(run_data)

        return {
            "status": status,
            "mode": mode,
            "stage": stage,
            "elapsed": elapsed,
            "stage_progress": stage_progress,
            "current_agent": current_agent or "idle",
            "run_id": run_data.get("run_id"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_pipeline_live(self) -> Dict[str, Any]:
        """Live Pipeline view (P3-2): per-agent lanes for the active-or-latest run.

        STEEX runs as cron one-shots and the run log persists only a start record
        and a final record (no mid-run progress), so true second-by-second
        streaming isn't available without an orchestrator change. This surfaces
        what IS real: whether a run is active right now, and the per-agent lanes
        (status + tools called, from the H4 trace telemetry) of the run in
        flight — or, if it hasn't produced traces yet, the most recent run that
        did, clearly flagged via `source`.
        """
        cur = self.get_pipeline_current()
        active = cur.get("status") == "running"
        run_id = cur.get("run_id")

        timeline = self.get_agent_timeline(run_id)
        source = "current"
        if not timeline.get("steps"):
            # The in-flight run has no traces yet (or none active) — show the
            # latest run that actually executed agents, labelled as the last run.
            runs_dir = self.data_dir / "runs"
            if runs_dir.exists():
                for f in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
                    d = self._load_json(f)
                    if d and d.get("mode") != "event_scan" and d.get("traces"):
                        timeline = self.get_agent_timeline(d.get("run_id"))
                        run_id = d.get("run_id")
                        source = "last_run"
                        break

        current_agent = cur.get("current_agent")
        lanes = []
        for s in timeline.get("steps", []):
            if active and source == "current" and s["agent"] == current_agent:
                status = "running"
            else:
                status = "ok" if s.get("success") else "failed"
            tools = s.get("tools_called") or []
            lanes.append({
                "agent": s.get("agent"),
                "role": s.get("role"),
                "status": status,
                "tools_called": tools,
                "tool_count": len(tools),
                "duration_seconds": s.get("duration_seconds"),
                "summary": s.get("summary"),
            })

        return {
            "active": active,
            "status": cur.get("status"),
            "mode": cur.get("mode"),
            "run_id": run_id,
            "elapsed": cur.get("elapsed"),
            "current_agent": current_agent,
            "source": source,
            "lanes": lanes,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_variants_results(self) -> Dict[str, Dict[str, Any]]:
        """Get results from all three analysis variants."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_variants_results()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_variants_results()

        conclusions = run_data.get("conclusions", {})
        variant_conclusions = run_data.get("variant_conclusions", [])

        results = {}
        for variant_item in variant_conclusions:
            variant_name = variant_item.get("variant")
            conclusion = variant_item.get("conclusion", {})
            if variant_name and conclusion:
                candidates = conclusion.get("candidates", [])
                scores = [c.get("score", 0) for c in candidates]
                avg_score = sum(scores) / len(scores) if scores else 0.0

                results[variant_name] = {
                    "variant": variant_name,
                    "status": "complete",
                    "candidate_count": len(candidates),
                    "avg_score": round(avg_score, 1),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }

        # Fill in missing variants with pending status
        for variant in ["conservative", "aggressive", "momentum"]:
            if variant not in results:
                results[variant] = {
                    "variant": variant,
                    "status": "pending",
                    "candidate_count": 0,
                    "avg_score": 0.0,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }

        return results

    def get_consensus(self) -> Dict[str, Any]:
        """Get consensus picks from meta-analysis."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_consensus()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_consensus()

        analysis_conclusion = run_data.get("conclusions", {}).get("analysis", {})
        if not analysis_conclusion:
            return self._default_consensus()

        # Extract consensus picks
        candidates = analysis_conclusion.get("candidates", [])

        high_conviction = [c for c in candidates if c.get("high_conviction", False)]
        consensus = [c for c in candidates if not c.get("high_conviction", False)]

        return {
            "high_conviction": high_conviction[:5],  # Top 5
            "consensus": consensus[:5],  # Top 5
            "speculative_excluded": analysis_conclusion.get("speculative_excluded", []),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_screening_stats(self) -> Dict[str, int]:
        """Get screening funnel statistics."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_screening_stats()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_screening_stats()

        screening = run_data.get("screening", {})
        return {
            "universe": screening.get("universe_size", 0),
            "passed_volume": screening.get("volume_filtered", 0),
            "passed_sentiment": screening.get("sentiment_filtered", 0),
            "passed_technical": screening.get("technical_filtered", 0),
            "passed_insider": screening.get("insider_filtered", 0),
            "final_screened": screening.get("final_count", 0),
            "final_picked": len(run_data.get("conclusions", {}).get("analysis", {}).get("candidates", [])),
        }

    def get_regime(self) -> Dict[str, Any]:
        """Get current market regime and VIX."""
        try:
            regime = self.regime_detector.detect_regime()
            regime_name = regime.name
            vix = regime.vix_level
            confidence = regime.confidence
        except Exception as e:
            logger.warning(f"Regime detection failed: {e}")
            regime_name = "unknown"
            vix = None
            confidence = 0.0

        return {
            "current": regime_name or "unknown",
            "vix": vix or 15.0,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "regimes": {
                "risk_on": {"probability": 0.15, "signal": "VIX < 12, inflows"},
                "cautious": {"probability": 0.60, "signal": "VIX 14-18, mixed flows"},
                "risk_off": {"probability": 0.20, "signal": "Rising volatility, outflows"},
                "crisis": {"probability": 0.05, "signal": "VIX > 30, panic selling"},
            },
        }

    def get_manager_decision(self) -> Dict[str, Any]:
        """Get manager's decision on trades."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_manager_decision()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_manager_decision()

        manager_decision = run_data.get("manager_decision", {})
        status = "approved" if not run_data.get("abort") else "rejected"

        return {
            "status": status,
            "reasoning": manager_decision.get("reasoning", "Review pending"),
            "adjustments": manager_decision.get("position_adjustments", {}),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_recent_runs(self, limit: int = 10) -> Dict[str, Any]:
        """Get recent pipeline runs across the trading modes.

        Excludes event_scan (it runs every minute and would crowd out the
        screen/enter/monitor runs); event activity has its own feed.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"runs": [], "timestamp": datetime.utcnow().isoformat() + "Z"}

        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)
        runs = []

        for run_file in run_files:
            if len(runs) >= limit:
                break
            run_data = self._load_json(run_file)
            if not run_data or run_data.get("mode") == "event_scan":
                continue

            runs.append({
                "run_id": run_data.get("run_id"),
                "mode": run_data.get("mode", "unknown"),
                "status": run_data.get("status", "unknown"),
                "started_at": run_data.get("started_at"),
                "completed_at": run_data.get("completed_at"),
                "elapsed": self._elapsed_seconds(run_data.get("started_at")),
                "current_agent": run_data.get("current_agent"),
                "stage": run_data.get("stage"),
            })

        return {
            "runs": runs,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_run_trace(self, run_id: str) -> Dict[str, Any]:
        """Get execution trace for a specific run."""
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"run_id": run_id, "traces": [], "message": "No run data available"}

        # Find run file with matching run_id
        for run_file in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
            run_data = self._load_json(run_file)
            if not run_data or run_data.get("run_id") != run_id:
                continue

            traces = run_data.get("traces", [])
            return {
                "run_id": run_id,
                "mode": run_data.get("mode"),
                "started_at": run_data.get("started_at"),
                "traces": traces,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        return {"run_id": run_id, "traces": [], "message": f"Run {run_id} not found"}

    def _calculate_stage_progress(self, run_data: Dict) -> float:
        """Estimate pipeline stage progress (0.0 - 1.0)."""
        status = run_data.get("status", "idle")
        if status == "idle":
            return 0.0
        if status == "complete":
            return 1.0
        if status == "failed":
            return 0.0

        # Estimate progress based on stage
        stage = run_data.get("stage", "")
        stage_progress_map = {
            "data": 0.15,
            "risk": 0.25,
            "fan_out": 0.35,
            "analysis": 0.65,
            "merge_variants": 0.80,
            "manager": 0.90,
            "execution": 1.0,
        }
        return stage_progress_map.get(stage, 0.5)

    def _default_pipeline_state(self) -> Dict[str, Any]:
        return {
            "status": "idle",
            "mode": "screen",
            "stage": "idle",
            "elapsed": 0,
            "stage_progress": 0.0,
            "current_agent": "idle",
            "run_id": None,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _default_variants_results(self) -> Dict[str, Dict[str, Any]]:
        return {
            variant: {
                "variant": variant,
                "status": "idle",
                "candidate_count": 0,
                "avg_score": 0.0,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            for variant in ["conservative", "aggressive", "momentum"]
        }

    def _default_consensus(self) -> Dict[str, Any]:
        return {
            "high_conviction": [],
            "consensus": [],
            "speculative_excluded": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _default_screening_stats(self) -> Dict[str, int]:
        return {
            "universe": 0,
            "passed_volume": 0,
            "passed_sentiment": 0,
            "passed_technical": 0,
            "passed_insider": 0,
            "final_screened": 0,
            "final_picked": 0,
        }

    def _default_manager_decision(self) -> Dict[str, Any]:
        return {
            "status": "pending",
            "reasoning": "No recent run data",
            "adjustments": {},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
