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
    """Send a short notification to the user (iMessage on the Mac mini).

    Respects the `messaging_enabled` kill switch — when off, this is a dry run
    that logs instead of sending. `to` optionally overrides the configured handle.
    """
    from src.notify.messaging import send_user_message as _send
    mgr = _state.init_manager()
    return _safe_json(_send(text, settings=mgr.settings, to=to or None))


__all__ = ["send_user_message"]
