"""Broker-race integration tests (Phase 6 WP1).

Every July incident was a distributed race — Alpaca's async server state versus
a cron one-shot's linear view of the world — and the mocked unit tests kept
passing straight through them because a mock never contradicts itself between
two calls. These tests drive a REAL :class:`~src.strategy.manager.QuantManager`
(real :class:`PositionManager` + :class:`TradeTracker`, backed by tmp JSON)
against :class:`tests.integration.fake_broker.FakeBroker`, which *does*
contradict itself the way the broker does.

Scenarios (mapping to WP1 Done-when):
  (a) stop fills DURING a managed exit → no double sell, no phantom trade.
  (b) transient empty ``get_positions()`` read → local book untouched.
  (c) async cancel race (stop stays open) → managed sell deferred, stop kept.
  (d) rename-style vanish (position gone, NO filled sell) → no trade recorded.
Plus reconciliation of a genuine server-side stop fill and the happy-path exit
as controls that the harness itself isn't rigged to always pass.
"""

from types import SimpleNamespace

from src.broker.base import BrokerPosition
from src.portfolio.positions import PositionManager
from src.portfolio.tracker import TradeTracker
from src.strategy.manager import QuantManager
from tests.integration.fake_broker import FakeBroker


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------

def _settings(tmp_path):
    """Minimal settings surface the exercised manager paths touch."""
    return SimpleNamespace(
        data_dir=str(tmp_path),
        positions_file="positions.json",
        trades_file="trades.json",
        initial_stop_pct=0.14,
        trailing_stops={0.10: 0.05, 0.20: 0.10, 0.30: 0.15},
        server_stops_enabled=True,
        messaging_enabled=False,
        execution_quality_enabled=False,
    )


def _manager(tmp_path, broker, *, closes_soon=False):
    """A QuantManager wired to REAL position/trade stores + the fake broker.

    Bypasses __init__ (which would try to build an Alpaca client and a pile of
    unrelated collaborators) and injects only what the race paths use.
    """
    settings = _settings(tmp_path)
    m = QuantManager.__new__(QuantManager)
    m.settings = settings
    m.broker = broker
    m.position_manager = PositionManager(settings, tmp_path / "positions.json")
    m.trade_tracker = TradeTracker(settings, tmp_path / "trades.json")
    m.execution_quality_tracker = None
    m.log = []
    m.report = {}
    m._account = None
    m._market_closes_within = lambda minutes: closes_soon
    return m


def _seed_local(mgr, ticker, *, shares, entry, stop, high=None):
    """Seed a local position (what the last run believed we held)."""
    pos = mgr.position_manager.add_position(
        ticker=ticker, entry_price=entry, shares=shares,
        score=60.0, reasons=["seed"],
    )
    pos.current_stop = stop
    pos.high_since_entry = high if high is not None else entry
    mgr.position_manager._save()
    return pos


def _sell_item(ticker, *, price, shares, urgency="immediate", reason="stop_loss"):
    return {
        "ticker": ticker, "urgency": urgency, "price": price, "shares": shares,
        "pnl_dollars": 0.0, "pnl_pct": 0.0, "reason": reason,
    }


# ---------------------------------------------------------------------------
# (a) stop fills DURING a managed exit
# ---------------------------------------------------------------------------

def test_stop_fill_during_managed_exit_no_double_sell(tmp_path):
    """The verify-at-broker guard: if a server stop already exited the name,
    execute_exits must NOT fire a second sell (which went short on 07-07)."""
    broker = FakeBroker([BrokerPosition("JBL", 9.0, 100.0)])
    broker.place_stop_order("JBL", 9, 86.0)
    mgr = _manager(tmp_path, broker)
    _seed_local(mgr, "JBL", shares=9, entry=100.0, stop=86.0)

    # The server-side stop fills between the sell-list build and execution.
    broker.vanish_position("JBL", filled_sell_price=86.0)

    executed = mgr.execute_exits(
        [_sell_item("JBL", price=95.0, shares=9)], dry_run=False, notify=False
    )

    assert executed == []  # no managed sell fired
    assert broker.calls.get("sell_market", 0) == 0  # never attempted a 2nd sell
    # No trade fabricated by execute_exits; the local book still shows it
    # (reconciliation happens in _sync_broker, not here).
    assert mgr.trade_tracker.get_all_trades() == []


# ---------------------------------------------------------------------------
# (b) transient empty read
# ---------------------------------------------------------------------------

def test_empty_read_leaves_book_untouched(tmp_path):
    """A single empty get_positions() (API blip) must not wipe the book or
    fabricate a full slate of phantom exits."""
    broker = FakeBroker([
        BrokerPosition("AAA", 10.0, 50.0),
        BrokerPosition("BBB", 5.0, 200.0),
    ])
    mgr = _manager(tmp_path, broker)
    _seed_local(mgr, "AAA", shares=10, entry=50.0, stop=43.0)
    _seed_local(mgr, "BBB", shares=5, entry=200.0, stop=172.0)

    broker.script_empty_reads(1)
    mgr._sync_broker()

    held = {p.ticker for p in mgr.position_manager.get_all_positions()}
    assert held == {"AAA", "BBB"}  # nothing removed
    assert mgr.trade_tracker.get_all_trades() == []  # no phantom exits


def test_empty_read_then_healthy_read_reconciles(tmp_path):
    """After the blip clears, a genuine removal still reconciles on the next
    healthy sync — the guard defers, it doesn't blind us permanently."""
    # A survivor stays held so the post-removal read is non-empty — otherwise
    # a genuine removal is indistinguishable from a transient empty read (the
    # empty-read guard would, correctly, defer it forever).
    broker = FakeBroker([
        BrokerPosition("AAA", 10.0, 50.0),
        BrokerPosition("KEEP", 3.0, 20.0),
    ])
    mgr = _manager(tmp_path, broker)
    _seed_local(mgr, "AAA", shares=10, entry=50.0, stop=43.0)
    _seed_local(mgr, "KEEP", shares=3, entry=20.0, stop=17.2)

    broker.script_empty_reads(1)
    mgr._sync_broker()
    assert {p.ticker for p in mgr.position_manager.get_all_positions()} == {"AAA", "KEEP"}

    # Now a real stop fill removes AAA, WITH a filled sell order backing it.
    broker.vanish_position("AAA", filled_sell_price=43.0)
    mgr._sync_broker()

    assert {p.ticker for p in mgr.position_manager.get_all_positions()} == {"KEEP"}
    trades = mgr.trade_tracker.get_all_trades()
    assert len(trades) == 1
    assert trades[0].ticker == "AAA"
    assert trades[0].exit_price == 43.0


# ---------------------------------------------------------------------------
# (c) async cancel race
# ---------------------------------------------------------------------------

def test_cancel_race_defers_sell_and_keeps_stop(tmp_path):
    """If the stop-cancel hasn't cleared at the broker, selling would race the
    stop (double-fill risk). The manager must defer the sell; the stop stays
    live so the position is never left unprotected."""
    broker = FakeBroker([BrokerPosition("MAR", 7.0, 375.0)])
    broker.place_stop_order("MAR", 7, 345.0)
    # Stop stays visible for more polls than the 5s / 0.25s ≈ 20-iter budget
    # will ever exhaust in a fast test — set it high so the loop times out.
    broker.script_stop_clear_lag("MAR", 999)
    mgr = _manager(tmp_path, broker)
    _seed_local(mgr, "MAR", shares=7, entry=375.0, stop=345.0)

    executed = mgr.execute_exits(
        [_sell_item("MAR", price=372.0, shares=7)], dry_run=False, notify=False
    )

    assert executed == []  # sell deferred
    assert broker.calls.get("sell_market", 0) == 0  # never sold under the stop
    assert broker.get_stop_order("MAR") is not None  # stop still protecting us
    assert mgr.trade_tracker.get_all_trades() == []


def test_cancel_clears_after_a_few_polls_then_sells(tmp_path):
    """Control for (c): once the async cancel actually clears, the sell fires
    and books exactly one trade."""
    broker = FakeBroker([BrokerPosition("MAR", 7.0, 375.0)])
    broker.place_stop_order("MAR", 7, 345.0)
    broker.script_stop_clear_lag("MAR", 2)  # clears on the 2nd poll
    mgr = _manager(tmp_path, broker)
    _seed_local(mgr, "MAR", shares=7, entry=375.0, stop=345.0)

    executed = mgr.execute_exits(
        [_sell_item("MAR", price=372.0, shares=7)], dry_run=False, notify=False
    )

    assert [i["ticker"] for i in executed] == ["MAR"]
    assert broker.calls.get("sell_market", 0) == 1
    trades = mgr.trade_tracker.get_all_trades()
    assert len(trades) == 1 and trades[0].shares == 7
    assert mgr.position_manager.get_position("MAR") is None


# ---------------------------------------------------------------------------
# (d) rename-style vanish (no sell order)
# ---------------------------------------------------------------------------

def test_rename_vanish_records_no_trade(tmp_path):
    """A position that disappears with NO filled sell order (ticker rename like
    BK->BNY, or a transient half-read) must NOT fabricate an exit with a made-up
    price. The sync integrity guard drops it for manual review."""
    broker = FakeBroker([
        BrokerPosition("BK", 20.0, 45.0),
        BrokerPosition("KEEP", 3.0, 20.0),
    ])
    mgr = _manager(tmp_path, broker)
    _seed_local(mgr, "BK", shares=20, entry=45.0, stop=38.7)
    _seed_local(mgr, "KEEP", shares=3, entry=20.0, stop=17.2)

    # Vanishes with NO sell order in history (survivor keeps the read non-empty).
    broker.vanish_position("BK", filled_sell_price=None)
    mgr._sync_broker()

    # Removed from the local book (broker is truth for what we hold) ...
    assert mgr.position_manager.get_position("BK") is None
    # ... but NO trade fabricated, and it's flagged for review in the log.
    assert mgr.trade_tracker.get_all_trades() == []
    assert any(
        "INTEGRITY" in e["detail"] and "BK" in e["detail"]
        for e in mgr.log if e["action"] == "sync"
    )


# ---------------------------------------------------------------------------
# reconciliation control: genuine server-side stop fill IS recorded
# ---------------------------------------------------------------------------

def test_server_stop_fill_records_one_order_backed_trade(tmp_path):
    """A real server-side stop fill (position gone + a matching FILLED sell in
    order history) is reconciled into exactly one trade at the REAL fill price,
    with the stop tier reconstructed from the high-water mark."""
    broker = FakeBroker([
        BrokerPosition("MNST", 12.0, 60.0),
        BrokerPosition("KEEP", 3.0, 20.0),
    ])
    mgr = _manager(tmp_path, broker)
    # Peaked +22% before reversing → should classify as trail_20.
    _seed_local(mgr, "MNST", shares=12, entry=60.0, stop=66.0, high=73.2)
    _seed_local(mgr, "KEEP", shares=3, entry=20.0, stop=17.2)

    broker.vanish_position("MNST", filled_sell_price=66.5)
    mgr._sync_broker()

    trades = mgr.trade_tracker.get_all_trades()
    assert len(trades) == 1
    t = trades[0]
    assert t.ticker == "MNST"
    assert t.exit_price == 66.5  # the REAL fill price, not a guessed one
    assert t.shares == 12
    assert t.exit_reason == "trail_20"
    assert mgr.position_manager.get_position("MNST") is None


def test_short_position_at_broker_not_adopted(tmp_path):
    """A negative-qty broker row (account went SHORT via a double-sell) must
    never be adopted as a local position — long-only book, flagged instead."""
    broker = FakeBroker([BrokerPosition("JBL", -9.0, 100.0)])
    mgr = _manager(tmp_path, broker)

    result = mgr.position_manager.sync_from_broker(broker)

    assert "JBL" not in [p.ticker for p in mgr.position_manager.get_all_positions()]
    assert result["added"] == []
