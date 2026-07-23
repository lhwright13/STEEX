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

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("steex.messaging")

# Failed sends are queued here and retried by flush_outbox() at the start of
# every scheduled run. During the 07-17..07-22 outage the heartbeat alerted
# every morning but the Telegram POST died with the same network error it was
# reporting — the alert must survive until the network comes back.
_OUTBOX_FILE = "notify_outbox.json"
_OUTBOX_MAX_AGE = timedelta(hours=48)
_OUTBOX_MAX_ITEMS = 50

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"


class Channel(ABC):
    """A delivery channel. Telegram today; SMS/Pushover/email can be added later."""

    @abstractmethod
    def send(self, text: str) -> bool:
        ...

    def send_photo(self, png_bytes: bytes, caption: str = "") -> bool:
        """Send an image. Default: unsupported channel -> log and report False."""
        logger.info("channel %s does not support photos", type(self).__name__)
        return False


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

    def send_photo(self, png_bytes: bytes, caption: str = "") -> bool:
        """Send a PNG via Telegram sendPhoto (multipart upload)."""
        if not self.token or not self.chat_id:
            logger.error("Telegram token/chat_id missing; not sending photo")
            return False
        try:
            import requests
            r = requests.post(
                _TELEGRAM_PHOTO_API.format(token=self.token),
                data={"chat_id": self.chat_id, "caption": caption[:1024]},
                files={"photo": ("chart.png", png_bytes, "image/png")},
                timeout=30,
            )
            if r.status_code != 200:
                logger.error("Telegram photo failed (%d): %s", r.status_code, r.text[:300])
                return False
            return True
        except Exception as e:
            logger.error("Telegram photo error: %s", e)
            return False


class DryRunChannel(Channel):
    """Logs the message instead of sending (messaging_enabled is off)."""

    def send(self, text: str) -> bool:
        logger.info("[messaging dry-run] would send: %s", text)
        return True

    def send_photo(self, png_bytes: bytes, caption: str = "") -> bool:
        logger.info("[messaging dry-run] would send photo (%d bytes): %s",
                    len(png_bytes or b""), caption[:120])
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
    if enabled and not ok:
        _enqueue_failed(settings, text, chat_id)
    return {"sent": ok, "dry_run": not enabled, "to": chat_id if enabled else None}


def _outbox_path(settings) -> Optional[Path]:
    data_dir = getattr(settings, "data_dir", None)
    return Path(data_dir) / _OUTBOX_FILE if data_dir else None


def _enqueue_failed(settings, text: str, to: str) -> None:
    """Queue a failed notification for retry. Never raises."""
    try:
        p = _outbox_path(settings)
        if p is None:
            return
        items = json.loads(p.read_text()) if p.exists() else []
        if not isinstance(items, list):
            items = []
        items.append({"ts": datetime.now(timezone.utc).isoformat(),
                      "text": text, "to": to})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(items[-_OUTBOX_MAX_ITEMS:], indent=2))
        logger.warning("notification send failed; queued for retry (%d pending)",
                       len(items))
    except Exception as e:
        logger.error("could not queue failed notification: %s", e)


def flush_outbox(settings=None) -> dict:
    """Retry queued notifications whose original send failed.

    Called at the start of every scheduled run, so an alert raised while the
    network was down goes out on the next run after it recovers. Retried
    messages are prefixed so the user can tell they arrived late. Entries older
    than 48h are dropped (a stale heartbeat alert is noise by then). Never
    raises.
    """
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    try:
        p = _outbox_path(settings)
        if p is None or not p.exists():
            return {"sent": 0, "pending": 0, "dropped": 0}
        items = json.loads(p.read_text())
        if not isinstance(items, list) or not items:
            p.unlink(missing_ok=True)
            return {"sent": 0, "pending": 0, "dropped": 0}
        if not getattr(settings, "messaging_enabled", False):
            return {"sent": 0, "pending": len(items), "dropped": 0}

        now = datetime.now(timezone.utc)
        sent, dropped, remaining = 0, 0, []
        for item in items:
            try:
                ts = datetime.fromisoformat(item.get("ts", ""))
            except (TypeError, ValueError):
                ts = now
            if now - ts > _OUTBOX_MAX_AGE:
                dropped += 1
                continue
            channel = get_channel(settings, chat_id=item.get("to") or None)
            if channel.send(f"⏳ (delayed) {item.get('text', '')}"):
                sent += 1
            else:
                remaining.append(item)
        if remaining:
            p.write_text(json.dumps(remaining, indent=2))
        else:
            p.unlink(missing_ok=True)
        if sent or dropped:
            logger.info("outbox flush: %d sent, %d dropped, %d still pending",
                        sent, dropped, len(remaining))
        return {"sent": sent, "pending": len(remaining), "dropped": dropped}
    except Exception as e:
        logger.error("outbox flush failed: %s", e)
        return {"sent": 0, "pending": -1, "dropped": 0, "error": str(e)}


def send_user_photo(png_bytes: bytes, caption: str = "", settings=None,
                    to: Optional[str] = None) -> dict:
    """Send a PNG image (e.g. a performance chart) to the user via Telegram.

    Same gating/semantics as send_user_message: messaging_enabled off => dry-run
    log; never raises into a trading path.
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

    ok = get_channel(settings, chat_id=chat_id).send_photo(png_bytes, caption)
    return {"sent": ok, "dry_run": not enabled, "to": chat_id if enabled else None}
