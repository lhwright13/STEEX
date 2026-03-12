"""Tests for the CrossReferencer module."""

import pytest

from src.learning.cross_reference import CrossReferencer


@pytest.fixture
def xref():
    return CrossReferencer()


@pytest.fixture
def sample_recommendations():
    return [
        {
            "agent": "RiskAgent",
            "prompt_suggestions": [
                "Add minimum hold period to avoid whipsaw exits",
                "Consider wider stop-loss for volatile stocks",
            ],
            "tool_suggestions": ["Add a tool for intraday volatility check"],
            "process_suggestions": [],
        },
        {
            "agent": "AnalysisAgent",
            "prompt_suggestions": [
                "Weight insider signal higher for small-cap stocks",
            ],
            "tool_suggestions": [],
            "process_suggestions": ["Run screening twice daily instead of once"],
        },
        {
            "agent": "DataAgent",
            "prompt_suggestions": [
                "Verify API rate limits before batch fetching",
            ],
            "tool_suggestions": [],
            "process_suggestions": [],
        },
    ]


@pytest.fixture
def sample_postmortem():
    return {
        "trades_analyzed": 25,
        "loss_breakdown": {
            "whipsaw": 5,
            "dead_money": 3,
            "missed_upside": 2,
            "early_exit": 1,
        },
        "score_correlation": 0.15,
        "recommendations": ["Consider wider stops"],
    }


@pytest.fixture
def sample_alpha_decay():
    return {
        "signals": [
            {"signal": "momentum_score", "trend": "stable"},
            {"signal": "insider_score", "trend": "degrading"},
            {"signal": "sentiment_score", "trend": "degrading"},
        ],
        "degrading": ["insider_score", "sentiment_score"],
        "watch_list": [],
    }


class TestCategorizeRecommendations:

    def test_empty_recommendations(self, xref):
        result = xref.cross_reference(None, None, [])
        assert result["total_recommendations"] == 0
        assert result["categories"]["param_relevant"] == []
        assert result["categories"]["prompt_relevant"] == []

    def test_param_relevant_detection(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        param_relevant = result["categories"]["param_relevant"]

        # "hold period" -> max_hold_days, "stop-loss" -> initial_stop_pct
        param_names = [r["mapped_param"] for r in param_relevant]
        assert "max_hold_days" in param_names
        assert "initial_stop_pct" in param_names

    def test_prompt_relevant_detection(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        prompt_relevant = result["categories"]["prompt_relevant"]

        # "Check sentiment data freshness" doesn't map to a param
        agents = [r["agent"] for r in prompt_relevant]
        assert "DataAgent" in agents

    def test_tool_suggestions_flagged_for_human(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        tool_relevant = result["categories"]["tool_relevant"]

        assert len(tool_relevant) == 1
        assert tool_relevant[0]["action"] == "flag_for_human"
        assert tool_relevant[0]["agent"] == "RiskAgent"

    def test_process_suggestions_flagged_for_human(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        process_relevant = result["categories"]["process_relevant"]

        assert len(process_relevant) == 1
        assert process_relevant[0]["action"] == "flag_for_human"
        assert process_relevant[0]["agent"] == "AnalysisAgent"

    def test_insider_keyword_maps_to_weight(self, xref):
        recs = [{
            "agent": "TestAgent",
            "prompt_suggestions": ["Increase insider weight for better signal"],
            "tool_suggestions": [],
            "process_suggestions": [],
        }]
        result = xref.cross_reference(None, None, recs)
        param_relevant = result["categories"]["param_relevant"]
        assert len(param_relevant) == 1
        assert param_relevant[0]["mapped_param"] == "weight_insider"


class TestCorrelateWithTrades:

    def test_no_postmortem_no_correlations(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        assert result["correlations"] == []

    def test_whipsaw_correlation(self, xref, sample_postmortem, sample_recommendations):
        result = xref.cross_reference(sample_postmortem, None, sample_recommendations)
        correlations = result["correlations"]

        # RiskAgent's "hold period" suggestion should correlate with whipsaw losses
        whipsaw_corrs = [c for c in correlations if c["loss_type"] == "whipsaw"]
        assert len(whipsaw_corrs) > 0
        assert any("RiskAgent" in c["agent"] for c in whipsaw_corrs)

    def test_alpha_decay_correlation(self, xref, sample_alpha_decay, sample_recommendations):
        result = xref.cross_reference(None, sample_alpha_decay, sample_recommendations)
        correlations = result["correlations"]

        # AnalysisAgent mentions "insider" and insider_score is degrading
        decay_corrs = [c for c in correlations if c["loss_type"] == "alpha_decay"]
        assert len(decay_corrs) > 0

    def test_combined_correlations(
        self, xref, sample_postmortem, sample_alpha_decay, sample_recommendations
    ):
        result = xref.cross_reference(
            sample_postmortem, sample_alpha_decay, sample_recommendations
        )
        assert len(result["correlations"]) > 0

    def test_zero_loss_count_skipped(self, xref, sample_recommendations):
        pm = {
            "trades_analyzed": 10,
            "loss_breakdown": {"whipsaw": 0, "dead_money": 0},
        }
        result = xref.cross_reference(pm, None, sample_recommendations)
        trade_corrs = [c for c in result["correlations"] if c["loss_type"] != "alpha_decay"]
        assert trade_corrs == []


class TestSummary:

    def test_summary_includes_counts(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        summary = result["summary"]
        assert "parameter-relevant" in summary
        assert "prompt-relevant" in summary

    def test_summary_mentions_correlations(
        self, xref, sample_postmortem, sample_recommendations
    ):
        result = xref.cross_reference(sample_postmortem, None, sample_recommendations)
        if result["correlations"]:
            assert "correlation" in result["summary"]

    def test_summary_mentions_human_review(self, xref, sample_recommendations):
        result = xref.cross_reference(None, None, sample_recommendations)
        assert "human review" in result["summary"]
