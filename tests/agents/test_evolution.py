"""Tests for PromptEvolver safety constraints (E7).

Tests safety phrase preservation, rate limiting, rollback, and history recording.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.agents.evolution import PromptEvolver, SAFETY_PHRASES


@pytest.fixture
def evolver(tmp_path):
    """Create a PromptEvolver with temp data directory."""
    e = PromptEvolver(data_dir=str(tmp_path))
    e._ensure_dirs()
    return e


SAFE_PROMPT = (
    "You are a trading agent. Always call sync_broker first as the source of truth. "
    "Use server-side GTC stop orders for safety. Support dry-run and dry_run modes. "
    "Enforce stop-loss rules. This is the crash-proof safety net."
)


class TestSafetyConstraints:
    """Prompt rewrites must preserve all safety phrases."""

    def test_safety_check_passes_when_phrases_present(self, evolver):
        new_prompt = SAFE_PROMPT + " Extra guidance here."
        assert evolver._safety_check(SAFE_PROMPT, new_prompt) is True

    def test_safety_check_fails_when_phrase_removed(self, evolver):
        """Removing any safety phrase should fail the check."""
        # Remove "sync_broker"
        broken_prompt = SAFE_PROMPT.replace("sync_broker", "fetch_data")
        assert evolver._safety_check(SAFE_PROMPT, broken_prompt) is False

    def test_safety_check_fails_for_each_phrase(self, evolver):
        """Every safety phrase, if removed, must cause failure."""
        for phrase in SAFETY_PHRASES:
            if phrase.lower() in SAFE_PROMPT.lower():
                broken = SAFE_PROMPT.replace(phrase, "REDACTED")
                result = evolver._safety_check(SAFE_PROMPT, broken)
                assert result is False, f"Safety check should fail when '{phrase}' is removed"

    def test_safety_check_ignores_phrases_not_in_original(self, evolver):
        """If a safety phrase wasn't in the original, its absence in the new prompt is OK."""
        original = "Analyze the data and report findings."
        new = "Review the data and summarize results."
        assert evolver._safety_check(original, new) is True

    def test_rewrite_returns_none_on_safety_failure(self, evolver):
        """rewrite_prompt should return None if the rewritten prompt fails safety check."""
        # Mock subprocess to return a prompt with a safety phrase removed
        unsafe_output = SAFE_PROMPT.replace("sync_broker", "")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=unsafe_output, stderr=""
            )
            with patch("shutil.which", return_value="/usr/bin/claude"):
                result = evolver.rewrite_prompt("test_agent", SAFE_PROMPT, ["suggestion"])

        assert result is None


class TestRateLimiting:
    """Max one prompt rewrite per agent per week."""

    def test_can_rewrite_when_no_history(self, evolver):
        assert evolver.can_rewrite("new_agent") is True

    def test_cannot_rewrite_within_one_week(self, evolver):
        """After a rewrite, can_rewrite should return False for 7 days."""
        history = [{
            "timestamp": datetime.now().isoformat(),
            "agent": "test_agent",
            "diff": "some diff",
            "prompt_path": "data/agents/prompts/test_agent.md",
        }]
        evolver._write_json(evolver.history_path, history)

        assert evolver.can_rewrite("test_agent") is False

    def test_can_rewrite_after_one_week(self, evolver):
        """After 7+ days, can_rewrite should return True again."""
        old_ts = (datetime.now() - timedelta(days=8)).isoformat()
        history = [{
            "timestamp": old_ts,
            "agent": "test_agent",
            "diff": "old diff",
            "prompt_path": "data/agents/prompts/test_agent.md",
        }]
        evolver._write_json(evolver.history_path, history)

        assert evolver.can_rewrite("test_agent") is True

    def test_rate_limit_per_agent(self, evolver):
        """Rate limit is per agent; other agents can still rewrite."""
        history = [{
            "timestamp": datetime.now().isoformat(),
            "agent": "agent_a",
            "diff": "diff",
            "prompt_path": "data/agents/prompts/agent_a.md",
        }]
        evolver._write_json(evolver.history_path, history)

        assert evolver.can_rewrite("agent_a") is False
        assert evolver.can_rewrite("agent_b") is True

    def test_rewrite_prompt_skips_if_rate_limited(self, evolver):
        """rewrite_prompt should return None when rate-limited."""
        history = [{
            "timestamp": datetime.now().isoformat(),
            "agent": "agent_x",
            "diff": "",
            "prompt_path": "",
        }]
        evolver._write_json(evolver.history_path, history)

        result = evolver.rewrite_prompt("agent_x", "prompt", ["suggestion"])
        assert result is None


class TestRollback:
    """revert_prompt should delete the disk override and restore code default."""

    def test_revert_removes_disk_override(self, evolver):
        """After revert, the disk prompt file should not exist."""
        prompt_path = evolver.prompts_dir / "test_agent.md"
        prompt_path.write_text("overridden prompt")
        assert prompt_path.exists()

        result = evolver.revert_prompt("test_agent")
        assert result is True
        assert not prompt_path.exists()

    def test_revert_returns_false_if_no_override(self, evolver):
        """Reverting an agent with no disk override returns False."""
        result = evolver.revert_prompt("never_overridden")
        assert result is False

    def test_apply_then_revert_cycle(self, evolver):
        """A full apply -> revert cycle should work cleanly."""
        evolver.apply_rewrite("cycle_agent", "old prompt", "new prompt content")

        prompt_path = evolver.prompts_dir / "cycle_agent.md"
        assert prompt_path.exists()
        assert prompt_path.read_text() == "new prompt content"

        evolver.revert_prompt("cycle_agent")
        assert not prompt_path.exists()


class TestHistoryRecording:
    """apply_rewrite should record diffs and mark recommendations as applied."""

    def test_apply_rewrite_records_history(self, evolver):
        evolver.apply_rewrite("hist_agent", "old text", "new text")

        history = evolver._read_json(evolver.history_path, default=[])
        assert len(history) == 1
        assert history[0]["agent"] == "hist_agent"
        assert "diff" in history[0]

    def test_apply_marks_recommendations_applied(self, evolver):
        """Pending recommendations for the agent should be marked applied."""
        recs = [
            {
                "agent": "mark_agent",
                "prompt_suggestions": ["do X"],
                "tool_suggestions": [],
                "process_suggestions": [],
                "applied": False,
            }
        ]
        evolver._write_json(evolver.recommendations_path, recs)

        evolver.apply_rewrite("mark_agent", "old", "new")

        updated = evolver._read_json(evolver.recommendations_path, default=[])
        assert updated[0]["applied"] is True
        assert "applied_at" in updated[0]

    def test_collect_meta_stores_recommendations(self, evolver):
        """collect_meta should extract and store meta-recommendations from session."""
        session = {
            "run_id": "test-123",
            "mode": "screen",
            "traces": [
                {
                    "role": "analysis",
                    "meta": {
                        "prompt_suggestions": ["Add more detail about momentum"],
                        "tool_suggestions": [],
                        "process_suggestions": ["Run screening earlier"],
                    },
                }
            ],
        }

        evolver.collect_meta(session)

        recs = evolver._read_json(evolver.recommendations_path, default=[])
        assert len(recs) == 1
        assert recs[0]["agent"] == "analysis"
        assert "Add more detail about momentum" in recs[0]["prompt_suggestions"]
