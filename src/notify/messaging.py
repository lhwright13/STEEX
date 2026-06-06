"""Outbound user notifications — channel abstraction + iMessage sender (P1-1).

The event-summary stage (P1-2) calls `send_user_message()` directly; agents call
the MCP tool (`mcp_tools/notify.py`) that wraps the same function. Sending is
gated by `settings.messaging_enabled`: off => dry-run (log only), so the whole
pipeline is testable without touching a phone. A notification must never raise
into a trading path, so failures are logged and returned, not propagated.

iMessage is sent via macOS `osascript`/AppleScript. The message text is passed
as a *run-handler argument*, not interpolated into the script, so quotes/newlines
in the text can't break or inject into the AppleScript.
"""
from __future__ import annotations

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("steex.messaging")

# AppleScript that takes (handle, message) as run-handler args — no interpolation.
# `launch` ensures Messages is running (a common cause of error -10810 is Messages
# not being open / no GUI session). It needs an active Aqua login session, so the
# Mac mini must be logged into the desktop (auto-login) for cron sends to work.
_IMESSAGE_SCRIPT = (
    "on run {targetHandle, msg}\n"
    '    tell application "Messages"\n'
    "        launch\n"
    "        set svc to 1st service whose service type = iMessage\n"
    "        set theBuddy to buddy targetHandle of svc\n"
    "        send msg to theBuddy\n"
    "    end tell\n"
    "end run"
)


class Channel(ABC):
    """A delivery channel. iMessage today; SMS/Telegram/email can be added later."""

    @abstractmethod
    def send(self, to: str, text: str) -> bool:
        ...


class IMessageChannel(Channel):
    """Send via macOS Messages using AppleScript. macOS-only; needs Automation
    permission to control Messages (granted once in System Settings)."""

    def send(self, to: str, text: str) -> bool:
        base = ["osascript", "-e", _IMESSAGE_SCRIPT, to, text]
        # Plain osascript works when we're already inside the GUI (Aqua) session
        # (e.g. a Terminal opened via Screen Sharing). From a context with no GUI
        # session (SSH shell / crontab) it fails with -10810; in that case try the
        # `launchctl asuser <uid>` bridge into the logged-in session (may itself
        # need privileges, so it's only a best-effort fallback).
        attempts = [base, ["launchctl", "asuser", str(os.getuid())] + base]
        last_err = ""
        for cmd in attempts:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    return True
                last_err = (r.stderr or "").strip()[:300]
                # Only the no-GUI-session failure is worth trying the bridge for.
                if "-10810" not in last_err and "audit session" not in last_err:
                    break
            except Exception as e:
                last_err = str(e)
        logger.error("iMessage send failed: %s", last_err)
        return False


class DryRunChannel(Channel):
    """Logs the message instead of sending (messaging_enabled is off)."""

    def send(self, to: str, text: str) -> bool:
        logger.info("[messaging dry-run] would send to %s: %s", to or "<unset>", text)
        return True


def get_channel(settings) -> Channel:
    return IMessageChannel() if getattr(settings, "messaging_enabled", False) else DryRunChannel()


def send_user_message(text: str, settings=None, to: Optional[str] = None) -> dict:
    """Send a notification to the configured user. Returns a result dict.

    Respects `settings.messaging_enabled` (off => dry-run log). `to` overrides
    the configured `imessage_to` handle. Never raises.
    """
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()

    enabled = bool(getattr(settings, "messaging_enabled", False))
    handle = (to or getattr(settings, "imessage_to", "") or "").strip()

    if enabled and not handle:
        logger.error("messaging_enabled but no imessage_to configured — not sending")
        return {"sent": False, "dry_run": False, "error": "no destination handle"}

    ok = get_channel(settings).send(handle, text)
    return {"sent": ok, "dry_run": not enabled, "to": handle if enabled else None}
