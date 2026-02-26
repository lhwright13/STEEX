"""Entry and exit signal generation."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from ..data.price import PriceProvider
from ..data.vix import VixProvider
from ..indicators.technical import TechnicalIndicators
from config.settings import Settings, get_settings


class ExitReason(Enum):
    """Reasons for exit signals."""

    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    MAX_HOLD_TIME = "max_hold_time"
    VIX_SPIKE = "vix_spike"
    BELOW_MA = "below_ma"
    DEAD_MONEY = "dead_money"
    SIGNAL_REVERSAL = "signal_reversal"


@dataclass
class EntrySignal:
    """Entry signal for a stock."""

    ticker: str
    signal_date: datetime
    recommended_entry: float  # Suggested entry price
    stop_loss: float  # Initial stop loss level
    position_size_pct: float  # Recommended position size
    score: float
    reasons: List[str]


@dataclass
class ExitSignal:
    """Exit signal for a position."""

    ticker: str
    signal_date: datetime
    reason: ExitReason
    recommended_exit: float
    current_price: float
    gain_pct: float  # Current gain/loss percentage
    urgency: str  # "immediate", "end_of_day", "next_session"


class SignalGenerator:
    """Generates entry and exit signals."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        price_provider: Optional[PriceProvider] = None,
        vix_provider: Optional[VixProvider] = None,
        technical: Optional[TechnicalIndicators] = None,
    ):
        """Initialize signal generator.

        Args:
            settings: Configuration settings
            price_provider: Price data provider
            vix_provider: VIX data provider
            technical: Technical indicators calculator
        """
        self.settings = settings or get_settings()
        self.price_provider = price_provider or PriceProvider()
        self.vix = vix_provider or VixProvider()
        self.technical = technical or TechnicalIndicators(self.price_provider)

    def generate_entry_signal(
        self,
        ticker: str,
        score: float,
        reasons: List[str],
        signal_date: Optional[datetime] = None,
    ) -> Optional[EntrySignal]:
        """Generate an entry signal for a stock.

        Args:
            ticker: Stock ticker symbol
            score: Composite score from ranking
            reasons: List of reasons for the pick
            signal_date: Date of signal (defaults to now)

        Returns:
            EntrySignal or None if price unavailable
        """
        current_price = self.price_provider.get_latest_price(ticker)
        if current_price is None:
            return None

        # Calculate stop loss
        stop_loss = current_price * (1 - self.settings.initial_stop_pct)

        # Adjust position size based on VIX
        position_size = self.settings.position_size_pct
        vix_level = self.vix.get_current()
        if vix_level and vix_level > self.settings.vix_caution_level:
            # Reduce position size in high volatility
            position_size *= 0.5

        return EntrySignal(
            ticker=ticker,
            signal_date=signal_date or datetime.now(),
            recommended_entry=current_price,
            stop_loss=stop_loss,
            position_size_pct=position_size,
            score=score,
            reasons=reasons,
        )

    def check_stop_loss(
        self,
        ticker: str,
        entry_price: float,
        current_high: float,
    ) -> Optional[ExitSignal]:
        """Check if stop loss or trailing stop is triggered.

        Args:
            ticker: Stock ticker symbol
            entry_price: Original entry price
            current_high: Highest price since entry

        Returns:
            ExitSignal if stop triggered, None otherwise
        """
        current_price = self.price_provider.get_latest_price(ticker)
        if current_price is None:
            return None

        gain_from_entry = (current_price - entry_price) / entry_price
        gain_from_high = (current_price - current_high) / current_high

        # Determine which stop applies
        if gain_from_entry <= 0:
            # Still at or below entry - use initial stop
            stop_distance = self.settings.initial_stop_pct
            if gain_from_entry <= -stop_distance:
                return ExitSignal(
                    ticker=ticker,
                    signal_date=datetime.now(),
                    reason=ExitReason.STOP_LOSS,
                    recommended_exit=current_price,
                    current_price=current_price,
                    gain_pct=gain_from_entry,
                    urgency="immediate",
                )
        else:
            # In profit - use trailing stop
            trail_stops = self.settings.trailing_stops
            applicable_trail = self.settings.initial_stop_pct

            for threshold, trail_pct in sorted(trail_stops.items()):
                if (current_high - entry_price) / entry_price >= threshold:
                    applicable_trail = trail_pct

            if -gain_from_high >= applicable_trail:
                return ExitSignal(
                    ticker=ticker,
                    signal_date=datetime.now(),
                    reason=ExitReason.TRAILING_STOP,
                    recommended_exit=current_price,
                    current_price=current_price,
                    gain_pct=gain_from_entry,
                    urgency="immediate",
                )

        return None

    def check_vix_exit(
        self,
        ticker: str,
        entry_price: float,
    ) -> Optional[ExitSignal]:
        """Check if VIX spike warrants exit.

        Args:
            ticker: Stock ticker symbol
            entry_price: Original entry price

        Returns:
            ExitSignal if VIX spike, None otherwise
        """
        vix_level = self.vix.get_current()
        if vix_level is None:
            return None

        if vix_level > self.settings.vix_exit_level:
            current_price = self.price_provider.get_latest_price(ticker)
            if current_price is None:
                return None

            gain_pct = (current_price - entry_price) / entry_price

            return ExitSignal(
                ticker=ticker,
                signal_date=datetime.now(),
                reason=ExitReason.VIX_SPIKE,
                recommended_exit=current_price,
                current_price=current_price,
                gain_pct=gain_pct,
                urgency="immediate",
            )

        return None

    def check_ma_exit(
        self,
        ticker: str,
        entry_price: float,
    ) -> Optional[ExitSignal]:
        """Check if price closed below 50-day MA.

        Args:
            ticker: Stock ticker symbol
            entry_price: Original entry price

        Returns:
            ExitSignal if below MA, None otherwise
        """
        ma_data = self.technical.price_vs_ma(ticker, self.settings.ma_short)
        if ma_data is None:
            return None

        if not ma_data["above_ma"]:
            current_price = ma_data["price"]
            gain_pct = (current_price - entry_price) / entry_price

            return ExitSignal(
                ticker=ticker,
                signal_date=datetime.now(),
                reason=ExitReason.BELOW_MA,
                recommended_exit=current_price,
                current_price=current_price,
                gain_pct=gain_pct,
                urgency="end_of_day",
            )

        return None

    def check_time_exit(
        self,
        ticker: str,
        entry_price: float,
        entry_date: datetime,
        current_date: Optional[datetime] = None,
    ) -> Optional[ExitSignal]:
        """Check if maximum hold time exceeded.

        Args:
            ticker: Stock ticker symbol
            entry_price: Original entry price
            entry_date: Date of entry
            current_date: Current date (defaults to now)

        Returns:
            ExitSignal if max hold time reached, None otherwise
        """
        current_date = current_date or datetime.now()
        days_held = (current_date - entry_date).days

        # Approximate trading days (weekdays only)
        trading_days = int(days_held * 5 / 7)

        if trading_days >= self.settings.max_hold_days:
            current_price = self.price_provider.get_latest_price(ticker)
            if current_price is None:
                return None

            gain_pct = (current_price - entry_price) / entry_price

            return ExitSignal(
                ticker=ticker,
                signal_date=current_date,
                reason=ExitReason.MAX_HOLD_TIME,
                recommended_exit=current_price,
                current_price=current_price,
                gain_pct=gain_pct,
                urgency="next_session",
            )

        return None

    def check_dead_money(
        self,
        ticker: str,
        entry_price: float,
        entry_date: datetime,
        current_date: Optional[datetime] = None,
    ) -> Optional[ExitSignal]:
        """Check if position is dead money (below entry for too long).

        Args:
            ticker: Stock ticker symbol
            entry_price: Original entry price
            entry_date: Date of entry
            current_date: Current date (defaults to now)

        Returns:
            ExitSignal if dead money, None otherwise
        """
        if not self.settings.dead_money_enabled:
            return None

        current_date = current_date or datetime.now()
        days_held = (current_date - entry_date).days
        trading_days = int(days_held * 5 / 7)

        if trading_days < self.settings.dead_money_days:
            return None

        current_price = self.price_provider.get_latest_price(ticker)
        if current_price is None:
            return None

        if current_price < entry_price:
            gain_pct = (current_price - entry_price) / entry_price

            return ExitSignal(
                ticker=ticker,
                signal_date=current_date,
                reason=ExitReason.DEAD_MONEY,
                recommended_exit=current_price,
                current_price=current_price,
                gain_pct=gain_pct,
                urgency="next_session",
            )

        return None

    def check_all_exits(
        self,
        ticker: str,
        entry_price: float,
        entry_date: datetime,
        high_since_entry: float,
        current_date: Optional[datetime] = None,
    ) -> List[ExitSignal]:
        """Check all exit conditions for a position.

        Args:
            ticker: Stock ticker symbol
            entry_price: Original entry price
            entry_date: Date of entry
            high_since_entry: Highest price since entry
            current_date: Current date (defaults to now)

        Returns:
            List of triggered exit signals (may be empty)
        """
        signals = []

        # Check in priority order
        # 1. Stop loss / trailing stop
        stop_signal = self.check_stop_loss(ticker, entry_price, high_since_entry)
        if stop_signal:
            signals.append(stop_signal)

        # 2. VIX spike
        vix_signal = self.check_vix_exit(ticker, entry_price)
        if vix_signal:
            signals.append(vix_signal)

        # 3. Below MA
        ma_signal = self.check_ma_exit(ticker, entry_price)
        if ma_signal:
            signals.append(ma_signal)

        # 4. Max hold time
        time_signal = self.check_time_exit(
            ticker, entry_price, entry_date, current_date
        )
        if time_signal:
            signals.append(time_signal)

        # 5. Dead money
        dead_signal = self.check_dead_money(
            ticker, entry_price, entry_date, current_date
        )
        if dead_signal:
            signals.append(dead_signal)

        return signals

    def get_adjusted_stop(
        self,
        entry_price: float,
        current_high: float,
    ) -> float:
        """Get the current stop loss level based on gain.

        Args:
            entry_price: Original entry price
            current_high: Highest price since entry

        Returns:
            Current stop loss price
        """
        gain_pct = (current_high - entry_price) / entry_price

        # Find applicable trailing stop
        trail_pct = self.settings.initial_stop_pct
        for threshold, trail in sorted(self.settings.trailing_stops.items()):
            if gain_pct >= threshold:
                trail_pct = trail

        # Check if VIX is elevated
        vix_level = self.vix.get_current()
        if vix_level and vix_level > self.settings.vix_caution_level:
            trail_pct = min(trail_pct, self.settings.vix_tight_stop_pct)

        # Calculate stop from high
        return current_high * (1 - trail_pct)
