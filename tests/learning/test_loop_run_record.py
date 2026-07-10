"""Tests for the learning loop's dashboard run-record emission (WP4 / B6).

Root cause fixed here: the deterministic learning loop completed with exit
code 0 but never wrote a data/runs/*.jsonl record, so the weekly learning slot
produced zero visible runs. LearningLoop.run() now always writes a run record
with an explicit outcome, distinguishing a legitimate no-op (too few closed
trades) from a real failure.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.learning.journal import LearningJournal
from src.learning.loop import LearningLoop


def _read_run_records(data_dir: Path):
    runs_dir = Path(data_dir) / "runs"
    records = []
    for f in sorted(runs_dir.glob("run_*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


@pytest.fixture
def loop_env(tmp_path, test_settings):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    test_settings.data_dir = str(data_dir)
    test_settings.learning_min_trades_for_analysis = 15
    journal = LearningJournal(data_dir=str(data_dir))
    loop = LearningLoop(settings=test_settings, journal=journal)
    return loop, data_dir


def _patch_phases(loop, trades_analyzed=0, pm_error=None, decay_error=None):
    """Patch the two analysis phases so we control trades_analyzed / errors."""
    min_trades = getattr(loop.settings, "learning_min_trades_for_analysis", 15)
    if pm_error:
        pm = {"error": pm_error}
    else:
        pm = {
            "trades_analyzed": trades_analyzed,
            "loss_breakdown": {},
            "score_correlation": 0.0,
            "avg_missed_upside": 0.0,
            "recommendations": [],
            "patterns": [],
            "sufficient_data": trades_analyzed >= min_trades,
        }
    decay = {"error": decay_error} if decay_error else {"signals": [], "degrading": []}
    return patch.object(loop, "_run_postmortem", return_value=pm), \
        patch.object(loop, "_run_alpha_decay", return_value=decay)


def test_run_writes_run_record(loop_env):
    """A learning run always writes exactly one dashboard run record."""
    loop, data_dir = loop_env
    pm_patch, decay_patch = _patch_phases(loop, trades_analyzed=20)
    with pm_patch, decay_patch:
        loop.run()

    records = _read_run_records(data_dir)
    learning_records = [r for r in records if r.get("mode") == "learning"]
    # start_run_log + finish_run_log => 2 lines, 1 file.
    assert len(list((data_dir / "runs").glob("run_*.jsonl"))) == 1
    assert any(r.get("status") == "complete" for r in learning_records)


def test_insufficient_trades_is_no_op_not_failure(loop_env):
    """Too few closed trades => explicit no_op outcome, run still 'complete'."""
    loop, data_dir = loop_env
    pm_patch, decay_patch = _patch_phases(loop, trades_analyzed=14)
    with pm_patch, decay_patch:
        results = loop.run()

    outcome = results["outcome"]
    assert outcome["status"] == "no_op"
    assert outcome["reason"] == "insufficient_trades"
    assert outcome["trades_analyzed"] == 14
    assert outcome["min_trades"] == 15

    records = _read_run_records(data_dir)
    final = [r for r in records if r.get("mode") == "learning" and "manager_decision" in r][-1]
    assert final["status"] == "complete"
    assert final["abort"] is False
    assert final["manager_decision"]["outcome"] == "no_op"


def test_sufficient_trades_is_analyzed(loop_env):
    """Enough closed trades => analyzed outcome."""
    loop, data_dir = loop_env
    pm_patch, decay_patch = _patch_phases(loop, trades_analyzed=15)
    with pm_patch, decay_patch:
        results = loop.run()

    assert results["outcome"]["status"] == "analyzed"
    assert results["outcome"]["trades_analyzed"] == 15


def test_phase_error_marks_run_failed(loop_env):
    """A phase error => error outcome and a failed/aborted run record."""
    loop, data_dir = loop_env
    pm_patch, decay_patch = _patch_phases(loop, pm_error="boom")
    with pm_patch, decay_patch:
        results = loop.run()

    assert results["outcome"]["status"] == "error"
    assert "boom" in results["outcome"]["reason"]

    records = _read_run_records(data_dir)
    final = [r for r in records if r.get("mode") == "learning" and "manager_decision" in r][-1]
    assert final["status"] == "failed"
    assert final["abort"] is True


def test_record_failure_never_breaks_run(loop_env):
    """If run-record writing raises, run() still returns results."""
    loop, data_dir = loop_env
    pm_patch, decay_patch = _patch_phases(loop, trades_analyzed=20)
    with pm_patch, decay_patch, \
         patch("src.agents.run_log.start_run_log", side_effect=RuntimeError("disk full")):
        results = loop.run()

    # The loop completed and classified an outcome despite the write failing.
    assert results["outcome"]["status"] == "analyzed"
    assert "phases_run" in results
