"""P1-1: the messaging layer — dry-run safety, osascript invocation, guards.

The real send can only be verified by the user receiving a text; here we assert
everything around it: dry-run logs (never sends), live mode invokes osascript
with the message as an ARGUMENT (not interpolated), the no-handle guard, and that
a send failure is reported rather than raised.
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from src.notify import messaging


def _settings(enabled=False, to=""):
    return SimpleNamespace(messaging_enabled=enabled, imessage_to=to)


def test_dry_run_does_not_send(caplog):
    with patch("src.notify.messaging.subprocess.run") as run:
        res = messaging.send_user_message("hello", settings=_settings(enabled=False, to="+1555"))
    run.assert_not_called()
    assert res["dry_run"] is True and res["sent"] is True
    assert res["to"] is None  # don't leak the handle in dry-run result


def test_live_invokes_osascript_with_text_as_argument():
    with patch("src.notify.messaging.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stderr="")
        res = messaging.send_user_message('hi "there"\nnewline',
                                          settings=_settings(enabled=True, to="+1555"))
    assert res["sent"] is True and res["dry_run"] is False and res["to"] == "+1555"
    args = run.call_args.args[0]
    # runs osascript inside the GUI session via `launchctl asuser <uid>`
    assert args[0] == "launchctl" and "asuser" in args and "osascript" in args
    # message + handle are passed as argv, NOT interpolated into the script
    assert "+1555" in args and 'hi "there"\nnewline' in args


def test_live_without_handle_is_guarded():
    with patch("src.notify.messaging.subprocess.run") as run:
        res = messaging.send_user_message("x", settings=_settings(enabled=True, to=""))
    run.assert_not_called()
    assert res["sent"] is False and "error" in res


def test_send_failure_is_reported_not_raised():
    with patch("src.notify.messaging.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stderr="not allowed")
        res = messaging.send_user_message("x", settings=_settings(enabled=True, to="+1555"))
    assert res["sent"] is False and res["dry_run"] is False


def test_to_override_beats_configured_handle():
    with patch("src.notify.messaging.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stderr="")
        messaging.send_user_message("x", settings=_settings(enabled=True, to="+1555"), to="+1999")
    assert "+1999" in run.call_args.args[0]


def test_mcp_tool_wraps_the_sender():
    import src.agents.mcp_server as mcp_server
    import src.agents.mcp_tools._state as state
    import json
    state.manager = SimpleNamespace(settings=_settings(enabled=False, to="+1555"))
    try:
        with patch("src.notify.messaging.subprocess.run") as run:
            out = json.loads(mcp_server.send_user_message("ping"))
        run.assert_not_called()  # dry-run
        assert out["dry_run"] is True
    finally:
        state.manager = None
