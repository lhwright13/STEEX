"""End-of-day recap: today's P&L + closed trades + alpha vs SPY, once per day."""
import json
from types import SimpleNamespace

from src.notify import user_updates
from src.notify.daily_recap import (
    send_daily_recap, _build_context, _format_sections,
)


def _settings(tmp_path, **kw):
    base = dict(
        data_dir=str(tmp_path), messaging_enabled=False, daily_recap_enabled=True,
        event_resolver_model=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


FINAL = {"today": "2026-06-16", "mode": "post_market", "abort": False}


def _stub(event):
    return "Quiet day; book up modestly and alpha holding versus the S&P."


def test_build_context_picks_out_todays_exits(tmp_path):
    trades = [
        {"ticker": "DELL", "exit_date": "2026-06-16", "pnl_dollars": 2164.0, "pnl_pct": 0.9375,
         "exit_reason": "trailing_stop"},
        {"ticker": "CCI", "exit_date": "2026-06-16", "pnl_dollars": -88.0, "pnl_pct": -0.04,
         "exit_reason": "below_ma"},
        {"ticker": "OLD", "exit_date": "2026-06-10", "pnl_dollars": 10.0, "pnl_pct": 0.01,
         "exit_reason": "server_stop"},
    ]
    (tmp_path / "trades.json").write_text(json.dumps(trades))
    c = _build_context(FINAL, _settings(tmp_path))
    tks = [e["ticker"] for e in c["today_exits"]]
    assert tks == ["DELL", "CCI"]  # only today's, sorted by $ desc
    assert c["realized_today"] == 2076.0
    assert c["overall"]["count"] == 3
    # perf is best-effort: None when the broker is unreachable, else a summary
    # dict — never an exception either way.
    assert c["perf_1d"] is None or "portfolio_return_pct" in c["perf_1d"]


def test_build_context_matches_exits_with_human_format_today(tmp_path):
    """Regression: orchestrator sets today='Thursday, July 02, 2026' (human
    format); the ISO comparison never matched, so '💰 Closed today' was
    permanently empty in every production recap."""
    trades = [{"ticker": "TER", "exit_date": "2026-07-02T08:01:00", "pnl_dollars": 14.68,
               "pnl_pct": 0.009, "exit_reason": "server_stop"}]
    (tmp_path / "trades.json").write_text(json.dumps(trades))
    final = {"today": "Thursday, July 02, 2026", "mode": "post_market"}
    c = _build_context(final, _settings(tmp_path))
    assert c["date"] == "2026-07-02"
    assert [e["ticker"] for e in c["today_exits"]] == ["TER"]
    assert c["realized_today"] == 14.68


def test_format_sections_renders_recap_blocks():
    ctx = {
        "perf_1d": {"start_equity": 50000, "end_equity": 50750, "portfolio_return_pct": 1.5},
        "today_exits": [{"ticker": "DELL", "pnl_pct": 93.8, "pnl_dollars": 2164.0,
                         "reason": "trailing_stop"}],
        "realized_today": 2164.0,
        "perf_1w": {"portfolio_return_pct": -0.4, "alpha_pct": 0.2},
        "perf_1m": {"portfolio_return_pct": 6.1, "alpha_pct": 4.0},
        "signals": {"overall_recent_win_rate": 0.62, "degrading": ["momentum_score"]},
    }
    out = _format_sections(ctx)
    assert "📈 Today: +$750 (+1.50%) | equity $50,750" in out
    assert "💰 Closed today: DELL +93.80% (+$2,164, trailing_stop)" in out
    assert "📊 Alpha: 1W -0.40% (+0.20% vs SPY) | 1M +6.10% (+4.00% vs SPY)" in out
    assert "🩺 Signals: recent win rate 62% | degrading: momentum_score" in out


def test_format_sections_handles_empty_day():
    out = _format_sections({"today_exits": [], "signals": {"overall_recent_win_rate": 0.5,
                                                           "degrading": []}})
    assert "💰 Closed today: nothing closed" in out
    assert "all healthy" in out


def test_sends_recap_and_is_idempotent(tmp_path):
    s = _settings(tmp_path)
    rec = send_daily_recap(FINAL, s, summarizer=_stub)
    assert rec is not None
    assert rec.id == "recap_2026-06-16"
    assert rec.title == "End-of-day recap — 2026-06-16"
    assert send_daily_recap(FINAL, s, summarizer=_stub) is None
    assert len(user_updates.read_updates(str(tmp_path))) == 1


def test_respects_disable_flag(tmp_path):
    s = _settings(tmp_path, daily_recap_enabled=False)
    assert send_daily_recap(FINAL, s, summarizer=_stub) is None
