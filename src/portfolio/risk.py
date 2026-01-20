"""Risk management for the portfolio."""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..data.price import PriceProvider
from ..data.vix import VixProvider
from ..strategy.signals import ExitSignal, SignalGenerator
from config.settings import Settings, get_settings

from .positions import Position, PositionManager


class RiskManager:
    """Manages portfolio risk and generates exit signals."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        position_manager: Optional[PositionManager] = None,
        signal_generator: Optional[SignalGenerator] = None,
        price_provider: Optional[PriceProvider] = None,
        vix_provider: Optional[VixProvider] = None,
    ):
        """Initialize risk manager.

        Args:
            settings: Configuration settings
            position_manager: Position manager instance
            signal_generator: Signal generator instance
            price_provider: Price data provider
            vix_provider: VIX data provider
        """
        self.settings = settings or get_settings()
        self.positions = position_manager or PositionManager(self.settings)
        self.price_provider = price_provider or PriceProvider()
        self.vix = vix_provider or VixProvider()
        self.signals = signal_generator or SignalGenerator(
            self.settings, self.price_provider, self.vix
        )

    def update_stops(self) -> Dict[str, float]:
        """Update trailing stops for all positions.

        Returns:
            Dict mapping ticker to new stop price
        """
        updates = {}

        for position in self.positions.get_all_positions():
            current_price = self.price_provider.get_latest_price(position.ticker)
            if current_price is None:
                continue

            # Update high price if needed
            self.positions.update_high(position.ticker, current_price)

            # Calculate new stop
            new_stop = self.signals.get_adjusted_stop(
                position.entry_price,
                position.high_since_entry,
            )

            # Only update if stop moved up (never lower it)
            if new_stop > position.current_stop:
                self.positions.update_stop(position.ticker, new_stop)
                updates[position.ticker] = new_stop

        return updates

    def check_all_exits(
        self,
        current_date: Optional[datetime] = None,
    ) -> List[Tuple[Position, List[ExitSignal]]]:
        """Check exit conditions for all positions.

        Args:
            current_date: Current date (defaults to now)

        Returns:
            List of (Position, [ExitSignal]) tuples for triggered exits
        """
        triggered = []

        for position in self.positions.get_all_positions():
            exit_signals = self.signals.check_all_exits(
                ticker=position.ticker,
                entry_price=position.entry_price,
                entry_date=position.entry_datetime,
                high_since_entry=position.high_since_entry,
                current_date=current_date,
            )

            if exit_signals:
                triggered.append((position, exit_signals))

        return triggered

    def get_immediate_exits(
        self,
        current_date: Optional[datetime] = None,
    ) -> List[Tuple[Position, ExitSignal]]:
        """Get positions that need immediate exit.

        Args:
            current_date: Current date (defaults to now)

        Returns:
            List of (Position, ExitSignal) for immediate exits
        """
        all_exits = self.check_all_exits(current_date)
        immediate = []

        for position, signals in all_exits:
            for signal in signals:
                if signal.urgency == "immediate":
                    immediate.append((position, signal))
                    break  # One immediate signal is enough

        return immediate

    def check_vix_risk(self) -> Dict:
        """Check VIX-based risk levels.

        Returns:
            Dict with VIX level and risk assessment
        """
        vix_level = self.vix.get_current()

        if vix_level is None:
            return {"vix": None, "status": "unknown", "action": "none"}

        if vix_level > self.settings.vix_exit_level:
            return {
                "vix": vix_level,
                "status": "spike",
                "action": "exit_50_percent",
            }
        elif vix_level > self.settings.vix_caution_level:
            return {
                "vix": vix_level,
                "status": "elevated",
                "action": "tighten_stops",
            }
        else:
            return {
                "vix": vix_level,
                "status": "normal",
                "action": "none",
            }

    def calculate_portfolio_drawdown(
        self,
        starting_value: float,
        current_prices: Dict[str, float],
        cash: float,
    ) -> Dict:
        """Calculate current portfolio drawdown.

        Args:
            starting_value: Portfolio value at start
            current_prices: Dict mapping ticker to current price
            cash: Current cash balance

        Returns:
            Dict with drawdown metrics
        """
        summary = self.positions.get_portfolio_summary(current_prices)
        current_value = summary["total_value"] + cash

        drawdown = (starting_value - current_value) / starting_value
        drawdown_pct = drawdown * 100

        # Determine action based on drawdown
        if drawdown >= self.settings.drawdown_exit:
            action = "exit_all"
        elif drawdown >= self.settings.drawdown_pause:
            action = "pause_entries"
        elif drawdown >= self.settings.drawdown_reduce:
            action = "reduce_size"
        elif drawdown >= self.settings.drawdown_review:
            action = "review"
        else:
            action = "none"

        return {
            "starting_value": starting_value,
            "current_value": current_value,
            "drawdown": drawdown,
            "drawdown_pct": drawdown_pct,
            "action": action,
        }

    def get_sector_exposure(
        self,
        sector_map: Dict[str, str],
    ) -> Dict[str, float]:
        """Calculate sector exposure.

        Args:
            sector_map: Dict mapping ticker to sector

        Returns:
            Dict mapping sector to exposure percentage
        """
        if not self.positions.get_all_positions():
            return {}

        total_cost = self.positions.get_total_cost_basis()
        if total_cost == 0:
            return {}

        sector_exposure = {}
        for position in self.positions.get_all_positions():
            sector = sector_map.get(position.ticker, "Unknown")
            if sector not in sector_exposure:
                sector_exposure[sector] = 0
            sector_exposure[sector] += position.cost_basis / total_cost

        return sector_exposure

    def check_sector_limits(
        self,
        sector_map: Dict[str, str],
    ) -> List[str]:
        """Check for sectors exceeding limits.

        Args:
            sector_map: Dict mapping ticker to sector

        Returns:
            List of sectors exceeding limit
        """
        exposure = self.get_sector_exposure(sector_map)
        exceeded = []

        for sector, pct in exposure.items():
            if pct > self.settings.max_sector_pct:
                exceeded.append(sector)

        return exceeded

    def get_risk_summary(
        self,
        starting_value: float,
        current_prices: Dict[str, float],
        cash: float,
        sector_map: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """Get comprehensive risk summary.

        Args:
            starting_value: Portfolio starting value
            current_prices: Current prices by ticker
            cash: Current cash
            sector_map: Optional sector mapping

        Returns:
            Comprehensive risk summary dict
        """
        vix_risk = self.check_vix_risk()
        drawdown = self.calculate_portfolio_drawdown(
            starting_value, current_prices, cash
        )

        # Count positions needing attention
        exits = self.check_all_exits()
        immediate_exits = sum(
            1 for _, signals in exits if any(s.urgency == "immediate" for s in signals)
        )
        pending_exits = sum(
            1 for _, signals in exits if any(s.urgency != "immediate" for s in signals)
        )

        summary = {
            "vix": vix_risk,
            "drawdown": drawdown,
            "position_count": self.positions.get_position_count(),
            "max_positions": self.settings.max_positions,
            "immediate_exits": immediate_exits,
            "pending_exits": pending_exits,
        }

        if sector_map:
            summary["sector_exposure"] = self.get_sector_exposure(sector_map)
            summary["sectors_over_limit"] = self.check_sector_limits(sector_map)

        return summary
