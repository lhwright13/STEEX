"""Backtesting engine for strategy simulation."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ..data.price import PriceProvider
from ..data.vix import VixProvider
from config.settings import Settings, get_settings

from .metrics import calculate_metrics


@dataclass
class BacktestTrade:
    """A trade in the backtest."""

    ticker: str
    entry_date: datetime
    exit_date: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    shares: float
    score: float
    exit_reason: Optional[str] = None

    @property
    def pnl(self) -> Optional[float]:
        """Calculate P&L if closed."""
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def pnl_pct(self) -> Optional[float]:
        """Calculate P&L percentage if closed."""
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class BacktestPosition:
    """An active position during backtest."""

    ticker: str
    entry_date: datetime
    entry_price: float
    shares: float
    high_since_entry: float
    score: float


@dataclass
class BacktestResult:
    """Results from a backtest run."""

    start_date: datetime
    end_date: datetime
    starting_capital: float
    ending_capital: float
    trades: List[BacktestTrade]
    equity_curve: pd.DataFrame
    metrics: Dict

    @property
    def total_return(self) -> float:
        """Total return percentage."""
        return (self.ending_capital - self.starting_capital) / self.starting_capital

    @property
    def total_return_pct(self) -> float:
        """Total return as percentage."""
        return self.total_return * 100


class BacktestEngine:
    """Event-driven backtesting engine."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        price_provider: Optional[PriceProvider] = None,
        vix_provider: Optional[VixProvider] = None,
    ):
        """Initialize backtest engine.

        Args:
            settings: Configuration settings
            price_provider: Price data provider
            vix_provider: VIX data provider
        """
        self.settings = settings or get_settings()
        self.price_provider = price_provider or PriceProvider()
        self.vix_provider = vix_provider or VixProvider()

    def run(
        self,
        signals: List[Dict],
        start_date: datetime,
        end_date: datetime,
        starting_capital: float = 10000,
        transaction_cost: Optional[float] = None,
    ) -> BacktestResult:
        """Run backtest simulation.

        Args:
            signals: List of entry signals with {date, ticker, score}
            start_date: Backtest start date
            end_date: Backtest end date
            starting_capital: Starting portfolio value
            transaction_cost: Cost per trade (defaults to settings)

        Returns:
            BacktestResult with all metrics
        """
        transaction_cost = (
            transaction_cost
            if transaction_cost is not None
            else self.settings.estimated_cost_per_trade
        )

        # Initialize state
        cash = starting_capital
        positions: Dict[str, BacktestPosition] = {}
        completed_trades: List[BacktestTrade] = []
        equity_history = []

        # Get all unique tickers for price data
        all_tickers = list(set(s["ticker"] for s in signals))

        # Fetch historical price data
        price_data = self._fetch_price_data(all_tickers, start_date, end_date)

        # Get VIX data
        vix_data = self._fetch_vix_data(start_date, end_date)

        # Group signals by date
        signals_by_date = {}
        for signal in signals:
            date = signal["date"]
            if isinstance(date, str):
                date = datetime.fromisoformat(date)
            date_key = date.date()
            if date_key not in signals_by_date:
                signals_by_date[date_key] = []
            signals_by_date[date_key].append(signal)

        # Simulate day by day
        current_date = start_date
        while current_date <= end_date:
            date_key = current_date.date()

            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # Get VIX for today
            vix_level = self._get_vix_for_date(vix_data, current_date)

            # Check exits first
            exits = self._check_exits(
                positions,
                price_data,
                vix_level,
                current_date,
            )

            for ticker, exit_price, reason in exits:
                pos = positions.pop(ticker)
                trade_value = exit_price * pos.shares
                cost = trade_value * transaction_cost
                cash += trade_value - cost

                completed_trades.append(
                    BacktestTrade(
                        ticker=ticker,
                        entry_date=pos.entry_date,
                        exit_date=current_date,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        shares=pos.shares,
                        score=pos.score,
                        exit_reason=reason,
                    )
                )

            # Process entry signals
            day_signals = signals_by_date.get(date_key, [])
            for signal in day_signals:
                ticker = signal["ticker"]

                # Skip if already have position
                if ticker in positions:
                    continue

                # Check position limits
                if len(positions) >= self.settings.max_positions:
                    continue

                # Get entry price
                entry_price = self._get_price_for_date(price_data, ticker, current_date)
                if entry_price is None:
                    continue

                # Calculate position size
                portfolio_value = cash + self._calculate_positions_value(
                    positions, price_data, current_date
                )

                if portfolio_value * self.settings.min_cash_reserve_pct > cash:
                    continue  # Not enough cash reserve

                position_value = portfolio_value * self.settings.position_size_pct
                shares = int(position_value / entry_price)

                if shares < 1:
                    continue

                # Execute entry
                cost = entry_price * shares * (1 + transaction_cost)
                if cost > cash:
                    continue

                cash -= cost
                positions[ticker] = BacktestPosition(
                    ticker=ticker,
                    entry_date=current_date,
                    entry_price=entry_price,
                    shares=shares,
                    high_since_entry=entry_price,
                    score=signal.get("score", 0),
                )

            # Update highs
            for ticker, pos in positions.items():
                price = self._get_price_for_date(price_data, ticker, current_date)
                if price and price > pos.high_since_entry:
                    pos.high_since_entry = price

            # Record equity
            positions_value = self._calculate_positions_value(
                positions, price_data, current_date
            )
            total_equity = cash + positions_value
            equity_history.append({
                "date": current_date,
                "equity": total_equity,
                "cash": cash,
                "positions_value": positions_value,
                "position_count": len(positions),
            })

            current_date += timedelta(days=1)

        # Close remaining positions at end
        for ticker, pos in list(positions.items()):
            exit_price = self._get_price_for_date(price_data, ticker, end_date)
            if exit_price:
                completed_trades.append(
                    BacktestTrade(
                        ticker=ticker,
                        entry_date=pos.entry_date,
                        exit_date=end_date,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        shares=pos.shares,
                        score=pos.score,
                        exit_reason="backtest_end",
                    )
                )

        # Create equity DataFrame
        equity_df = pd.DataFrame(equity_history)
        if not equity_df.empty:
            equity_df.set_index("date", inplace=True)

        # Calculate metrics
        metrics = calculate_metrics(completed_trades, equity_df)
        metrics["transaction_costs"] = sum(
            abs(t.pnl or 0) * transaction_cost for t in completed_trades
        )

        ending_capital = equity_history[-1]["equity"] if equity_history else starting_capital

        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            starting_capital=starting_capital,
            ending_capital=ending_capital,
            trades=completed_trades,
            equity_curve=equity_df,
            metrics=metrics,
        )

    def _fetch_price_data(
        self,
        tickers: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch price data for all tickers."""
        # Add buffer for MA calculations
        buffer_start = start_date - timedelta(days=300)
        return self.price_provider.get_ohlcv_batch(
            tickers, start=buffer_start, end=end_date
        )

    def _fetch_vix_data(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Fetch VIX data for the period."""
        buffer_start = start_date - timedelta(days=30)
        return self.vix_provider.fetch(start=buffer_start, end=end_date)

    def _get_price_for_date(
        self,
        price_data: Dict[str, pd.DataFrame],
        ticker: str,
        date: datetime,
    ) -> Optional[float]:
        """Get closing price for a ticker on a date."""
        df = price_data.get(ticker)
        if df is None or df.empty:
            return None

        # Find closest date on or before
        try:
            mask = df.index <= date
            if not mask.any():
                return None
            closest_date = df.index[mask][-1]
            return df.loc[closest_date, "Close"]
        except (KeyError, IndexError):
            return None

    def _get_vix_for_date(
        self,
        vix_data: pd.DataFrame,
        date: datetime,
    ) -> Optional[float]:
        """Get VIX level for a date."""
        if vix_data.empty:
            return None

        try:
            mask = vix_data.index <= date
            if not mask.any():
                return None
            closest_date = vix_data.index[mask][-1]
            return vix_data.loc[closest_date, "Close"]
        except (KeyError, IndexError):
            return None

    def _calculate_positions_value(
        self,
        positions: Dict[str, BacktestPosition],
        price_data: Dict[str, pd.DataFrame],
        date: datetime,
    ) -> float:
        """Calculate total value of positions."""
        total = 0
        for ticker, pos in positions.items():
            price = self._get_price_for_date(price_data, ticker, date)
            if price:
                total += price * pos.shares
        return total

    def _check_exits(
        self,
        positions: Dict[str, BacktestPosition],
        price_data: Dict[str, pd.DataFrame],
        vix_level: Optional[float],
        current_date: datetime,
    ) -> List[Tuple[str, float, str]]:
        """Check exit conditions for all positions.

        Returns:
            List of (ticker, exit_price, reason) tuples
        """
        exits = []

        for ticker, pos in list(positions.items()):
            price = self._get_price_for_date(price_data, ticker, current_date)
            if price is None:
                continue

            # Check stop loss
            gain_from_entry = (price - pos.entry_price) / pos.entry_price
            gain_from_high = (price - pos.high_since_entry) / pos.high_since_entry

            # Initial stop
            if gain_from_entry <= -self.settings.initial_stop_pct:
                exits.append((ticker, price, "stop_loss"))
                continue

            # Trailing stop
            if gain_from_entry > 0:
                trail_pct = self.settings.initial_stop_pct
                for threshold, trail in sorted(self.settings.trailing_stops.items()):
                    if (pos.high_since_entry - pos.entry_price) / pos.entry_price >= threshold:
                        trail_pct = trail

                if -gain_from_high >= trail_pct:
                    exits.append((ticker, price, "trailing_stop"))
                    continue

            # VIX spike
            if vix_level and vix_level > self.settings.vix_exit_level:
                exits.append((ticker, price, "vix_spike"))
                continue

            # Max hold time
            hold_days = (current_date - pos.entry_date).days
            trading_days = int(hold_days * 5 / 7)
            if trading_days >= self.settings.max_hold_days:
                exits.append((ticker, price, "max_hold_time"))
                continue

            # Dead money
            if trading_days >= self.settings.dead_money_days and price < pos.entry_price:
                exits.append((ticker, price, "dead_money"))
                continue

            # Below MA check (simplified - use 50-day MA)
            df = price_data.get(ticker)
            if df is not None and len(df) >= 50:
                try:
                    mask = df.index <= current_date
                    recent = df[mask].tail(50)
                    if len(recent) >= 50:
                        ma_50 = recent["Close"].mean()
                        if price < ma_50:
                            exits.append((ticker, price, "below_ma"))
                            continue
                except (KeyError, IndexError):
                    pass

        return exits

    def run_with_screener(
        self,
        start_date: datetime,
        end_date: datetime,
        starting_capital: float = 10000,
    ) -> BacktestResult:
        """Run backtest using the full screener pipeline.

        This is a simplified version that generates signals from historical
        insider data. For more accurate results, use pre-generated signals.

        Args:
            start_date: Backtest start date
            end_date: Backtest end date
            starting_capital: Starting portfolio value

        Returns:
            BacktestResult
        """
        # This would require historical insider data
        # For now, return empty result
        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            starting_capital=starting_capital,
            ending_capital=starting_capital,
            trades=[],
            equity_curve=pd.DataFrame(),
            metrics={},
        )
