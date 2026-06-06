"""Outbound user notifications — channel abstraction + Telegram sender (P1-1).

Telegram works over plain HTTPS (no GUI, no desktop session, no permissions), so
it sends reliably from cron on a headless Mac mini — unlike iMessage, which needs
a logged-in Aqua session. Notifications arrive as a push message in the user's
Telegram app.

Sending is gated by `settings.messaging_enabled`: off => dry-run (log only), so
the pipeline is testable without a phone. A notification must never raise into a
trading path, so failures are logged and returned, not propagated.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("steex.messaging")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class Channel(ABC):
    """A delivery channel. Telegram today; SMS/Pushover/email can be added later."""

    @abstractmethod
    def send(self, text: str) -> bool:
        ...


class TelegramChannel(Channel):
    """Send a message to a Telegram chat via the Bot API (HTTPS POST)."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            logger.error("Telegram token/chat_id missing; not sending")
            return False
        try:
            import requests
            r = requests.post(
                _TELEGRAM_API.format(token=self.token),
                json={"chat_id": self.chat_id, "text": text,
                      "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code != 200:
                logger.error("Telegram send failed (%d): %s", r.status_code, r.text[:300])
                return False
            return True
        except Exception as e:
            logger.error("Telegram send error: %s", e)
            return False


class DryRunChannel(Channel):
    """Logs the message instead of sending (messaging_enabled is off)."""

    def send(self, text: str) -> bool:
        logger.info("[messaging dry-run] would send: %s", text)
        return True


def get_channel(settings, chat_id: Optional[str] = None) -> Channel:
    if not getattr(settings, "messaging_enabled", False):
        return DryRunChannel()
    return TelegramChannel(
        token=(getattr(settings, "telegram_bot_token", "") or "").strip(),
        chat_id=(chat_id or getattr(settings, "telegram_chat_id", "") or "").strip(),
    )


def send_user_message(text: str, settings=None, to: Optional[str] = None) -> dict:
    """Send a notification to the configured user via Telegram. Returns a result dict.

    Respects `settings.messaging_enabled` (off => dry-run log). `to` overrides the
    configured chat_id. Never raises.
    """
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()

    enabled = bool(getattr(settings, "messaging_enabled", False))
    token = (getattr(settings, "telegram_bot_token", "") or "").strip()
    chat_id = (to or getattr(settings, "telegram_chat_id", "") or "").strip()

    if enabled and (not token or not chat_id):
        logger.error("messaging_enabled but Telegram bot token / chat id not configured")
        return {"sent": False, "dry_run": False, "error": "telegram not configured"}

    ok = get_channel(settings, chat_id=chat_id).send(text)
    return {"sent": ok, "dry_run": not enabled, "to": chat_id if enabled else None}
