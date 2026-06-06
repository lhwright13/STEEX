"""WorkflowMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class WorkflowMixin:
    def get_workflow_topology(self, mode: Optional[str] = None) -> Dict[str, Any]:
        """Workflow graph(s) derived from the live compiled LangGraph (P0-4).

        Single source of truth for the Workflows UI (P3-7): no mode given ->
        every mode's topology; a mode -> just that one. Supersedes the older
        get_graph_structure, which re-derived edges from ModeConfig.
        """
        from src.agents.graph_introspect import mode_topology, all_topologies
        if mode:
            return mode_topology(mode, registry=self.registry, settings=self.settings)
        return {
            "modes": all_topologies(registry=self.registry, settings=self.settings),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

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
                project_root=Path(__file__).resolve().parents[2],
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
