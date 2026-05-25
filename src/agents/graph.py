"""LangGraph StateGraph construction for the multi-agent orchestration.

Builds a compiled graph per mode from agents.yaml, wiring nodes and edges
based on the mode configuration. Each mode defines its own pipeline structure
(pre-actions, sub-agents, manager, executor, post-actions).
"""

import logging
from typing import Callable

from langgraph.graph import StateGraph, END

from .nodes import (
    make_agent_node,
    make_manager_node,
    make_execution_node,
    make_load_screen_node,
    make_save_screen_node,
    make_evolve_prompts_node,
    make_report_node,
    make_fallback_node,
    make_fan_out_node,
    make_variant_agent_node,
    make_merge_variants_node,
    route_after_agent,
    route_execution_gate,
    route_evolve_gate,
)
from .registry import ModeConfig
from .state import PipelineState, RunnerContext

logger = logging.getLogger("steex.graph")


def build_graph(
    mode: str,
    mode_config: ModeConfig,
    ctx: RunnerContext,
    format_conclusions_fn: Callable,
    fallback_fn: Callable,
) -> "langgraph.graph.Compiled":
    """Construct and compile a StateGraph for the given mode.

    Args:
        mode: The mode name (screen, enter, monitor, etc.)
        mode_config: The ModeConfig from agents.yaml
        ctx: RunnerContext with settings, registry, etc.
        format_conclusions_fn: Function to format conclusions dict for manager
        fallback_fn: Function to call for deterministic fallback

    Returns:
        A compiled LangGraph that can be invoked with an initial state.
    """
    graph = StateGraph(PipelineState)

    # Determine critical agent set
    critical_set = set(mode_config.critical_agents) if mode_config.critical_agents else set()

    # --- Add nodes ---

    # Pre-action nodes
    if "load_screen" in mode_config.pre_actions:
        graph.add_node("load_screen", make_load_screen_node(ctx))

    # Sub-agent nodes
    for agent_name in mode_config.sub_agents:
        is_critical = agent_name in critical_set
        graph.add_node(agent_name, make_agent_node(agent_name, is_critical, ctx))

    # Parallel analysis variant nodes (Tier 1 upgrade)
    if mode_config.parallel_agents:
        graph.add_node("fan_out", make_fan_out_node(mode_config.parallel_agents))

        for agent_name in mode_config.parallel_agents:
            graph.add_node(agent_name, make_variant_agent_node(agent_name, ctx))

        if mode_config.meta_agent:
            graph.add_node(
                "merge_variants",
                make_merge_variants_node(mode_config.meta_agent, ctx, format_conclusions_fn),
            )

    # Manager node
    graph.add_node(
        "manager",
        make_manager_node(mode_config.manager, mode, ctx, format_conclusions_fn),
    )

    # Executor node (optional)
    if mode_config.executor:
        graph.add_node("execution", make_execution_node(mode_config.executor, ctx))

    # Post-action nodes
    if "save_screen" in mode_config.post_actions:
        graph.add_node("save_screen", make_save_screen_node(ctx))

    if "evolve_prompts" in mode_config.post_actions:
        graph.add_node(
            "evolve_prompts",
            make_evolve_prompts_node(ctx, format_conclusions_fn),
        )

    if "report" in mode_config.post_actions:
        graph.add_node("report", make_report_node(mode, ctx))

    # Fallback node (always present)
    graph.add_node("fallback", make_fallback_node(mode, ctx, fallback_fn))

    # --- Wire edges ---

    # Determine entry point and pre-action edge target
    if "load_screen" in mode_config.pre_actions:
        entry_point = "load_screen"
    elif mode_config.sub_agents:
        entry_point = mode_config.sub_agents[0]
    elif mode_config.parallel_agents:
        entry_point = "fan_out"
    else:
        entry_point = "manager"

    graph.set_entry_point(entry_point)

    # Pre-action → next node (sub-agent, fan-out, or manager)
    if "load_screen" in mode_config.pre_actions:
        if mode_config.sub_agents:
            next_after_load = mode_config.sub_agents[0]
        elif mode_config.parallel_agents:
            next_after_load = "fan_out"
        else:
            next_after_load = "manager"
        graph.add_edge("load_screen", next_after_load)

    # Chain sub-agents. Critical agents have conditional edges, others are direct.
    # If parallel agents follow, last sub-agent connects to fan_out instead of manager.
    agents = mode_config.sub_agents
    next_after_agents = "fan_out" if mode_config.parallel_agents else "manager"

    for i, agent_name in enumerate(agents):
        next_node = agents[i + 1] if i + 1 < len(agents) else next_after_agents

        if agent_name in critical_set:
            graph.add_conditional_edges(
                agent_name,
                route_after_agent,
                {"continue": next_node, "fallback": "fallback"},
            )
        else:
            graph.add_edge(agent_name, next_node)

    # Parallel variant analysis edges (Tier 1 upgrade)
    if mode_config.parallel_agents and mode_config.meta_agent:
        # Each parallel variant → merge_variants
        for agent_name in mode_config.parallel_agents:
            graph.add_edge(agent_name, "merge_variants")

        # merge_variants → manager (with conditional edge for abort)
        graph.add_conditional_edges(
            "merge_variants",
            route_after_agent,
            {"continue": "manager", "fallback": "fallback"},
        )

    # Manager → executor or post-actions
    if mode_config.executor:
        first_after_manager = _get_first_post_action(mode_config)
        graph.add_conditional_edges(
            "manager",
            route_execution_gate,
            {"execute": "execution", "skip_execution": first_after_manager or END},
        )
        graph.add_edge("execution", first_after_manager or END)
    else:
        first_after_manager = _get_first_post_action(mode_config)
        if first_after_manager:
            graph.add_edge("manager", first_after_manager)
        else:
            graph.add_edge("manager", END)

    # Post-action chain
    post_nodes = _get_post_action_nodes(mode_config)
    for i, node_name in enumerate(post_nodes):
        next_node = post_nodes[i + 1] if i + 1 < len(post_nodes) else END

        if node_name == "evolve_prompts":
            graph.add_conditional_edges(
                "manager",
                route_evolve_gate,
                {"evolve": "evolve_prompts", "skip_evolve": next_node},
            )
            graph.add_edge("evolve_prompts", next_node)
        else:
            graph.add_edge(node_name, next_node)

    # Fallback → END
    graph.add_edge("fallback", END)

    return graph.compile()


def _get_first_post_action(mode_config: ModeConfig) -> str:
    """Get the first post-action node in execution order, or None."""
    post_actions = mode_config.post_actions or []
    order = ["save_screen", "evolve_prompts", "report"]
    for action in order:
        if action in post_actions:
            return action
    return None


def _get_post_action_nodes(mode_config: ModeConfig) -> list[str]:
    """Get all post-action nodes in execution order."""
    post_actions = mode_config.post_actions or []
    order = ["save_screen", "evolve_prompts", "report"]
    result = []
    for action in order:
        if action in post_actions:
            result.append(action)
    return result
