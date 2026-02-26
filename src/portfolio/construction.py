"""Portfolio construction with diversification and risk-parity weighting.

Sits between AnalysisAgent (ranking) and ExecutionAgent (buying).
Instead of blindly taking top N by score, optimizes for portfolio-level
diversification using correlation constraints and risk-parity weights.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import Settings, get_settings
from ..data.price import PriceProvider
from ..data.geopolitical import get_ticker_sector
from ..strategy.ranking import RankedStock
from .positions import PositionManager


@dataclass
class PortfolioCandidate:
    """A candidate selected for the portfolio."""

    ranked_stock: RankedStock
    sector: str
    correlation_to_portfolio: float
    suggested_weight: float


@dataclass
class PortfolioProposal:
    """Result of portfolio construction."""

    selected: List[PortfolioCandidate]
    rejected: List[Tuple[RankedStock, str]]  # (stock, reason)
    sector_exposure: Dict[str, float]
    diversification_ratio: float


class PortfolioConstructor:
    """Constructs diversified portfolios from ranked candidates.

    Greedy selection: take the top pick, then skip candidates that are
    too correlated with already-selected stocks or would exceed sector
    concentration limits. Apply risk-parity (inverse-variance) weights.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        price_provider: Optional[PriceProvider] = None,
        position_manager: Optional[PositionManager] = None,
    ):
        self.settings = settings or get_settings()
        self.price_provider = price_provider or PriceProvider()
        self.position_manager = position_manager

    def compute_correlation_matrix(
        self,
        tickers: List[str],
        lookback_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Compute pairwise return correlations.

        Args:
            tickers: List of ticker symbols
            lookback_days: Number of calendar days for correlation window

        Returns:
            DataFrame with pairwise correlations
        """
        lookback = lookback_days or self.settings.portfolio_correlation_lookback
        calendar_days = int(lookback * 1.5) + 10

        returns_dict = {}
        for ticker in tickers:
            df = self.price_provider.get_ohlcv(ticker, days=calendar_days)
            if df.empty or len(df) < 20:
                continue
            returns_dict[ticker] = df["Close"].pct_change().dropna()

        if not returns_dict:
            return pd.DataFrame()

        returns_df = pd.DataFrame(returns_dict).dropna()
        if returns_df.empty or len(returns_df) < 10:
            return pd.DataFrame()

        return returns_df.corr()

    def compute_volatilities(
        self,
        tickers: List[str],
        lookback_days: Optional[int] = None,
    ) -> Dict[str, float]:
        """Compute annualized volatility for each ticker.

        Args:
            tickers: List of ticker symbols
            lookback_days: Lookback window

        Returns:
            Dict mapping ticker to annualized volatility
        """
        lookback = lookback_days or self.settings.portfolio_correlation_lookback
        calendar_days = int(lookback * 1.5) + 10

        vols = {}
        for ticker in tickers:
            df = self.price_provider.get_ohlcv(ticker, days=calendar_days)
            if df.empty or len(df) < 20:
                continue
            daily_vol = df["Close"].pct_change().dropna().std()
            vols[ticker] = daily_vol * np.sqrt(252)

        return vols

    def risk_parity_weights(
        self,
        tickers: List[str],
        volatilities: Dict[str, float],
    ) -> Dict[str, float]:
        """Compute inverse-variance (risk-parity) weights.

        Each position contributes equal risk to the portfolio.

        Args:
            tickers: List of selected tickers
            volatilities: Dict mapping ticker to annualized vol

        Returns:
            Dict mapping ticker to weight (sums to 1.0)
        """
        inv_vars = {}
        for ticker in tickers:
            vol = volatilities.get(ticker, 0.30)  # Default 30% vol
            if vol > 0:
                inv_vars[ticker] = 1.0 / (vol ** 2)
            else:
                inv_vars[ticker] = 1.0

        total = sum(inv_vars.values())
        if total == 0:
            n = len(tickers)
            return {t: 1.0 / n for t in tickers}

        return {t: v / total for t, v in inv_vars.items()}

    def select_portfolio(
        self,
        ranked: List[RankedStock],
        max_picks: Optional[int] = None,
        max_correlation: Optional[float] = None,
    ) -> PortfolioProposal:
        """Select diversified portfolio from ranked candidates.

        Greedy algorithm:
        1. Take the top-ranked pick
        2. For each subsequent candidate, check correlation with
           already-selected stocks
        3. Skip if max pairwise correlation exceeds threshold
        4. Check sector concentration limits
        5. Assign risk-parity weights

        Args:
            ranked: Ranked stocks (highest score first)
            max_picks: Maximum number to select
            max_correlation: Max pairwise correlation threshold

        Returns:
            PortfolioProposal with selected and rejected candidates
        """
        max_picks = max_picks or self.settings.daily_picks
        max_corr = max_correlation or self.settings.portfolio_max_pairwise_corr

        if not ranked:
            return PortfolioProposal(
                selected=[], rejected=[], sector_exposure={},
                diversification_ratio=0.0,
            )

        # Get all candidate tickers for correlation computation
        all_tickers = [r.ticker for r in ranked]
        corr_matrix = self.compute_correlation_matrix(all_tickers)
        volatilities = self.compute_volatilities(all_tickers)

        # Track existing portfolio sectors
        existing_sectors: Dict[str, int] = {}
        if self.position_manager is not None:
            for pos in self.position_manager.get_all_positions():
                sec = get_ticker_sector(pos.ticker)
                existing_sectors[sec] = existing_sectors.get(sec, 0) + 1

        selected: List[PortfolioCandidate] = []
        rejected: List[Tuple[RankedStock, str]] = []
        selected_tickers: List[str] = []
        sector_counts: Dict[str, int] = dict(existing_sectors)

        for stock in ranked:
            if len(selected) >= max_picks:
                break

            ticker = stock.ticker
            sector = get_ticker_sector(ticker)

            # Check correlation with already-selected
            if selected_tickers and not corr_matrix.empty and ticker in corr_matrix.columns:
                max_pairwise = 0.0
                for sel_ticker in selected_tickers:
                    if sel_ticker in corr_matrix.columns:
                        corr_val = abs(corr_matrix.loc[ticker, sel_ticker])
                        max_pairwise = max(max_pairwise, corr_val)

                if max_pairwise > max_corr:
                    rejected.append((
                        stock,
                        f"{ticker}: corr {max_pairwise:.2f} > {max_corr:.2f} with selected",
                    ))
                    continue
            else:
                max_pairwise = 0.0

            # Check sector concentration
            max_per_sector = max(2, self.settings.max_positions // 3)
            if sector_counts.get(sector, 0) >= max_per_sector:
                rejected.append((stock, f"{ticker}: sector {sector} at limit"))
                continue

            # Accept
            selected.append(PortfolioCandidate(
                ranked_stock=stock,
                sector=sector,
                correlation_to_portfolio=max_pairwise,
                suggested_weight=0.0,  # Will be set below
            ))
            selected_tickers.append(ticker)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        # Compute risk-parity weights
        if selected and self.settings.portfolio_risk_parity_enabled:
            weights = self.risk_parity_weights(selected_tickers, volatilities)
            for candidate in selected:
                candidate.suggested_weight = weights.get(
                    candidate.ranked_stock.ticker, 1.0 / len(selected)
                )
        elif selected:
            equal_weight = 1.0 / len(selected)
            for candidate in selected:
                candidate.suggested_weight = equal_weight

        # Build sector exposure
        total_selected = len(selected)
        sector_exposure = {}
        for sec, count in sector_counts.items():
            sector_exposure[sec] = count / max(total_selected + sum(existing_sectors.values()), 1)

        # Diversification ratio: avg pairwise correlation (lower = more diverse)
        div_ratio = 0.0
        if len(selected_tickers) > 1 and not corr_matrix.empty:
            pairs = 0
            total_corr = 0.0
            for i, t1 in enumerate(selected_tickers):
                for t2 in selected_tickers[i + 1:]:
                    if t1 in corr_matrix.columns and t2 in corr_matrix.columns:
                        total_corr += abs(corr_matrix.loc[t1, t2])
                        pairs += 1
            if pairs > 0:
                avg_corr = total_corr / pairs
                div_ratio = 1.0 - avg_corr  # Higher = more diversified

        return PortfolioProposal(
            selected=selected,
            rejected=rejected,
            sector_exposure=sector_exposure,
            diversification_ratio=div_ratio,
        )
