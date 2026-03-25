"""Abstract broker interface for order execution."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class OrderResult:
    """Result of an order submission."""

    order_id: str = ""
    filled_qty: float = 0.0
    filled_price: float = 0.0
    status: str = ""  # "filled", "partial", "cancelled", "failed"
    error: Optional[str] = None


@dataclass
class AccountInfo:
    """Broker account summary."""

    buying_power: float = 0.0
    equity: float = 0.0
    cash: float = 0.0


@dataclass
class AssetInfo:
    """Basic asset metadata for pre-order checks."""

    ticker: str = ""
    tradable: bool = False
    fractionable: bool = False
    shortable: bool = False
    asset_class: str = ""
    exchange: str = ""
    status: str = ""


@dataclass
class AccountConfig:
    """Broker account configuration flags."""

    pdt_check: str = ""
    trade_confirm_email: str = ""
    no_shorting: bool = False
    suspend_trade: bool = False


@dataclass
class BrokerPosition:
    """A position as reported by the broker."""

    ticker: str = ""
    qty: float = 0.0
    avg_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class Broker(ABC):
    """Abstract broker interface."""

    # -----------------------------------------------------------------
    # Limit orders (original)
    # -----------------------------------------------------------------

    @abstractmethod
    def buy(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        """Place a limit buy order."""
        ...

    @abstractmethod
    def sell(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        """Place a limit sell order."""
        ...

    # -----------------------------------------------------------------
    # Market orders
    # -----------------------------------------------------------------

    @abstractmethod
    def buy_market(self, ticker: str, qty: int) -> OrderResult:
        """Place a market buy order."""
        ...

    @abstractmethod
    def sell_market(self, ticker: str, qty: int) -> OrderResult:
        """Place a market sell order."""
        ...

    # -----------------------------------------------------------------
    # Account & positions
    # -----------------------------------------------------------------

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account info (buying power, equity, cash)."""
        ...

    @abstractmethod
    def get_positions(self) -> List[BrokerPosition]:
        """Get current broker positions."""
        ...

    @abstractmethod
    def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        """Get a single position by ticker. Returns None if not held."""
        ...

    @abstractmethod
    def get_account_config(self) -> AccountConfig:
        """Get account configuration (PDT flag, shorting status, etc.)."""
        ...

    # -----------------------------------------------------------------
    # Order management
    # -----------------------------------------------------------------

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True if cancelled."""
        ...

    @abstractmethod
    def get_order_history(
        self,
        status: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Get historical orders. status: 'closed', 'all', etc."""
        ...

    # -----------------------------------------------------------------
    # Stop orders
    # -----------------------------------------------------------------

    @abstractmethod
    def place_stop_order(
        self, ticker: str, qty: int, stop_price: float
    ) -> OrderResult:
        """Place a GTC stop sell order as a server-side safety net."""
        ...

    @abstractmethod
    def place_trailing_stop_order(
        self, ticker: str, qty: int, trail_percent: float
    ) -> OrderResult:
        """Place a GTC trailing stop sell order."""
        ...

    @abstractmethod
    def cancel_stop_for_ticker(self, ticker: str) -> bool:
        """Cancel active stop order for a ticker before a managed exit."""
        ...

    @abstractmethod
    def update_stop_order(
        self, ticker: str, qty: int, new_stop_price: float
    ) -> OrderResult:
        """Cancel existing stop and place new one at a higher price."""
        ...

    @abstractmethod
    def get_stop_order(self, ticker: str) -> Optional[Dict]:
        """Find the open GTC stop order for a symbol, if any."""
        ...

    @abstractmethod
    def get_all_stop_orders(self) -> List[Dict]:
        """List all open GTC stop orders for reconciliation."""
        ...

    # -----------------------------------------------------------------
    # Bracket orders
    # -----------------------------------------------------------------

    @abstractmethod
    def place_bracket_order(
        self,
        ticker: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        take_profit_price: float,
    ) -> OrderResult:
        """Place a bracket order (entry + stop-loss + take-profit)."""
        ...

    # -----------------------------------------------------------------
    # Position close
    # -----------------------------------------------------------------

    @abstractmethod
    def close_position(self, ticker: str) -> OrderResult:
        """Close an entire position using Alpaca's native close endpoint."""
        ...

    @abstractmethod
    def close_all_positions(self) -> List[OrderResult]:
        """Emergency liquidation — close all positions."""
        ...

    # -----------------------------------------------------------------
    # Asset info
    # -----------------------------------------------------------------

    @abstractmethod
    def get_asset(self, ticker: str) -> Optional[AssetInfo]:
        """Get asset metadata (tradability, fractionability, etc.)."""
        ...

    # -----------------------------------------------------------------
    # Market clock and calendar
    # -----------------------------------------------------------------

    @abstractmethod
    def get_clock(self) -> Dict:
        """Return market clock: {is_open, next_open, next_close}."""
        ...

    @abstractmethod
    def get_calendar(self, start: str, end: str) -> List[Dict]:
        """Return market calendar entries for a date range."""
        ...
