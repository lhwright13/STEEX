"""Tests for LearningConclusion and LearningManagerDecision models."""

import json

import pytest

from src.agents.conclusions import (
    AgentMeta,
    LearningConclusion,
    LearningManagerDecision,
    PromptEvolutionRecommendation,
)


class TestPromptEvolutionRecommendation:

    def test_basic_construction(self):
        rec = PromptEvolutionRecommendation(
            agent_name="risk",
            suggestion="Add minimum hold period check",
            rationale="5 whipsaw losses in last 30 trades",
        )
        assert rec.agent_name == "risk"
        assert rec.priority == "medium"

    def test_all_priorities(self):
        for priority in ["high", "medium", "low"]:
            rec = PromptEvolutionRecommendation(
                agent_name="data",
                suggestion="test",
                rationale="test rationale",
                priority=priority,
            )
            assert rec.priority == priority

    def test_json_roundtrip(self):
        rec = PromptEvolutionRecommendation(
            agent_name="analysis",
            suggestion="Weight insider higher",
            rationale="Insider signal IC = 0.12, above threshold",
            priority="high",
        )
        data = json.loads(rec.model_dump_json())
        rec2 = PromptEvolutionRecommendation.model_validate(data)
        assert rec2.agent_name == rec.agent_name
        assert rec2.suggestion == rec.suggestion


class TestLearningConclusion:

    def test_minimal_construction(self):
        lc = LearningConclusion(reasoning="No trades to analyze")
        assert lc.trades_analyzed == 0
        assert lc.win_rate is None
        assert lc.config_changes_applied is False
        assert lc.prompt_evolution_recommendations == []
        assert lc.gaps_flagged == []

    def test_full_construction(self):
        lc = LearningConclusion(
            trades_analyzed=25,
            win_rate=0.56,
            signals_degrading=["momentum_score"],
            weight_changes_proposed={"weight_momentum": 0.25},
            config_changes_applied=True,
            prompt_evolution_recommendations=[
                PromptEvolutionRecommendation(
                    agent_name="risk",
                    suggestion="Check whipsaw pattern",
                    rationale="5 whipsaw losses",
                    priority="high",
                ),
            ],
            agent_insights_used=["RiskAgent: minimum hold period suggestion"],
            gaps_flagged=["Insufficient options data"],
            gaps_resolved=["Previous sentiment data gap resolved"],
            reasoning="Full analysis complete",
        )
        assert lc.trades_analyzed == 25
        assert lc.win_rate == 0.56
        assert len(lc.prompt_evolution_recommendations) == 1
        assert lc.prompt_evolution_recommendations[0].agent_name == "risk"

    def test_json_roundtrip(self):
        lc = LearningConclusion(
            trades_analyzed=10,
            signals_degrading=["insider_score"],
            reasoning="Test roundtrip",
            meta=AgentMeta(
                prompt_suggestions=["Add more context"],
            ),
        )
        data = json.loads(lc.model_dump_json())
        lc2 = LearningConclusion.model_validate(data)
        assert lc2.trades_analyzed == 10
        assert lc2.signals_degrading == ["insider_score"]
        assert lc2.meta.prompt_suggestions == ["Add more context"]

    def test_parse_from_agent_output(self):
        """Simulate parsing an agent's JSON output."""
        agent_output = json.dumps({
            "trades_analyzed": 0,
            "win_rate": None,
            "signals_degrading": [],
            "weight_changes_proposed": {},
            "config_changes_applied": False,
            "prompt_evolution_recommendations": [],
            "agent_insights_used": [],
            "gaps_flagged": ["Only 0 trades available (need 15)"],
            "gaps_resolved": [],
            "reasoning": "Insufficient trade data for analysis",
        })
        lc = LearningConclusion.model_validate(json.loads(agent_output))
        assert lc.trades_analyzed == 0
        assert len(lc.gaps_flagged) == 1


class TestLearningManagerDecision:

    def test_minimal_construction(self):
        d = LearningManagerDecision(reasoning="All rejected")
        assert d.config_changes_approved is False
        assert d.prompt_evolutions_approved == []
        assert d.prompt_evolutions_rejected == []
        assert d.escalations == []

    def test_approval_with_escalations(self):
        d = LearningManagerDecision(
            config_changes_approved=True,
            prompt_evolutions_approved=["risk", "analysis"],
            prompt_evolutions_rejected=["data"],
            escalations=["Tool suggestion needs human review"],
            reasoning="Config changes OOS validated. Risk and analysis prompts backed by data.",
        )
        assert d.config_changes_approved is True
        assert "risk" in d.prompt_evolutions_approved
        assert "data" in d.prompt_evolutions_rejected
        assert len(d.escalations) == 1

    def test_json_roundtrip(self):
        d = LearningManagerDecision(
            config_changes_approved=True,
            prompt_evolutions_approved=["risk"],
            reasoning="Approved after OOS validation",
            meta=AgentMeta(process_suggestions=["Review more frequently"]),
        )
        data = json.loads(d.model_dump_json())
        d2 = LearningManagerDecision.model_validate(data)
        assert d2.config_changes_approved is True
        assert d2.prompt_evolutions_approved == ["risk"]
        assert d2.meta.process_suggestions == ["Review more frequently"]

    def test_parse_from_agent_output(self):
        """Simulate parsing a manager agent's JSON output."""
        agent_output = json.dumps({
            "config_changes_approved": False,
            "prompt_evolutions_approved": [],
            "prompt_evolutions_rejected": ["risk"],
            "escalations": ["Insufficient data for reliable decisions"],
            "reasoning": "Not enough trades to validate changes",
        })
        d = LearningManagerDecision.model_validate(json.loads(agent_output))
        assert d.config_changes_approved is False
        assert "risk" in d.prompt_evolutions_rejected
