"""
Live data service layer for dashboard.

Connects dashboard to trading system:
- QuantManager for current market state
- AgentRegistry for agent configs
- Orchestrator for pipeline state
- Recent run data from data/
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

from config.settings import get_settings
from src.agents.registry import AgentRegistry, ModeConfig
from src.regime.detector import RegimeDetector

logger = logging.getLogger("steex.dashboard")


class DashboardService:
    """Provide live data from trading system to dashboard."""

    def __init__(self):
        self.settings = get_settings()
        config_dir = Path(__file__).parent.parent / "config"
        self.registry = AgentRegistry(config_dir / "agents.yaml")
        self.regime_detector = RegimeDetector(self.settings)
        self.data_dir = Path(__file__).parent.parent / "data"

    # ====================================================================
    # Pipeline State
    # ====================================================================

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

    # ====================================================================
    # System Configuration
    # ====================================================================

    def get_system_agents(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all agent configurations with recent stats."""
        agents = []

        # Get latest run for stats context
        latest_run_file = self._get_latest_run_file()
        latest_run = self._load_json(latest_run_file) if latest_run_file else None

        for agent_name, agent_config in self.registry.agents.items():
            last_run_info = self._get_agent_last_run(agent_name)

            agent_data = {
                "name": agent_name,
                "role": agent_config.prompt_key or agent_name,
                "status": self._get_agent_status(agent_name),
                "max_turns": agent_config.max_turns,
                "needs_tools": agent_config.needs_tools,
                "external_servers": agent_config.external_servers or [],
                "prompt_id": agent_config.prompt_key or agent_name,
                "critical": agent_name in self.registry.modes.get("screen", ModeConfig("screen")).critical_agents,
                "last_run_timestamp": last_run_info.get("timestamp"),
                "success_rate": last_run_info.get("success_rate"),
                "avg_duration": last_run_info.get("avg_duration", "—"),
            }
            agents.append(agent_data)

        return {"agents": sorted(agents, key=lambda a: a["name"])}

    def get_system_schedules(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get schedule configuration from registered modes."""
        schedules = []

        # Build schedules from modes
        for mode_name, mode_config in self.registry.modes.items():
            # Get description from mode config
            description = getattr(mode_config, 'description', f"Run {mode_name} mode")

            # Get recent runs for this mode to estimate next run
            recent_runs = self._get_runs_for_mode(mode_name, limit=1)
            next_run = datetime.utcnow() + timedelta(hours=1)
            if recent_runs:
                # Estimate next run as ~1 hour from last run
                try:
                    last_run_str = recent_runs[0].get("started_at")
                    last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                    next_run = last_run + timedelta(hours=1)
                except Exception:
                    pass

            schedule_info = {
                "name": mode_name,
                "mode": mode_name,
                "cron": "—",  # Not available from config currently
                "description": description,
                "next_run": next_run.isoformat() + "Z",
                "enabled": True,
                "recent_runs": len(recent_runs),
            }
            schedules.append(schedule_info)

        return {
            "schedules": sorted(schedules, key=lambda s: s["name"]),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_agent_detail(self, agent_name: str) -> Dict[str, Any]:
        """Get detailed configuration for a specific agent."""
        agent_config = self.registry.agents.get(agent_name)
        if not agent_config:
            return {"error": f"Agent {agent_name} not found"}

        # Load prompt if available
        prompt_text = "Prompt not found"
        try:
            prompt_file = self.settings.data_dir.parent / f"src/agents/prompts/{agent_config.prompt_key}.py"
            if prompt_file.exists():
                with open(prompt_file) as f:
                    content = f.read()
                    # Extract the prompt variable (simple heuristic)
                    if "_PROMPT = " in content:
                        prompt_text = content.split("_PROMPT = ", 1)[1][:500] + "..."
        except Exception as e:
            logger.debug(f"Could not load prompt for {agent_name}: {e}")

        last_run_info = self._get_agent_last_run(agent_name)

        return {
            "name": agent_name,
            "role": agent_config.prompt_key or agent_name,
            "status": self._get_agent_status(agent_name),
            "preprompt": prompt_text,
            "tools": self._get_agent_tools(agent_name),
            "external_servers": agent_config.external_servers or [],
            "last_run": last_run_info.get("timestamp"),
            "success_rate": last_run_info.get("success_rate"),
        }

    def get_agent_last_output(self, agent_name: str) -> Dict[str, Any]:
        """Get last execution output for a specific agent."""
        run_file = self._get_latest_run_file()
        if not run_file:
            return {
                "agent": agent_name,
                "output": None,
                "message": "No run data available",
            }

        run_data = self._load_json(run_file)
        if not run_data:
            return {
                "agent": agent_name,
                "output": None,
                "message": "No run data available",
            }

        # Check conclusions for this agent
        conclusions = run_data.get("conclusions", {})
        agent_conclusion = conclusions.get(agent_name)
        if not agent_conclusion:
            # Check variant conclusions
            variant_conclusions = run_data.get("variant_conclusions", [])
            if isinstance(variant_conclusions, list):
                for vc in variant_conclusions:
                    if vc.get("variant") == agent_name:
                        agent_conclusion = vc.get("conclusion")
                        break

        traces = run_data.get("traces", [])
        agent_trace = None
        if isinstance(traces, list):
            agent_trace = next((t for t in traces if t.get("agent") == agent_name), None)

        return {
            "agent": agent_name,
            "conclusion": agent_conclusion,
            "trace_summary": agent_trace.get("summary") if agent_trace else None,
            "timestamp": run_data.get("started_at"),
        }

    # ====================================================================
    # Helper Methods
    # ====================================================================

    def _get_latest_run_file(self) -> Optional[Path]:
        """Get the most recent run data file."""
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return None

        # Get most recent JSONL file
        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)
        return run_files[0] if run_files else None

    def _load_json(self, path: Path) -> Optional[Dict]:
        """Load last line of JSONL file as JSON."""
        try:
            with open(path) as f:
                lines = f.readlines()
                if lines:
                    return json.loads(lines[-1])
        except Exception as e:
            logger.debug(f"Failed to load {path}: {e}")
        return None

    def _elapsed_seconds(self, started_at: Optional[str]) -> int:
        """Calculate seconds since started_at timestamp."""
        if not started_at:
            return 0
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed = (datetime.utcnow() - start).total_seconds()
            return max(0, int(elapsed))
        except Exception:
            return 0

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

    def _get_agent_status(self, agent_name: str) -> str:
        """Get agent status (ready, running, complete, failed)."""
        # TODO: Check actual agent state from orchestrator
        return "ready"

    def _get_agent_tools(self, agent_name: str) -> List[Dict[str, str]]:
        """Get tools available to agent."""
        agent_config = self.registry.agents.get(agent_name)
        if not agent_config or not agent_config.allowed_tools:
            return []

        return [
            {"name": tool, "description": f"Tool: {tool}"}
            for tool in agent_config.allowed_tools
        ]

    def _get_agent_last_run(self, agent_name: str) -> Dict[str, Any]:
        """Get info about agent's last run from recent execution history."""
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {
                "timestamp": None,
                "success_rate": None,
                "avg_duration": "—",
            }

        # Get most recent 10 runs
        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)[:10]

        success_count = 0
        total_count = 0
        durations = []
        last_timestamp = None

        for run_file in run_files:
            run_data = self._load_json(run_file)
            if not run_data:
                continue

            # Check if this agent was in this run
            conclusions = run_data.get("conclusions", {})
            variant_conclusions = run_data.get("variant_conclusions", [])

            agent_in_run = (agent_name in conclusions or
                          any(vc.get("variant") == agent_name for vc in variant_conclusions))

            if not agent_in_run:
                continue

            total_count += 1

            # Track success (assuming successful runs have agent conclusions)
            if agent_name in conclusions or any(vc.get("variant") == agent_name for vc in variant_conclusions):
                success_count += 1

            # Calculate duration if available
            started = run_data.get("started_at")
            completed = run_data.get("completed_at")
            if started and completed and not last_timestamp:
                last_timestamp = started
                try:
                    start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                    duration_secs = int((end_dt - start_dt).total_seconds())
                    durations.append(duration_secs)
                except Exception:
                    pass

        success_rate = (success_count / total_count) if total_count > 0 else None
        avg_duration = "—"
        if durations:
            avg_secs = int(sum(durations) / len(durations))
            if avg_secs < 60:
                avg_duration = f"{avg_secs}s"
            else:
                avg_duration = f"{avg_secs // 60}:{avg_secs % 60:02d}"

        return {
            "timestamp": last_timestamp,
            "success_rate": success_rate,
            "avg_duration": avg_duration,
        }

    # ====================================================================
    # Default Values (for when no live data available)
    # ====================================================================

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

    # ====================================================================
    # Agents & Schedules (for system transparency UI)
    # ====================================================================

    def get_agents_summary(self) -> Dict[str, Any]:
        """Get all agents with their current config and recent stats."""
        agents_list = []

        for agent_name, agent_config in self.registry.agents.items():
            # Get recent run data for this agent
            run_file = self._get_latest_run_file()
            last_run_info = None
            recent_output = None

            if run_file:
                run_data = self._load_json(run_file)
                if run_data:
                    conclusions = run_data.get("conclusions", {})
                    if agent_name in conclusions:
                        last_run_info = {
                            "timestamp": run_data.get("started_at"),
                            "status": "passed",
                        }
                        recent_output = conclusions[agent_name]

            agent_info = {
                "name": agent_name,
                "role": agent_config.prompt_key,
                "type": getattr(agent_config, 'agent_type', 'agent'),
                "max_turns": agent_config.max_turns,
                "tools": agent_config.allowed_tools or [],
                "external_servers": agent_config.external_servers or [],
                "last_run": last_run_info,
                "recent_output": recent_output,
                "prompt_file": f"src/agents/prompts/{agent_config.prompt_key}.py",
            }
            agents_list.append(agent_info)

        return {
            "agents": agents_list,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_schedules_status(self) -> Dict[str, Any]:
        """Get current schedule status and recent runs."""
        schedules = []

        # Get modes and their schedule info
        for mode_name, mode_config in self.registry.modes.items():
            # Find recent runs for this mode
            recent_runs = self._get_runs_for_mode(mode_name, limit=3)

            schedule_info = {
                "mode": mode_name,
                "status": "configured",
                "recent_runs": recent_runs,
            }
            schedules.append(schedule_info)

        return {
            "schedules": schedules,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_agent_trace(self, agent_name: str) -> Dict[str, Any]:
        """Get recent execution trace for a specific agent."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return {
                "agent": agent_name,
                "trace": None,
                "message": "No recent run data",
            }

        run_data = self._load_json(run_file)
        if not run_data:
            return {
                "agent": agent_name,
                "trace": None,
                "message": "No recent run data",
            }

        # Extract agent conclusion and trace
        conclusions = run_data.get("conclusions", {})
        agent_conclusion = conclusions.get(agent_name)

        traces = run_data.get("traces", [])
        agent_trace = next((t for t in traces if t.get("agent") == agent_name), None)

        return {
            "agent": agent_name,
            "conclusion": agent_conclusion,
            "trace_summary": agent_trace.get("summary") if agent_trace else None,
            "timestamp": run_data.get("started_at"),
            "run_id": run_data.get("run_id"),
        }

    def _get_runs_for_mode(self, mode: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent runs for a specific mode."""
        runs = []
        data_dir = self.data_dir / "runs"

        if not data_dir.exists():
            return runs

        # Find run files for this mode
        run_files = sorted(
            data_dir.glob(f"run_*.jsonl"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        for run_file in run_files[:limit]:
            run_data = self._load_json(run_file)
            if run_data and run_data.get("mode") == mode:
                runs.append({
                    "run_id": run_data.get("run_id"),
                    "started_at": run_data.get("started_at"),
                    "status": run_data.get("status"),
                    "stage": run_data.get("stage"),
                    "elapsed": self._elapsed_seconds(run_data.get("started_at")),
                })

        return runs

    # ====================================================================
    # Graph Structure (for transparency UI)
    # ====================================================================

    def get_graph_structure(self, mode: str) -> Dict[str, Any]:
        """Get the LangGraph structure for a given mode.

        Returns nodes and edges in a format suitable for visualization.
        """
        try:
            from src.agents.graph import build_graph
            from src.agents.state import RunnerContext
            from src.agents.evolution import PromptEvolver

            mode_config = self.registry.modes.get(mode)
            if not mode_config:
                return {"error": f"Mode '{mode}' not found", "modes": list(self.registry.modes.keys())}

            # Build execution context (minimal for graph structure extraction)
            ctx = RunnerContext(
                settings=self.settings,
                paper=True,
                dry_run=True,
                auto_confirm=False,
                verbose=False,
                registry=self.registry,
                evolver=PromptEvolver(str(self.data_dir)),
                project_root=Path(__file__).parent.parent,
            )

            # Build the graph
            graph = build_graph(
                mode=mode,
                mode_config=mode_config,
                ctx=ctx,
                format_conclusions_fn=lambda x: x,
                fallback_fn=lambda x: x,
            )

            # Extract structure from compiled graph
            return self._extract_graph_structure(mode, mode_config, graph)
        except Exception as e:
            logger.warning(f"Graph structure extraction failed: {e}")
            return {"error": str(e), "modes": list(self.registry.modes.keys())}

    def _extract_graph_structure(self, mode: str, mode_config, compiled_graph) -> Dict[str, Any]:
        """Extract nodes and edges from a compiled LangGraph."""
        nodes = []
        edges = []
        node_map = {}

        # Get the underlying Graph object
        graph = compiled_graph.get_graph()

        # Extract nodes (excluding start/end markers)
        node_counter = 0
        for node_name in graph.nodes:
            if node_name in ("__start__", "__end__"):
                continue
            node_counter += 1
            node_type = self._classify_node_type(node_name, mode_config)
            is_critical = node_name in (mode_config.critical_agents or [])

            node_info = {
                "id": node_name,
                "label": self._node_label(node_name),
                "type": node_type,
                "critical": is_critical,
                "index": node_counter,
            }
            nodes.append(node_info)
            node_map[node_name] = node_counter

        # Build edges based on mode configuration structure instead of trying to extract from graph
        # This is more reliable than trying to parse LangGraph's internal representation
        critical_agents = set(mode_config.critical_agents or [])
        parallel_agents = mode_config.parallel_agents or []
        post_actions = mode_config.post_actions or []

        # Chain sub-agents sequentially
        if mode_config.sub_agents:
            for i, agent in enumerate(mode_config.sub_agents[:-1]):
                edges.append({"from": agent, "to": mode_config.sub_agents[i + 1], "type": "direct"})
            # Last sub-agent connects to fan_out or manager
            last_agent = mode_config.sub_agents[-1]
            if parallel_agents:
                edges.append({"from": last_agent, "to": "fan_out", "type": "direct"})
            else:
                edges.append({"from": last_agent, "to": "manager", "type": "direct"})

        # Fan-out and parallel variants
        if parallel_agents:
            edges.append({"from": "fan_out", "to": parallel_agents[0], "type": "direct"})
            for i, agent in enumerate(parallel_agents[:-1]):
                edges.append({"from": agent, "to": parallel_agents[i + 1], "type": "direct"})
            # All parallel agents connect to merge
            for agent in parallel_agents:
                edges.append({"from": agent, "to": "merge_variants", "type": "direct"})
            # Merge connects to manager
            edges.append({"from": "merge_variants", "to": "manager", "type": "direct"})

        # Manager connects to executor or post-actions
        if mode_config.executor:
            edges.append({"from": "manager", "to": "execution", "type": "direct"})
            if post_actions:
                edges.append({"from": "execution", "to": post_actions[0], "type": "direct"})
        else:
            if post_actions:
                edges.append({"from": "manager", "to": post_actions[0], "type": "direct"})

        # Chain post-actions
        if post_actions:
            for i, action in enumerate(post_actions[:-1]):
                edges.append({"from": action, "to": post_actions[i + 1], "type": "direct"})

        # Critical agents have conditional edge to fallback
        for agent in critical_agents:
            if agent in node_map:
                edges.append({"from": agent, "to": "fallback", "type": "conditional"})

        return {
            "mode": mode,
            "nodes": nodes,
            "edges": edges,
            "layout": "auto",  # Will be computed on frontend
            "summary": {
                "total_nodes": len(nodes),
                "critical_nodes": sum(1 for n in nodes if n["critical"]),
                "parallel_nodes": len(mode_config.parallel_agents or []),
            }
        }

    def _classify_node_type(self, node_name: str, mode_config) -> str:
        """Classify a node by its type."""
        if node_name == "load_screen":
            return "pre-action"
        elif node_name == "fan_out":
            return "fan-out"
        elif node_name == "merge_variants":
            return "merge"
        elif node_name in (mode_config.parallel_agents or []):
            return "variant"
        elif node_name == "manager":
            return "manager"
        elif node_name == "execution":
            return "executor"
        elif node_name in ("save_screen", "evolve_prompts", "report"):
            return "post-action"
        elif node_name == "fallback":
            return "fallback"
        elif node_name in (mode_config.sub_agents or []):
            return "agent"
        return "unknown"

    def _node_label(self, node_name: str) -> str:
        """Get display label for a node."""
        labels = {
            "load_screen": "Load Screen",
            "fan_out": "Fan Out",
            "merge_variants": "Merge Variants",
            "manager": "Manager",
            "execution": "Executor",
            "save_screen": "Save Screen",
            "evolve_prompts": "Evolve Prompts",
            "report": "Report",
            "fallback": "Fallback",
            "data": "Data Agent",
            "risk": "Risk Agent",
            "analysis_conservative": "Conservative",
            "analysis_aggressive": "Aggressive",
            "analysis_momentum": "Momentum",
        }
        return labels.get(node_name, node_name.replace("_", " ").title())


# Singleton instance
_service_instance: Optional[DashboardService] = None


def get_dashboard_service() -> DashboardService:
    """Get or create dashboard service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = DashboardService()
    return _service_instance
