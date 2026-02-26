"""Abstract broker interface for order execution."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


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
class BrokerPosition:
    """A position as reported by the broker."""

    ticker: str = ""
    qty: float = 0.0
    avg_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class Broker(ABC):
    """Abstract broker interface."""

    @abstractmethod
    def buy(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        """Place a buy order."""
        ...

    @abstractmethod
    def sell(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        """Place a sell order."""
        ...

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account info (buying power, equity, cash)."""
        ...

    @abstractmethod
    def get_positions(self) -> List[BrokerPosition]:
        """Get current broker positions."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True if cancelled."""
        ...
