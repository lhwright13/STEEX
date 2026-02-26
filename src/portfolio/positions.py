"""Position tracking and management."""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """A single position in the portfolio."""

    ticker: str
    entry_date: str  # ISO format date string
    entry_price: float
    shares: float
    cost_basis: float
    high_since_entry: float
    current_stop: float
    score: float
    reasons: List[str] = field(default_factory=list)

    @property
    def entry_datetime(self) -> datetime:
        """Parse entry date as datetime."""
        return datetime.fromisoformat(self.entry_date)

    def update_high(self, price: float) -> bool:
        """Update high price if new high reached.

        Args:
            price: Current price

        Returns:
            True if new high was set
        """
        if price > self.high_since_entry:
            self.high_since_entry = price
            return True
        return False

    def calculate_pnl(self, current_price: float) -> Dict:
        """Calculate current P&L.

        Args:
            current_price: Current market price

        Returns:
            Dict with pnl_dollars and pnl_pct
        """
        current_value = current_price * self.shares
        pnl_dollars = current_value - self.cost_basis
        pnl_pct = (current_price - self.entry_price) / self.entry_price

        return {
            "pnl_dollars": pnl_dollars,
            "pnl_pct": pnl_pct,
            "current_value": current_value,
        }


class PositionManager:
    """Manages portfolio positions with persistence.

    Alpaca broker is the source of truth for what we own. Local JSON
    stores supplementary metadata (stops, high_since_entry, score,
    reasons) that the broker cannot track.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        positions_file: Optional[Path] = None,
    ):
        """Initialize position manager.

        Args:
            settings: Configuration settings
            positions_file: Path to positions JSON file
        """
        self.settings = settings or get_settings()
        self.positions_file = positions_file or Path(
            self.settings.data_dir
        ) / self.settings.positions_file
        self.positions: Dict[str, Position] = {}
        self._load()

    def sync_from_broker(self, broker) -> Dict:
        """Sync local positions with the broker (source of truth).

        Any position the broker has that we don't track locally gets
        created with default metadata. Any local position the broker
        does not have gets removed.

        Args:
            broker: Broker instance with get_positions() method

        Returns:
            Dict with sync results (added, removed, synced counts)
        """
        broker_positions = {p.ticker: p for p in broker.get_positions()}
        local_tickers = set(self.positions.keys())
        broker_tickers = set(broker_positions.keys())

        added = []
        removed = []

        # Add positions that exist in broker but not locally
        for ticker in broker_tickers - local_tickers:
            bp = broker_positions[ticker]
            self.positions[ticker] = Position(
                ticker=ticker,
                entry_date=datetime.now().isoformat(),
                entry_price=bp.avg_price,
                shares=bp.qty,
                cost_basis=bp.avg_price * bp.qty,
                high_since_entry=bp.avg_price,
                current_stop=bp.avg_price * (1 - self.settings.initial_stop_pct),
                score=0,
                reasons=["synced from broker"],
            )
            added.append(ticker)
            logger.info("Synced from broker: %s (%d shares @ $%.2f)", ticker, int(bp.qty), bp.avg_price)

        # Remove positions that exist locally but not in broker
        for ticker in local_tickers - broker_tickers:
            removed.append(ticker)
            del self.positions[ticker]
            logger.info("Removed stale local position: %s (not in broker)", ticker)

        # Update share counts from broker (broker is truth for qty)
        for ticker in broker_tickers & local_tickers:
            bp = broker_positions[ticker]
            pos = self.positions[ticker]
            if pos.shares != bp.qty:
                logger.info(
                    "Updated %s shares: %d -> %d (broker sync)",
                    ticker, int(pos.shares), int(bp.qty),
                )
                pos.shares = bp.qty
                pos.cost_basis = pos.entry_price * pos.shares

        if added or removed:
            self._save()

        result = {
            "added": added,
            "removed": removed,
            "synced": len(broker_tickers & local_tickers),
            "total": len(self.positions),
        }
        logger.info("Broker sync complete: %s", result)
        return result

    def _load(self) -> None:
        """Load positions from file."""
        if self.positions_file.exists():
            try:
                with open(self.positions_file) as f:
                    data = json.load(f)
                    for ticker, pos_data in data.items():
                        self.positions[ticker] = Position(**pos_data)
            except (json.JSONDecodeError, TypeError):
                self.positions = {}

    def _save(self) -> None:
        """Save positions to file."""
        self.positions_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.positions_file, "w") as f:
            data = {ticker: asdict(pos) for ticker, pos in self.positions.items()}
            json.dump(data, f, indent=2)

    def add_position(
        self,
        ticker: str,
        entry_price: float,
        shares: float,
        score: float = 0,
        reasons: Optional[List[str]] = None,
        entry_date: Optional[datetime] = None,
    ) -> Position:
        """Add a new position.

        Args:
            ticker: Stock ticker
            entry_price: Entry price per share
            shares: Number of shares
            score: Strategy score
            reasons: List of reasons for entry
            entry_date: Date of entry (defaults to now)

        Returns:
            The created Position
        """
        entry_date = entry_date or datetime.now()
        cost_basis = entry_price * shares
        initial_stop = entry_price * (1 - self.settings.initial_stop_pct)

        position = Position(
            ticker=ticker,
            entry_date=entry_date.isoformat(),
            entry_price=entry_price,
            shares=shares,
            cost_basis=cost_basis,
            high_since_entry=entry_price,
            current_stop=initial_stop,
            score=score,
            reasons=reasons or [],
        )

        self.positions[ticker] = position
        self._save()
        return position

    def remove_position(self, ticker: str) -> Optional[Position]:
        """Remove a position.

        Args:
            ticker: Stock ticker

        Returns:
            The removed Position or None
        """
        position = self.positions.pop(ticker, None)
        if position:
            self._save()
        return position

    def get_position(self, ticker: str) -> Optional[Position]:
        """Get a position by ticker.

        Args:
            ticker: Stock ticker

        Returns:
            Position or None
        """
        return self.positions.get(ticker)

    def get_all_positions(self) -> List[Position]:
        """Get all positions.

        Returns:
            List of all positions
        """
        return list(self.positions.values())

    def get_position_count(self) -> int:
        """Get number of open positions.

        Returns:
            Number of positions
        """
        return len(self.positions)

    def has_position(self, ticker: str) -> bool:
        """Check if we have a position in a ticker.

        Args:
            ticker: Stock ticker

        Returns:
            True if position exists
        """
        return ticker in self.positions

    def update_stop(self, ticker: str, new_stop: float) -> bool:
        """Update stop loss for a position.

        Args:
            ticker: Stock ticker
            new_stop: New stop loss price

        Returns:
            True if updated successfully
        """
        position = self.positions.get(ticker)
        if position is None:
            return False

        position.current_stop = new_stop
        self._save()
        return True

    def update_high(self, ticker: str, price: float) -> bool:
        """Update high price for a position.

        Args:
            ticker: Stock ticker
            price: Current price

        Returns:
            True if new high was set
        """
        position = self.positions.get(ticker)
        if position is None:
            return False

        if position.update_high(price):
            self._save()
            return True
        return False

    def get_total_cost_basis(self) -> float:
        """Get total cost basis of all positions.

        Returns:
            Total cost basis
        """
        return sum(p.cost_basis for p in self.positions.values())

    def can_add_position(
        self, portfolio_value: float, cash: Optional[float] = None
    ) -> bool:
        """Check if we can add another position.

        Args:
            portfolio_value: Current portfolio value (equity from broker)
            cash: Actual cash from broker. If None, estimated from
                portfolio_value minus cost basis.

        Returns:
            True if we can add a position
        """
        if len(self.positions) >= self.settings.max_positions:
            return False

        if cash is None:
            total_invested = self.get_total_cost_basis()
            cash = portfolio_value - total_invested

        min_cash = portfolio_value * self.settings.min_cash_reserve_pct
        return cash > min_cash

    def get_position_size(
        self,
        portfolio_value: float,
        current_price: float,
    ) -> int:
        """Calculate recommended position size in shares.

        Args:
            portfolio_value: Current portfolio value
            current_price: Stock price

        Returns:
            Number of shares to buy
        """
        target_value = portfolio_value * self.settings.position_size_pct
        shares = int(target_value / current_price)
        return max(1, shares)

    def get_portfolio_summary(
        self,
        current_prices: Dict[str, float],
    ) -> Dict:
        """Get portfolio summary with current values.

        Args:
            current_prices: Dict mapping ticker to current price

        Returns:
            Summary dict with total value, P&L, etc.
        """
        total_cost = 0
        total_value = 0
        position_summaries = []

        for ticker, position in self.positions.items():
            price = current_prices.get(ticker, position.entry_price)
            pnl = position.calculate_pnl(price)

            total_cost += position.cost_basis
            total_value += pnl["current_value"]

            position_summaries.append({
                "ticker": ticker,
                "shares": position.shares,
                "entry_price": position.entry_price,
                "current_price": price,
                "cost_basis": position.cost_basis,
                "current_value": pnl["current_value"],
                "pnl_dollars": pnl["pnl_dollars"],
                "pnl_pct": pnl["pnl_pct"],
                "days_held": (datetime.now() - position.entry_datetime).days,
            })

        total_pnl = total_value - total_cost
        total_pnl_pct = total_pnl / total_cost if total_cost > 0 else 0

        return {
            "position_count": len(self.positions),
            "total_cost": total_cost,
            "total_value": total_value,
            "total_pnl_dollars": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "positions": position_summaries,
        }
