"""Morning market brief (after the morning screen run).

Distills what the screening workflow already collected — market regime + VIX, the
risk/portfolio read, the manager's picks, and who we're watching — into ONE
Telegram message + a Today's Events entry, once per day. Reuses the P1-2
`summarize_and_notify` plumbing (Claude summary, idempotency, user_updates,
Telegram send, length clamp), so it's the same "event summary" agent the user
already gets event notifications from — just pointed at the daily screen.

Idempotent per date via the update id `digest_<YYYY-MM-DD>`, so although the
screen mode runs several times a day, only the FIRST screen of the day sends.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

logger = logging.getLogger("steex.morning_digest")


def _build_context(final_state: Dict, settings) -> Dict:
    """Pull the morning's numbers out of the screen run's conclusions."""
    concl = (final_state or {}).get("conclusions") or {}
    risk = concl.get("risk") or {}
    md = (final_state or {}).get("manager_decision") or {}
    buys = md.get("buys") or []

    figs = [
        f.get("name") for f in (getattr(settings, "event_figures", None) or [])
        if isinstance(f, dict) and f.get("name")
    ] or ["realDonaldTrump"]

    return {
        "date": (final_state or {}).get("today"),
        "regime": risk.get("regime_name"),
        "regime_confidence": risk.get("regime_confidence"),
        "vix": risk.get("vix_level"),
        "entries_allowed": risk.get("entries_allowed"),
        "sizing_multiplier": risk.get("sizing_multiplier"),
        "positions": risk.get("position_count"),
        "equity": risk.get("portfolio_equity"),
        "cash": risk.get("cash_available"),
        "exits_recommended": risk.get("exits_recommended"),
        "risk_alerts": risk.get("risk_alerts"),
        "risk_reasoning": risk.get("reasoning"),
        "entries_approved": md.get("entries_approved"),
        "picks": [{"ticker": b.get("ticker"), "score": b.get("score")} for b in buys],
        "manager_reasoning": md.get("reasoning"),
        "watching_figures": figs,
    }


def _digest_summarizer(settings) -> Callable[[Dict], str]:
    """A tool-free Claude call that writes the morning brief from the context."""
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
            "You write the trader's MORNING MARKET BRIEF for their automated trading "
            "system, produced right after the morning screen. In 3-5 plain-language "
            "sentences, cover: (1) the market regime + VIX and what it implies for risk; "
            "(2) the portfolio — open positions, equity, and any exits flagged; (3) whether "
            "the system is entering anything today and the top pick(s) by ticker; and (4) "
            "who/what we're watching for news. Use the specific numbers given. No preamble, "
            'no markdown. Output ONLY JSON: {"summary": "..."}'
        )
        conclusion, _ = run_agent(
            ctx, role="MorningDigest", system_prompt=prompt,
            task_message=f"This morning's data:\n{event.get('context')}",
            conclusion_type=EventSummary, max_turns=1, needs_tools=False,
            model=getattr(settings, "event_resolver_model", None) or None,
        )
        return conclusion.summary if conclusion else ""
    return summarize


def send_morning_digest(
    final_state: Dict,
    settings,
    summarizer: Optional[Callable[[Dict], str]] = None,
    send: bool = True,
):
    """Build + send the once-per-day morning brief. Returns the UserUpdate, or
    None if already sent today (idempotent) or disabled."""
    if not getattr(settings, "morning_digest_enabled", True):
        return None

    from src.notify.event_summary import summarize_and_notify

    day = (final_state or {}).get("today") or datetime.now(timezone.utc).date().isoformat()
    context = _build_context(final_state, settings)
    return summarize_and_notify(
        {
            "id": f"digest_{day}",
            "type": "system",
            "title": f"Morning market brief — {day}",
            "context": context,
        },
        settings=settings,
        summarizer=summarizer or _digest_summarizer(settings),
        send=send,
    )
