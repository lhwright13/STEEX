"""P0-2 termination gate: the MCP server split must preserve the exact tool
surface and the shared-state contract.

Freezes the 41-tool name set so a future refactor can't silently drop, rename,
or fail to register a tool. Also asserts the shim re-exports (the claude CLI
launches the shim path and tests import tools by name) and that shared session
state lives in the single _state module.
"""
import src.agents.mcp_server as mcp_server
from src.agents.mcp_tools import _state

# The frozen contract. Tool names are an external API: config/agents.yaml
# allowed_tools reference them, and nodes.py rewrites them into
# mcp__steex__<name> permission strings. Changing this set is a breaking change.
EXPECTED_TOOLS = {
    "apply_config_changes", "assess_portfolio_risk", "check_alpha_decay",
    "check_data_health", "construct_portfolio", "cross_reference_findings",
    "execute_entries", "execute_exits", "generate_buy_list", "generate_report",
    "generate_sell_list", "get_account", "get_config_change_history",
    "get_current_weights", "get_exit_signals", "get_learning_gaps",
    "get_learning_journal", "get_order_status", "get_pending_recommendations",
    "get_positions", "get_regime", "get_regime_screening_params",
    "get_signal_confidence", "get_trade_history", "get_unusual_options_activity",
    "load_screen_results", "place_paper_order", "prefetch_data",
    "propose_config_changes", "rank_candidates", "rank_candidates_with_weights",
    "refresh_data", "run_learning_loop", "run_postmortem", "run_screening",
    "run_screening_variant", "run_signal_research", "save_screen_results",
    "size_buy_list", "sync_broker", "validate_oos",
    "send_user_message",  # P1-1
}


def _registered_names():
    return {t.name for t in mcp_server.mcp._tool_manager.list_tools()}


def test_exactly_42_tools_registered():
    assert len(EXPECTED_TOOLS) == 42  # 41 from P0-2 + send_user_message (P1-1)
    names = _registered_names()
    assert names == EXPECTED_TOOLS, {
        "missing": EXPECTED_TOOLS - names,
        "unexpected": names - EXPECTED_TOOLS,
    }


def test_shim_reexports_every_tool_callable():
    # `from src.agents.mcp_server import <tool>` must keep resolving.
    for name in EXPECTED_TOOLS:
        assert callable(getattr(mcp_server, name, None)), f"shim missing tool {name}"


def test_shim_reexports_constants_and_helpers():
    # External importers: manager.py imports PAPER_ORDER_MAX_USD; tier1 tests
    # import VARIANT_PARAMS/REGIME_PARAMS; variant tests import _safe_json.
    assert mcp_server.PAPER_ORDER_MAX_USD == 1000.0
    assert "conservative" in mcp_server.VARIANT_PARAMS
    assert "risk_on" in mcp_server.REGIME_PARAMS
    assert callable(mcp_server._safe_json)


def test_shared_state_lives_in_one_state_module():
    for attr in ("settings", "manager", "dry_run", "pipeline_result", "ranked",
                 "exit_signals", "regime", "buy_list", "sell_list"):
        assert hasattr(_state, attr), f"_state missing {attr}"
    assert callable(_state.init_manager)


def test_no_domain_module_exceeds_size_budget():
    # P0-2 termination: no split module is itself a monolith (the original was
    # 1510 lines). Budget is ~450 — screen.py runs ~440 because it carries the
    # VARIANT_PARAMS + REGIME_PARAMS preset dicts (~47 lines of data) alongside
    # its 9 cohesive tools; fragmenting those would be over-engineering.
    import pathlib
    pkg = pathlib.Path(_state.__file__).parent
    oversized = {
        f.name: sum(1 for _ in f.open())
        for f in pkg.glob("*.py")
        if sum(1 for _ in f.open()) > 450
    }
    assert not oversized, oversized
