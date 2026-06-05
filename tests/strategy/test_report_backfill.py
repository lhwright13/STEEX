"""M1: generate_daily_report must backfill regime/portfolio/data_health when
self.report is empty (the agent-mode ReportAgent runs with a fresh manager),
and must NOT redo that work when the sections are already populated.

Exercises the method against a mock `self` so no real broker/data deps are
needed.
"""
from unittest.mock import MagicMock

from src.strategy.manager import QuantManager


def _fake_mgr(report):
    m = MagicMock()
    m.report = report
    m.log = []
    m.trade_tracker.calculate_metrics.return_value = {
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_pnl_pct": 0.0,
    }
    return m


def test_report_backfills_empty_sections():
    m = _fake_mgr({})
    QuantManager.generate_daily_report(m, "post_market")
    m.get_regime.assert_called_once()
    m.assess_portfolio_risk.assert_called_once()
    m.check_data_health.assert_called_once()


def test_report_skips_backfill_when_populated():
    m = _fake_mgr({
        "regime": {"name": "risk_on"},
        "portfolio": {"position_count": 10},
        "data_health": {"healthy": True},
    })
    QuantManager.generate_daily_report(m, "post_market")
    m.get_regime.assert_not_called()
    m.assess_portfolio_risk.assert_not_called()
    m.check_data_health.assert_not_called()


def test_report_backfill_failure_does_not_raise():
    m = _fake_mgr({})
    m.get_regime.side_effect = RuntimeError("no data feed")
    m.assess_portfolio_risk.side_effect = RuntimeError("no broker")
    m.check_data_health.side_effect = RuntimeError("boom")
    # Must still produce a report rather than blowing up.
    report = QuantManager.generate_daily_report(m, "post_market")
    assert report["mode"] == "post_market"
