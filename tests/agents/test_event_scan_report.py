"""WP5 / B7: event_scan report spam.

A quiet event scan (nothing actionable, nothing executed) must not write a full
report_*.json — otherwise cron floods data/reports with one file per minute. A
scan that found something actionable, or executed a trade, still writes a report.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.agents.orchestrator import Orchestrator


def _make_orchestrator(settings):
    with patch.object(Orchestrator, "_find_claude", return_value="/usr/bin/claude"), \
         patch("src.agents.orchestrator.AgentRegistry"), \
         patch("src.agents.orchestrator.PromptEvolver"):
        return Orchestrator(settings=settings, dry_run=True)


def _run_scan_with(scan_result, orch):
    """Drive run_event_scan with a scripted trigger.run() result, patching out
    every heavy dependency, and capture whether _save_report was called."""
    trigger = MagicMock()
    trigger.run.return_value = scan_result

    move_watcher = MagicMock()
    move_watcher.return_value.scan.return_value = []

    with patch("src.strategy.manager.QuantManager"), \
         patch("src.data.event_source.NewsEventSource"), \
         patch("src.data.event_source.TruthSocialEventSource"), \
         patch("src.data.event_source.CompositeEventSource"), \
         patch("src.data.sentiment.SentimentProvider"), \
         patch("src.strategy.event_trigger.EventTrigger", return_value=trigger), \
         patch("src.strategy.move_watch.MoveWatcher", move_watcher), \
         patch("src.agents.orchestrator.start_run_log", return_value="/tmp/run.json"), \
         patch("src.agents.orchestrator.finish_run_log"), \
         patch("src.agents.orchestrator.cleanup_mcp"), \
         patch.object(orch, "_save_report") as save_report:
        orch.run_event_scan()
    return save_report


def _base_settings():
    s = MagicMock()
    s.event_trigger_enabled = True
    s.event_figures = []
    s.event_truth_social_enabled = False
    s.event_watchlist = ["AAPL"]
    s.event_news_lookback_days = 1
    s.data_dir = "data"
    s.event_resolver_model = None
    return s


def test_quiet_scan_does_not_save_report():
    settings = _base_settings()
    orch = _make_orchestrator(settings)
    scan = {"scanned": 5, "actionable": [], "executed": [], "regime": "neutral"}
    save_report = _run_scan_with(scan, orch)
    save_report.assert_not_called()


def test_actionable_scan_saves_report():
    settings = _base_settings()
    orch = _make_orchestrator(settings)
    # actionable but not executed (e.g. guardrails blocked, or dry_run)
    scan = {
        "scanned": 5,
        "actionable": [{"ticker": "AAPL"}],
        "executed": [],
        "regime": "neutral",
    }
    save_report = _run_scan_with(scan, orch)
    save_report.assert_called_once()


def test_executed_scan_saves_report():
    settings = _base_settings()
    orch = _make_orchestrator(settings)
    scan = {
        "scanned": 5,
        "actionable": [{"ticker": "AAPL"}],
        "executed": [{"ticker": "AAPL", "shares": 1, "price": 100.0, "stop": 95.0,
                      "event": {}}],
        "regime": "neutral",
    }
    save_report = _run_scan_with(scan, orch)
    save_report.assert_called_once()
