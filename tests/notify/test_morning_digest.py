"""Morning market brief: distill the screen run -> one Telegram message + Today's
Events entry, once per day (idempotent)."""
from types import SimpleNamespace

from src.notify import user_updates
from src.notify.morning_digest import send_morning_digest, _build_context


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
