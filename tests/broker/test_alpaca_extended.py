"""Tests for new AlpacaBroker capabilities (B1-B9)."""

from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType, TimeInForce

from src.broker.alpaca import AlpacaBroker
from src.broker.base import AccountConfig, AssetInfo, BrokerPosition, OrderResult


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def broker(mock_client):
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
    qty=10,
    submitted_at=None,
    filled_at=None,
):
    order = MagicMock()
    order.id = order_id
    order.status = status
    order.filled_avg_price = filled_avg_price
    order.filled_qty = filled_qty
    order.symbol = symbol
    order.order_type = order_type
    order.side = side
    order.qty = qty
    order.submitted_at = submitted_at
    order.filled_at = filled_at
    return order


# ---------------------------------------------------------------------------
# B1: Market orders
# ---------------------------------------------------------------------------

class TestMarketOrders:
    def test_buy_market_filled(self, broker, mock_client):
        submitted = _make_order(status=OrderStatus.NEW)
        filled = _make_order(
            status=OrderStatus.FILLED,
            filled_avg_price=150.50,
            filled_qty=10,
        )
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = filled

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy_market("AAPL", 10)

        assert result.status == "filled"
        assert result.filled_price == 150.50
        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.side == OrderSide.BUY
        assert not hasattr(call_args, "limit_price") or call_args.limit_price is None

    def test_sell_market_filled(self, broker, mock_client):
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
            result = broker.sell_market("AAPL", 10)

        assert result.status == "filled"
        assert result.filled_price == 155.00

    def test_buy_market_api_error(self, broker, mock_client):
        mock_client.submit_order.side_effect = APIError({"message": "no funds"})
        result = broker.buy_market("AAPL", 10)
        assert result.status == "failed"

    def test_market_order_timeout(self, broker, mock_client):
        submitted = _make_order(status=OrderStatus.NEW)
        pending = _make_order(status=OrderStatus.NEW)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = pending

        with patch("src.broker.alpaca.time.sleep"):
            with patch("src.broker.alpaca.FILL_TIMEOUT", 2.0):
                with patch("src.broker.alpaca.FILL_POLL_INTERVAL", 1.0):
                    result = broker.buy_market("AAPL", 10)

        assert result.status == "cancelled"

    def test_market_order_rejected(self, broker, mock_client):
        submitted = _make_order(status=OrderStatus.NEW)
        rejected = _make_order(status=OrderStatus.REJECTED)
        mock_client.submit_order.return_value = submitted
        mock_client.get_order_by_id.return_value = rejected

        with patch("src.broker.alpaca.time.sleep"):
            result = broker.buy_market("AAPL", 10)

        assert result.status == "rejected"


# ---------------------------------------------------------------------------
# B2: Trailing stop orders
# ---------------------------------------------------------------------------

class TestTrailingStopOrders:
    def test_trailing_stop_accepted(self, broker, mock_client):
        order = MagicMock()
        order.id = "trail-001"
        mock_client.submit_order.return_value = order

        result = broker.place_trailing_stop_order("AAPL", 10, 5.0)

        assert result.status == "accepted"
        assert result.order_id == "trail-001"
        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.side == OrderSide.SELL
        assert call_args.time_in_force == TimeInForce.GTC
        assert call_args.trail_percent == 5.0

    def test_trailing_stop_percent_rounded(self, broker, mock_client):
        order = MagicMock()
        order.id = "trail-002"
        mock_client.submit_order.return_value = order

        broker.place_trailing_stop_order("AAPL", 10, 5.6789)

        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.trail_percent == 5.68

    def test_trailing_stop_api_error(self, broker, mock_client):
        mock_client.submit_order.side_effect = APIError({"message": "invalid"})
        result = broker.place_trailing_stop_order("AAPL", 10, 5.0)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# B3: Bracket orders
# ---------------------------------------------------------------------------

class TestBracketOrders:
    def test_bracket_order_accepted(self, broker, mock_client):
        order = MagicMock()
        order.id = "bracket-001"
        mock_client.submit_order.return_value = order

        result = broker.place_bracket_order("AAPL", 10, 150.00, 140.00, 170.00)

        assert result.status == "accepted"
        assert result.order_id == "bracket-001"
        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.limit_price == 150.00

    def test_bracket_order_prices_rounded(self, broker, mock_client):
        order = MagicMock()
        order.id = "bracket-002"
        mock_client.submit_order.return_value = order

        broker.place_bracket_order("AAPL", 10, 150.1234, 139.5678, 170.9876)

        call_args = mock_client.submit_order.call_args[0][0]
        assert call_args.limit_price == 150.12

    def test_bracket_order_api_error(self, broker, mock_client):
        mock_client.submit_order.side_effect = APIError({"message": "bracket error"})
        result = broker.place_bracket_order("AAPL", 10, 150.00, 140.00, 170.00)
        assert result.status == "failed"
        assert "bracket error" in result.error


# ---------------------------------------------------------------------------
# B4: Get single position
# ---------------------------------------------------------------------------

class TestGetPosition:
    def test_position_found(self, broker, mock_client):
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.qty = "10"
        pos.avg_entry_price = "150.00"
        pos.market_value = "1550.00"
        pos.unrealized_pl = "50.00"
        mock_client.get_open_position.return_value = pos

        result = broker.get_position("AAPL")

        assert isinstance(result, BrokerPosition)
        assert result.ticker == "AAPL"
        assert result.qty == 10.0
        assert result.unrealized_pnl == 50.0

    def test_position_not_found(self, broker, mock_client):
        mock_client.get_open_position.side_effect = APIError(
            {"message": "position does not exist"}
        )
        result = broker.get_position("AAPL")
        assert result is None

    def test_position_404(self, broker, mock_client):
        mock_client.get_open_position.side_effect = APIError({"message": "404 Not Found"})
        result = broker.get_position("AAPL")
        assert result is None

    def test_position_other_api_error_raises(self, broker, mock_client):
        mock_client.get_open_position.side_effect = APIError({"message": "server error"})
        with pytest.raises(APIError):
            broker.get_position("AAPL")


# ---------------------------------------------------------------------------
# B5: Order history
# ---------------------------------------------------------------------------

class TestOrderHistory:
    def test_get_order_history(self, broker, mock_client):
        order = _make_order(
            order_id="hist-001",
            status=OrderStatus.FILLED,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            filled_avg_price=150.00,
            filled_qty=10,
            submitted_at=datetime(2026, 3, 24, 9, 30),
            filled_at=datetime(2026, 3, 24, 9, 31),
        )
        mock_client.get_orders.return_value = [order]

        result = broker.get_order_history(status="closed", limit=50)

        assert len(result) == 1
        assert result[0]["order_id"] == "hist-001"
        assert result[0]["filled_avg_price"] == 150.00
        assert result[0]["submitted_at"] is not None

    def test_get_order_history_empty(self, broker, mock_client):
        mock_client.get_orders.return_value = []
        result = broker.get_order_history()
        assert result == []

    def test_get_order_history_api_error(self, broker, mock_client):
        mock_client.get_orders.side_effect = APIError({"message": "error"})
        result = broker.get_order_history()
        assert result == []


# ---------------------------------------------------------------------------
# B6: Asset info
# ---------------------------------------------------------------------------

class TestGetAsset:
    def test_asset_found(self, broker, mock_client):
        asset = MagicMock()
        asset.symbol = "AAPL"
        asset.tradable = True
        asset.fractionable = True
        asset.shortable = True
        asset.asset_class = "us_equity"
        asset.exchange = "NASDAQ"
        asset.status = "active"
        mock_client.get_asset.return_value = asset

        result = broker.get_asset("AAPL")

        assert isinstance(result, AssetInfo)
        assert result.ticker == "AAPL"
        assert result.tradable is True
        assert result.fractionable is True
        assert result.shortable is True
        assert result.exchange == "NASDAQ"

    def test_asset_not_found(self, broker, mock_client):
        mock_client.get_asset.side_effect = APIError({"message": "not found"})
        result = broker.get_asset("ZZZZZ")
        assert result is None

    def test_asset_not_tradable(self, broker, mock_client):
        asset = MagicMock()
        asset.symbol = "DELIST"
        asset.tradable = False
        asset.fractionable = False
        asset.shortable = False
        asset.asset_class = "us_equity"
        asset.exchange = "OTC"
        asset.status = "inactive"
        mock_client.get_asset.return_value = asset

        result = broker.get_asset("DELIST")
        assert result.tradable is False


# ---------------------------------------------------------------------------
# B7: Account config
# ---------------------------------------------------------------------------

class TestAccountConfig:
    def test_get_account_config(self, broker, mock_client):
        cfg = MagicMock()
        cfg.pdt_check = "entry"
        cfg.trade_confirm_email = "all"
        cfg.no_shorting = False
        cfg.suspend_trade = False
        mock_client.get_account_configurations.return_value = cfg

        result = broker.get_account_config()

        assert isinstance(result, AccountConfig)
        assert result.pdt_check == "entry"
        assert result.no_shorting is False

    def test_get_account_config_api_error(self, broker, mock_client):
        mock_client.get_account_configurations.side_effect = APIError({"message": "error"})
        with pytest.raises(APIError):
            broker.get_account_config()


# ---------------------------------------------------------------------------
# B8: Close position
# ---------------------------------------------------------------------------

class TestClosePosition:
    def test_close_position_success(self, broker, mock_client):
        order = MagicMock()
        order.id = "close-001"
        mock_client.close_position.return_value = order

        result = broker.close_position("AAPL")

        assert result.status == "accepted"
        assert result.order_id == "close-001"
        mock_client.close_position.assert_called_once_with("AAPL")

    def test_close_position_api_error(self, broker, mock_client):
        mock_client.close_position.side_effect = APIError({"message": "no position"})
        result = broker.close_position("AAPL")
        assert result.status == "failed"
        assert "no position" in result.error


# ---------------------------------------------------------------------------
# B9: Close all positions
# ---------------------------------------------------------------------------

class TestCloseAllPositions:
    def test_close_all_success(self, broker, mock_client):
        resp1 = MagicMock()
        resp1.body = MagicMock()
        resp1.body.id = "close-all-1"
        resp2 = MagicMock()
        resp2.body = MagicMock()
        resp2.body.id = "close-all-2"
        mock_client.close_all_positions.return_value = [resp1, resp2]

        results = broker.close_all_positions()

        assert len(results) == 2
        assert results[0].order_id == "close-all-1"
        assert results[1].order_id == "close-all-2"
        mock_client.close_all_positions.assert_called_once_with(cancel_orders=True)

    def test_close_all_empty_portfolio(self, broker, mock_client):
        mock_client.close_all_positions.return_value = []
        results = broker.close_all_positions()
        assert results == []

    def test_close_all_api_error(self, broker, mock_client):
        mock_client.close_all_positions.side_effect = APIError({"message": "error"})
        results = broker.close_all_positions()
        assert len(results) == 1
        assert results[0].status == "failed"
