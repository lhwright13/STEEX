"""Tests for stock ranking."""

import pytest

from src.strategy.ranking import RankedStock, StockRanker
from src.strategy.screener import ScreeningResult


class TestStockRanker:
    """Tests for StockRanker class."""

    def test_init(self, test_settings):
        """Test ranker initialization."""
        ranker = StockRanker(settings=test_settings)
        assert ranker.settings == test_settings

    def test_calculate_composite_score(self, test_settings):
        """Test composite score calculation."""
        ranker = StockRanker(settings=test_settings)

        score = ranker.calculate_composite_score(
            momentum_score=80,
            insider_score=70,
            volume_score=60,
            sentiment_score=50,
            fundamental_score=50,
            options_score=50,
            pysr_score=50,
        )

        expected = (
            test_settings.weight_momentum * 80
            + test_settings.weight_insider * 70
            + test_settings.weight_volume * 60
            + test_settings.weight_sentiment * 50
            + test_settings.weight_fundamental * 50
            + test_settings.weight_options * 50
            + test_settings.weight_pysr * 50
        )
        assert score == pytest.approx(expected)

    def test_rank_stocks(self, test_settings):
        """Test stock ranking."""
        ranker = StockRanker(settings=test_settings)

        results = [
            ScreeningResult(
                ticker="AAPL",
                momentum_6m=0.20,
                momentum_percentile=0.8,
                insider_score=80,
            ),
            ScreeningResult(
                ticker="MSFT",
                momentum_6m=0.15,
                momentum_percentile=0.6,
                insider_score=70,
            ),
            ScreeningResult(
                ticker="GOOGL",
                momentum_6m=0.25,
                momentum_percentile=0.9,
                insider_score=60,
            ),
        ]

        ranked = ranker.rank_stocks(results)

        assert len(ranked) == 3
        # Should be sorted by score descending
        assert ranked[0].rank == 1
        assert ranked[1].rank == 2
        assert ranked[2].rank == 3
        # First should have highest score
        assert ranked[0].composite_score >= ranked[1].composite_score
        assert ranked[1].composite_score >= ranked[2].composite_score

    def test_get_top_picks(self, test_settings):
        """Test getting top N picks."""
        ranker = StockRanker(settings=test_settings)

        results = [
            ScreeningResult(ticker="AAPL", momentum_percentile=0.8, insider_score=80),
            ScreeningResult(ticker="MSFT", momentum_percentile=0.6, insider_score=70),
            ScreeningResult(ticker="GOOGL", momentum_percentile=0.9, insider_score=60),
            ScreeningResult(ticker="AMZN", momentum_percentile=0.7, insider_score=75),
        ]

        picks = ranker.get_top_picks(results, n=2)

        assert len(picks) == 2
        assert picks[0].rank == 1
        assert picks[1].rank == 2

    def test_rank_empty_list(self, test_settings):
        """Test ranking empty list."""
        ranker = StockRanker(settings=test_settings)
        ranked = ranker.rank_stocks([])
        assert ranked == []

    def test_format_pick_summary(self, test_settings):
        """Test formatting pick summary."""
        ranker = StockRanker(settings=test_settings)

        result = ScreeningResult(
            ticker="AAPL",
            momentum_6m=0.20,
            momentum_percentile=0.8,
            insider_score=80,
            insider_buyers=4,
            total_insider_value=2000000,
        )

        ranked = RankedStock(
            ticker="AAPL",
            composite_score=75,
            momentum_score=80,
            insider_score=80,
            volume_score=50,
            sentiment_score=50,
            fundamental_score=50,
            options_score=50,
            pysr_score=50,
            rank=1,
            screening_result=result,
        )

        summary = ranker.format_pick_summary(ranked)

        assert summary["ticker"] == "AAPL"
        assert summary["rank"] == 1
        assert summary["score"] == 75
        assert "20.0%" in summary["momentum_6m"]
        assert summary["insider_buyers"] == 4
        assert len(summary["reasons"]) > 0
