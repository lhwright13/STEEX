"""Tests for ConfigWriter safety mechanisms (E6).

Tests market hours blocking, weight normalization, bounds clamping, and audit trail.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.learning.config_writer import ConfigWriter, PARAM_BOUNDS, WEIGHT_KEYS


@pytest.fixture
def config_env(tmp_path, test_settings):
    """Set up a temporary config.yaml and data directory for ConfigWriter."""
    config_path = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Write a minimal config.yaml that ConfigWriter can parse
    config_path.write_text(
        "weight_momentum: 0.30\n"
        "weight_insider: 0.25\n"
        "weight_volume: 0.15\n"
        "weight_sentiment: 0.15\n"
        "weight_fundamental: 0.10\n"
        "weight_options: 0.05\n"
        "initial_stop_pct: 0.10\n"
        "max_hold_days: 30\n"
        "position_size_pct: 0.05\n"
        "manager_min_score_entry: 55.0\n"
    )

    # Patch get_settings to return our test_settings and CONFIG_FILE to our temp path
    with patch("src.learning.config_writer.get_settings", return_value=test_settings), \
         patch("src.learning.config_writer.CONFIG_FILE", config_path):
        writer = ConfigWriter(config_path=config_path, data_dir=str(data_dir))
        yield writer, config_path, data_dir, test_settings


class TestBoundsClamping:
    """Proposed values outside PARAM_BOUNDS should be clamped."""

    def test_clamp_above_max(self, config_env):
        writer, _, _, settings = config_env
        settings.weight_momentum = 0.30

        result = writer.propose_changes(
            {"weight_momentum": 0.99},  # max is 0.50
            source="test",
            reason="test clamping",
        )

        # The validated value should be clamped to 0.50 max,
        # then further clamped by max delta (0.10) -> 0.30 + 0.10 = 0.40
        wm = result["changes"].get("weight_momentum")
        assert wm is not None
        assert wm["validated"] <= 0.50

    def test_clamp_below_min(self, config_env):
        writer, _, _, settings = config_env
        settings.weight_momentum = 0.30

        result = writer.propose_changes(
            {"weight_momentum": -0.50},  # min is 0.05
            source="test",
            reason="test",
        )

        wm = result["changes"].get("weight_momentum")
        assert wm is not None
        assert wm["validated"] >= 0.05

    def test_delta_cap_enforced(self, config_env):
        """Change per cycle should not exceed max_delta (0.10 for weights)."""
        writer, _, _, settings = config_env
        settings.weight_momentum = 0.30

        result = writer.propose_changes(
            {"weight_momentum": 0.50},  # delta = +0.20, cap = 0.10
            source="test",
            reason="test",
        )

        wm = result["changes"].get("weight_momentum")
        assert wm is not None
        assert abs(wm["delta"]) <= 0.10 + 1e-6

    def test_unknown_param_skipped(self, config_env):
        writer, _, _, _ = config_env

        result = writer.propose_changes(
            {"nonexistent_param": 42.0},
            source="test",
            reason="test",
        )

        assert "nonexistent_param" in result["skipped"]
        assert len(result["changes"]) == 0


class TestWeightNormalization:
    """After changing any weight, all weights should sum to 1.0."""

    def test_weights_sum_to_one_after_single_change(self, config_env):
        writer, _, _, settings = config_env
        # Set up known weights that sum to 1.0
        settings.weight_momentum = 0.30
        settings.weight_insider = 0.25
        settings.weight_volume = 0.15
        settings.weight_sentiment = 0.15
        settings.weight_fundamental = 0.10
        settings.weight_options = 0.05

        result = writer.propose_changes(
            {"weight_momentum": 0.40},
            source="test",
            reason="test normalization",
        )

        # Collect all final weight values (changed + unchanged)
        all_weights = {}
        for key in WEIGHT_KEYS:
            if key in result["changes"]:
                all_weights[key] = result["changes"][key]["validated"]
            else:
                all_weights[key] = getattr(settings, key)

        total = sum(all_weights.values())
        assert abs(total - 1.0) < 1e-4, f"Weights sum to {total}, expected 1.0"

    def test_weights_sum_to_one_after_multiple_changes(self, config_env):
        writer, _, _, settings = config_env
        settings.weight_momentum = 0.30
        settings.weight_insider = 0.25
        settings.weight_volume = 0.15
        settings.weight_sentiment = 0.15
        settings.weight_fundamental = 0.10
        settings.weight_options = 0.05

        result = writer.propose_changes(
            {"weight_momentum": 0.35, "weight_insider": 0.30},
            source="test",
            reason="test multi",
        )

        all_weights = {}
        for key in WEIGHT_KEYS:
            if key in result["changes"]:
                all_weights[key] = result["changes"][key]["validated"]
            else:
                all_weights[key] = getattr(settings, key)

        total = sum(all_weights.values())
        assert abs(total - 1.0) < 1e-4, f"Weights sum to {total}, expected 1.0"


class TestAuditTrail:
    """apply_changes should log every change to config_history.json."""

    def test_audit_entry_written(self, config_env):
        writer, config_path, data_dir, settings = config_env
        settings.weight_momentum = 0.30
        settings.weight_insider = 0.25
        settings.weight_volume = 0.15
        settings.weight_sentiment = 0.15
        settings.weight_fundamental = 0.10
        settings.weight_options = 0.05

        validated = writer.propose_changes(
            {"weight_momentum": 0.35},
            source="signal_research",
            reason="momentum alpha improving",
        )

        result = writer.apply_changes(validated, source="signal_research", reason="test")
        assert result["applied"] is True

        history = writer.get_history()
        assert len(history) >= 1
        entry = history[0]
        assert entry["source"] == "signal_research"
        assert "applied" in entry

    def test_audit_history_accumulates(self, config_env):
        writer, _, _, settings = config_env
        settings.weight_momentum = 0.30
        settings.weight_insider = 0.25
        settings.weight_volume = 0.15
        settings.weight_sentiment = 0.15
        settings.weight_fundamental = 0.10
        settings.weight_options = 0.05

        # Apply two rounds of changes
        v1 = writer.propose_changes(
            {"weight_momentum": 0.35}, source="s1", reason="r1"
        )
        writer.apply_changes(v1, source="s1", reason="r1")

        # Update the "current" value for second round
        settings.weight_momentum = 0.35
        v2 = writer.propose_changes(
            {"weight_momentum": 0.30}, source="s2", reason="r2"
        )
        writer.apply_changes(v2, source="s2", reason="r2")

        history = writer.get_history()
        assert len(history) >= 2

    def test_no_changes_returns_not_applied(self, config_env):
        writer, _, _, settings = config_env
        settings.weight_momentum = 0.30

        # Propose the same value -> no change
        validated = writer.propose_changes(
            {"weight_momentum": 0.30},
            source="test",
            reason="no-op",
        )

        result = writer.apply_changes(validated, source="test", reason="no-op")
        assert result["applied"] is False
