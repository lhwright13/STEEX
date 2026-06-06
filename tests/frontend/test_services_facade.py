"""P0-5 termination gate: the services split must preserve the public surface.

DashboardService was split into domain mixins behind a facade. app.py and every
caller import from `frontend.services` exactly as before, so the public method
set is a frozen contract — this freezes it and asserts the split's structural
invariants (mixin composition, module size budget, facade-only public import).
"""
from pathlib import Path

from frontend.services import DashboardService, get_dashboard_service

# The public surface callers depend on. Adding a method is fine (update this set);
# silently dropping or renaming one is a breaking change app.py won't survive.
PUBLIC_METHODS = {
    "get_pipeline_current", "get_pipeline_live", "get_consensus",
    "get_screening_stats", "get_regime", "get_manager_decision",
    "get_portfolio_performance", "get_portfolio_holdings", "get_signal_health",
    "get_system_agents",
    "get_system_schedules", "get_agent_detail", "get_agent_last_output",
    "get_workflow_topology", "get_graph_structure", "get_recent_runs",
    "get_event_activity", "get_event_aggregate", "get_event_figures",
    "get_event_trade_cards", "get_user_updates", "get_user_update", "get_run_trace",
    "get_controls", "set_controls", "get_trade_history", "get_agent_timeline",
}


def test_public_surface_intact():
    for name in PUBLIC_METHODS:
        assert callable(getattr(DashboardService, name, None)), f"missing {name}"


def test_no_unexpected_public_methods():
    actual = {n for n in dir(DashboardService)
              if not n.startswith("_") and callable(getattr(DashboardService, n))}
    assert actual == PUBLIC_METHODS, {
        "added": actual - PUBLIC_METHODS, "removed": PUBLIC_METHODS - actual}


def test_composed_from_domain_mixins():
    base_names = {c.__name__ for c in DashboardService.__mro__}
    assert "DashboardServiceBase" in base_names
    for mixin in ("PipelineMixin", "HoldingsMixin", "SystemMixin", "SchedulesMixin",
                  "WorkflowMixin", "EventsMixin", "UserUpdatesMixin", "ControlsMixin"):
        assert mixin in base_names, f"facade missing {mixin}"


def test_singleton():
    assert get_dashboard_service() is get_dashboard_service()


def test_no_module_exceeds_size_budget():
    pkg = Path(__file__).resolve().parents[2] / "frontend" / "services"
    oversized = {f.name: sum(1 for _ in f.open())
                 for f in pkg.glob("*.py")
                 if sum(1 for _ in f.open()) > 500}
    assert not oversized, oversized
