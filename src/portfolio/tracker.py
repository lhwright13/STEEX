"""Trade tracking and performance logging."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import Settings, get_settings


@dataclass
class Trade:
    """A completed trade."""

    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: float
    cost_basis: float
    proceeds: float
    pnl_dollars: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    score: float = 0
    reasons: List[str] = field(default_factory=list)


class TradeTracker:
    """Tracks completed trades and calculates performance metrics."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        trades_file: Optional[Path] = None,
    ):
        """Initialize trade tracker.

        Args:
            settings: Configuration settings
            trades_file: Path to trades JSON file
        """
        self.settings = settings or get_settings()
        self.trades_file = trades_file or Path(
            self.settings.data_dir
        ) / self.settings.trades_file
        self.trades: List[Trade] = []
        self._load()

    def _load(self) -> None:
        """Load trades from file."""
        if self.trades_file.exists():
            try:
                with open(self.trades_file) as f:
                    data = json.load(f)
                    self.trades = [Trade(**t) for t in data]
            except (json.JSONDecodeError, TypeError):
                self.trades = []

    def _save(self) -> None:
        """Save trades to file."""
        self.trades_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trades_file, "w") as f:
            json.dump([asdict(t) for t in self.trades], f, indent=2)

    def record_trade(
        self,
        ticker: str,
        entry_date: datetime,
        exit_date: datetime,
        entry_price: float,
        exit_price: float,
        shares: float,
        exit_reason: str,
        score: float = 0,
        reasons: Optional[List[str]] = None,
    ) -> Trade:
        """Record a completed trade.

        Args:
            ticker: Stock ticker
            entry_date: Entry datetime
            exit_date: Exit datetime
            entry_price: Entry price per share
            exit_price: Exit price per share
            shares: Number of shares
            exit_reason: Reason for exit
            score: Strategy score at entry
            reasons: Reasons for entry

        Returns:
            The recorded Trade
        """
        cost_basis = entry_price * shares
        proceeds = exit_price * shares
        pnl_dollars = proceeds - cost_basis
        pnl_pct = (exit_price - entry_price) / entry_price
        hold_days = (exit_date - entry_date).days

        trade = Trade(
            ticker=ticker,
            entry_date=entry_date.isoformat(),
            exit_date=exit_date.isoformat(),
            entry_price=entry_price,
            exit_price=exit_price,
            shares=shares,
            cost_basis=cost_basis,
            proceeds=proceeds,
            pnl_dollars=pnl_dollars,
            pnl_pct=pnl_pct,
            hold_days=hold_days,
            exit_reason=exit_reason,
            score=score,
            reasons=reasons or [],
        )

        self.trades.append(trade)
        self._save()
        return trade

    def get_all_trades(self) -> List[Trade]:
        """Get all recorded trades.

        Returns:
            List of all trades
        """
        return self.trades

    def get_trades_in_range(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Trade]:
        """Get trades within a date range.

        Args:
            start_date: Start of range
            end_date: End of range

        Returns:
            Filtered list of trades
        """
        filtered = self.trades

        if start_date:
            start_iso = start_date.isoformat()
            filtered = [t for t in filtered if t.exit_date >= start_iso]

        if end_date:
            end_iso = end_date.isoformat()
            filtered = [t for t in filtered if t.exit_date <= end_iso]

        return filtered

    def calculate_metrics(
        self,
        trades: Optional[List[Trade]] = None,
    ) -> Dict:
        """Calculate performance metrics.

        Args:
            trades: List of trades (defaults to all trades)

        Returns:
            Dict with performance metrics
        """
        trades = trades if trades is not None else self.trades

        if not trades:
            return {
                "total_trades": 0,
                "winners": 0,
                "losers": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl_pct": 0,
                "avg_winner_pct": 0,
                "avg_loser_pct": 0,
                "profit_factor": 0,
                "avg_hold_days": 0,
            }

        winners = [t for t in trades if t.pnl_dollars > 0]
        losers = [t for t in trades if t.pnl_dollars <= 0]

        total_pnl = sum(t.pnl_dollars for t in trades)
        gross_profit = sum(t.pnl_dollars for t in winners)
        gross_loss = abs(sum(t.pnl_dollars for t in losers))

        win_rate = len(winners) / len(trades) if trades else 0
        avg_pnl_pct = sum(t.pnl_pct for t in trades) / len(trades) if trades else 0
        avg_winner = (
            sum(t.pnl_pct for t in winners) / len(winners) if winners else 0
        )
        avg_loser = (
            sum(t.pnl_pct for t in losers) / len(losers) if losers else 0
        )
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_hold_days = (
            sum(t.hold_days for t in trades) / len(trades) if trades else 0
        )

        return {
            "total_trades": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
            "avg_winner_pct": avg_winner,
            "avg_loser_pct": avg_loser,
            "profit_factor": profit_factor,
            "avg_hold_days": avg_hold_days,
        }

    def get_exit_reason_breakdown(
        self,
        trades: Optional[List[Trade]] = None,
    ) -> Dict[str, Dict]:
        """Get breakdown of trades by exit reason.

        Args:
            trades: List of trades (defaults to all)

        Returns:
            Dict mapping exit reason to stats
        """
        trades = trades if trades is not None else self.trades

        breakdown = {}
        for trade in trades:
            reason = trade.exit_reason
            if reason not in breakdown:
                breakdown[reason] = {
                    "count": 0,
                    "total_pnl": 0,
                    "winners": 0,
                    "losers": 0,
                }

            breakdown[reason]["count"] += 1
            breakdown[reason]["total_pnl"] += trade.pnl_dollars

            if trade.pnl_dollars > 0:
                breakdown[reason]["winners"] += 1
            else:
                breakdown[reason]["losers"] += 1

        # Calculate win rates
        for reason in breakdown:
            total = breakdown[reason]["count"]
            breakdown[reason]["win_rate"] = (
                breakdown[reason]["winners"] / total if total > 0 else 0
            )

        return breakdown

    def get_monthly_summary(
        self,
        trades: Optional[List[Trade]] = None,
    ) -> Dict[str, Dict]:
        """Get monthly performance summary.

        Args:
            trades: List of trades (defaults to all)

        Returns:
            Dict mapping month to performance metrics
        """
        trades = trades if trades is not None else self.trades

        by_month = {}
        for trade in trades:
            # Extract YYYY-MM from exit date
            month = trade.exit_date[:7]
            if month not in by_month:
                by_month[month] = []
            by_month[month].append(trade)

        summary = {}
        for month, month_trades in sorted(by_month.items()):
            summary[month] = self.calculate_metrics(month_trades)

        return summary

    def get_equity_curve(
        self,
        starting_capital: float = 10000,
        trades: Optional[List[Trade]] = None,
    ) -> List[Dict]:
        """Generate equity curve from trades.

        Args:
            starting_capital: Starting portfolio value
            trades: List of trades (defaults to all)

        Returns:
            List of equity points
        """
        trades = trades if trades is not None else self.trades

        # Sort by exit date
        sorted_trades = sorted(trades, key=lambda t: t.exit_date)

        curve = [{"date": None, "equity": starting_capital, "trade": None}]
        current_equity = starting_capital

        for trade in sorted_trades:
            current_equity += trade.pnl_dollars
            curve.append({
                "date": trade.exit_date,
                "equity": current_equity,
                "trade": trade.ticker,
            })

        return curve

    def format_trade_log(
        self,
        trades: Optional[List[Trade]] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Format trades for display.

        Args:
            trades: List of trades (defaults to all)
            limit: Maximum trades to return

        Returns:
            List of formatted trade dicts
        """
        trades = trades if trades is not None else self.trades

        # Sort by exit date descending
        sorted_trades = sorted(trades, key=lambda t: t.exit_date, reverse=True)

        formatted = []
        for trade in sorted_trades[:limit]:
            formatted.append({
                "ticker": trade.ticker,
                "entry": trade.entry_date[:10],
                "exit": trade.exit_date[:10],
                "hold_days": trade.hold_days,
                "pnl": f"${trade.pnl_dollars:,.2f}",
                "pnl_pct": f"{trade.pnl_pct * 100:+.1f}%",
                "exit_reason": trade.exit_reason,
            })

        return formatted
