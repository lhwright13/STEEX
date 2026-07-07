"""B3: reconstruct the real stop tier for broker-filled stops.

A server-side stop that fills while STEEX is offline used to be recorded as the
catch-all "server_stop" (11/12 of all exits), making "are the stops too tight?"
unanswerable. _classify_stop_exit maps the position's peak gain to the tier that
was actually protecting it.
"""
from types import SimpleNamespace

from src.strategy.manager import QuantManager


def _mgr():
    m = QuantManager.__new__(QuantManager)
    # trailing_stops keys are the gain thresholds at which each trail engages.
    m.settings = SimpleNamespace(trailing_stops={0.10: 0.12, 0.20: 0.15, 0.30: 0.12})
    return m


def _pos(entry, high):
    return SimpleNamespace(entry_price=entry, high_since_entry=high)


def test_never_reached_first_tier_is_initial_stop():
    # peaked +4% → never crossed +10% → the fixed initial stop
    assert _mgr()._classify_stop_exit(_pos(100.0, 104.0)) == "initial_stop"


def test_peak_between_10_and_20_is_trail_10():
    assert _mgr()._classify_stop_exit(_pos(100.0, 115.0)) == "trail_10"


def test_peak_between_20_and_30_is_trail_20():
    assert _mgr()._classify_stop_exit(_pos(100.0, 125.0)) == "trail_20"


def test_big_winner_is_trail_30():
    # DELL-style: peaked +142% → the widest tier was active
    assert _mgr()._classify_stop_exit(_pos(100.0, 242.0)) == "trail_30"


def test_exact_threshold_counts_for_that_tier():
    assert _mgr()._classify_stop_exit(_pos(100.0, 110.0)) == "trail_10"
    assert _mgr()._classify_stop_exit(_pos(100.0, 130.0)) == "trail_30"


def test_bad_data_falls_back_to_catch_all():
    assert _mgr()._classify_stop_exit(_pos(0.0, 0.0)) == "server_stop"
