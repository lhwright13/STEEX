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
from src.broker.base import BrokerPosition, OrderResult


def _broker(qty=7.0):
    """Broker mock satisfying the new pre-sell invariants: a live long
    position of `qty` and a stop that clears immediately after cancel."""
    b = MagicMock()
    b.get_position.return_value = BrokerPosition(ticker="X", qty=qty, avg_price=100.0)
    b.get_stop_order.return_value = None  # stop already cleared
    return b


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
    broker = _broker(qty=7.0)
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
    broker = _broker(qty=72.0)  # enough for both tickers' qtys
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
    broker = _broker()
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = _position()

    sell_list = [{"ticker": "IRM", "urgency": "end_of_day", "price": 116.0, "shares": 31,
                  "pnl_dollars": -380.0, "pnl_pct": -9.6, "reason": "below_ma"}]
    mgr.execute_exits(sell_list, dry_run=False, notify=False)

    broker.sell_market.assert_not_called()


# --- 07-07 incident invariants: never double-sell, never sell held shares ----

def _immediate_item(ticker="MAR", shares=7):
    return {"ticker": ticker, "urgency": "immediate", "price": 372.0, "shares": shares,
            "pnl_dollars": -20.0, "pnl_pct": -0.9, "reason": "stop_loss"}


def test_position_gone_at_broker_skips_managed_sell(tmp_path):
    """If the server stop already exited the position (gap-down open), the
    managed sell must NOT fire — selling the cached qty again made the book
    go short (-9 JBL, 07-07)."""
    broker = _broker()
    broker.get_position.return_value = None  # already exited at broker
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = _position()

    executed = mgr.execute_exits([_immediate_item()], dry_run=False, notify=False)

    assert executed == []
    broker.sell_market.assert_not_called()
    mgr.trade_tracker.record_trade.assert_not_called()  # no fabricated trade


def test_short_position_at_broker_skips_managed_sell(tmp_path):
    """qty <= 0 at the broker (already short) must never be sold into."""
    broker = _broker(qty=-9.0)
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = _position()

    mgr.execute_exits([_immediate_item()], dry_run=False, notify=False)
    broker.sell_market.assert_not_called()


def test_sell_qty_capped_at_broker_qty(tmp_path):
    """Local cache says 7 shares but broker holds only 4 -> sell 4, record 4."""
    broker = _broker(qty=4.0)
    broker.sell_market.return_value = OrderResult(
        status="filled", filled_price=370.0, filled_qty=4, order_id="s2")
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = _position(shares=7)

    mgr.execute_exits([_immediate_item(shares=7)], dry_run=False, notify=False)

    broker.sell_market.assert_called_once_with("MAR", 4.0)
    assert mgr.trade_tracker.record_trade.call_args.kwargs["shares"] == 4.0


def test_unconfirmed_stop_cancel_defers_sell(tmp_path, monkeypatch):
    """If the GTC stop won't clear, the market sell is deferred — selling while
    the stop holds the shares is why liquid names 'timed out' (MNST 07-07)."""
    monkeypatch.setattr("src.strategy.manager.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "src.strategy.manager.time.time",
        iter(__import__("itertools").count(0, 3.0)).__next__,  # fast-forward clock
    )
    broker = _broker()
    broker.get_stop_order.return_value = {"order_id": "stuck", "stop_price": 345.0}
    mgr = _mgr(tmp_path, broker, closes_soon=False)
    mgr.position_manager.get_position.return_value = _position()

    mgr.execute_exits([_immediate_item()], dry_run=False, notify=False)
    broker.sell_market.assert_not_called()
