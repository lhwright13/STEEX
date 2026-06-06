"""P1-2: scheduled buy/sell fills emit a user_update + notification.

QuantManager.execute_entries/execute_exits are the universal fill funnel; this
pins that a real fill records a user_update (the dashboard + Telegram source) and
that the notification path can never raise into the trading run.
"""
from types import SimpleNamespace

from src.strategy.manager import QuantManager
from src.notify import user_updates


def _mgr(tmp_path, **settings):
    # Bypass the heavy broker/data __init__; _notify_fill only needs settings.
    m = QuantManager.__new__(QuantManager)
    m.settings = SimpleNamespace(data_dir=str(tmp_path), messaging_enabled=False, **settings)
    return m


def test_buy_fill_records_user_update(tmp_path):
    _mgr(tmp_path)._notify_fill(
        "buy", ticker="AAPL", shares=10, price=150.0, stop=140.0,
        reasons=["momentum breakout"],
    )
    ups = user_updates.read_updates(str(tmp_path))
    assert len(ups) == 1
    u = ups[0]
    assert u.type == "buy" and u.title == "Bought AAPL"
    assert "10 shares of AAPL" in u.summary and "$150.00" in u.summary
    assert "momentum breakout" in u.summary
    assert u.payload["ticker"] == "AAPL" and u.payload["stop"] == 140.0


def test_sell_fill_records_pnl(tmp_path):
    _mgr(tmp_path)._notify_fill(
        "sell", ticker="MO", shares=5, price=45.0, pnl=120.0, pnl_pct=8.5,
        reason="target hit",
    )
    u = user_updates.read_updates(str(tmp_path))[0]
    assert u.type == "sell" and u.title == "Sold MO"
    assert "$45.00" in u.summary and "8.5%" in u.summary and "target hit" in u.summary


def test_notify_never_raises_into_trading(tmp_path):
    # Settings missing data_dir would normally blow up the write — _notify_fill
    # must swallow it so a fill is never lost to a notification failure.
    m = QuantManager.__new__(QuantManager)
    m.settings = SimpleNamespace()  # no data_dir, no messaging_enabled
    m._notify_fill("buy", ticker="X", shares=1, price=1.0)  # must not raise
