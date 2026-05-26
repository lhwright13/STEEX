"""Tests for parallel variant execution and state merging.

Tests the fan-out/fan-in orchestration, node factories, and state reducer
without requiring actual claude CLI execution.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from langgraph.types import Send

from src.agents.state import PipelineState, RunnerContext
from src.agents.registry import AgentRegistry, ModeConfig
from src.agents.nodes import (
    make_fan_out_node,
    make_variant_agent_node,
    make_merge_variants_node,
    _trace_to_dict,
)
from src.agents.graph import build_graph
from src.agents.conclusions import AnalysisConclusion, MetaAnalysisConclusion, CandidateStock
from src.agents.trace import AgentTrace
from config.settings import get_settings


@pytest.fixture
def registry():
    """Load the actual registry from config/agents.yaml"""
    return AgentRegistry(Path("config/agents.yaml"))


@pytest.fixture
def settings():
    """Get settings"""
    return get_settings()


@pytest.fixture
def mock_context(registry, settings):
    """Create a mock RunnerContext"""
    from src.agents.evolution import PromptEvolver

    return RunnerContext(
        settings=settings,
        paper=True,
        dry_run=True,
        auto_confirm=False,
        verbose=False,
        registry=registry,
        evolver=PromptEvolver(settings.data_dir),
        project_root=Path("."),
    )


@pytest.fixture
def sample_pipeline_state():
    """Create a sample pipeline state"""
    return {
        "mode": "screen",
        "task_context": "Screen for trading candidates",
        "today": "2026-05-24",
        "run_id": "test_run_123",
        "conclusions": {},
        "variant_conclusions": [],
        "traces": [],
        "manager_decision": None,
        "screen_data": None,
        "abort": False,
        "abort_reason": None,
    }


@pytest.fixture
def mock_agent_conclusion():
    """Create a mock AnalysisConclusion"""
    candidate = CandidateStock(
        ticker="AAPL",
        composite_score=72.5,
        momentum_6m=0.15,
        momentum_score=62.0,
        insider_buyers=4,
        insider_score=78.5,
        volume_score=65.0,
        sentiment_score=75.0,
        fundamental_score=82.0,
        volume_surge=1.3,
    )
    return AnalysisConclusion(
        universe_size=500,
        screening_funnel={"stage_1": 500, "stage_2": 250, "stage_3": 120, "stage_4": 85, "stage_5": 8},
        candidates=[candidate],
        portfolio_selected=1,
        diversification_ratio=0.75,
        reasoning="Test conclusion",
    )


# ============================================================================
# Test: Graph Building with Parallel Agents
# ============================================================================


class TestGraphBuildingParallel:
    """Verify graph structure for modes with parallel agents"""

    def test_screen_mode_has_parallel_config(self, registry):
        """Screen mode should have parallel_agents and meta_agent configured"""
        screen_mode = registry.modes.get("screen")
        assert screen_mode is not None
        assert len(screen_mode.parallel_agents) == 3
        assert screen_mode.meta_agent == "meta_analysis"

    def test_parallel_agents_registered(self, registry):
        """All 4 new agents should be registered"""
        new_agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum", "meta_analysis"]
        for agent in new_agents:
            assert agent in registry.agents, f"{agent} not in registry"

    def test_variant_agents_have_correct_config(self, registry):
        """Variant agents should have correct tools and external servers"""
        variant_agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]
        for agent_name in variant_agents:
            agent = registry.agents[agent_name]
            assert agent.needs_tools is True
            assert "alpaca" in agent.external_servers
            assert "alphavantage" in agent.external_servers
            assert "polygon" in agent.external_servers

    def test_meta_analysis_agent_config(self, registry):
        """Meta analysis agent should have no tools"""
        meta = registry.agents["meta_analysis"]
        assert meta.needs_tools is False
        assert meta.conclusion_name == "MetaAnalysisConclusion"

    def test_graph_builds_with_all_nodes(self, registry, mock_context):
        """Graph for screen mode should build with all parallel nodes"""
        mode_config = registry.modes["screen"]
        graph = build_graph(
            "screen",
            mode_config,
            mock_context,
            lambda x: str(x),
            lambda: None
        )

        node_names = set(graph.nodes.keys()) if hasattr(graph, "nodes") else set()

        # Verify core nodes
        assert "data" in node_names
        assert "risk" in node_names
        assert "manager" in node_names

        # Verify parallel execution nodes
        assert "fan_out" in node_names
        assert "analysis_conservative" in node_names
        assert "analysis_aggressive" in node_names
        assert "analysis_momentum" in node_names
        assert "merge_variants" in node_names

    def test_entry_point_correct_for_parallel_mode(self, registry, mock_context):
        """Entry point should be first sub-agent when sub_agents exist"""
        mode_config = registry.modes["screen"]
        graph = build_graph(
            "screen",
            mode_config,
            mock_context,
            lambda x: str(x),
            lambda: None
        )

        # Entry point should be first sub_agent (data)
        start_targets = [e.target for e in graph.get_graph().edges if e.source == "__start__"]
        assert start_targets == ["data"]


# ============================================================================
# Test: Fan-Out Node Factory
# ============================================================================


class TestFanOutNode:
    """Test the fan-out node that dispatches to parallel variants"""

    def test_fan_out_returns_send_objects(self, sample_pipeline_state):
        """Fan-out node should return list of Send objects"""
        parallel_agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]
        node = make_fan_out_node(parallel_agents)

        result = node(sample_pipeline_state)

        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(send, Send) for send in result)

    def test_fan_out_sends_to_correct_agents(self, sample_pipeline_state):
        """Each Send should target the correct agent"""
        parallel_agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]
        node = make_fan_out_node(parallel_agents)

        result = node(sample_pipeline_state)

        agent_names = [send.node for send in result]
        assert agent_names == parallel_agents

    def test_fan_out_preserves_state(self, sample_pipeline_state):
        """Fan-out should pass state to each Send unchanged"""
        parallel_agents = ["analysis_conservative", "analysis_aggressive"]
        node = make_fan_out_node(parallel_agents)

        result = node(sample_pipeline_state)

        for send in result:
            # Each Send should contain the same state
            assert send.arg == sample_pipeline_state


# ============================================================================
# Test: State Reducer (variant_conclusions)
# ============================================================================


class TestStateReducer:
    """Test that variant_conclusions reducer properly merges results"""

    def test_variant_conclusions_field_exists(self):
        """PipelineState should have variant_conclusions field"""
        from typing import get_type_hints
        annotations = PipelineState.__annotations__
        assert "variant_conclusions" in annotations

    def test_variant_conclusions_is_annotated_list(self):
        """variant_conclusions should be Annotated[list, add]"""
        from typing import get_origin, get_args
        annotations = PipelineState.__annotations__
        vc_type = annotations["variant_conclusions"]

        # Check it's Annotated
        assert hasattr(vc_type, "__metadata__")
        # The metadata should contain the add operator
        assert len(vc_type.__metadata__) > 0

    def test_initial_state_has_empty_variant_conclusions(self, sample_pipeline_state):
        """Initial state should have empty variant_conclusions list"""
        assert sample_pipeline_state["variant_conclusions"] == []

    def test_can_append_to_variant_conclusions(self, sample_pipeline_state):
        """Appending to variant_conclusions should work like normal list"""
        sample_pipeline_state["variant_conclusions"].append({"variant": "conservative", "conclusion": {}})
        assert len(sample_pipeline_state["variant_conclusions"]) == 1


# ============================================================================
# Test: Variant Agent Node Factory
# ============================================================================


class TestVariantAgentNode:
    """Test variant agent node factories"""

    def test_variant_node_returns_dict(self, mock_context, sample_pipeline_state, mock_agent_conclusion):
        """Variant node should return a state update dict"""
        with patch("src.agents.nodes.run_agent") as mock_run:
            mock_run.return_value = (mock_agent_conclusion, AgentTrace(run_id="123", role="test", mode="screen"))

            node = make_variant_agent_node("analysis_conservative", mock_context)
            result = node(sample_pipeline_state)

            assert isinstance(result, dict)
            assert "variant_conclusions" in result
            assert "traces" in result

    def test_variant_node_appends_to_list(self, mock_context, sample_pipeline_state, mock_agent_conclusion):
        """Variant node should append (not overwrite) variant_conclusions"""
        with patch("src.agents.nodes.run_agent") as mock_run:
            mock_run.return_value = (mock_agent_conclusion, AgentTrace(run_id="123", role="test", mode="screen"))

            node = make_variant_agent_node("analysis_conservative", mock_context)
            result = node(sample_pipeline_state)

            # Should return list with single item
            assert isinstance(result["variant_conclusions"], list)
            assert len(result["variant_conclusions"]) == 1
            assert result["variant_conclusions"][0]["variant"] == "analysis_conservative"

    def test_variant_node_stores_conclusion(self, mock_context, sample_pipeline_state, mock_agent_conclusion):
        """Variant node should store the conclusion in variant_conclusions"""
        with patch("src.agents.nodes.run_agent") as mock_run:
            mock_run.return_value = (mock_agent_conclusion, AgentTrace(run_id="123", role="test", mode="screen"))

            node = make_variant_agent_node("analysis_aggressive", mock_context)
            result = node(sample_pipeline_state)

            item = result["variant_conclusions"][0]
            assert item["variant"] == "analysis_aggressive"
            assert item["conclusion"] is not None
            assert item["conclusion"]["universe_size"] == 500

    def test_variant_node_handles_failure(self, mock_context, sample_pipeline_state):
        """A variant returning no conclusion is recorded as None without aborting.

        Parallel fan-out is resilient by design: one variant producing nothing
        must not abort the whole screen, so the meta-merge can still synthesize
        from the surviving variants.
        """
        with patch("src.agents.nodes.run_agent") as mock_run:
            mock_run.return_value = (None, AgentTrace(run_id="123", role="test", mode="screen"))

            node = make_variant_agent_node("analysis_momentum", mock_context)
            result = node(sample_pipeline_state)

            assert result.get("abort") is not True
            assert result["variant_conclusions"][0]["conclusion"] is None


# ============================================================================
# Test: Merge Variants Node
# ============================================================================


class TestMergeVariantsNode:
    """Test the meta-analysis merge node"""

    def test_merge_requires_variant_conclusions(self, mock_context, sample_pipeline_state):
        """Merge should fail if no variant_conclusions provided"""
        node = make_merge_variants_node("meta_analysis", mock_context, lambda x: str(x))

        # Empty variant_conclusions
        result = node(sample_pipeline_state)
        assert result["abort"] is True

    def test_merge_calls_meta_agent(self, mock_context, sample_pipeline_state):
        """Merge should invoke MetaAnalysisAgent with variant data"""
        sample_pipeline_state["variant_conclusions"] = [
            {"variant": "conservative", "conclusion": {"candidates": []}},
            {"variant": "aggressive", "conclusion": {"candidates": []}},
            {"variant": "momentum", "conclusion": {"candidates": []}},
        ]

        with patch("src.agents.nodes.run_agent") as mock_run:
            meta_conclusion = MetaAnalysisConclusion(
                candidates=[],
                high_conviction_count=0,
                consensus_count=0,
                reasoning="Test synthesis"
            )
            mock_run.return_value = (meta_conclusion, AgentTrace(run_id="123", role="MetaAnalysis", mode="screen"))

            node = make_merge_variants_node("meta_analysis", mock_context, lambda x: str(x))
            result = node(sample_pipeline_state)

            assert not result["abort"]
            assert "analysis" in result["conclusions"]


# ============================================================================
# Test: Consensus Rules (via MetaAnalysisConclusion)
# ============================================================================


class TestConsensusRules:
    """Test the consensus synthesis logic"""

    def test_high_conviction_all_three_variants(self):
        """Stock appearing in all 3 variants = high conviction"""
        from src.agents.conclusions import ConsensusStock

        stock = ConsensusStock(
            ticker="AAPL",
            composite_score=72.5,
            variants_agreeing=3,
            high_conviction=True,
            reasons=["Conservative picked", "Aggressive picked", "Momentum picked"]
        )

        assert stock.variants_agreeing == 3
        assert stock.high_conviction is True

    def test_medium_conviction_two_variants(self):
        """Stock appearing in 2/3 variants = medium conviction (not high)"""
        from src.agents.conclusions import ConsensusStock

        stock = ConsensusStock(
            ticker="MSFT",
            composite_score=73.8,
            variants_agreeing=2,
            high_conviction=False,
            reasons=["Aggressive picked", "Momentum picked"]
        )

        assert stock.variants_agreeing == 2
        assert stock.high_conviction is False

    def test_meta_analysis_consensus_count(self):
        """MetaAnalysisConclusion should track consensus counts"""
        from src.agents.conclusions import ConsensusStock

        candidates = [
            ConsensusStock(ticker="A", composite_score=70, variants_agreeing=3, high_conviction=True),
            ConsensusStock(ticker="B", composite_score=72, variants_agreeing=3, high_conviction=True),
            ConsensusStock(ticker="C", composite_score=68, variants_agreeing=2, high_conviction=False),
        ]

        meta = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=2,
            consensus_count=3,
            reasoning="Test synthesis"
        )

        assert meta.high_conviction_count == 2
        assert meta.consensus_count == 3
        assert len(meta.candidates) == 3


# ============================================================================
# Test: Agent Configuration Resolution
# ============================================================================


class TestAgentConfigResolution:
    """Test that agents can be resolved from registry"""

    def test_all_variant_agents_resolvable(self, registry, mock_context):
        """All variant agents should be in registry with correct config"""
        for agent_name in ["analysis_conservative", "analysis_aggressive", "analysis_momentum", "meta_analysis"]:
            agent_cfg = registry.get_agent(agent_name)
            assert agent_cfg is not None
            assert agent_cfg.name == agent_name

    def test_variant_agents_have_prompts(self, registry, mock_context):
        """Variant agents should have resolvable prompts"""
        for agent_name in ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]:
            agent_cfg = registry.get_agent(agent_name)
            prompt = registry.resolve_prompt(agent_name, data_dir=mock_context.settings.data_dir)
            assert prompt is not None
            assert len(prompt) > 0
            assert agent_name.replace("_", " ") in prompt.lower() or "variant" in prompt.lower()

    def test_meta_analysis_prompt_resolvable(self, registry, mock_context):
        """Meta analysis prompt should exist"""
        prompt = registry.resolve_prompt("meta_analysis", data_dir=mock_context.settings.data_dir)
        assert prompt is not None
        assert "consensus" in prompt.lower() or "synthesis" in prompt.lower()

    def test_conclusion_types_resolvable(self, registry):
        """All new agents should have resolvable conclusion types"""
        # Variants should resolve to AnalysisConclusion
        for agent_name in ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]:
            conclusion_type = registry.resolve_conclusion_type(agent_name)
            assert conclusion_type == AnalysisConclusion

        # Meta should resolve to MetaAnalysisConclusion
        conclusion_type = registry.resolve_conclusion_type("meta_analysis")
        assert conclusion_type == MetaAnalysisConclusion
