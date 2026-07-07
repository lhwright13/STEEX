"""B4: notional (fractional) entries for high-priced names.

Integer share rounding under-deployed expensive momentum winners (SNDK at
$1,858 got 1 share vs its intended ~$1,400 allocation). Notional buys deploy the
exact dollar amount; since a fractional qty can't carry a broker GTC stop, the
whole-share FLOOR gets the stop and the monitor covers the remainder.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.strategy.manager import QuantManager
from src.broker.base import OrderResult


def _mgr(tmp_path, broker):
    m = QuantManager.__new__(QuantManager)
    m.settings = SimpleNamespace(
        data_dir=str(tmp_path), messaging_enabled=False, server_stops_enabled=True,
        server_stop_offset_pct=0.005, buy_limit_buffer_pct=0.005,
    )
    m.broker = broker
    m.log = []
    m.report = {}
    m.execution_quality_tracker = None
    m.position_manager = MagicMock()
    m.trade_tracker = MagicMock()
    from src.strategy import control
    control.set_controls(str(tmp_path), trading_armed=True)
    return m


def _notional_entry(shares=2.31, notional=1400.0):
    return {"ticker": "SNDK", "price": 605.0, "shares": shares, "cost": notional,
            "notional": notional, "stop": 520.0, "score": 60.0, "size_pct": 2.6,
            "reasons": ["momentum"]}


def test_notional_entry_uses_buy_notional_and_floor_stop(tmp_path):
    broker = MagicMock()
    broker.buy_notional.return_value = OrderResult(
        status="filled", filled_price=605.0, filled_qty=2.31, order_id="n1")
    broker.place_stop_order.return_value = OrderResult(status="accepted", order_id="s1")
    mgr = _mgr(tmp_path, broker)

    executed = mgr.execute_entries([_notional_entry()], dry_run=False, auto_confirm=True, notify=False)

    assert len(executed) == 1
    # dollar order placed, NOT an integer limit buy
    broker.buy_notional.assert_called_once_with("SNDK", 1400.0)
    broker.buy.assert_not_called()
    # stop placed on the whole-share FLOOR (2), not the fractional 2.31
    broker.place_stop_order.assert_called_once()
    assert broker.place_stop_order.call_args[0][1] == 2
    # the position keeps the fractional qty
    assert mgr.position_manager.add_position.call_args.kwargs["shares"] == 2.31


def test_pure_fractional_entry_skips_broker_stop(tmp_path):
    """A sub-1-share fill can't carry a broker stop; the monitor covers it."""
    broker = MagicMock()
    broker.buy_notional.return_value = OrderResult(
        status="filled", filled_price=1858.0, filled_qty=0.75, order_id="n2")
    mgr = _mgr(tmp_path, broker)
    entry = _notional_entry(shares=0.75, notional=1400.0)
    entry["price"] = 1858.0

    executed = mgr.execute_entries([entry], dry_run=False, auto_confirm=True, notify=False)

    assert len(executed) == 1
    broker.place_stop_order.assert_not_called()  # floor(0.75)=0 -> no broker stop


def test_integer_entry_still_uses_limit_buy(tmp_path):
    """Non-notional entries keep the well-tested integer limit path."""
    broker = MagicMock()
    broker.buy.return_value = OrderResult(
        status="filled", filled_price=150.0, filled_qty=16, order_id="b1")
    broker.place_stop_order.return_value = OrderResult(status="accepted", order_id="s1")
    mgr = _mgr(tmp_path, broker)
    entry = {"ticker": "AAPL", "price": 150.0, "shares": 16, "cost": 2400.0,
             "notional": None, "stop": 135.0, "score": 60.0, "size_pct": 5.0,
             "reasons": ["x"]}

    mgr.execute_entries([entry], dry_run=False, auto_confirm=True, notify=False)

    broker.buy.assert_called_once()
    broker.buy_notional.assert_not_called()
    assert broker.place_stop_order.call_args[0][1] == 16
