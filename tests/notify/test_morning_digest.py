"""Morning market brief: distill the screen run -> one Telegram message + Today's
Events entry, once per day (idempotent)."""
import json
from types import SimpleNamespace

from src.notify import user_updates
from src.notify.morning_digest import (
    send_morning_digest, _build_context, _close_calls, _sell_watch, _format_sections,
)


def _settings(tmp_path, **kw):
    base = dict(
        data_dir=str(tmp_path), messaging_enabled=False, morning_digest_enabled=True,
        event_figures=[{"name": "realDonaldTrump", "enabled": True}],
        event_resolver_model=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


FINAL = {
    "today": "2026-06-08", "mode": "screen", "abort": False,
    "conclusions": {"risk": {
        "regime_name": "risk_on", "vix_level": 18.82, "entries_allowed": True,
        "sizing_multiplier": 1.25, "position_count": 13, "portfolio_equity": 53995.46,
        "cash_available": 8516.96, "exits_recommended": [], "reasoning": "calm tape",
    }},
    "manager_decision": {
        "entries_approved": True, "buys": [{"ticker": "MO", "score": 54.85}],
        "reasoning": "thin slate, one consensus pick",
    },
}


def _stub(event):
    return ("Market is risk-on with VIX 18.82, so full sizing. 13 open positions, "
            "equity ~$54k, no exits flagged. Entering MO today. Watching realDonaldTrump.")


def test_build_context_pulls_the_morning_numbers():
    c = _build_context(FINAL, _settings("/tmp"))
    assert c["regime"] == "risk_on" and c["vix"] == 18.82
    assert c["positions"] == 13 and c["entries_approved"] is True
    assert c["picks"] == [{"ticker": "MO", "score": 54.85}]
    assert "realDonaldTrump" in c["watching_figures"]


def test_sends_digest_and_is_idempotent_per_day(tmp_path):
    s = _settings(tmp_path)
    rec = send_morning_digest(FINAL, s, summarizer=_stub)
    assert rec is not None
    assert rec.type == "system"
    assert rec.title == "Morning market brief — 2026-06-08"
    assert rec.id == "digest_2026-06-08"
    assert "MO" in rec.summary
    # the screen run's data is carried on the payload (shows in Today's Events)
    assert rec.payload["regime"] == "risk_on"
    assert rec.payload["picks"][0]["ticker"] == "MO"

    # second screen of the day must NOT re-send
    again = send_morning_digest(FINAL, s, summarizer=_stub)
    assert again is None
    assert len(user_updates.read_updates(str(tmp_path))) == 1


def test_respects_disable_flag(tmp_path):
    s = _settings(tmp_path, morning_digest_enabled=False)
    assert send_morning_digest(FINAL, s, summarizer=_stub) is None
    assert user_updates.read_updates(str(tmp_path)) == []


# --- Close calls (surfaced but not bought, with the reason why) -------------

def test_close_calls_classify_why_we_passed():
    final = {
        "manager_decision": {"buys": [{"ticker": "NVDA", "score": 68}]},
        "conclusions": {"meta_analysis": {
            "candidates": [
                {"ticker": "NVDA", "composite_score": 68},   # approved -> not a close call
                {"ticker": "MO", "composite_score": 50},     # already held
                {"ticker": "AMD", "composite_score": 35},    # below the 40 gate
            ],
            "speculative_excluded": ["XYZ"],
        }},
    }
    cc = _close_calls(final, _settings("/tmp"), held={"MO"})
    by = {r["ticker"]: r for r in cc}
    assert "NVDA" not in by  # we bought it
    assert by["MO"]["reason"] == "already held"
    assert "below score gate" in by["AMD"]["reason"]
    assert by["XYZ"]["reason"].startswith("excluded")
    # ranked best-first; None scores (XYZ) sort last
    assert [r["ticker"] for r in cc] == ["MO", "AMD", "XYZ"]


def test_sell_watch_includes_risk_flagged_without_provider(tmp_path):
    """With no open positions, the proximity scan is skipped but risk-flagged
    exits still surface (no price/signal providers touched)."""
    final = {"conclusions": {"risk": {"exits_recommended": ["GOOGL"]}}}
    sw = _sell_watch(final, _settings(tmp_path), positions={})
    assert sw == [{"ticker": "GOOGL", "reason": "flagged by risk", "rank": 0}]


def test_format_sections_renders_three_blocks():
    ctx = {
        "picks": [{"ticker": "MO", "score": 54.85}],
        "close_calls": [
            {"ticker": "AMD", "score": 35, "reason": "below score gate (35 < 40)"},
            {"ticker": "XYZ", "score": None, "reason": "excluded — low conviction"},
        ],
        "sell_watch": [{"ticker": "GOOGL", "reason": "4.2% above stop"}],
    }
    out = _format_sections(ctx)
    assert "🎯 Approved to buy: MO (55)" in out
    assert "👀 Close calls: AMD (35) — below score gate" in out
    assert "⚠️ Watching to sell: GOOGL — 4.2% above stop" in out


def test_format_sections_caps_and_counts_remainder():
    ctx = {"picks": [], "close_calls": [
        {"ticker": f"T{i}", "score": 50 - i, "reason": "passed by manager"} for i in range(6)
    ], "sell_watch": []}
    out = _format_sections(ctx)
    assert "🎯 Approved to buy: none today" in out
    assert "+3 more" in out  # 6 close calls, capped at 3


def test_sell_watch_handles_exit_recommendation_dicts(tmp_path):
    """Regression: risk's exits_recommended holds ExitRecommendation DICTS; a
    stringified dict once leaked into the 07-01 Telegram brief as a 'ticker'."""
    final = {"conclusions": {"risk": {"exits_recommended": [
        {"ticker": "MAR", "reason": "below_ma", "urgency": "end_of_day"},
    ]}}}
    sw = _sell_watch(final, _settings(tmp_path), positions={})
    assert sw == [{"ticker": "MAR", "reason": "flagged by risk (below_ma)", "rank": 0}]
    assert "{" not in sw[0]["ticker"]  # never a stringified dict


def test_iso_day_normalizes_orchestrator_human_format():
    from src.notify.morning_digest import iso_day
    assert iso_day({"today": "Thursday, July 02, 2026"}) == "2026-07-02"
    assert iso_day({"today": "2026-06-08"}) == "2026-06-08"
    assert len(iso_day({})) == 10  # falls back to today, ISO shaped


def test_build_context_attaches_close_calls_and_sell_watch(tmp_path):
    (tmp_path / "positions.json").write_text(json.dumps({"MO": {"ticker": "MO"}}))
    final = {
        "today": "2026-06-16",
        "manager_decision": {"buys": [{"ticker": "NVDA", "score": 68}]},
        "conclusions": {
            "risk": {"regime_name": "risk_on", "exits_recommended": ["GOOGL"]},
            "meta_analysis": {"candidates": [{"ticker": "MO", "composite_score": 50}],
                              "speculative_excluded": []},
        },
    }
    c = _build_context(final, _settings(tmp_path))
    assert any(r["ticker"] == "MO" and r["reason"] == "already held" for r in c["close_calls"])
    assert c["sell_watch"][0]["ticker"] == "GOOGL"
