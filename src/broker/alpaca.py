"""Alpaca broker implementation."""

import logging
import os
import time
from typing import Dict, List, Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType, TimeInForce, QueryOrderStatus
from alpaca.trading.requests import (
    ClosePositionRequest,
    GetCalendarRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)

from .base import AccountConfig, AccountInfo, AssetInfo, Broker, BrokerPosition, OrderResult

logger = logging.getLogger(__name__)

FILL_POLL_INTERVAL = 1.0  # seconds between fill checks
FILL_TIMEOUT = 30.0  # max seconds to wait for fill


class AlpacaBroker(Broker):
    """Alpaca Markets broker integration.

    Uses alpaca-py TradingClient for order execution. Defaults to paper
    trading. API keys are read from environment variables.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
    ):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca credentials required. Set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY environment variables."
            )

        self.paper = paper
        self.client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
        )
        logger.info("AlpacaBroker initialized (paper=%s)", paper)

    def buy(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        """Place a limit buy order and poll for fill."""
        return self._place_order(ticker, qty, limit_price, OrderSide.BUY)

    def sell(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        """Place a limit sell order and poll for fill."""
        return self._place_order(ticker, qty, limit_price, OrderSide.SELL)

    def get_account(self) -> AccountInfo:
        """Get account buying power, equity, and cash."""
        try:
            acct = self.client.get_account()
            return AccountInfo(
                buying_power=float(acct.buying_power),
                equity=float(acct.equity),
                cash=float(acct.cash),
            )
        except APIError as e:
            logger.error("Failed to get account: %s", e)
            raise

    def get_positions(self) -> List[BrokerPosition]:
        """Get all open positions from Alpaca."""
        try:
            positions = self.client.get_all_positions()
            return [
                BrokerPosition(
                    ticker=p.symbol,
                    qty=float(p.qty),
                    avg_price=float(p.avg_entry_price),
                    market_value=float(p.market_value),
                    unrealized_pnl=float(p.unrealized_pl),
                )
                for p in positions
            ]
        except APIError as e:
            logger.error("Failed to get positions: %s", e)
            raise

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except APIError as e:
            logger.warning("Failed to cancel order %s: %s", order_id, e)
            return False

    # -----------------------------------------------------------------
    # Server-side stop orders
    # -----------------------------------------------------------------

    def place_stop_order(
        self, ticker: str, qty: int, stop_price: float
    ) -> OrderResult:
        """Place a GTC stop sell order. Does not poll for fill."""
        order_data = StopOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=round(stop_price, 2),
        )
        try:
            order = self.client.submit_order(order_data)
            order_id = str(order.id)
            logger.info(
                "Stop order placed: %s %d shares @ $%.2f (id=%s)",
                ticker, qty, stop_price, order_id,
            )
            return OrderResult(
                order_id=order_id,
                status="accepted",
            )
        except APIError as e:
            logger.error("Stop order failed for %s: %s", ticker, e)
            return OrderResult(status="failed", error=str(e))

    def cancel_stop_for_ticker(self, ticker: str) -> bool:
        """Cancel the active GTC stop order for a ticker."""
        stop = self.get_stop_order(ticker)
        if stop is None:
            logger.debug("No active stop order found for %s", ticker)
            return True
        return self.cancel_order(stop["order_id"])

    def update_stop_order(
        self, ticker: str, qty: int, new_stop_price: float
    ) -> OrderResult:
        """Cancel existing stop and place a new one at the new price."""
        self.cancel_stop_for_ticker(ticker)
        return self.place_stop_order(ticker, qty, new_stop_price)

    def get_stop_order(self, ticker: str) -> Optional[Dict]:
        """Find the open GTC stop sell order for a symbol."""
        try:
            request = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[ticker],
            )
            orders = self.client.get_orders(filter=request)
            for order in orders:
                if (
                    order.order_type == OrderType.STOP
                    and order.side == OrderSide.SELL
                    and order.time_in_force == TimeInForce.GTC
                ):
                    return {
                        "order_id": str(order.id),
                        "ticker": order.symbol,
                        "qty": float(order.qty),
                        "stop_price": float(order.stop_price),
                        "status": order.status.value,
                    }
        except APIError as e:
            logger.error("Failed to get stop order for %s: %s", ticker, e)
        return None

    def get_all_stop_orders(self) -> List[Dict]:
        """List all open GTC stop sell orders."""
        try:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            orders = self.client.get_orders(filter=request)
            stops = []
            for order in orders:
                if (
                    order.order_type == OrderType.STOP
                    and order.side == OrderSide.SELL
                    and order.time_in_force == TimeInForce.GTC
                ):
                    stops.append({
                        "order_id": str(order.id),
                        "ticker": order.symbol,
                        "qty": float(order.qty),
                        "stop_price": float(order.stop_price),
                        "status": order.status.value,
                    })
            return stops
        except APIError as e:
            logger.error("Failed to list stop orders: %s", e)
            return []

    # -----------------------------------------------------------------
    # Market clock and calendar
    # -----------------------------------------------------------------

    def get_clock(self) -> Dict:
        """Return market clock info."""
        try:
            clock = self.client.get_clock()
            return {
                "is_open": clock.is_open,
                "next_open": clock.next_open.isoformat() if clock.next_open else None,
                "next_close": clock.next_close.isoformat() if clock.next_close else None,
            }
        except APIError as e:
            logger.error("Failed to get market clock: %s", e)
            raise

    def get_calendar(self, start: str, end: str) -> List[Dict]:
        """Return market calendar entries for a date range."""
        try:
            request = GetCalendarRequest(start=start, end=end)
            entries = self.client.get_calendar(filters=request)
            return [
                {
                    "date": str(entry.date),
                    "open": str(entry.open),
                    "close": str(entry.close),
                }
                for entry in entries
            ]
        except APIError as e:
            logger.error("Failed to get calendar: %s", e)
            return []

    # -----------------------------------------------------------------
    # Market orders
    # -----------------------------------------------------------------

    def buy_market(self, ticker: str, qty: int) -> OrderResult:
        """Place a market buy order and poll for fill."""
        return self._place_market_order(ticker, qty, OrderSide.BUY)

    def sell_market(self, ticker: str, qty: int) -> OrderResult:
        """Place a market sell order and poll for fill."""
        return self._place_market_order(ticker, qty, OrderSide.SELL)

    # -----------------------------------------------------------------
    # Single position lookup
    # -----------------------------------------------------------------

    def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        """Get a single position by ticker. Returns None if not held."""
        try:
            p = self.client.get_open_position(ticker)
            return BrokerPosition(
                ticker=p.symbol,
                qty=float(p.qty),
                avg_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pnl=float(p.unrealized_pl),
            )
        except APIError as e:
            if "position does not exist" in str(e).lower() or "404" in str(e):
                return None
            logger.error("Failed to get position for %s: %s", ticker, e)
            raise

    # -----------------------------------------------------------------
    # Account configuration
    # -----------------------------------------------------------------

    def get_account_config(self) -> AccountConfig:
        """Get account configuration (PDT flag, shorting, etc.)."""
        try:
            cfg = self.client.get_account_configurations()
            return AccountConfig(
                pdt_check=str(getattr(cfg, "pdt_check", "")),
                trade_confirm_email=str(getattr(cfg, "trade_confirm_email", "")),
                no_shorting=bool(getattr(cfg, "no_shorting", False)),
                suspend_trade=bool(getattr(cfg, "suspend_trade", False)),
            )
        except APIError as e:
            logger.error("Failed to get account config: %s", e)
            raise

    # -----------------------------------------------------------------
    # Order history
    # -----------------------------------------------------------------

    def get_order_history(
        self,
        status: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Get historical orders."""
        try:
            request_params = {}
            if status:
                request_params["status"] = QueryOrderStatus(status)
            if limit:
                request_params["limit"] = limit

            request = GetOrdersRequest(**request_params)
            orders = self.client.get_orders(filter=request)
            return [
                {
                    "order_id": str(o.id),
                    "ticker": o.symbol,
                    "side": o.side.value if o.side else None,
                    "type": o.order_type.value if o.order_type else None,
                    "status": o.status.value if o.status else None,
                    "qty": float(o.qty) if o.qty else 0,
                    "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                    "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else 0,
                    "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                    "filled_at": o.filled_at.isoformat() if o.filled_at else None,
                }
                for o in orders
            ]
        except APIError as e:
            logger.error("Failed to get order history: %s", e)
            return []

    # -----------------------------------------------------------------
    # Trailing stop orders
    # -----------------------------------------------------------------

    def place_trailing_stop_order(
        self, ticker: str, qty: int, trail_percent: float
    ) -> OrderResult:
        """Place a GTC trailing stop sell order."""
        order_data = TrailingStopOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            trail_percent=round(trail_percent, 2),
        )
        try:
            order = self.client.submit_order(order_data)
            order_id = str(order.id)
            logger.info(
                "Trailing stop placed: %s %d shares trail %.1f%% (id=%s)",
                ticker, qty, trail_percent, order_id,
            )
            return OrderResult(order_id=order_id, status="accepted")
        except APIError as e:
            logger.error("Trailing stop failed for %s: %s", ticker, e)
            return OrderResult(status="failed", error=str(e))

    # -----------------------------------------------------------------
    # Bracket orders (entry + stop-loss + take-profit)
    # -----------------------------------------------------------------

    def place_bracket_order(
        self,
        ticker: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        take_profit_price: float,
    ) -> OrderResult:
        """Place a bracket order (OTO: entry + stop-loss + take-profit)."""
        from alpaca.trading.requests import TakeProfitRequest, StopLossRequest
        from alpaca.trading.enums import OrderClass

        order_data = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        try:
            order = self.client.submit_order(order_data)
            order_id = str(order.id)
            logger.info(
                "Bracket order placed: %s %d shares limit=$%.2f stop=$%.2f tp=$%.2f (id=%s)",
                ticker, qty, limit_price, stop_price, take_profit_price, order_id,
            )
            return OrderResult(order_id=order_id, status="accepted")
        except APIError as e:
            logger.error("Bracket order failed for %s: %s", ticker, e)
            return OrderResult(status="failed", error=str(e))

    # -----------------------------------------------------------------
    # Position close
    # -----------------------------------------------------------------

    def close_position(self, ticker: str) -> OrderResult:
        """Close an entire position using Alpaca's native close endpoint."""
        try:
            order = self.client.close_position(ticker)
            order_id = str(order.id) if hasattr(order, "id") else ""
            logger.info("Close position submitted for %s (id=%s)", ticker, order_id)
            return OrderResult(order_id=order_id, status="accepted")
        except APIError as e:
            logger.error("Failed to close position %s: %s", ticker, e)
            return OrderResult(status="failed", error=str(e))

    def close_all_positions(self) -> List[OrderResult]:
        """Emergency liquidation — close all positions."""
        try:
            responses = self.client.close_all_positions(cancel_orders=True)
            results = []
            for resp in responses:
                if hasattr(resp, "body") and hasattr(resp.body, "id"):
                    results.append(OrderResult(
                        order_id=str(resp.body.id),
                        status="accepted",
                    ))
                elif hasattr(resp, "id"):
                    results.append(OrderResult(
                        order_id=str(resp.id),
                        status="accepted",
                    ))
                else:
                    results.append(OrderResult(status="accepted"))
            logger.info("Close-all submitted: %d positions", len(results))
            return results
        except APIError as e:
            logger.error("Failed to close all positions: %s", e)
            return [OrderResult(status="failed", error=str(e))]

    # -----------------------------------------------------------------
    # Asset info
    # -----------------------------------------------------------------

    def get_asset(self, ticker: str) -> Optional[AssetInfo]:
        """Get asset metadata for pre-order checks."""
        try:
            asset = self.client.get_asset(ticker)
            return AssetInfo(
                ticker=asset.symbol,
                tradable=bool(asset.tradable),
                fractionable=bool(asset.fractionable),
                shortable=bool(asset.shortable),
                asset_class=str(asset.asset_class) if asset.asset_class else "",
                exchange=str(asset.exchange) if asset.exchange else "",
                status=str(asset.status) if asset.status else "",
            )
        except APIError as e:
            logger.error("Failed to get asset %s: %s", ticker, e)
            return None

    # -----------------------------------------------------------------

    def _place_market_order(
        self, ticker: str, qty: int, side: OrderSide
    ) -> OrderResult:
        """Place a market order and wait for fill."""
        order_data = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )

        try:
            order = self.client.submit_order(order_data)
        except APIError as e:
            logger.error("Market order submission failed for %s: %s", ticker, e)
            return OrderResult(status="failed", error=str(e))

        order_id = str(order.id)
        logger.info(
            "Market %s order submitted: %s %d shares (id=%s)",
            side.value, ticker, qty, order_id,
        )

        # Poll for fill
        elapsed = 0.0
        while elapsed < FILL_TIMEOUT:
            time.sleep(FILL_POLL_INTERVAL)
            elapsed += FILL_POLL_INTERVAL

            try:
                order = self.client.get_order_by_id(order_id)
            except APIError:
                continue

            if order.status == OrderStatus.FILLED:
                filled_price = float(order.filled_avg_price)
                filled_qty = float(order.filled_qty)
                logger.info(
                    "Market order filled: %s %d x %s @ $%.2f",
                    ticker, int(filled_qty), side.value, filled_price,
                )
                return OrderResult(
                    order_id=order_id,
                    filled_qty=filled_qty,
                    filled_price=filled_price,
                    status="filled",
                )

            if order.status in (
                OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
            ):
                return OrderResult(
                    order_id=order_id,
                    status=order.status.value,
                    error=f"Order {order.status.value}",
                )

        logger.warning("Market order %s timed out after %.0fs", order_id, FILL_TIMEOUT)
        self.cancel_order(order_id)
        return OrderResult(
            order_id=order_id,
            status="cancelled",
            error=f"Fill timeout ({FILL_TIMEOUT:.0f}s)",
        )

    def _place_order(
        self, ticker: str, qty: int, limit_price: float, side: OrderSide
    ) -> OrderResult:
        """Place a limit order and wait for fill."""
        order_data = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
        )

        try:
            order = self.client.submit_order(order_data)
        except APIError as e:
            logger.error("Order submission failed for %s: %s", ticker, e)
            return OrderResult(
                status="failed",
                error=str(e),
            )

        order_id = str(order.id)
        logger.info(
            "%s order submitted: %s %d x %s @ $%.2f (id=%s)",
            side.value, ticker, qty, side.value, limit_price, order_id,
        )

        # Poll for fill
        elapsed = 0.0
        while elapsed < FILL_TIMEOUT:
            time.sleep(FILL_POLL_INTERVAL)
            elapsed += FILL_POLL_INTERVAL

            try:
                order = self.client.get_order_by_id(order_id)
            except APIError:
                continue

            if order.status == OrderStatus.FILLED:
                filled_price = float(order.filled_avg_price)
                filled_qty = float(order.filled_qty)
                logger.info(
                    "Order filled: %s %d x %s @ $%.2f",
                    ticker, int(filled_qty), side.value, filled_price,
                )
                return OrderResult(
                    order_id=order_id,
                    filled_qty=filled_qty,
                    filled_price=filled_price,
                    status="filled",
                )

            if order.status in (
                OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
            ):
                logger.warning("Order %s ended with status: %s", order_id, order.status)
                return OrderResult(
                    order_id=order_id,
                    status=order.status.value,
                    error=f"Order {order.status.value}",
                )

        # Timed out waiting for fill - cancel the order
        logger.warning("Order %s timed out after %.0fs, cancelling", order_id, FILL_TIMEOUT)
        self.cancel_order(order_id)
        return OrderResult(
            order_id=order_id,
            status="cancelled",
            error=f"Fill timeout ({FILL_TIMEOUT:.0f}s)",
        )
