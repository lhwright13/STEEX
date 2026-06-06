"""P0-4: the workflow graph is a single source of truth.

The emitted topology must be derived from the SAME compiled LangGraph the
orchestrator runs, for every mode — so the dashboard can't drift from the
implementation. These tests rebuild each mode's graph independently and assert
the introspected topology matches node-for-node and edge-for-edge.
"""
from pathlib import Path

import pytest

from src.agents import graph_introspect as gi
from src.agents.graph import build_graph
from src.agents.registry import AgentRegistry
from src.agents.state import RunnerContext
from config.settings import get_settings

MODES = ["screen", "enter", "monitor", "post_market", "learning"]


@pytest.fixture(scope="module")
def registry():
    return AgentRegistry(Path("config/agents.yaml"))


def _compiled_graph(mode, registry):
    from src.agents.evolution import PromptEvolver
    s = get_settings()
    ctx = RunnerContext(
        settings=s, paper=True, dry_run=True, auto_confirm=False, verbose=False,
        registry=registry, evolver=PromptEvolver(s.data_dir), project_root=Path("."),
    )
    return build_graph(mode, registry.get_mode(mode), ctx, lambda **k: "", lambda *a, **k: {})


@pytest.mark.parametrize("mode", MODES)
def test_topology_matches_compiled_graph(mode, registry):
    topo = gi.mode_topology(mode, registry=registry)
    gg = _compiled_graph(mode, registry).get_graph()

    assert {n["id"] for n in topo["nodes"]} == set(gg.nodes), mode
    assert topo["node_count"] == len(gg.nodes)
    assert topo["edge_count"] == len(gg.edges)

    topo_edges = {(e["source"], e["target"]) for e in topo["edges"]}
    real_edges = {(e.source, e.target) for e in gg.edges}
    assert topo_edges == real_edges, mode


def test_node_metadata_and_known_wiring(registry):
    monitor = gi.mode_topology("monitor", registry=registry)
    ids = {n["id"] for n in monitor["nodes"]}
    # The deterministic exit floor (H3) and the executor must be present.
    assert "reconcile_exits" in ids
    risk = next(n for n in monitor["nodes"] if n["id"] == "risk")
    assert risk["kind"] == "agent" and "critical" in risk["roles"]
    assert risk["prompt_key"] and risk["conclusion"]

    screen = gi.mode_topology("screen", registry=registry)
    sids = {n["id"] for n in screen["nodes"]}
    assert "save_screen" in sids
    # parallel analysis variants are tagged
    variants = [n for n in screen["nodes"] if "variant" in n.get("roles", [])]
    assert len(variants) >= 1


def test_all_topologies_covers_every_mode(registry):
    topo = gi.all_topologies(registry=registry)
    assert set(topo.keys()) == set(registry.modes)
    for mode, t in topo.items():
        # each mode is either: an error, a real graph, or an honestly-flagged
        # non-pipeline mode (event_scan/test_roundtrip run a special path).
        assert (
            "error" in t
            or t.get("graph_backed") is False
            or (t["node_count"] > 0 and t["edge_count"] > 0)
        ), mode


def test_special_modes_flagged_not_graph_backed(registry):
    for mode in ("event_scan", "test_roundtrip"):
        t = gi.mode_topology(mode, registry=registry)
        assert t["graph_backed"] is False and t["nodes"] == []


def test_route_returns_topology():
    from frontend.app import create_app
    app = create_app()
    client = app.test_client()

    r = client.get("/api/v1/system/workflow-graph/monitor")
    assert r.status_code == 200
    data = r.get_json()
    assert data["mode"] == "monitor"
    assert any(n["id"] == "reconcile_exits" for n in data["nodes"])

    r_all = client.get("/api/v1/system/workflow-graph")
    assert r_all.status_code == 200
    assert "monitor" in r_all.get_json()["modes"]
