"""P1-3: big-move detector. Notification-only; reference is the last-scan price."""
from types import SimpleNamespace

from config.settings import get_settings
from src.strategy.move_watch import MoveWatcher


class _Pos:
    def __init__(self, ticker, entry_price, shares=10):
        self.ticker = ticker
        self.entry_price = entry_price
        self.shares = shares
        self.cost_basis = entry_price * shares

    def calculate_pnl(self, price):
        return {"pnl_pct": (price - self.entry_price) / self.entry_price,
                "pnl_dollars": (price - self.entry_price) * self.shares,
                "current_value": price * self.shares}


class _Mgr:
    def __init__(self, positions, prices):
        self._positions = positions
        self._prices = prices
        self.position_manager = self
        self.price_provider = self

    def get_all_positions(self):
        return self._positions

    def get_latest_price(self, t):
        return self._prices.get(t)


def _settings(tmp_path, **over):
    s = get_settings()
    s.data_dir = str(tmp_path)
    s.big_move_enabled = over.get("enabled", True)
    s.big_move_threshold_pct = over.get("threshold", 0.08)
    s.big_move_cooldown_minutes = over.get("cooldown", 180)
    return s


def test_first_scan_sets_reference_no_alert(tmp_path):
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 100.0})
    w = MoveWatcher(mgr, _settings(tmp_path))
    assert w.scan() == []  # first sighting -> reference only


def test_alerts_on_jump_beyond_threshold(tmp_path):
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 100.0})
    s = _settings(tmp_path)
    w = MoveWatcher(mgr, s)
    w.scan()                                   # set ref @ 100
    mgr._prices["DELL"] = 112.0                # +12% jump
    events = w.scan()
    assert len(events) == 1
    e = events[0]
    assert e["ticker"] == "DELL" and e["direction"] == "up" and e["move_pct"] == 12.0


def test_silent_below_threshold(tmp_path):
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 100.0})
    w = MoveWatcher(mgr, _settings(tmp_path))
    w.scan()
    mgr._prices["DELL"] = 103.0                # +3%, below 8%
    assert w.scan() == []


def test_long_held_winner_does_not_spam(tmp_path):
    # Reference is last-scan price, not entry — a stable +119% holding never trips.
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 219.0})
    w = MoveWatcher(mgr, _settings(tmp_path))
    assert w.scan() == []     # first sight @219 -> ref
    assert w.scan() == []     # still @219 -> no move


def test_cooldown_blocks_repeat_alert(tmp_path):
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 100.0})
    w = MoveWatcher(mgr, _settings(tmp_path, cooldown=180))
    w.scan()
    mgr._prices["DELL"] = 112.0
    assert len(w.scan()) == 1          # alerts, advances ref to 112, stamps cooldown
    mgr._prices["DELL"] = 126.0        # another +12.5% but within cooldown
    assert w.scan() == []


def test_down_move_detected(tmp_path):
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 100.0})
    w = MoveWatcher(mgr, _settings(tmp_path))
    w.scan()
    mgr._prices["DELL"] = 88.0         # -12%
    e = w.scan()[0]
    assert e["direction"] == "down" and e["move_pct"] == -12.0


def test_disabled_returns_nothing(tmp_path):
    mgr = _Mgr([_Pos("DELL", 100.0)], {"DELL": 150.0})
    w = MoveWatcher(mgr, _settings(tmp_path, enabled=False))
    assert w.scan() == []
