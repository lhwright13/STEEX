"""Unified data service for the dashboard."""

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def retry_on_error(max_retries: int = 3, delay: float = 1.0):
    """Decorator to retry a function on error.

    Args:
        max_retries: Maximum number of retries
        delay: Delay between retries in seconds
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))
            # Return None or empty result instead of raising
            return None
        return wrapper
    return decorator

from config.settings import Settings, get_settings
from src.data.price import PriceProvider
from src.data.vix import VixProvider
from src.portfolio.positions import Position, PositionManager
from src.portfolio.tracker import Trade, TradeTracker
from src.strategy.screener import ScreeningPipelineResult, ScreeningResult, StockScreener


@dataclass
class PortfolioSummary:
    """Summary of portfolio state."""

    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    position_count: int
    cash: float
    positions: List[dict]


class DataLoader:
    """Unified data service for the dashboard."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize data loader.

        Args:
            settings: Configuration settings
        """
        self.settings = settings or get_settings()
        self._price_provider = None
        self._vix_provider = None
        self._position_manager = None
        self._trade_tracker = None
        self._screener = None
        self._pysr_predictor = None

    @property
    def price_provider(self) -> PriceProvider:
        """Lazy-load price provider."""
        if self._price_provider is None:
            self._price_provider = PriceProvider()
        return self._price_provider

    @property
    def vix_provider(self) -> VixProvider:
        """Lazy-load VIX provider."""
        if self._vix_provider is None:
            self._vix_provider = VixProvider()
        return self._vix_provider

    @property
    def position_manager(self) -> PositionManager:
        """Lazy-load position manager."""
        if self._position_manager is None:
            self._position_manager = PositionManager(self.settings)
        return self._position_manager

    @property
    def trade_tracker(self) -> TradeTracker:
        """Lazy-load trade tracker."""
        if self._trade_tracker is None:
            self._trade_tracker = TradeTracker(self.settings)
        return self._trade_tracker

    @property
    def screener(self) -> StockScreener:
        """Lazy-load stock screener."""
        if self._screener is None:
            self._screener = StockScreener(
                self.settings,
                pysr_predictor=self.pysr_predictor if self.settings.pysr_enabled else None,
            )
        return self._screener

    @property
    def pysr_predictor(self):
        """Lazy-load PySR predictor."""
        if self._pysr_predictor is None:
            try:
                from src.ml.predictor import PySRPredictor
                self._pysr_predictor = PySRPredictor(self.settings)
            except ImportError:
                self._pysr_predictor = None
        return self._pysr_predictor

    def get_pysr_equations(self) -> Optional[dict]:
        """Get discovered PySR equations for display."""
        predictor = self.pysr_predictor
        if predictor is None or not predictor.is_available():
            return None
        return predictor.get_active_equations()

    def get_pysr_walk_forward_results(self) -> Optional[dict]:
        """Get walk-forward results if a results file exists."""
        from pathlib import Path
        import json

        model_dir = Path(self.settings.pysr_model_dir)
        results_file = model_dir / "walk_forward_results.json"
        if results_file.exists():
            try:
                with open(results_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def get_pysr_predictions(self, tickers: list) -> Optional[dict]:
        """Get PySR predictions for given tickers."""
        predictor = self.pysr_predictor
        if predictor is None or not predictor.is_available():
            return None
        try:
            return predictor.predict_batch(tickers)
        except Exception:
            return None

    def get_current_vix(self) -> Optional[float]:
        """Get current VIX level."""
        try:
            return self.vix_provider.get_current()
        except Exception:
            return None

    def get_vix_historical(self, days: int = 252) -> pd.DataFrame:
        """Get historical VIX data."""
        try:
            return self.vix_provider.get_historical(days)
        except Exception:
            return pd.DataFrame()

    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        return self.position_manager.get_all_positions()

    def get_position_prices(self, positions: List[Position]) -> Dict[str, float]:
        """Get current prices for positions."""
        tickers = [p.ticker for p in positions]
        if not tickers:
            return {}

        prices = {}
        for ticker in tickers:
            try:
                price = self.price_provider.get_latest_price(ticker)
                if price is not None:
                    prices[ticker] = price
            except Exception:
                # Use entry price as fallback
                pass

        return prices

    def get_portfolio_summary(
        self,
        portfolio_value: Optional[float] = None,
    ) -> PortfolioSummary:
        """Get portfolio summary with current values.

        Args:
            portfolio_value: Total portfolio value (for cash calculation)

        Returns:
            PortfolioSummary with current state
        """
        positions = self.get_positions()

        if not positions:
            return PortfolioSummary(
                total_value=portfolio_value or 0,
                total_cost=0,
                total_pnl=0,
                total_pnl_pct=0,
                position_count=0,
                cash=portfolio_value or 0,
                positions=[],
            )

        prices = self.get_position_prices(positions)
        summary = self.position_manager.get_portfolio_summary(prices)

        invested = summary["total_value"]
        cash = (portfolio_value - invested) if portfolio_value else 0

        return PortfolioSummary(
            total_value=invested + cash if portfolio_value else invested,
            total_cost=summary["total_cost"],
            total_pnl=summary["total_pnl_dollars"],
            total_pnl_pct=summary["total_pnl_pct"],
            position_count=summary["position_count"],
            cash=cash,
            positions=summary["positions"],
        )

    def get_position_details(self) -> List[dict]:
        """Get detailed position data for display."""
        positions = self.get_positions()
        if not positions:
            return []

        prices = self.get_position_prices(positions)
        details = []

        for pos in positions:
            price = prices.get(pos.ticker, pos.entry_price)
            pnl = pos.calculate_pnl(price)
            days_held = (datetime.now() - pos.entry_datetime).days

            stop_distance = (price - pos.current_stop) / price if price > 0 else 0

            details.append({
                "ticker": pos.ticker,
                "shares": pos.shares,
                "entry_price": pos.entry_price,
                "current_price": price,
                "cost_basis": pos.cost_basis,
                "current_value": pnl["current_value"],
                "pnl_dollars": pnl["pnl_dollars"],
                "pnl_pct": pnl["pnl_pct"],
                "days_held": days_held,
                "entry_date": pos.entry_date,
                "stop_price": pos.current_stop,
                "stop_distance_pct": stop_distance,
                "high_since_entry": pos.high_since_entry,
                "score": pos.score,
                "reasons": pos.reasons,
            })

        return details

    def get_trades(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Trade]:
        """Get trades within date range."""
        return self.trade_tracker.get_trades_in_range(start_date, end_date)

    def get_all_trades(self) -> List[Trade]:
        """Get all trades."""
        return self.trade_tracker.get_all_trades()

    def get_trade_metrics(self, trades: Optional[List[Trade]] = None) -> Dict:
        """Get performance metrics for trades."""
        return self.trade_tracker.calculate_metrics(trades)

    def get_exit_reason_breakdown(
        self,
        trades: Optional[List[Trade]] = None,
    ) -> Dict[str, Dict]:
        """Get trades grouped by exit reason."""
        return self.trade_tracker.get_exit_reason_breakdown(trades)

    def get_monthly_summary(
        self,
        trades: Optional[List[Trade]] = None,
    ) -> Dict[str, Dict]:
        """Get monthly performance summary."""
        return self.trade_tracker.get_monthly_summary(trades)

    def get_equity_curve(
        self,
        starting_capital: float = 10000,
        trades: Optional[List[Trade]] = None,
    ) -> List[Dict]:
        """Get equity curve data."""
        return self.trade_tracker.get_equity_curve(starting_capital, trades)

    def run_screening(
        self,
        reference_date: Optional[datetime] = None,
    ) -> ScreeningPipelineResult:
        """Run the screening pipeline."""
        return self.screener.run_pipeline(reference_date)

    def get_screening_candidates(
        self,
        reference_date: Optional[datetime] = None,
    ) -> List[ScreeningResult]:
        """Get screening candidates."""
        return self.screener.get_candidates(reference_date)

    def get_price_history(
        self,
        ticker: str,
        days: int = 365,
    ) -> pd.DataFrame:
        """Get price history for a ticker."""
        try:
            return self.price_provider.get_ohlcv(ticker, days=days)
        except Exception:
            return pd.DataFrame()

    def get_spy_comparison(
        self,
        equity_curve: pd.DataFrame,
        starting_capital: float,
    ) -> pd.DataFrame:
        """Get SPY comparison data aligned with equity curve."""
        if equity_curve.empty:
            return pd.DataFrame()

        try:
            start_date = equity_curve.index[0]
            end_date = equity_curve.index[-1]

            spy_data = self.price_provider.get_ohlcv(
                "SPY",
                start=start_date - timedelta(days=5),
                end=end_date + timedelta(days=5),
            )

            if spy_data.empty:
                return pd.DataFrame()

            # Normalize SPY to match starting capital
            spy_start_price = spy_data["Close"].iloc[0]
            spy_shares = starting_capital / spy_start_price

            spy_equity = spy_data["Close"] * spy_shares
            spy_equity.name = "spy_equity"

            return spy_equity.to_frame()
        except Exception:
            return pd.DataFrame()


@st.cache_resource
def get_data_loader() -> DataLoader:
    """Get cached data loader instance."""
    return DataLoader()
