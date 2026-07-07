"""Tests for AlpacaBroker — full mock coverage of the Alpaca TradingClient."""

from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType, TimeInForce

from src.broker.alpaca import AlpacaBroker, FILL_POLL_INTERVAL, FILL_TIMEOUT
from src.broker.base import AccountInfo, BrokerPosition, OrderResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """A MagicMock standing in for alpaca TradingClient."""
    return MagicMock()


@pytest.fixture
def broker(mock_client):
    """AlpacaBroker with a mocked TradingClient (bypasses __init__ credentials check)."""
    with patch("src.broker.alpaca.TradingClient", return_value=mock_client):
        b = AlpacaBroker(api_key="test-key", secret_key="test-secret", paper=True)
    return b


def _make_order(
    order_id="ord-123",
    status=OrderStatus.NEW,
    filled_avg_price=None,
    filled_qty=None,
    symbol="AAPL",
    order_type=OrderType.LIMIT,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
    qty=10,
    stop_price=None,
):
    """Create a mock order object mimicking Alpaca's Order model."""
    order = MagicMock()
    order.id = order_id
    order.status = status
    order.filled_avg_price = filled_avg_price
    order.filled_qty = filled_qty
    order.symbol = symbol
    order.order_type = order_type
    order.side = side
    order.time_in_force = time_in_force
    order.qty = qty
    order.stop_price = stop_price
    return order


# ---------------------------------------------------------------------------
# A1: buy() tests
# ---------------------------------------------------------------------------

class TestBuy:
    def test_buy_filled_immediately(self, broker, mock_client):
        """Order fills on first poll."""
        submitted = _make_order(status=OrderStatus.NEW)
        filled = _make_order(
            status=OrderStatus.FILLED,
            filled_avg_price=150.50,
            filled_qty=10,
        )
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy("AAPL", 10, 151.00)

        assert result.status == "filled"
        assert result.filled_price == 150.50
        assert result.filled_qty == 10
        assert result.order_id == "ord-123"

        # Verify order request params
        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.symbol == "AAPL"
        assert call_args.qty == 10
        assert call_args.side == OrderSide.BUY
        assert call_args.time_in_force == TimeInForce.DAY
        assert call_args.limit_price == 151.00

    def test_buy_fills_after_multiple_polls(self, broker, mock_client):
        """Order fills after several polling iterations."""
        submitted = _make_order(status=OrderStatus.NEW)
        pending = _make_order(status=OrderStatus.NEW)
        filled = _make_order(
            status=OrderStatus.FILLED,
            filled_avg_price=100.0,
            filled_qty=5,
        )
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.side_effect = [pending, pending, filled]

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy("MSFT", 5, 100.50)

        assert result.status == "filled"
        assert mock_client.get_order_by_id.call_count == 3

    def test_buy_timeout_cancels_order(self, broker, mock_client):
        """Order times out and gets cancelled."""
        submitted = _make_order(status=OrderStatus.NEW)
        pending = _make_order(status=OrderStatus.NEW)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = pending

        with patch("src.broker.alpaca.time.sleep"):
            with patch("src.broker.alpaca.FILL_TIMEOUT", 2.0):
                with patch("src.broker.alpaca.FILL_POLL_INTERVAL", 1.0):
                    result = broker.buy("AAPL", 10, 150.00)

        assert result.status == "cancelled"
        assert "timeout" in result.error.lower()
        mock_client.cancel_order_by_id.assert_called_once_with("ord-123")

    def test_buy_rejected(self, broker, mock_client):
        """Order gets rejected by Alpaca."""
        submitted = _make_order(status=OrderStatus.NEW)
        rejected = _make_order(status=OrderStatus.REJECTED)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = rejected

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy("AAPL", 10, 150.00)

        assert result.status == "rejected"
        assert result.error is not None

    def test_buy_submit_api_error(self, broker, mock_client):
        """API error during order submission returns failed result."""
        mock_client.submit_order.side_effect = APIError({"message": "insufficient funds"})

        result = broker.buy("AAPL", 10, 150.00)

        assert result.status == "failed"
        assert "insufficient funds" in result.error

    def test_buy_cancelled_by_exchange(self, broker, mock_client):
        """Order cancelled by exchange."""
        submitted = _make_order(status=OrderStatus.NEW)
        cancelled = _make_order(status=OrderStatus.CANCELED)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = cancelled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy("AAPL", 10, 150.00)

        assert result.status == "canceled"

    def test_buy_expired(self, broker, mock_client):
        """DAY order expires at market close."""
        submitted = _make_order(status=OrderStatus.NEW)
        expired = _make_order(status=OrderStatus.EXPIRED)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = expired

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy("AAPL", 10, 150.00)

        assert result.status == "expired"

    def test_buy_limit_price_rounded(self, broker, mock_client):
        """Limit price is rounded to 2 decimal places."""
        submitted = _make_order(status=OrderStatus.NEW)
        filled = _make_order(status=OrderStatus.FILLED, filled_avg_price=150.12, filled_qty=10)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            broker.buy("AAPL", 10, 150.1234567)

        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.limit_price == 150.12

    def test_buy_poll_api_error_retries(self, broker, mock_client):
        """APIError during polling is swallowed and polling continues."""
        submitted = _make_order(status=OrderStatus.NEW)
        filled = _make_order(status=OrderStatus.FILLED, filled_avg_price=100.0, filled_qty=5)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.side_effect = [
            APIError({"message": "transient"}),
            filled,
        ]

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy("AAPL", 5, 100.00)

        assert result.status == "filled"

    def test_buy_limit_escalates_to_market_when_stalled(self, broker, mock_client):
        """A stalled buy LIMIT is cancelled and re-submitted as a market order.

        Regression (07-06): sized buy limits use 15-min-delayed quotes and sat
        below a rising open, timing out with zero fills. Buys now escalate
        limit->market after BUY_LIMIT_FALLBACK_SECS like sells do.
        """
        limit_pending = _make_order(order_id="lim-1", status=OrderStatus.NEW)
        market_filled = _make_order(
            order_id="mkt-1", status=OrderStatus.FILLED,
            order_type=OrderType.MARKET, filled_avg_price=101.25, filled_qty=5,
        )
        # First submit = the limit order; second submit = the escalated market.
        mock_client.submit_order.side_effect = [limit_pending, market_filled]
        # Limit polls never fill (stays NEW); once escalated, the market poll fills.
        mock_client.get_order_by_id.side_effect = [
            limit_pending, limit_pending, market_filled,
        ]

        with patch("src.broker.alpaca.time.sleep"), \
             patch("src.broker.alpaca.FILL_POLL_INTERVAL", 1.0), \
             patch("src.broker.alpaca.BUY_LIMIT_FALLBACK_SECS", 2.0), \
             patch("src.broker.alpaca.FILL_TIMEOUT", 30.0):
            result = broker.buy("AAPL", 5, 100.00)

        assert result.status == "filled"
        assert result.filled_price == 101.25
        # the stalled limit was cancelled, then a market order submitted
        mock_client.cancel_order_by_id.assert_called_once_with("lim-1")
        assert mock_client.submit_order.call_count == 2
        assert mock_client.submit_order.call_args_list[1][0][0].type == OrderType.MARKET


    def test_buy_notional_submits_dollar_order_and_returns_fractional(self, broker, mock_client):
        """B4: notional buy submits a dollar-amount market order and returns
        the fractional filled qty (for exact-allocation deployment)."""
        submitted = _make_order(order_id="not-1", status=OrderStatus.NEW,
                                order_type=OrderType.MARKET)
        filled = _make_order(
            order_id="not-1", status=OrderStatus.FILLED, order_type=OrderType.MARKET,
            filled_avg_price=1858.0, filled_qty=0.7535,
        )
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy_notional("SNDK", 1400.0)

        assert result.status == "filled"
        assert result.filled_qty == 0.7535  # fractional preserved
        req = mock_client.submit_order.call_args[0][0]
        assert req.notional == 1400.0 and req.side == OrderSide.BUY


# ---------------------------------------------------------------------------
# A2: sell() tests
# ---------------------------------------------------------------------------

class TestSell:
    def test_sell_filled(self, broker, mock_client):
        submitted = _make_order(status=OrderStatus.NEW, side=OrderSide.SELL)
        filled = _make_order(
            status=OrderStatus.FILLED,
            side=OrderSide.SELL,
            filled_avg_price=155.00,
            filled_qty=10,
        )
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.sell("AAPL", 10, 154.50)

        assert result.status == "filled"
        assert result.filled_price == 155.00

        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.side == OrderSide.SELL

    def test_sell_submit_api_error(self, broker, mock_client):
        mock_client.submit_order.side_effect = APIError({"message": "no position"})
        result = broker.sell("AAPL", 10, 150.00)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# A3: place_stop_order() tests
# ---------------------------------------------------------------------------

def _mock_position_above_stop(mock_client, current_price: float) -> None:
    """Configure mock_client.get_open_position to return a position at current_price."""
    pos = MagicMock()
    pos.current_price = str(current_price)
    mock_client.get_open_position.return_value = pos


class TestPlaceStopOrder:
    def test_stop_order_accepted(self, broker, mock_client):
        _mock_position_above_stop(mock_client, 145.00)  # price above stop
        order = _make_order(order_id="stop-001", status=OrderStatus.ACCEPTED)
        mock_client.submit_order.return_value = order

        result = broker.place_stop_order("AAPL", 10, 135.00)

        assert result.status == "accepted"
        assert result.order_id == "stop-001"

        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.side == OrderSide.SELL
        assert call_args.time_in_force == TimeInForce.GTC
        assert call_args.stop_price == 135.00

    def test_stop_order_price_rounded(self, broker, mock_client):
        _mock_position_above_stop(mock_client, 145.00)
        order = _make_order(order_id="stop-002", status=OrderStatus.ACCEPTED)
        mock_client.submit_order.return_value = order

        broker.place_stop_order("AAPL", 10, 135.6789)

        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.stop_price == 135.68

    def test_stop_order_api_error(self, broker, mock_client):
        _mock_position_above_stop(mock_client, 145.00)
        mock_client.submit_order.side_effect = APIError({"message": "invalid stop"})

        result = broker.place_stop_order("AAPL", 10, 135.00)

        assert result.status == "failed"
        assert "invalid stop" in result.error

    def test_stop_order_does_not_poll(self, broker, mock_client):
        """Stop orders are fire-and-forget — no fill polling."""
        _mock_position_above_stop(mock_client, 145.00)
        order = _make_order(order_id="stop-003", status=OrderStatus.ACCEPTED)
        mock_client.submit_order.return_value = order

        broker.place_stop_order("AAPL", 10, 135.00)

        mock_client.get_order_by_id.assert_not_called()

    def test_stop_order_falls_back_to_market_when_price_below_stop(self, broker, mock_client):
        """When current price <= stop price, submit market sell immediately."""
        # Price has already fallen below the intended stop level
        _mock_position_above_stop(mock_client, 120.00)  # below stop of 135

        market_order = _make_order(order_id="mkt-001", status=OrderStatus.NEW)
        filled = _make_order(
            order_id="mkt-001", status=OrderStatus.FILLED,
            filled_avg_price=119.50, filled_qty=10,
        )
        mock_client.submit_order.return_value = market_order
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.place_stop_order("AAPL", 10, 135.00)

        assert result.status == "filled"
        call_args = mock_client.submit_order.call_args[0][0]
        # Should be a market order, not a stop order
        from alpaca.trading.requests import MarketOrderRequest
        assert isinstance(call_args, MarketOrderRequest)

    def test_stop_order_falls_back_to_market_on_rejection(self, broker, mock_client):
        """If Alpaca rejects stop due to price movement between check and submit, fall back."""
        _mock_position_above_stop(mock_client, 145.00)  # price appeared OK

        market_order = _make_order(order_id="mkt-002", status=OrderStatus.NEW)
        filled = _make_order(
            order_id="mkt-002", status=OrderStatus.FILLED,
            filled_avg_price=134.50, filled_qty=10,
        )
        # First submit (stop order) is rejected, second (market) succeeds
        mock_client.submit_order.side_effect = [
            APIError({"message": "stop price must be below current price"}),
            market_order,
        ]
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.place_stop_order("AAPL", 10, 135.00)

        assert result.status == "filled"


# ---------------------------------------------------------------------------
# A4: update_stop_order() tests
# ---------------------------------------------------------------------------

class TestUpdateStopOrder:
    def test_existing_fixed_stop_uses_atomic_replace(self, broker, mock_client):
        """An existing fixed STOP is moved via Alpaca's native replace — never
        cancelled — so the position is never left unprotected (H1)."""
        existing_stop = _make_order(
            order_id="old-stop",
            order_type=OrderType.STOP,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=130.00,
        )
        replaced = _make_order(order_id="replaced-stop", status=OrderStatus.ACCEPTED)
        mock_client.get_orders.return_value = [existing_stop]
        mock_client.replace_order_by_id.return_value = replaced

        result = broker.update_stop_order("AAPL", 10, 140.00)

        assert result.order_id == "replaced-stop"
        # The race-prone cancel-then-place path must NOT be used here.
        mock_client.cancel_order_by_id.assert_not_called()
        mock_client.submit_order.assert_not_called()
        called_id = mock_client.replace_order_by_id.call_args.args[0]
        assert called_id == "old-stop"

    def test_idempotent_when_stop_already_at_target(self, broker, mock_client):
        """If the live fixed stop already equals the target, do nothing."""
        existing_stop = _make_order(
            order_id="same-stop",
            order_type=OrderType.STOP,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=140.00,
        )
        mock_client.get_orders.return_value = [existing_stop]

        result = broker.update_stop_order("AAPL", 10, 140.00)

        assert result.order_id == "same-stop"
        mock_client.replace_order_by_id.assert_not_called()
        mock_client.cancel_order_by_id.assert_not_called()
        mock_client.submit_order.assert_not_called()

    def test_replace_failure_falls_back_to_cancel_then_place(self, broker, mock_client):
        """If the atomic replace errors, fall back to cancel-wait-place."""
        from alpaca.common.exceptions import APIError
        _mock_position_above_stop(mock_client, 150.00)
        existing_stop = _make_order(
            order_id="old-stop",
            order_type=OrderType.STOP,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=130.00,
        )
        new_stop = _make_order(order_id="new-stop", status=OrderStatus.ACCEPTED)
        # get_orders sequence: (1) get_stop_order finds it, (2) cancel-and-wait
        # initial query still sees it -> cancels, (3) poll sees it cleared.
        mock_client.get_orders.side_effect = [[existing_stop], [existing_stop], []]
        mock_client.replace_order_by_id.side_effect = APIError("cannot replace")
        mock_client.cancel_order_by_id.return_value = None
        mock_client.submit_order.return_value = new_stop

        result = broker.update_stop_order("AAPL", 10, 140.00)

        assert result.order_id == "new-stop"
        mock_client.cancel_order_by_id.assert_called_once_with("old-stop")

    def test_update_when_no_existing_stop(self, broker, mock_client):
        """If no existing stop, just places a new one (no cancel, no replace)."""
        _mock_position_above_stop(mock_client, 150.00)  # price above new stop of 140
        mock_client.get_orders.return_value = []  # no stops found
        new_stop = _make_order(order_id="new-stop", status=OrderStatus.ACCEPTED)
        mock_client.submit_order.return_value = new_stop

        result = broker.update_stop_order("AAPL", 10, 140.00)

        assert result.order_id == "new-stop"
        mock_client.cancel_order_by_id.assert_not_called()
        mock_client.replace_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# A5: get_stop_order() / get_all_stop_orders() tests
# ---------------------------------------------------------------------------

class TestGetStopOrders:
    def test_get_stop_order_found(self, broker, mock_client):
        stop = _make_order(
            order_id="stop-100",
            order_type=OrderType.STOP,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            symbol="AAPL",
            qty=10,
            stop_price=130.00,
            status=OrderStatus.ACCEPTED,
        )
        stop.status = MagicMock()
        stop.status.value = "accepted"
        mock_client.get_orders.return_value = [stop]

        result = broker.get_stop_order("AAPL")

        assert result is not None
        assert result["order_id"] == "stop-100"
        assert result["ticker"] == "AAPL"
        assert result["stop_price"] == 130.00
        assert result["qty"] == 10

    def test_get_stop_order_not_found(self, broker, mock_client):
        mock_client.get_orders.return_value = []
        result = broker.get_stop_order("AAPL")
        assert result is None

    def test_get_stop_order_ignores_limit_orders(self, broker, mock_client):
        """Only GTC STOP SELL orders match."""
        limit_order = _make_order(
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        mock_client.get_orders.return_value = [limit_order]

        result = broker.get_stop_order("AAPL")
        assert result is None

    def test_get_stop_order_ignores_buy_stops(self, broker, mock_client):
        """A stop-buy is not our safety net stop."""
        buy_stop = _make_order(
            order_type=OrderType.STOP,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
        )
        mock_client.get_orders.return_value = [buy_stop]

        result = broker.get_stop_order("AAPL")
        assert result is None

    def test_get_stop_order_api_error(self, broker, mock_client):
        mock_client.get_orders.side_effect = APIError({"message": "server error"})
        result = broker.get_stop_order("AAPL")
        assert result is None

    def test_get_all_stop_orders(self, broker, mock_client):
        stop1 = _make_order(
            order_id="s1", order_type=OrderType.STOP, side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC, symbol="AAPL", qty=10,
            stop_price=130.0, status=OrderStatus.ACCEPTED,
        )
        stop1.status = MagicMock()
        stop1.status.value = "accepted"

        stop2 = _make_order(
            order_id="s2", order_type=OrderType.STOP, side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC, symbol="MSFT", qty=5,
            stop_price=280.0, status=OrderStatus.ACCEPTED,
        )
        stop2.status = MagicMock()
        stop2.status.value = "accepted"

        # Also include a non-stop order that should be filtered out
        limit = _make_order(order_type=OrderType.LIMIT, side=OrderSide.BUY)
        mock_client.get_orders.return_value = [stop1, limit, stop2]

        result = broker.get_all_stop_orders()

        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"
        assert result[1]["ticker"] == "MSFT"

    def test_get_all_stop_orders_api_error(self, broker, mock_client):
        mock_client.get_orders.side_effect = APIError({"message": "error"})
        result = broker.get_all_stop_orders()
        assert result == []


# ---------------------------------------------------------------------------
# A6: get_account() / get_positions() tests
# ---------------------------------------------------------------------------

class TestAccountAndPositions:
    def test_get_account(self, broker, mock_client):
        acct = MagicMock()
        acct.buying_power = "50000.00"
        acct.equity = "100000.00"
        acct.cash = "50000.00"
        mock_client.get_account.return_value = acct

        result = broker.get_account()

        assert isinstance(result, AccountInfo)
        assert result.buying_power == 50000.00
        assert result.equity == 100000.00
        assert result.cash == 50000.00

    def test_get_account_api_error(self, broker, mock_client):
        mock_client.get_account.side_effect = APIError({"message": "auth failed"})
        with pytest.raises(APIError):
            broker.get_account()

    def test_get_positions(self, broker, mock_client):
        pos1 = MagicMock()
        pos1.symbol = "AAPL"
        pos1.qty = "10"
        pos1.avg_entry_price = "150.00"
        pos1.market_value = "1550.00"
        pos1.unrealized_pl = "50.00"

        pos2 = MagicMock()
        pos2.symbol = "MSFT"
        pos2.qty = "5"
        pos2.avg_entry_price = "300.00"
        pos2.market_value = "1525.00"
        pos2.unrealized_pl = "25.00"

        mock_client.get_all_positions.return_value = [pos1, pos2]

        result = broker.get_positions()

        assert len(result) == 2
        assert isinstance(result[0], BrokerPosition)
        assert result[0].ticker == "AAPL"
        assert result[0].qty == 10.0
        assert result[0].avg_price == 150.00
        assert result[0].unrealized_pnl == 50.00
        assert result[1].ticker == "MSFT"

    def test_get_positions_empty(self, broker, mock_client):
        mock_client.get_all_positions.return_value = []
        result = broker.get_positions()
        assert result == []

    def test_get_positions_api_error(self, broker, mock_client):
        mock_client.get_all_positions.side_effect = APIError({"message": "error"})
        with pytest.raises(APIError):
            broker.get_positions()


# ---------------------------------------------------------------------------
# A7: cancel_order() tests
# ---------------------------------------------------------------------------

class TestCancelOrder:
    def test_cancel_success(self, broker, mock_client):
        mock_client.cancel_order_by_id.return_value = None
        assert broker.cancel_order("ord-123") is True

    def test_cancel_api_error(self, broker, mock_client):
        mock_client.cancel_order_by_id.side_effect = APIError({"message": "not found"})
        assert broker.cancel_order("ord-999") is False


# ---------------------------------------------------------------------------
# A8: get_clock() / get_calendar() tests
# ---------------------------------------------------------------------------

class TestClockAndCalendar:
    def test_get_clock(self, broker, mock_client):
        clock = MagicMock()
        clock.is_open = True
        clock.next_open = datetime(2026, 3, 24, 9, 30)
        clock.next_close = datetime(2026, 3, 24, 16, 0)
        mock_client.get_clock.return_value = clock

        result = broker.get_clock()

        assert result["is_open"] is True
        assert "2026-03-24" in result["next_open"]
        assert "2026-03-24" in result["next_close"]

    def test_get_clock_next_open_none(self, broker, mock_client):
        clock = MagicMock()
        clock.is_open = False
        clock.next_open = None
        clock.next_close = None
        mock_client.get_clock.return_value = clock

        result = broker.get_clock()

        assert result["is_open"] is False
        assert result["next_open"] is None
        assert result["next_close"] is None

    def test_get_clock_api_error(self, broker, mock_client):
        mock_client.get_clock.side_effect = APIError({"message": "error"})
        with pytest.raises(APIError):
            broker.get_clock()

    def test_get_calendar(self, broker, mock_client):
        entry = MagicMock()
        entry.date = "2026-03-24"
        entry.open = "09:30"
        entry.close = "16:00"
        mock_client.get_calendar.return_value = [entry]

        result = broker.get_calendar("2026-03-24", "2026-03-24")

        assert len(result) == 1
        assert result[0]["date"] == "2026-03-24"
        assert result[0]["open"] == "09:30"
        assert result[0]["close"] == "16:00"

    def test_get_calendar_empty(self, broker, mock_client):
        mock_client.get_calendar.return_value = []
        result = broker.get_calendar("2026-12-25", "2026-12-25")
        assert result == []

    def test_get_calendar_api_error(self, broker, mock_client):
        mock_client.get_calendar.side_effect = APIError({"message": "error"})
        result = broker.get_calendar("2026-03-24", "2026-03-24")
        assert result == []


# ---------------------------------------------------------------------------
# cancel_stop_for_ticker() tests
# ---------------------------------------------------------------------------

class TestCancelStopForTicker:
    def test_cancels_existing_stop(self, broker, mock_client):
        stop = _make_order(
            order_id="stop-to-cancel",
            order_type=OrderType.STOP,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=130.00,
        )
        stop.status = MagicMock()
        stop.status.value = "accepted"
        mock_client.get_orders.return_value = [stop]

        result = broker.cancel_stop_for_ticker("AAPL")

        assert result is True
        mock_client.cancel_order_by_id.assert_called_once_with("stop-to-cancel")

    def test_no_stop_to_cancel(self, broker, mock_client):
        mock_client.get_orders.return_value = []
        result = broker.cancel_stop_for_ticker("AAPL")
        assert result is True
        mock_client.cancel_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_missing_credentials_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="Alpaca credentials required"):
                AlpacaBroker(api_key="", secret_key="")

    def test_paper_mode_default(self):
        with patch("src.broker.alpaca.TradingClient") as mock_tc:
            AlpacaBroker(api_key="k", secret_key="s")
            mock_tc.assert_called_once_with(api_key="k", secret_key="s", paper=True)

    def test_live_mode(self):
        with patch("src.broker.alpaca.TradingClient") as mock_tc:
            AlpacaBroker(api_key="k", secret_key="s", paper=False)
            mock_tc.assert_called_once_with(api_key="k", secret_key="s", paper=False)
