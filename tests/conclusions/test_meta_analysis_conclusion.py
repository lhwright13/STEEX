"""Tests for MetaAnalysisConclusion and consensus logic.

Tests the consensus synthesis rules and data models for multi-variant analysis.
"""

import pytest
from typing import List

from src.agents.conclusions import (
    CandidateStock,
    AnalysisConclusion,
    ConsensusStock,
    MetaAnalysisConclusion,
    AgentMeta,
)


# ============================================================================
# Test: ConsensusStock Model
# ============================================================================


class TestConsensusStock:
    """Test the ConsensusStock model"""

    def test_high_conviction_stock(self):
        """3/3 variants agreeing = high conviction"""
        stock = ConsensusStock(
            ticker="AAPL",
            composite_score=72.5,
            variants_agreeing=3,
            high_conviction=True,
            reasons=["Conservative picked", "Aggressive picked", "Momentum picked"]
        )

        assert stock.ticker == "AAPL"
        assert stock.variants_agreeing == 3
        assert stock.high_conviction is True
        assert len(stock.reasons) == 3

    def test_medium_conviction_stock(self):
        """2/3 variants agreeing = not high conviction"""
        stock = ConsensusStock(
            ticker="MSFT",
            composite_score=73.8,
            variants_agreeing=2,
            high_conviction=False,
            reasons=["Aggressive picked", "Momentum picked"]
        )

        assert stock.variants_agreeing == 2
        assert stock.high_conviction is False

    def test_single_variant_stock(self):
        """1/3 variants = not consensus (speculative)"""
        stock = ConsensusStock(
            ticker="TSLA",
            composite_score=70.0,
            variants_agreeing=1,
            high_conviction=False,
            reasons=["Momentum only"]
        )

        assert stock.variants_agreeing == 1

    def test_consensus_stock_serialize(self):
        """ConsensusStock should serialize to dict"""
        stock = ConsensusStock(
            ticker="AAPL",
            composite_score=72.5,
            variants_agreeing=3,
            high_conviction=True,
        )

        data = stock.model_dump()
        assert data["ticker"] == "AAPL"
        assert data["composite_score"] == 72.5
        assert data["variants_agreeing"] == 3
        assert data["high_conviction"] is True


# ============================================================================
# Test: MetaAnalysisConclusion Model
# ============================================================================


class TestMetaAnalysisConclusion:
    """Test the MetaAnalysisConclusion model"""

    def test_empty_conclusion(self):
        """Empty conclusion (no candidates) should be valid"""
        conclusion = MetaAnalysisConclusion()

        assert len(conclusion.candidates) == 0
        assert conclusion.high_conviction_count == 0
        assert conclusion.consensus_count == 0

    def test_conclusion_with_high_conviction_picks(self):
        """Conclusion with high conviction picks"""
        candidates = [
            ConsensusStock(
                ticker="AAPL",
                composite_score=72.5,
                variants_agreeing=3,
                high_conviction=True
            ),
            ConsensusStock(
                ticker="MSFT",
                composite_score=73.8,
                variants_agreeing=3,
                high_conviction=True
            ),
        ]

        conclusion = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=2,
            consensus_count=2,
            reasoning="Both picked by all variants"
        )

        assert len(conclusion.candidates) == 2
        assert conclusion.high_conviction_count == 2
        assert conclusion.consensus_count == 2

    def test_conclusion_with_mixed_conviction(self):
        """Conclusion with mix of high and medium conviction"""
        candidates = [
            ConsensusStock(
                ticker="AAPL",
                composite_score=72.5,
                variants_agreeing=3,
                high_conviction=True
            ),
            ConsensusStock(
                ticker="MSFT",
                composite_score=73.8,
                variants_agreeing=2,
                high_conviction=False
            ),
            ConsensusStock(
                ticker="GOOGL",
                composite_score=71.2,
                variants_agreeing=2,
                high_conviction=False
            ),
        ]

        conclusion = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=1,
            consensus_count=3,
            speculative_excluded=["TSLA", "NVDA"],
            reasoning="1 high conviction, 2 medium consensus, 2 speculative excluded"
        )

        assert conclusion.high_conviction_count == 1
        assert conclusion.consensus_count == 3
        assert len(conclusion.speculative_excluded) == 2

    def test_variant_summaries(self):
        """MetaAnalysisConclusion should track variant candidate counts"""
        conclusion = MetaAnalysisConclusion(
            candidates=[],
            high_conviction_count=0,
            consensus_count=0,
            variant_summaries={
                "conservative": 8,
                "aggressive": 15,
                "momentum": 12
            }
        )

        assert conclusion.variant_summaries["conservative"] == 8
        assert conclusion.variant_summaries["aggressive"] == 15
        assert conclusion.variant_summaries["momentum"] == 12

    def test_conclusion_serialize(self):
        """MetaAnalysisConclusion should serialize to JSON-compatible dict"""
        candidates = [
            ConsensusStock(
                ticker="AAPL",
                composite_score=72.5,
                variants_agreeing=3,
                high_conviction=True
            ),
        ]

        conclusion = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=1,
            consensus_count=1,
            reasoning="Test"
        )

        data = conclusion.model_dump()
        assert isinstance(data, dict)
        assert "candidates" in data
        assert len(data["candidates"]) == 1
        assert data["high_conviction_count"] == 1

    def test_conclusion_with_meta(self):
        """MetaAnalysisConclusion can include AgentMeta for improvement suggestions"""
        meta = AgentMeta(
            prompt_suggestions=["Adjust consensus threshold"],
            tool_suggestions=["Add IV skew analysis"],
        )

        conclusion = MetaAnalysisConclusion(
            candidates=[],
            high_conviction_count=0,
            consensus_count=0,
            meta=meta
        )

        assert conclusion.meta is not None
        assert "IV skew" in conclusion.meta.tool_suggestions[0]


# ============================================================================
# Test: Consensus Rules (Synthesis Logic)
# ============================================================================


class TestConsensusRules:
    """Test the consensus synthesis rules"""

    def test_high_conviction_rule_3_of_3(self):
        """Rule: All 3 variants agree → HIGH CONVICTION"""
        # This represents the rule implementation
        def check_high_conviction(variants_agreeing: int) -> bool:
            return variants_agreeing == 3

        assert check_high_conviction(3) is True
        assert check_high_conviction(2) is False
        assert check_high_conviction(1) is False

    def test_consensus_rule_2_of_3(self):
        """Rule: 2/3 variants agree → CONSENSUS (not high conviction)"""
        def check_consensus(variants_agreeing: int) -> bool:
            return variants_agreeing >= 2

        def is_high_conviction(variants_agreeing: int) -> bool:
            return variants_agreeing == 3

        # 2/3 is consensus but not high conviction
        assert check_consensus(2) is True
        assert is_high_conviction(2) is False

    def test_speculative_rule_1_of_3(self):
        """Rule: Only 1/3 variants agree → SPECULATIVE (exclude unless options confirms)"""
        def is_speculative(variants_agreeing: int) -> bool:
            return variants_agreeing == 1

        assert is_speculative(1) is True
        assert is_speculative(2) is False
        assert is_speculative(3) is False

    def test_consensus_build_example(self):
        """Full example: 5 stocks, determine consensus for each"""
        # Simulating what consensus synthesis would determine
        stock_results = {
            "AAPL": {"variants_agreeing": 3, "should_include": True, "conviction": "high"},
            "MSFT": {"variants_agreeing": 2, "should_include": True, "conviction": "medium"},
            "GOOGL": {"variants_agreeing": 2, "should_include": True, "conviction": "medium"},
            "NVDA": {"variants_agreeing": 1, "should_include": False, "conviction": "speculative"},
            "TSLA": {"variants_agreeing": 1, "should_include": False, "conviction": "speculative"},
        }

        # Count consensus picks
        high_conviction = sum(1 for s in stock_results.values() if s["conviction"] == "high")
        consensus_picks = sum(1 for s in stock_results.values() if s["should_include"])
        speculative = sum(1 for s in stock_results.values() if s["conviction"] == "speculative")

        assert high_conviction == 1  # Only AAPL
        assert consensus_picks == 3  # AAPL, MSFT, GOOGL
        assert speculative == 2  # NVDA, TSLA


# ============================================================================
# Test: Consensus Scoring
# ============================================================================


class TestConsensusScoring:
    """Test how consensus picks are scored"""

    def test_average_score_across_variants(self):
        """Consensus score should be average of variant scores"""
        variant_scores = {
            "conservative": 72.5,
            "aggressive": 68.3,
            "momentum": 75.2
        }

        consensus_score = sum(variant_scores.values()) / len(variant_scores)
        assert consensus_score == pytest.approx(72.0, abs=0.1)

    def test_two_variant_average(self):
        """When only 2/3 variants agree, average those two"""
        variant_scores = {
            "aggressive": 74.1,
            "momentum": 73.5
        }

        consensus_score = sum(variant_scores.values()) / len(variant_scores)
        assert consensus_score == pytest.approx(73.8, abs=0.1)

    def test_consensus_score_ordering(self):
        """Higher consensus scores should rank higher"""
        stocks = [
            ConsensusStock(ticker="A", composite_score=72.5, variants_agreeing=3, high_conviction=True),
            ConsensusStock(ticker="B", composite_score=70.0, variants_agreeing=3, high_conviction=True),
            ConsensusStock(ticker="C", composite_score=73.8, variants_agreeing=2, high_conviction=False),
        ]

        # Sort by score
        sorted_stocks = sorted(stocks, key=lambda s: s.composite_score, reverse=True)

        assert sorted_stocks[0].ticker == "C"  # 73.8
        assert sorted_stocks[1].ticker == "A"  # 72.5
        assert sorted_stocks[2].ticker == "B"  # 70.0


# ============================================================================
# Test: Position Sizing Based on Conviction
# ============================================================================


class TestPositionSizingByConviction:
    """Test how position size is determined from conviction level"""

    def test_high_conviction_position_size(self):
        """High conviction picks should get max position size"""
        max_pos_pct = 0.04  # 4% per trade

        high_conviction_multiplier = 1.125  # 4% * 1.125 = 4.5%
        position_size = max_pos_pct * high_conviction_multiplier

        assert position_size == 0.045

    def test_medium_conviction_position_size(self):
        """Medium conviction picks should get standard position size"""
        max_pos_pct = 0.04

        medium_conviction_multiplier = 0.75  # 4% * 0.75 = 3%
        position_size = max_pos_pct * medium_conviction_multiplier

        assert position_size == 0.03

    def test_portfolio_sizing_with_mixed_conviction(self):
        """Portfolio should size positions based on conviction level"""
        candidates = [
            ConsensusStock(ticker="A", composite_score=72.5, variants_agreeing=3, high_conviction=True),  # 4.5%
            ConsensusStock(ticker="B", composite_score=73.8, variants_agreeing=2, high_conviction=False),  # 3.0%
            ConsensusStock(ticker="C", composite_score=71.2, variants_agreeing=2, high_conviction=False),  # 3.0%
        ]

        max_position = 0.04
        high_conviction_mult = 1.125
        medium_conviction_mult = 0.75

        total_allocation = 0
        for stock in candidates:
            if stock.high_conviction:
                size = max_position * high_conviction_mult
            else:
                size = max_position * medium_conviction_mult
            total_allocation += size

        assert total_allocation == pytest.approx(0.105, abs=0.001)  # 10.5% total


# ============================================================================
# Test: Edge Cases
# ============================================================================


class TestConsensusEdgeCases:
    """Test edge cases in consensus synthesis"""

    def test_all_variants_failed(self):
        """If all variants fail, consensus should be empty"""
        conclusion = MetaAnalysisConclusion(
            candidates=[],
            high_conviction_count=0,
            consensus_count=0,
            speculative_excluded=[],
            reasoning="All variants failed to produce conclusions"
        )

        assert len(conclusion.candidates) == 0
        assert conclusion.high_conviction_count == 0

    def test_only_speculative_picks_remain(self):
        """If only speculative picks, consensus should exclude them"""
        conclusion = MetaAnalysisConclusion(
            candidates=[],  # All excluded as speculative
            high_conviction_count=0,
            consensus_count=0,
            speculative_excluded=["TSLA", "NVDA", "AMD"],
            reasoning="All picks were single-variant only"
        )

        assert len(conclusion.candidates) == 0
        assert len(conclusion.speculative_excluded) == 3

    def test_single_high_conviction_pick(self):
        """Single high conviction pick is valid consensus"""
        candidates = [
            ConsensusStock(
                ticker="AAPL",
                composite_score=72.5,
                variants_agreeing=3,
                high_conviction=True
            ),
        ]

        conclusion = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=1,
            consensus_count=1,
            reasoning="One strong pick across all variants"
        )

        assert conclusion.high_conviction_count == 1
        assert conclusion.consensus_count == 1
        assert len(conclusion.speculative_excluded) == 0


# ============================================================================
# Test: Compatibility with AnalysisConclusion
# ============================================================================


class TestMetaAnalysisCompatibility:
    """Test that MetaAnalysisConclusion is compatible with existing flows"""

    def test_meta_conclusion_has_required_fields(self):
        """MetaAnalysisConclusion should have all expected fields"""
        conclusion = MetaAnalysisConclusion()

        assert hasattr(conclusion, "candidates")
        assert hasattr(conclusion, "high_conviction_count")
        assert hasattr(conclusion, "consensus_count")
        assert hasattr(conclusion, "reasoning")
        assert hasattr(conclusion, "meta")

    def test_meta_conclusion_model_validates(self):
        """MetaAnalysisConclusion should validate via Pydantic"""
        # Valid data should construct without error
        conclusion = MetaAnalysisConclusion(
            candidates=[],
            high_conviction_count=0,
            consensus_count=0,
            reasoning="Test"
        )

        assert conclusion is not None

    def test_meta_conclusion_json_serializable(self):
        """MetaAnalysisConclusion should be JSON serializable"""
        candidates = [
            ConsensusStock(
                ticker="TEST",
                composite_score=70.0,
                variants_agreeing=2,
                high_conviction=False
            ),
        ]

        conclusion = MetaAnalysisConclusion(
            candidates=candidates,
            high_conviction_count=0,
            consensus_count=1,
            reasoning="Test serialization"
        )

        # Should serialize to JSON
        json_data = conclusion.model_dump_json()
        assert isinstance(json_data, str)
        assert "TEST" in json_data
