"""Tests for dashboard filter/template functions."""

import pytest
from markupsafe import Markup

from dashboard.app import (
    _duration_human,
    _mode_icon,
    _status_class,
    _ts_short,
    _ts_time,
)


class TestDurationHuman:
    def test_none(self):
        assert _duration_human(None) == "-"

    def test_seconds(self):
        assert _duration_human(45) == "45s"

    def test_minutes_seconds(self):
        assert _duration_human(125) == "2m 5s"

    def test_hours_minutes(self):
        assert _duration_human(3725) == "1h 2m"

    def test_zero(self):
        assert _duration_human(0) == "0s"

    def test_float_input(self):
        assert _duration_human(90.7) == "1m 30s"


class TestTsShort:
    def test_full_iso(self):
        assert _ts_short("2026-02-25T19:08:08.152645") == "Feb 25, 19:08"

    def test_short_string(self):
        assert _ts_short("2026-02") == "2026-02"

    def test_none(self):
        assert _ts_short(None) == "-"

    def test_empty(self):
        assert _ts_short("") == "-"

    def test_january(self):
        assert _ts_short("2026-01-05T08:00:00") == "Jan 5, 08:00"

    def test_december(self):
        assert _ts_short("2025-12-31T23:59:00") == "Dec 31, 23:59"


class TestTsTime:
    def test_full_iso(self):
        assert _ts_time("2026-02-25T19:08:45.152645") == "19:08:45"

    def test_none(self):
        assert _ts_time(None) == ""

    def test_short_string(self):
        assert _ts_time("short") == "short"


class TestModeIcon:
    def test_known_modes(self):
        for mode in ["heartbeat", "screen", "enter", "monitor", "stop_sync", "post_market", "learning", "pre_market"]:
            result = _mode_icon(mode)
            assert isinstance(result, Markup)
            assert "mode-icon" in result

    def test_unknown_mode(self):
        assert _mode_icon("unknown_mode") == ""


class TestStatusClass:
    def test_success(self):
        assert _status_class("success") == "ok"

    def test_failed(self):
        assert _status_class("failed") == "warn"

    def test_running(self):
        assert _status_class("running") == "running"

    def test_unknown(self):
        assert _status_class("other") == "neutral"
