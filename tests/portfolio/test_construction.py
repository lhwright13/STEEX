"""Tests for portfolio construction constraints (E4).

Tests correlation filtering, sector cap enforcement, and risk-parity weighting.
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from src.portfolio.construction import PortfolioConstructor, PortfolioCandidate
from src.strategy.ranking import RankedStock


def _make_ranked(ticker: str, score: float = 80.0) -> RankedStock:
    """Helper to build a RankedStock with minimal fields."""
    return RankedStock(
        ticker=ticker,
        composite_score=score,
        momentum_score=50.0,
        insider_score=50.0,
        volume_score=50.0,
        sentiment_score=50.0,
        fundamental_score=50.0,
        options_score=50.0,
        pysr_score=0.0,
        rank=1,
        screening_result=MagicMock(),
    )


def _build_corr_matrix(tickers, correlations):
    """Build a correlation DataFrame from a dict of (t1,t2)->corr values.

    Diagonal is 1.0; missing pairs default to 0.0.
    """
    n = len(tickers)
    mat = np.eye(n)
    idx = {t: i for i, t in enumerate(tickers)}
    for (t1, t2), val in correlations.items():
        mat[idx[t1], idx[t2]] = val
        mat[idx[t2], idx[t1]] = val
    return pd.DataFrame(mat, index=tickers, columns=tickers)


class TestMaxPairwiseCorrelationFilter:
    """The constructor should reject candidates that exceed max pairwise correlation."""

    def test_high_correlation_rejected(self, test_settings, mock_price_provider):
        """A candidate highly correlated with an already-selected stock is rejected."""
        test_settings.portfolio_max_pairwise_corr = 0.70
        test_settings.portfolio_risk_parity_enabled = False

        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        ranked = [_make_ranked("AAPL", 90), _make_ranked("MSFT", 85)]

        # Correlation 0.95 between AAPL and MSFT  -> MSFT should be rejected
        fake_corr = _build_corr_matrix(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.95})
        constructor.compute_correlation_matrix = MagicMock(return_value=fake_corr)
        constructor.compute_volatilities = MagicMock(return_value={"AAPL": 0.25, "MSFT": 0.28})

        proposal = constructor.select_portfolio(ranked, max_picks=5, max_correlation=0.70)

        selected_tickers = [c.ranked_stock.ticker for c in proposal.selected]
        rejected_tickers = [r[0].ticker for r in proposal.rejected]

        assert "AAPL" in selected_tickers
        assert "MSFT" in rejected_tickers

    def test_low_correlation_accepted(self, test_settings, mock_price_provider):
        """A candidate with low correlation to selected stocks is accepted."""
        test_settings.portfolio_max_pairwise_corr = 0.70
        test_settings.portfolio_risk_parity_enabled = False

        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        ranked = [_make_ranked("AAPL", 90), _make_ranked("XOM", 85)]

        fake_corr = _build_corr_matrix(["AAPL", "XOM"], {("AAPL", "XOM"): 0.20})
        constructor.compute_correlation_matrix = MagicMock(return_value=fake_corr)
        constructor.compute_volatilities = MagicMock(return_value={"AAPL": 0.25, "XOM": 0.30})

        proposal = constructor.select_portfolio(ranked, max_picks=5, max_correlation=0.70)

        selected_tickers = [c.ranked_stock.ticker for c in proposal.selected]
        assert "AAPL" in selected_tickers
        assert "XOM" in selected_tickers

    def test_empty_corr_matrix_accepts_all(self, test_settings, mock_price_provider):
        """When correlation matrix is empty, candidates are still accepted."""
        test_settings.portfolio_risk_parity_enabled = False

        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )
        ranked = [_make_ranked("AAPL", 90), _make_ranked("MSFT", 85)]

        constructor.compute_correlation_matrix = MagicMock(return_value=pd.DataFrame())
        constructor.compute_volatilities = MagicMock(return_value={})

        proposal = constructor.select_portfolio(ranked, max_picks=5)
        selected_tickers = [c.ranked_stock.ticker for c in proposal.selected]
        assert len(selected_tickers) == 2


class TestSectorCapEnforcement:
    """Sector concentration should be limited (max_positions // 3, min 2)."""

    @patch("src.portfolio.construction.get_ticker_sector")
    def test_sector_cap_blocks_excess(self, mock_sector, test_settings, mock_price_provider):
        """When a sector already has max allowed stocks, the next one is rejected."""
        test_settings.max_positions = 6  # max_per_sector = max(2, 6//3) = 2
        test_settings.portfolio_risk_parity_enabled = False

        mock_sector.side_effect = lambda t: "Technology"

        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        ranked = [
            _make_ranked("AAPL", 95),
            _make_ranked("MSFT", 90),
            _make_ranked("GOOG", 85),  # 3rd Tech -> should be rejected
        ]

        constructor.compute_correlation_matrix = MagicMock(return_value=pd.DataFrame())
        constructor.compute_volatilities = MagicMock(return_value={})

        proposal = constructor.select_portfolio(ranked, max_picks=5)

        selected_tickers = [c.ranked_stock.ticker for c in proposal.selected]
        rejected_tickers = [r[0].ticker for r in proposal.rejected]

        assert "AAPL" in selected_tickers
        assert "MSFT" in selected_tickers
        assert "GOOG" in rejected_tickers

    @patch("src.portfolio.construction.get_ticker_sector")
    def test_different_sectors_all_accepted(self, mock_sector, test_settings, mock_price_provider):
        """Candidates in different sectors should all pass sector cap."""
        test_settings.max_positions = 6
        test_settings.portfolio_risk_parity_enabled = False

        sectors = {"AAPL": "Technology", "XOM": "Energy", "JNJ": "Healthcare"}
        mock_sector.side_effect = lambda t: sectors.get(t, "Unknown")

        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        ranked = [_make_ranked("AAPL", 95), _make_ranked("XOM", 90), _make_ranked("JNJ", 85)]

        constructor.compute_correlation_matrix = MagicMock(return_value=pd.DataFrame())
        constructor.compute_volatilities = MagicMock(return_value={})

        proposal = constructor.select_portfolio(ranked, max_picks=5)
        assert len(proposal.selected) == 3


class TestRiskParityWeighting:
    """Risk-parity weights should be inverse-variance and sum to 1.0."""

    def test_weights_sum_to_one(self, test_settings, mock_price_provider):
        """Risk-parity weights must always sum to 1.0."""
        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        vols = {"AAPL": 0.20, "XOM": 0.30, "JNJ": 0.15}
        weights = constructor.risk_parity_weights(list(vols.keys()), vols)

        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_lower_vol_gets_higher_weight(self, test_settings, mock_price_provider):
        """Lower volatility stocks should receive higher risk-parity weights."""
        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        vols = {"AAPL": 0.15, "TSLA": 0.60}
        weights = constructor.risk_parity_weights(["AAPL", "TSLA"], vols)

        assert weights["AAPL"] > weights["TSLA"]

    def test_equal_vol_gives_equal_weight(self, test_settings, mock_price_provider):
        """Equal volatility should produce equal weights."""
        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        vols = {"A": 0.25, "B": 0.25, "C": 0.25}
        weights = constructor.risk_parity_weights(["A", "B", "C"], vols)

        for w in weights.values():
            assert abs(w - 1.0 / 3) < 1e-9

    def test_missing_vol_uses_default(self, test_settings, mock_price_provider):
        """Missing volatility defaults to 0.30, still sums to 1.0."""
        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        vols = {"AAPL": 0.20}  # "XOM" is missing
        weights = constructor.risk_parity_weights(["AAPL", "XOM"], vols)

        assert abs(sum(weights.values()) - 1.0) < 1e-9
        # AAPL (vol 0.20) should have higher weight than XOM (default 0.30)
        assert weights["AAPL"] > weights["XOM"]

    def test_zero_vol_fallback(self, test_settings, mock_price_provider):
        """Zero volatility uses fallback weight of 1.0."""
        constructor = PortfolioConstructor(
            settings=test_settings, price_provider=mock_price_provider
        )

        vols = {"A": 0.0, "B": 0.20}
        weights = constructor.risk_parity_weights(["A", "B"], vols)

        assert abs(sum(weights.values()) - 1.0) < 1e-9
