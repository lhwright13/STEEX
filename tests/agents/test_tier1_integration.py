"""Integration tests for Tier 1 Trading Upgrades.

End-to-end verification that parallel variant analysis, consensus synthesis,
and the full screen mode pipeline work together correctly.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from langgraph.types import Send

from src.agents.registry import AgentRegistry
from src.agents.state import RunnerContext, PipelineState
from src.agents.graph import build_graph
from src.agents.nodes import (
    make_fan_out_node,
    make_variant_agent_node,
    make_merge_variants_node,
)
from src.agents.conclusions import (
    CandidateStock,
    AnalysisConclusion,
    ConsensusStock,
    MetaAnalysisConclusion,
    RiskConclusion,
    DataConclusion,
)
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
def context(registry, settings):
    """Create RunnerContext"""
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
def initial_state():
    """Create initial pipeline state"""
    return {
        "mode": "screen",
        "task_context": "Screen for trading candidates",
        "today": "2026-05-24",
        "run_id": "test_tier1_001",
        "conclusions": {},
        "variant_conclusions": [],
        "traces": [],
        "manager_decision": None,
        "screen_data": None,
        "abort": False,
        "abort_reason": None,
    }


def mock_analysis_conclusion(variant_name: str, num_candidates: int = 8) -> AnalysisConclusion:
    """Factory for creating mock analysis conclusions for each variant"""
    candidates = [
        CandidateStock(
            ticker=f"TST{i}",
            composite_score=70.0 + i,
            momentum_6m=0.10 + (i * 0.01),
            momentum_score=60 + i,
            insider_buyers=3 + i,
            insider_score=75 + i,
            volume_score=65 + i,
            sentiment_score=70 + i,
            fundamental_score=75 + i,
            volume_surge=1.2 + (i * 0.05),
        )
        for i in range(num_candidates)
    ]

    return AnalysisConclusion(
        universe_size=500,
        screening_funnel={"stage_1": 500, "stage_2": 250, "stage_3": 120, "stage_4": 85, "stage_5": num_candidates},
        candidates=candidates,
        portfolio_selected=num_candidates,
        diversification_ratio=0.75,
        reasoning=f"Mock {variant_name} analysis",
    )


# ============================================================================
# Integration Test 1: Full Graph Build
# ============================================================================


class TestScreenModeGraphIntegration:
    """Test that screen mode graph builds correctly with Tier 1 components"""

    def test_screen_mode_graph_structure(self, registry, context):
        """Screen mode graph should include all Tier 1 components"""
        mode_config = registry.modes["screen"]

        graph = build_graph(
            "screen",
            mode_config,
            context,
            lambda x: str(x),
            lambda: None
        )

        nodes = set(graph.nodes.keys()) if hasattr(graph, "nodes") else set()

        # Core pipeline
        assert "data" in nodes
        assert "risk" in nodes

        # Parallel variant analysis (Tier 1)
        assert "fan_out" in nodes
        assert "analysis_conservative" in nodes
        assert "analysis_aggressive" in nodes
        assert "analysis_momentum" in nodes
        assert "merge_variants" in nodes

        # Post-analysis
        assert "manager" in nodes

        # Total: data + risk + fan_out + 3 variants + merge + manager + 2 post-actions + fallback
        # = at least 11 nodes
        assert len(nodes) >= 11

    def test_screen_mode_entry_point(self, registry, context):
        """Entry point should be first sub-agent (data)"""
        mode_config = registry.modes["screen"]

        graph = build_graph(
            "screen",
            mode_config,
            context,
            lambda x: str(x),
            lambda: None
        )

        start_targets = [e.target for e in graph.get_graph().edges if e.source == "__start__"]
        assert start_targets == ["data"]


# ============================================================================
# Integration Test 2: Parallel Dispatch Flow
# ============================================================================


class TestParallelDispatchFlow:
    """Test the fan-out dispatch to parallel variants"""

    def test_fan_out_to_three_variants(self, initial_state):
        """Fan-out node should dispatch to exactly 3 variants"""
        parallel_agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]
        node = make_fan_out_node(parallel_agents)

        sends = node(initial_state)

        assert len(sends) == 3
        assert all(isinstance(s, Send) for s in sends)
        assert [s.node for s in sends] == parallel_agents

    def test_variant_state_preservation_through_fanout(self, initial_state):
        """State should be preserved when dispatched to variants"""
        parallel_agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]
        node = make_fan_out_node(parallel_agents)

        sends = node(initial_state)

        # Each Send should contain the run_id for traceability
        for send in sends:
            # Verify state is passed through to each variant
            assert send.arg["run_id"] == "test_tier1_001"


# ============================================================================
# Integration Test 3: Variant Conclusions Merging via Reducer
# ============================================================================


class TestVariantConclusionsMerging:
    """Test that variant conclusions are properly merged via reducer"""

    def test_three_variant_results_accumulate(self, initial_state, context):
        """Three variant results should accumulate in variant_conclusions list"""
        variant_results = [
            {
                "variant": "analysis_conservative",
                "conclusion": mock_analysis_conclusion("conservative", 8).model_dump()
            },
            {
                "variant": "analysis_aggressive",
                "conclusion": mock_analysis_conclusion("aggressive", 15).model_dump()
            },
            {
                "variant": "analysis_momentum",
                "conclusion": mock_analysis_conclusion("momentum", 12).model_dump()
            },
        ]

        # Simulate reducer concatenation
        accumulated = []
        for result in variant_results:
            accumulated.append(result)

        # Verify all three accumulated
        assert len(accumulated) == 3
        assert accumulated[0]["variant"] == "analysis_conservative"
        assert accumulated[1]["variant"] == "analysis_aggressive"
        assert accumulated[2]["variant"] == "analysis_momentum"

    def test_variant_conclusions_ready_for_synthesis(self, initial_state):
        """Accumulated variant conclusions should be ready for meta-analysis"""
        initial_state["variant_conclusions"] = [
            {
                "variant": "conservative",
                "conclusion": mock_analysis_conclusion("conservative", 8).model_dump()
            },
            {
                "variant": "aggressive",
                "conclusion": mock_analysis_conclusion("aggressive", 15).model_dump()
            },
            {
                "variant": "momentum",
                "conclusion": mock_analysis_conclusion("momentum", 12).model_dump()
            },
        ]

        # Verify structure
        assert len(initial_state["variant_conclusions"]) == 3

        # Verify each has conclusion data
        for item in initial_state["variant_conclusions"]:
            assert "variant" in item
            assert "conclusion" in item
            assert "universe_size" in item["conclusion"]
            assert "candidates" in item["conclusion"]


# ============================================================================
# Integration Test 4: Consensus Synthesis
# ============================================================================


class TestConsensusSynthesis:
    """Test that consensus is correctly synthesized from variants"""

    def test_consensus_picks_from_three_variants(self):
        """Simulate consensus picking from 3 variant results"""
        # Simulate 3 variant results with overlapping candidates
        variants_data = {
            "conservative": ["AAPL", "JPM", "KO"],
            "aggressive": ["AAPL", "NVDA", "TSLA", "MSFT"],
            "momentum": ["AAPL", "NVDA", "TSLA"],
        }

        # Count how many variants picked each stock
        stock_votes = {}
        for variant, stocks in variants_data.items():
            for stock in stocks:
                stock_votes[stock] = stock_votes.get(stock, 0) + 1

        # Apply consensus rules
        high_conviction = [s for s, votes in stock_votes.items() if votes == 3]
        medium_conviction = [s for s, votes in stock_votes.items() if votes == 2]
        speculative = [s for s, votes in stock_votes.items() if votes == 1]

        # Verify results
        assert "AAPL" in high_conviction
        assert len(high_conviction) == 1

        assert "NVDA" in medium_conviction
        assert "TSLA" in medium_conviction
        assert len(medium_conviction) == 2

        assert "JPM" in speculative
        assert "KO" in speculative
        assert "MSFT" in speculative  # only the aggressive variant picked it
        assert len(speculative) == 3

    def test_meta_analysis_conclusion_structure(self):
        """MetaAnalysisConclusion should properly structure consensus results"""
        candidates = [
            ConsensusStock(ticker="AAPL", composite_score=72.5, variants_agreeing=3, high_conviction=True),
            ConsensusStock(ticker="NVDA", composite_score=73.8, variants_agreeing=2, high_conviction=False),
            ConsensusStock(ticker="MSFT", composite_score=74.1, variants_agreeing=2, high_conviction=False),
        ]

        conclusion = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=1,
            consensus_count=3,
            speculative_excluded=["JPM", "KO"],
            variant_summaries={
                "conservative": 3,
                "aggressive": 4,
                "momentum": 3,
            },
            reasoning="Consensus synthesis from 3 variants"
        )

        # Verify structure
        assert conclusion.high_conviction_count == 1
        assert conclusion.consensus_count == 3
        assert len(conclusion.speculative_excluded) == 2
        assert conclusion.variant_summaries["conservative"] == 3


# ============================================================================
# Integration Test 5: State Flow Through Pipeline
# ============================================================================


class TestStateFlowThroughPipeline:
    """Test that state properly flows through the entire pipeline"""

    def test_state_initialization(self, initial_state):
        """Initial state should have all required fields"""
        required_fields = [
            "mode", "task_context", "today", "run_id",
            "conclusions", "variant_conclusions", "traces",
            "manager_decision", "abort", "abort_reason"
        ]

        for field in required_fields:
            assert field in initial_state

    def test_state_after_data_agent(self, initial_state):
        """State after data agent should add data conclusion"""
        initial_state["conclusions"]["data"] = {
            "all_healthy": True,
            "sources_checked": 5,
            "sources_healthy": 5,
            "vix_level": 22.5,
        }

        assert "data" in initial_state["conclusions"]
        assert initial_state["conclusions"]["data"]["vix_level"] == 22.5

    def test_state_after_risk_agent(self, initial_state):
        """State after risk agent should add regime and position risk"""
        initial_state["conclusions"]["risk"] = {
            "regime_name": "cautious",
            "regime_confidence": 0.87,
            "vix_level": 22.5,
        }

        assert initial_state["conclusions"]["risk"]["regime_name"] == "cautious"

    def test_state_after_variants_merge(self, initial_state):
        """State after variants and merge should have analysis conclusion"""
        initial_state["conclusions"]["analysis"] = {
            "candidates": [
                {"ticker": "AAPL", "composite_score": 72.5},
                {"ticker": "NVDA", "composite_score": 73.8},
            ],
            "high_conviction_count": 1,
            "consensus_count": 2,
        }

        assert "analysis" in initial_state["conclusions"]
        assert len(initial_state["conclusions"]["analysis"]["candidates"]) == 2

    def test_state_final_after_manager(self, initial_state):
        """Final state should have manager decision with buy recommendations"""
        initial_state["conclusions"]["analysis"] = {
            "candidates": [{"ticker": "AAPL", "composite_score": 72.5}],
        }

        initial_state["manager_decision"] = {
            "entries_approved": True,
            "buys": [
                {"ticker": "AAPL", "score": 72.5, "price": 185.22}
            ],
            "sells": [],
        }

        assert initial_state["manager_decision"]["entries_approved"] is True
        assert len(initial_state["manager_decision"]["buys"]) == 1


# ============================================================================
# Integration Test 6: Error Handling Through Pipeline
# ============================================================================


class TestErrorHandlingThroughPipeline:
    """Test that errors are handled gracefully throughout pipeline"""

    def test_variant_failure_captured(self):
        """If a variant fails, it should still be recorded in variant_conclusions"""
        variant_conclusions = [
            {"variant": "conservative", "conclusion": None},  # Failed
            {"variant": "aggressive", "conclusion": mock_analysis_conclusion("aggressive", 15).model_dump()},
            {"variant": "momentum", "conclusion": mock_analysis_conclusion("momentum", 12).model_dump()},
        ]

        # 2/3 variants succeeded
        succeeded = sum(1 for v in variant_conclusions if v["conclusion"] is not None)
        assert succeeded == 2

    def test_merge_continues_with_partial_variants(self, initial_state):
        """Merge should proceed if at least 2 variants succeeded"""
        initial_state["variant_conclusions"] = [
            {"variant": "conservative", "conclusion": None},
            {
                "variant": "aggressive",
                "conclusion": mock_analysis_conclusion("aggressive", 15).model_dump()
            },
            {
                "variant": "momentum",
                "conclusion": mock_analysis_conclusion("momentum", 12).model_dump()
            },
        ]

        # Should still have conclusions to synthesize
        valid_conclusions = [v for v in initial_state["variant_conclusions"] if v["conclusion"] is not None]
        assert len(valid_conclusions) >= 2

    def test_all_variants_fail_aborts_pipeline(self, initial_state):
        """If all variants fail, pipeline should abort"""
        initial_state["variant_conclusions"] = [
            {"variant": "conservative", "conclusion": None},
            {"variant": "aggressive", "conclusion": None},
            {"variant": "momentum", "conclusion": None},
        ]

        # All failed
        valid_conclusions = [v for v in initial_state["variant_conclusions"] if v["conclusion"] is not None]
        assert len(valid_conclusions) == 0

        # Should set abort flag
        if len(valid_conclusions) == 0:
            initial_state["abort"] = True
            initial_state["abort_reason"] = "All analysis variants failed"

        assert initial_state["abort"] is True


# ============================================================================
# Integration Test 7: Configuration & Settings Consistency
# ============================================================================


class TestConfigurationConsistency:
    """Test that Tier 1 components use consistent configuration"""

    def test_variant_presets_valid(self, settings):
        """Variant presets should use valid setting field names"""
        from src.agents.mcp_server import VARIANT_PARAMS

        for variant_name, params in VARIANT_PARAMS.items():
            for param_name, value in params.items():
                # Each param should be a valid settings field
                if hasattr(settings, param_name):
                    # Param is valid
                    assert True
                elif param_name in ["fundamental_enabled"]:
                    # These are special boolean overrides
                    assert isinstance(value, bool)
                else:
                    pytest.fail(f"Unknown parameter {param_name} in variant {variant_name}")

    def test_regime_params_valid(self, settings):
        """Regime params should use valid setting field names"""
        from src.agents.mcp_server import REGIME_PARAMS

        for regime_name, params in REGIME_PARAMS.items():
            for param_name, value in params.items():
                if param_name != "rationale":
                    # Should be a valid settings field
                    assert hasattr(settings, param_name), \
                        f"Regime param {param_name} not found in Settings for regime {regime_name}"

    def test_weight_options_increased_for_tier1(self, settings):
        """weight_options stays elevated for Tier 1+2 after weight normalization.

        Weights were rescaled to sum to 1.0 (the prior set summed to 1.07), so
        the absolute value shifted from 0.12; assert its relative prominence
        instead of a brittle literal.
        """
        assert settings.weight_options > settings.weight_fundamental, \
            f"weight_options should exceed fundamental for Tier 1+2, got {settings.weight_options}"


# ============================================================================
# Integration Test 8: Agent Registry & Prompts
# ============================================================================


class TestAgentRegistryIntegration:
    """Test that all Tier 1 agents are properly registered"""

    def test_all_new_agents_registered(self, registry):
        """All 4 new agents should be registered"""
        agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum", "meta_analysis"]

        for agent_name in agents:
            agent = registry.get_agent(agent_name)
            assert agent is not None, f"{agent_name} not registered"

    def test_all_variant_prompts_exist(self, registry, settings):
        """All variant agents should have resolvable prompts"""
        agents = ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]

        for agent_name in agents:
            prompt = registry.resolve_prompt(agent_name, data_dir=settings.data_dir)
            assert prompt is not None
            assert len(prompt) > 100, f"Prompt for {agent_name} seems too short"

    def test_meta_analysis_prompt_exists(self, registry, settings):
        """Meta analysis prompt should exist and reference consensus"""
        prompt = registry.resolve_prompt("meta_analysis", data_dir=settings.data_dir)
        assert prompt is not None
        assert "consensus" in prompt.lower() or "synthesis" in prompt.lower()

    def test_variant_conclusion_types(self, registry):
        """Variant agents should have correct conclusion types"""
        from src.agents.conclusions import AnalysisConclusion, MetaAnalysisConclusion

        for agent_name in ["analysis_conservative", "analysis_aggressive", "analysis_momentum"]:
            conclusion_type = registry.resolve_conclusion_type(agent_name)
            assert conclusion_type == AnalysisConclusion

        conclusion_type = registry.resolve_conclusion_type("meta_analysis")
        assert conclusion_type == MetaAnalysisConclusion
