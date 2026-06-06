"""Tests for the kill-switch control flags."""

from src.strategy.control import (
    get_controls, set_controls, trading_armed, event_armed,
)


def test_defaults_armed(tmp_path):
    c = get_controls(str(tmp_path))
    assert c["trading_armed"] is True
    assert c["event_armed"] is True


def test_set_and_persist(tmp_path):
    set_controls(str(tmp_path), trading_armed=False)
    assert trading_armed(str(tmp_path)) is False
    # event_armed untouched but master gates it
    assert event_armed(str(tmp_path)) is False


def test_event_requires_master(tmp_path):
    set_controls(str(tmp_path), trading_armed=True, event_armed=False)
    assert trading_armed(str(tmp_path)) is True
    assert event_armed(str(tmp_path)) is False  # event off
    set_controls(str(tmp_path), event_armed=True)
    assert event_armed(str(tmp_path)) is True


def test_master_off_disables_events_even_if_event_armed(tmp_path):
    set_controls(str(tmp_path), trading_armed=False, event_armed=True)
    assert event_armed(str(tmp_path)) is False


def test_corrupt_control_file_fails_closed(tmp_path):
    # A control file that EXISTS but is unreadable must fail closed (disarmed):
    # we can't confirm the intended state, so the safe default is NOT to trade.
    (tmp_path / "control.json").write_text("{not json")
    assert trading_armed(str(tmp_path)) is False
    assert event_armed(str(tmp_path)) is False


def test_absent_control_file_defaults_armed(tmp_path):
    # No file at all -> never configured -> armed (preserve existing behavior).
    assert trading_armed(str(tmp_path)) is True
    assert event_armed(str(tmp_path)) is True


def test_non_object_control_file_fails_closed(tmp_path):
    (tmp_path / "control.json").write_text("[1, 2, 3]")  # valid JSON, wrong shape
    assert trading_armed(str(tmp_path)) is False
