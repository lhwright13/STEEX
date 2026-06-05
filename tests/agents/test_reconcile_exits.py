"""Tests for the deterministic exit/stop reconciliation node (H3) and the
transient CLI error classifier (H5).

The reconcile node is the deterministic floor under monitor/post_market: it
force-merges real exit signals into the LLM manager's advisory `sells` so the
execution gate can't stay fail-open, and re-syncs server-side stops in the
agent path. These tests pin the merge/dedup behavior and the graph wiring with
a fully mocked manager (no broker, no CLI).
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.nodes import make_reconcile_exits_node, _classify_transient_cli_error
from src.agents.graph import build_graph
from src.agents.registry import AgentRegistry
from src.agents.state import RunnerContext
from config.settings import get_settings


@pytest.fixture
def registry():
    return AgentRegistry(Path("config/agents.yaml"))


@pytest.fixture
def ctx(registry):
    from src.agents.evolution import PromptEvolver
    settings = get_settings()
    return RunnerContext(
        settings=settings,
        paper=True,
        dry_run=True,  # dry_run -> reconcile skips the broker stop-sync loop
        auto_confirm=False,
        verbose=False,
        registry=registry,
        evolver=PromptEvolver(settings.data_dir),
        project_root=Path("."),
    )


def _state(mode, manager_decision):
    return {
        "mode": mode,
        "task_context": "",
        "today": "2026-06-05",
        "run_id": "test",
        "conclusions": {},
        "variant_conclusions": [],
        "traces": [],
        "manager_decision": manager_decision,
        "screen_data": None,
        "abort": False,
        "abort_reason": None,
    }


def _mock_manager(det_sells):
    mgr = MagicMock()
    mgr.get_exit_signals.return_value = []
    mgr.generate_sell_list.return_value = det_sells
    mgr.broker = None  # skip stop-sync loop regardless of dry_run
    mgr.position_manager.get_all_positions.return_value = []
    return mgr


def test_deterministic_exit_forced_in_when_llm_missed_it(ctx):
    """A live exit signal the LLM omitted must be force-merged into sells."""
    det = [{"ticker": "MO", "shares": 10, "reason": "stop_loss", "urgency": "immediate"}]
    state = _state("monitor", {"sells": []})  # LLM saw no exits
    with patch("src.agents.nodes._get_reconcile_manager", return_value=_mock_manager(det)):
        out = make_reconcile_exits_node(ctx)(state)
    tickers = [s["ticker"] for s in out["manager_decision"]["sells"]]
    assert "MO" in tickers


def test_merge_dedups_by_ticker_deterministic_wins(ctx):
    """When LLM and engine both name a ticker, the deterministic fields win."""
    det = [{"ticker": "MAR", "shares": 7, "reason": "max_hold", "urgency": "end_of_day"}]
    state = _state("monitor", {"sells": [{"ticker": "MAR", "reason": "llm_hunch"}]})
    with patch("src.agents.nodes._get_reconcile_manager", return_value=_mock_manager(det)):
        out = make_reconcile_exits_node(ctx)(state)
    sells = out["manager_decision"]["sells"]
    assert len(sells) == 1  # not duplicated
    assert sells[0]["reason"] == "max_hold"  # deterministic signal won


def test_no_signals_leaves_decision_unchanged(ctx):
    state = _state("monitor", {"sells": [{"ticker": "X"}]})
    with patch("src.agents.nodes._get_reconcile_manager", return_value=_mock_manager([])):
        out = make_reconcile_exits_node(ctx)(state)
    assert out["manager_decision"]["sells"] == [{"ticker": "X"}]


def test_manager_failure_is_swallowed(ctx):
    """A reconcile failure must not break the run — returns no change."""
    state = _state("monitor", {"sells": []})
    with patch("src.agents.nodes._get_reconcile_manager", side_effect=RuntimeError("boom")):
        out = make_reconcile_exits_node(ctx)(state)
    assert out == {}


def test_graph_wires_reconcile_for_monitor_and_postmarket(ctx, registry):
    fmt = lambda **k: ""
    fb = lambda *a, **k: {}
    for mode in ("monitor", "post_market"):
        compiled = build_graph(mode, registry.get_mode(mode), ctx, fmt, fb)
        assert "reconcile_exits" in compiled.get_graph().nodes


def test_graph_no_reconcile_for_enter(ctx, registry):
    fmt = lambda **k: ""
    fb = lambda *a, **k: {}
    compiled = build_graph("enter", registry.get_mode("enter"), ctx, fmt, fb)
    assert "reconcile_exits" not in compiled.get_graph().nodes


@pytest.mark.parametrize("stdout,stderr,expected", [
    ('{"is_error": true, "result": "API Error 401"}', "", "auth 401"),
    ("", "rate limit 429 exceeded", "rate-limit 429"),
    ("503 service unavailable", "", "server 503"),
    ("overloaded_error", "", "transient"),
    ('{"is_error": true, "result": "400 bad request"}', "", None),
    ("", "", None),
])
def test_transient_classifier(stdout, stderr, expected):
    assert _classify_transient_cli_error(stdout, stderr) == expected
