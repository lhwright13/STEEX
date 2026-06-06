"""P1-2: the event-summary stage — summarize -> user_updates -> notify, idempotent."""
from types import SimpleNamespace
from unittest.mock import patch

from src.notify import user_updates
from src.notify.event_summary import summarize_and_notify, _default_title


def _settings(tmp_path):
    return SimpleNamespace(data_dir=str(tmp_path), messaging_enabled=False, imessage_to="")


def _stub_summary(_event):
    return "We bought it because the signal was strong. Stop is set."


def test_writes_user_update_and_sends(tmp_path):
    s = _settings(tmp_path)
    with patch("src.notify.messaging.subprocess.run") as run:
        rec = summarize_and_notify(
            {"id": "e1", "type": "event_trade", "ticker": "DELL",
             "context": {"headline": "buy a Dell"}},
            settings=s, summarizer=_stub_summary,
        )
    # message attempted (dry-run -> osascript not actually called)
    run.assert_not_called()
    assert rec is not None and rec.type == "event_trade" and rec.title == "Bought DELL"
    # recorded on the stream and retrievable by id (deep-link)
    got = user_updates.get_update(str(tmp_path), "e1")
    assert got is not None and "bought it" in got.summary
    assert got.payload["headline"] == "buy a Dell" and got.payload["ticker"] == "DELL"


def test_idempotent_per_event_id(tmp_path):
    s = _settings(tmp_path)
    ev = {"id": "dup", "type": "buy", "ticker": "AAPL"}
    first = summarize_and_notify(ev, settings=s, summarizer=_stub_summary, send=False)
    second = summarize_and_notify(ev, settings=s, summarizer=_stub_summary, send=False)
    assert first is not None and second is None  # not double-notified
    assert len(user_updates.read_updates(str(tmp_path))) == 1


def test_missing_id_is_skipped(tmp_path):
    assert summarize_and_notify({"type": "buy"}, settings=_settings(tmp_path),
                                summarizer=_stub_summary, send=False) is None


def test_summarizer_failure_degrades_to_title(tmp_path):
    def boom(_):
        raise RuntimeError("model down")
    rec = summarize_and_notify(
        {"id": "e2", "type": "sell", "ticker": "MO"},
        settings=_settings(tmp_path), summarizer=boom, send=False,
    )
    assert rec is not None and rec.summary == ""  # didn't raise; title still set
    assert rec.title == "Sold MO"


def test_default_titles():
    assert _default_title({"type": "event_trade", "ticker": "DELL"}) == "Bought DELL"
    assert _default_title({"type": "big_move", "ticker": "NVDA",
                           "context": {"direction": "up", "move_pct": 12.0}}) == "NVDA ▲ 12.0%"


def test_severity_mapping(tmp_path):
    s = _settings(tmp_path)
    big = summarize_and_notify({"id": "m1", "type": "big_move", "ticker": "NVDA",
                                "context": {"direction": "up", "move_pct": 9}},
                               settings=s, summarizer=_stub_summary, send=False)
    assert big.severity == "warning"
