"""Exit-path safety regressions (07-02 / 07-06 incident).

execute_exits cancels a position's server-side stop BEFORE the managed sell.
If that sell then fails (e.g. a fill timeout), the position must not be left
unprotected — the stop is re-placed. Also: near-close, deferred exit urgencies
(end_of_day / next_session) escalate to immediate so they fill while the market
is still open, in BOTH the deterministic and agent-mode paths (the escalation
lives inside execute_exits).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.strategy.manager import QuantManager
from src.broker.base import OrderResult


def _mgr(tmp_path, broker, *, closes_soon):
    m = QuantManager.__new__(QuantManager)
    m.settings = SimpleNamespace(
        data_dir=str(tmp_path), messaging_enabled=False, server_stops_enabled=True,
    )
    m.broker = broker
    m.log = []
    m.execution_quality_tracker = None
    m.position_manager = MagicMock()
    m.trade_tracker = MagicMock()
    # Force the near-close decision deterministically.
    m._market_closes_within = lambda minutes: closes_soon
    return m


def _position(ticker="MAR", shares=7, stop=345.61):
    p = MagicMock()
    p.ticker, p.shares, p.current_stop = ticker, shares, stop
    p.entry_price, p.entry_datetime, p.score, p.reasons = 375.05, None, 60.0, []
    return p


def test_failed_sell_restores_the_stop(tmp_path):
    """A failed managed sell must re-place the stop it cancelled."""
    broker = MagicMock()
    broker.sell_market.return_value = OrderResult(status="cancelled", error="Fill timeout (300s)")
    broker.place_stop_order.return_value = OrderResult(order_id="stop-new", status="accepted")
    pos = _position()
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = pos

    sell_list = [{"ticker": "MAR", "urgency": "immediate", "price": 372.0,
                  "shares": 7, "pnl_dollars": -20.0, "pnl_pct": -0.9, "reason": "below_ma"}]
    executed = mgr.execute_exits(sell_list, dry_run=False, notify=False)

    assert executed == []  # sell didn't fill
    broker.cancel_stop_for_ticker.assert_called_once_with("MAR")
    # the cancelled stop was RE-PLACED at the same level (never left unprotected)
    broker.place_stop_order.assert_called_once_with("MAR", 7, 345.61)


def test_near_close_escalates_deferred_urgencies(tmp_path):
    """end_of_day / next_session become immediate inside the last ~50 min so
    they fill while the market is open (covers the agent-mode MCP path)."""
    broker = MagicMock()
    broker.sell_market.return_value = OrderResult(
        status="filled", filled_price=372.0, filled_qty=7, order_id="s1")
    pos = _position()
    mgr = _mgr(tmp_path, broker, closes_soon=True)
    mgr.position_manager.get_position.return_value = pos

    sell_list = [{"ticker": "IRM", "urgency": "end_of_day", "price": 116.0, "shares": 31,
                  "pnl_dollars": -380.0, "pnl_pct": -9.6, "reason": "below_ma"},
                 {"ticker": "MAR", "urgency": "next_session", "price": 372.0, "shares": 7,
                  "pnl_dollars": -20.0, "pnl_pct": -0.9, "reason": "dead_money"}]
    mgr.execute_exits(sell_list, dry_run=False, notify=False)

    # both deferred exits escalated -> a real market sell was attempted for each
    assert broker.sell_market.call_count == 2


def test_no_escalation_when_not_near_close(tmp_path):
    """Away from the close, a deferred exit stays a recommendation (no sell)."""
    broker = MagicMock()
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = _position()

    sell_list = [{"ticker": "IRM", "urgency": "end_of_day", "price": 116.0, "shares": 31,
                  "pnl_dollars": -380.0, "pnl_pct": -9.6, "reason": "below_ma"}]
    mgr.execute_exits(sell_list, dry_run=False, notify=False)

    broker.sell_market.assert_not_called()
