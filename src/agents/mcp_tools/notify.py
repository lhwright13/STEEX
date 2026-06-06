"""Notification MCP tools (P1-1).

Exposes the messaging layer to agents. The core logic lives in
src/notify/messaging.py so the P1-2 pipeline stage can call it directly without
going through MCP; this is the agent-facing wrapper over the same function.
"""
import logging

from .server import mcp
from . import _state
from ._util import _safe_json

logger = logging.getLogger("steex.mcp")


@mcp.tool()
def send_user_message(text: str, to: str = "") -> str:
    """Send a short notification to the user (Telegram).

    Respects the `messaging_enabled` kill switch — when off, this is a dry run
    that logs instead of sending. `to` optionally overrides the configured chat id.
    """
    from src.notify.messaging import send_user_message as _send
    # Settings only — a notification never needs the broker/QuantManager, so don't
    # let a broker init failure take the Telegram path down with it.
    settings = _state.get_settings_only()
    return _safe_json(_send(text, settings=settings, to=to or None))


__all__ = ["send_user_message"]
