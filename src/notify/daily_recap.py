"""End-of-day recap (after the post-market run).

Closes the loop on the morning brief: how the book actually did today and how
our alpha is trending. Sent once per day to Telegram + Today's Events.

Like the morning brief, it pairs a Claude-written interpretive **lede** with
deterministic **bullet sections** (accurate numbers, never hallucinated):
    📈 Today          — equity change $ / % and end equity
    💰 Closed today   — realized exits with P&L and exit reason
    📊 Alpha          — 1W and 1M portfolio return vs SPY
    🩺 Signals        — overall recent win rate + any degrading signals

Performance + alpha reuse src/portfolio/performance.py (the same authoritative
broker equity curve the dashboard charts). Idempotent per date via the update id
`recap_<YYYY-MM-DD>`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("steex.daily_recap")

_SECTION_CAP = 5


def _today(final_state: Dict) -> str:
    # ISO-normalized: the orchestrator's final_state["today"] is a human string
    # ("Thursday, July 02, 2026"), which could never equal trades.json's ISO
    # exit_date — so '💰 Closed today' was ALWAYS empty and realized_today
    # always $0 (audit 2026-07-02). iso_day handles both formats.
    from src.notify.morning_digest import iso_day
    return iso_day(final_state)


def _load_trades(settings) -> List[Dict]:
    try:
        from pathlib import Path
        p = Path(settings.data_dir) / "trades.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug("trades load failed: %s", e)
        return []


def _perf(period: str) -> Dict:
    try:
        from src.portfolio.performance import portfolio_performance
        return portfolio_performance(period) or {}
    except Exception as e:
        logger.debug("performance(%s) failed: %s", period, e)
        return {"available": False}


def _build_context(final_state: Dict, settings) -> Dict:
    day = _today(final_state)
    trades = _load_trades(settings)

    # Trades closed today (exit_date is an ISO date/datetime string).
    today_exits = []
    realized_today = 0.0
    for t in trades:
        ed = str(t.get("exit_date") or "")[:10]
        if ed == day:
            pnl_d = t.get("pnl_dollars") or 0
            realized_today += pnl_d
            today_exits.append({
                "ticker": t.get("ticker"),
                "pnl_pct": round((t.get("pnl_pct") or 0) * 100, 1),
                "pnl_dollars": round(pnl_d, 2),
                "reason": t.get("exit_reason"),
            })
    today_exits.sort(key=lambda r: r["pnl_dollars"], reverse=True)

    # Overall realized stats (all-time, from trades.json).
    wins = [t for t in trades if (t.get("pnl_dollars") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl_dollars") or 0) < 0]
    gross_loss = sum(t["pnl_dollars"] for t in losses)
    overall = {
        "count": len(trades),
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else None,
        "total_realized_pnl": round(sum(t.get("pnl_dollars") or 0 for t in trades), 2),
        "profit_factor": round(abs(sum(t["pnl_dollars"] for t in wins) / gross_loss), 2)
                         if gross_loss else None,
    }

    perf_1d = _perf("1D")
    perf_1w = _perf("1W")
    perf_1m = _perf("1M")

    # Signal health / alpha decay (best-effort).
    signals = {}
    try:
        from src.research.alpha_monitor import AlphaDecayMonitor
        rep = AlphaDecayMonitor(settings).generate_report()
        signals = {
            "overall_recent_win_rate": rep.get("overall_recent_win_rate"),
            "degrading": rep.get("degrading") or [],
            "watch_list": rep.get("watch_list") or [],
            "total_trades": rep.get("total_trades"),
        }
    except Exception as e:
        logger.debug("signal health unavailable: %s", e)

    return {
        "date": day,
        "today_exits": today_exits,
        "realized_today": round(realized_today, 2),
        "overall": overall,
        "perf_1d": perf_1d.get("summary") if perf_1d.get("available") else None,
        "perf_1w": perf_1w.get("summary") if perf_1w.get("available") else None,
        "perf_1m": perf_1m.get("summary") if perf_1m.get("available") else None,
        "signals": signals,
    }


def _fmt_signed(v, pct: bool = False, dollar: bool = False) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if v >= 0 else ""
    if dollar:
        return f"{sign}${v:,.0f}"
    if pct:
        return f"{sign}{v:.2f}%"
    return f"{sign}{v}"


def _alpha_phrase(summary: Optional[Dict], label: str) -> Optional[str]:
    if not summary:
        return None
    port = summary.get("portfolio_return_pct")
    alpha = summary.get("alpha_pct")
    if port is None:
        return None
    if alpha is not None:
        return f"{label} {_fmt_signed(port, pct=True)} ({_fmt_signed(alpha, pct=True)} vs SPY)"
    return f"{label} {_fmt_signed(port, pct=True)}"


def _format_sections(ctx: Dict) -> str:
    lines: List[str] = []

    d1 = ctx.get("perf_1d")
    if d1:
        start, end = d1.get("start_equity"), d1.get("end_equity")
        chg = (end - start) if (start is not None and end is not None) else None
        bits = []
        if chg is not None:
            bits.append(_fmt_signed(chg, dollar=True))
        if d1.get("portfolio_return_pct") is not None:
            bits.append(f"({_fmt_signed(d1.get('portfolio_return_pct'), pct=True)})")
        if end is not None:
            bits.append(f"| equity ${end:,.0f}")
        lines.append(f"📈 Today: {' '.join(bits)}".rstrip())

    exits = ctx.get("today_exits") or []
    if exits:
        shown = exits[:_SECTION_CAP]
        body = "; ".join(
            f"{e['ticker']} {_fmt_signed(e['pnl_pct'], pct=True)} "
            f"({_fmt_signed(e['pnl_dollars'], dollar=True)}"
            f"{', ' + e['reason'] if e.get('reason') else ''})"
            for e in shown
        )
        more = len(exits) - len(shown)
        if more > 0:
            body += f"; +{more} more"
        lines.append(f"💰 Closed today: {body} | realized {_fmt_signed(ctx.get('realized_today'), dollar=True)}")
    else:
        lines.append("💰 Closed today: nothing closed")

    alpha_bits = [p for p in (
        _alpha_phrase(ctx.get("perf_1w"), "1W"),
        _alpha_phrase(ctx.get("perf_1m"), "1M"),
    ) if p]
    if alpha_bits:
        lines.append(f"📊 Alpha: {' | '.join(alpha_bits)}")

    sig = ctx.get("signals") or {}
    if sig:
        wr = sig.get("overall_recent_win_rate")
        parts = []
        if wr is not None:
            parts.append(f"recent win rate {wr * 100:.0f}%" if wr <= 1 else f"recent win rate {wr:.0f}%")
        degrading = sig.get("degrading") or []
        parts.append(f"degrading: {', '.join(degrading)}" if degrading else "all healthy")
        lines.append(f"🩺 Signals: {' | '.join(parts)}")

    return "\n".join(lines)


def _recap_summarizer(settings) -> Callable[[Dict], str]:
    """Claude writes the interpretive lede; code appends deterministic sections."""
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
            "You write the LEDE of the trader's END-OF-DAY RECAP for their "
            "automated trading system. In 2-3 plain-language sentences, interpret "
            "how the book did today (the day's equity change), how our ALPHA is "
            "trending versus the S&P 500 over the last week and month, and call "
            "out anything notable (a big winner/loser closed today, or a "
            "degrading signal). Do NOT list every ticker or number — those are in "
            "the bullet sections below your lede. Use the specific figures given. "
            'No preamble, no markdown. Output ONLY JSON: {"summary": "..."}'
        )
        # LLM lede is best-effort: still ship the deterministic sections with a
        # canned lede when the CLI is down (see morning_digest for rationale).
        try:
            conclusion, _ = run_agent(
                ctx, role="DailyRecap", system_prompt=prompt,
                task_message=f"Today's results:\n{event.get('context')}",
                conclusion_type=EventSummary, max_turns=1, needs_tools=False,
                model=getattr(settings, "event_resolver_model", None) or None,
            )
            lede = (conclusion.summary if conclusion else "") or "End-of-day recap."
        except Exception as e:
            logger.warning("recap lede LLM call failed (%s); using canned lede", e)
            lede = "End-of-day recap (auto-generated; summary agent unavailable)."
        sections = _format_sections(event.get("context") or {})
        return f"{lede}\n\n{sections}".strip()
    return summarize


def send_daily_recap(
    final_state: Dict,
    settings,
    summarizer: Optional[Callable[[Dict], str]] = None,
    send: bool = True,
):
    """Build + send the once-per-day end-of-day recap. Returns the UserUpdate, or
    None if already sent today (idempotent) or disabled."""
    if not getattr(settings, "daily_recap_enabled", True):
        return None

    from src.notify.event_summary import summarize_and_notify

    day = _today(final_state)
    context = _build_context(final_state, settings)
    return summarize_and_notify(
        {
            "id": f"recap_{day}",
            "type": "system",
            "title": f"End-of-day recap — {day}",
            "context": context,
        },
        settings=settings,
        summarizer=summarizer or _recap_summarizer(settings),
        send=send,
        max_sentences=None,  # multi-section recap; the lede is already bounded
    )
