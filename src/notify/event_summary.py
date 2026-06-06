"""Event-summary stage (P1-2): turn a trading event into a user notification.

One entry point, `summarize_and_notify(event)`, used by every producer (an
event-trigger fill, a scheduled buy/sell, a big move). It generates a concise
Claude summary of *why* the event happened, records it on the user_updates
stream (P0-3), and pushes it to the user via the messaging layer (P1-1).

Idempotent per event id — the same event is never double-notified. The Claude
call is injectable (`summarizer=`) so the stage is fully testable without the
CLI, and any failure degrades to the event's own title rather than raising into
a trading path.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from src.notify import user_updates

logger = logging.getLogger("steex.event_summary")

# event type -> user_updates severity
_SEVERITY = {
    "buy": "success", "event_trade": "success",
    "sell": "info", "big_move": "warning", "system": "info",
}


def _default_title(event: Dict) -> str:
    t, tk = event.get("type", "system"), event.get("ticker", "")
    if t in ("buy", "event_trade"):
        return f"Bought {tk}" if tk else "New position"
    if t == "sell":
        return f"Sold {tk}" if tk else "Position closed"
    if t == "big_move":
        ctx = event.get("context", {})
        arrow = "▲" if ctx.get("direction") == "up" else "▼"
        return f"{tk} {arrow} {abs(ctx.get('move_pct', 0))}%" if tk else "Big move"
    return event.get("title") or "Update"


def _claude_summarizer(settings) -> Callable[[Dict], str]:
    """Build the default summarizer: a tool-free Claude call returning 3-5 sentences."""
    def summarize(event: Dict) -> str:
        from pathlib import Path
        from src.agents.nodes import run_agent
        from src.agents.state import RunnerContext
        from src.agents.registry import AgentRegistry
        from src.agents.evolution import PromptEvolver
        from src.agents.conclusions import EventSummary

        root = Path(__file__).resolve().parents[2]
        ctx = RunnerContext(
            settings=settings, paper=True, dry_run=True, auto_confirm=False,
            verbose=False, registry=AgentRegistry(root / "config" / "agents.yaml"),
            evolver=PromptEvolver(settings.data_dir), project_root=root,
        )
        prompt = (
            "You write a brief notification for the trader about one event in their "
            "automated system. Explain WHAT happened and WHY, in plain language, in "
            "3-5 concise sentences. No preamble, no markdown. Output ONLY JSON: "
            '{"summary": "..."}'
        )
        task = f"Event:\n{event}"
        conclusion, _ = run_agent(
            ctx, role="EventSummary", system_prompt=prompt, task_message=task,
            conclusion_type=EventSummary, max_turns=1, needs_tools=False,
            model=getattr(settings, "event_resolver_model", None) or None,
        )
        return conclusion.summary if conclusion else event.get("title", "")
    return summarize


def summarize_and_notify(
    event: Dict,
    settings=None,
    summarizer: Optional[Callable[[Dict], str]] = None,
    send: bool = True,
):
    """Summarize one event, record it on user_updates, and notify the user.

    `event` requires: id, type (buy|sell|event_trade|big_move|system). Optional:
    ticker, title, context (dict), links (list). Returns the UserUpdate, or None
    if it was already notified (idempotent) or had no id.
    """
    if not event.get("id"):
        logger.error("summarize_and_notify: event has no id; skipping")
        return None
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()
    data_dir = settings.data_dir
    eid = str(event["id"])

    # Idempotent: never double-notify the same event.
    if user_updates.get_update(data_dir, eid):
        return None

    summarizer = summarizer or _claude_summarizer(settings)
    try:
        summary = summarizer(event) or ""
    except Exception as e:
        logger.error("event summary failed (%s); using title", e)
        summary = ""

    title = event.get("title") or _default_title(event)
    payload = dict(event.get("context", {}))
    if event.get("ticker"):
        payload.setdefault("ticker", event["ticker"])
    rec = user_updates.write_update(
        data_dir,
        type=event.get("type", "system"),
        title=title,
        summary=summary,
        severity=_SEVERITY.get(event.get("type"), "info"),
        payload=payload,
        links=event.get("links", []),
        update_id=eid,
    )
    if send:
        from src.notify.messaging import send_user_message
        body = f"{title}\n{summary}".strip()
        send_user_message(body, settings=settings)
    return rec
