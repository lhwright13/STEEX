"""Workflow-graph single source of truth (P0-4).

Emits a serializable topology (nodes, edges, per-node metadata) for every mode by
introspecting the COMPILED LangGraph produced by `build_graph` — so the dashboard
renders the real wiring instead of a hand-maintained SVG that drifts from the
implementation (the audit's P-8). Build-time only: the format/fallback callbacks
fire at runtime, so no-ops are sufficient and nothing connects to a broker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .graph import build_graph
from .registry import AgentRegistry, ModeConfig
from .state import RunnerContext

_TERMINALS = {"__start__", "__end__"}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _PROJECT_ROOT / "config" / "agents.yaml"

# Modes the orchestrator runs via a special path, NOT build_graph (see
# Orchestrator.run_mode). build_graph would still compile *a* graph for these, but
# the runtime never executes it — so emitting it as authoritative would lie. We
# flag them graph_backed=False instead.
_NON_PIPELINE_MODES = {"event_scan", "test_roundtrip", "stop_sync", "heartbeat"}


def _noop(*args, **kwargs):
    return {}


def _registry(registry: Optional[AgentRegistry]) -> AgentRegistry:
    return registry or AgentRegistry(_CONFIG)


def _settings(settings):
    if settings is not None:
        return settings
    from config.settings import get_settings
    return get_settings()


def _build_ctx(registry: AgentRegistry, settings) -> RunnerContext:
    from .evolution import PromptEvolver
    return RunnerContext(
        settings=settings, paper=True, dry_run=True, auto_confirm=False,
        verbose=False, registry=registry, evolver=PromptEvolver(settings.data_dir),
        project_root=_PROJECT_ROOT,
    )


def _describe_node(node_id: str, registry: AgentRegistry, cfg: ModeConfig) -> Dict[str, Any]:
    """Tag a graph node with what it is, pulling agent metadata from the registry."""
    if node_id in _TERMINALS:
        return {"id": node_id, "kind": "terminal"}

    agent = registry.get_agent(node_id)
    if agent is not None:
        roles = []
        if node_id in cfg.critical_agents:
            roles.append("critical")
        if node_id == cfg.manager:
            roles.append("manager")
        if node_id == cfg.executor:
            roles.append("executor")
        if node_id in cfg.parallel_agents:
            roles.append("variant")
        if node_id == cfg.meta_agent:
            roles.append("meta")
        return {
            "id": node_id,
            "kind": "agent",
            "prompt_key": agent.prompt_key,
            "conclusion": agent.conclusion_name,
            "needs_tools": agent.needs_tools,
            "tools": list(agent.allowed_tools),
            "external_servers": list(agent.external_servers),
            "roles": roles,
        }

    # Non-agent nodes: load_screen / save_screen / report / fallback / fan_out /
    # merge_variants / reconcile_exits / execution.
    return {"id": node_id, "kind": "action"}


def mode_topology(
    mode: str, registry: Optional[AgentRegistry] = None, settings=None
) -> Dict[str, Any]:
    """Topology for one mode, derived from its compiled LangGraph."""
    registry = _registry(registry)
    cfg = registry.get_mode(mode)
    if cfg is None:
        raise ValueError(f"unknown mode {mode!r}")

    if mode in _NON_PIPELINE_MODES:
        # Don't present a compiled graph the runtime never runs.
        return {
            "mode": mode,
            "graph_backed": False,
            "runtime": "special",
            "note": ("Runs via a dedicated orchestrator path, not the LangGraph "
                     "pipeline — no agent graph to render."),
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
        }

    compiled = build_graph(mode, cfg, _build_ctx(registry, _settings(settings)), _noop, _noop)
    gg = compiled.get_graph()
    nodes = [_describe_node(nid, registry, cfg) for nid in gg.nodes]
    edges = [
        {"source": e.source, "target": e.target, "conditional": bool(e.conditional)}
        for e in gg.edges
    ]
    return {
        "mode": mode,
        "graph_backed": True,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def all_topologies(registry: Optional[AgentRegistry] = None, settings=None) -> Dict[str, Any]:
    """Topology for every mode the registry knows how to build a graph for.

    A mode that isn't graph-backed (e.g. the deterministic event_scan path) is
    recorded with an `error` rather than omitted, so the caller sees the full
    mode list.
    """
    registry = _registry(registry)
    settings = _settings(settings)
    out: Dict[str, Any] = {}
    for mode in registry.modes:
        try:
            out[mode] = mode_topology(mode, registry, settings)
        except Exception as e:  # non-graph mode or build error — surface it
            out[mode] = {"mode": mode, "error": str(e)}
    return out
