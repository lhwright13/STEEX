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
    state.manager = SimpleNamespace(settings=_settings(enabled=False, token="t", chat="c"))
    try:
        with patch("requests.post") as post:
            out = json.loads(mcp_server.send_user_message("ping"))
        post.assert_not_called()  # dry-run
        assert out["dry_run"] is True
    finally:
        state.manager = None
