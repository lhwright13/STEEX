"""P1-1: the messaging layer — dry-run safety, Telegram send, guards.

Telegram sends over HTTPS, so everything is mockable: dry-run never calls the
network, live mode POSTs to the Bot API, the no-config guard fires, and a failed
send is reported rather than raised.
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from src.notify import messaging


def _settings(enabled=False, token="", chat=""):
    return SimpleNamespace(messaging_enabled=enabled,
                           telegram_bot_token=token, telegram_chat_id=chat)


def test_dry_run_does_not_send():
    with patch("requests.post") as post:
        res = messaging.send_user_message(
            "hello", settings=_settings(enabled=False, token="t", chat="c"))
    post.assert_not_called()
    assert res["dry_run"] is True and res["sent"] is True
    assert res["to"] is None  # don't leak the chat id in dry-run


def test_live_posts_to_telegram_api():
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200, text="ok")
        res = messaging.send_user_message(
            "hi there", settings=_settings(enabled=True, token="TOKEN", chat="42"))
    assert res["sent"] is True and res["dry_run"] is False and res["to"] == "42"
    url = post.call_args.args[0]
    assert "api.telegram.org/botTOKEN/sendMessage" in url
    payload = post.call_args.kwargs["json"]
    assert payload["chat_id"] == "42" and payload["text"] == "hi there"


def test_live_without_config_is_guarded():
    with patch("requests.post") as post:
        res = messaging.send_user_message("x", settings=_settings(enabled=True, token="", chat=""))
    post.assert_not_called()
    assert res["sent"] is False and "error" in res


def test_send_failure_is_reported_not_raised():
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=403, text="forbidden")
        res = messaging.send_user_message("x", settings=_settings(enabled=True, token="T", chat="c"))
    assert res["sent"] is False and res["dry_run"] is False


def test_network_error_is_reported_not_raised():
    with patch("requests.post", side_effect=RuntimeError("no net")):
        res = messaging.send_user_message("x", settings=_settings(enabled=True, token="T", chat="c"))
    assert res["sent"] is False


def test_to_override_beats_configured_chat():
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200, text="ok")
        messaging.send_user_message("x", settings=_settings(enabled=True, token="T", chat="c"), to="99")
    assert post.call_args.kwargs["json"]["chat_id"] == "99"


def test_mcp_tool_wraps_the_sender():
    import src.agents.mcp_server as mcp_server
    import src.agents.mcp_tools._state as state
    import json
    # The notification tool reads session settings directly (it must NOT build the
    # broker/QuantManager just to send a message), so seed state.settings.
    state.settings = _settings(enabled=False, token="t", chat="c")
    try:
        with patch("requests.post") as post:
            out = json.loads(mcp_server.send_user_message("ping"))
        post.assert_not_called()  # dry-run
        assert out["dry_run"] is True
    finally:
        state.settings = None


# ---- notification outbox: failed sends are queued and retried -------------

def _settings_with_dir(tmp_path, enabled=True):
    return SimpleNamespace(messaging_enabled=enabled, telegram_bot_token="T",
                           telegram_chat_id="42", data_dir=str(tmp_path))


def test_failed_send_is_queued_for_retry(tmp_path):
    s = _settings_with_dir(tmp_path)
    with patch("requests.post", side_effect=RuntimeError("no net")):
        res = messaging.send_user_message("critical alert", settings=s)
    assert res["sent"] is False
    import json
    items = json.loads((tmp_path / "notify_outbox.json").read_text())
    assert len(items) == 1 and items[0]["text"] == "critical alert"


def test_flush_outbox_resends_and_clears(tmp_path):
    s = _settings_with_dir(tmp_path)
    with patch("requests.post", side_effect=RuntimeError("no net")):
        messaging.send_user_message("critical alert", settings=s)
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200, text="ok")
        res = messaging.flush_outbox(s)
    assert res["sent"] == 1 and res["pending"] == 0
    assert not (tmp_path / "notify_outbox.json").exists()
    assert "⏳ (delayed) critical alert" in post.call_args.kwargs["json"]["text"]


def test_flush_keeps_items_that_fail_again(tmp_path):
    s = _settings_with_dir(tmp_path)
    with patch("requests.post", side_effect=RuntimeError("no net")):
        messaging.send_user_message("alert", settings=s)
        res = messaging.flush_outbox(s)
    assert res["sent"] == 0 and res["pending"] == 1
    assert (tmp_path / "notify_outbox.json").exists()


def test_flush_drops_stale_items(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    (tmp_path / "notify_outbox.json").write_text(
        json.dumps([{"ts": old, "text": "ancient", "to": "42"}]))
    s = _settings_with_dir(tmp_path)
    with patch("requests.post") as post:
        res = messaging.flush_outbox(s)
    post.assert_not_called()
    assert res["dropped"] == 1 and res["sent"] == 0
    assert not (tmp_path / "notify_outbox.json").exists()


def test_flush_without_outbox_is_noop(tmp_path):
    res = messaging.flush_outbox(_settings_with_dir(tmp_path))
    assert res == {"sent": 0, "pending": 0, "dropped": 0}


def test_no_data_dir_means_no_queue():
    with patch("requests.post", side_effect=RuntimeError("no net")):
        res = messaging.send_user_message(
            "x", settings=_settings(enabled=True, token="T", chat="c"))
    assert res["sent"] is False  # reported, and nothing written anywhere
