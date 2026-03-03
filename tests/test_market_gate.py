"""Tests for market_gate.py gate rules."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the GATE_RULES directly
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from scripts.market_gate import GATE_RULES


class TestGateRules:
    def test_heartbeat_requires_nothing(self):
        rules = GATE_RULES["heartbeat"]
        assert rules["requires_open"] is False
        assert rules["requires_market_day"] is False

    def test_screen_requires_market_day_only(self):
        rules = GATE_RULES["screen"]
        assert rules["requires_open"] is False
        assert rules["requires_market_day"] is True

    def test_enter_requires_open_and_market_day(self):
        rules = GATE_RULES["enter"]
        assert rules["requires_open"] is True
        assert rules["requires_market_day"] is True

    def test_monitor_requires_open_and_market_day(self):
        rules = GATE_RULES["monitor"]
        assert rules["requires_open"] is True
        assert rules["requires_market_day"] is True

    def test_stop_sync_requires_open_and_market_day(self):
        rules = GATE_RULES["stop_sync"]
        assert rules["requires_open"] is True
        assert rules["requires_market_day"] is True

    def test_post_market_requires_market_day_only(self):
        rules = GATE_RULES["post_market"]
        assert rules["requires_open"] is False
        assert rules["requires_market_day"] is True

    def test_learning_requires_nothing(self):
        rules = GATE_RULES["learning"]
        assert rules["requires_open"] is False
        assert rules["requires_market_day"] is False

    def test_pre_market_requires_market_day_only(self):
        rules = GATE_RULES["pre_market"]
        assert rules["requires_open"] is False
        assert rules["requires_market_day"] is True


class TestGateLogic:
    """Test the gate decision logic without calling brokers."""

    def test_no_requirements_always_runs(self):
        """Modes with no requirements should always run."""
        for mode in ["heartbeat", "learning"]:
            rules = GATE_RULES[mode]
            assert not rules["requires_open"] and not rules["requires_market_day"]

    def test_requires_market_day_blocked_on_holiday(self):
        """Modes requiring market day should be blocked on holidays."""
        rules = GATE_RULES["screen"]
        is_market_day = False
        should_run = not (rules["requires_market_day"] and not is_market_day)
        assert should_run is False

    def test_requires_open_blocked_when_closed(self):
        """Modes requiring open market should be blocked when closed."""
        rules = GATE_RULES["enter"]
        is_open = False
        is_market_day = True
        blocked_by_day = rules["requires_market_day"] and not is_market_day
        blocked_by_open = rules["requires_open"] and not is_open
        should_run = not blocked_by_day and not blocked_by_open
        assert should_run is False

    def test_all_conditions_met(self):
        """When all conditions are met, mode should run."""
        rules = GATE_RULES["enter"]
        is_open = True
        is_market_day = True
        blocked_by_day = rules["requires_market_day"] and not is_market_day
        blocked_by_open = rules["requires_open"] and not is_open
        should_run = not blocked_by_day and not blocked_by_open
        assert should_run is True

    def test_all_modes_have_rules(self):
        expected = {"heartbeat", "screen", "enter", "monitor", "stop_sync", "post_market", "learning", "pre_market"}
        assert expected.issubset(set(GATE_RULES.keys()))
