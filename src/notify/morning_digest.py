"""Morning market brief (after the morning screen run).

Distills what the screening workflow already collected into ONE Telegram message
+ a Today's Events entry, once per day. Reuses the P1-2 `summarize_and_notify`
plumbing (Claude summary, idempotency, user_updates, Telegram send).

The brief has two parts:
  * an interpretive **lede** written by Claude (regime/VIX read + the day's
    posture), and
  * deterministic **bullet sections** assembled in code so the tickers, scores
    and reasons are always accurate (never hallucinated by the LLM):
      🎯 Bought          — the manager's approved entries
      👀 Close calls     — names the screen surfaced but we did NOT buy, + why
      ⚠️ Watching to sell — open positions near an exit trigger (proximity)

Idempotent per date via the update id `digest_<YYYY-MM-DD>`, so although the
screen mode runs several times a day, only the FIRST screen of the day sends.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("steex.morning_digest")

# Proximity thresholds for the pre-market sell-watch. The morning brief runs
# before exits actually trigger (those fire in the midday monitor), so "watching
# to sell" means *approaching* a trigger, not triggered.
_STOP_PROXIMITY_PCT = 7.0      # within this % above the stop → flag
_MAX_HOLD_BUFFER_DAYS = 10     # within this many trading days of max hold → flag
_SECTION_CAP = 3               # max rows per section (keeps it a brief)


def _load_positions(settings) -> Dict[str, Dict]:
    """Locally tracked open positions (data/positions.json), keyed by ticker."""
    try:
        from pathlib import Path
        p = Path(settings.data_dir) / "positions.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug("positions load failed: %s", e)
        return {}


def _close_calls(final_state: Dict, settings, held: set) -> List[Dict]:
    """Names the screen surfaced but we did NOT buy, each with a reason.

    Sources: the meta-analysis synthesized slate (`candidates` +
    `speculative_excluded`), diffed against the manager's approved buys. The
    reason for passing is computed deterministically from the score gate,
    exclusion list, and current holdings.
    """
    concl = (final_state or {}).get("conclusions") or {}
    meta = concl.get("meta_analysis") or {}
    md = (final_state or {}).get("manager_decision") or {}
    approved = {(b.get("ticker") or "").upper() for b in (md.get("buys") or [])}
    min_score = float(getattr(settings, "manager_min_score_entry", 40) or 40)

    excluded = {str(t).upper() for t in (meta.get("speculative_excluded") or [])}
    candidates = meta.get("candidates") or []

    # Fallback: if meta-analysis produced no slate, union the raw variant picks
    # so the brief still shows what each analyst surfaced.
    if not candidates:
        seen = {}
        for v in (final_state or {}).get("variant_conclusions") or []:
            for c in ((v.get("conclusion") or {}).get("candidates") or []):
                tk = (c.get("ticker") or "").upper()
                if tk:
                    sc = c.get("score")
                    seen[tk] = max(seen.get(tk) or 0, sc) if sc is not None else seen.get(tk)
        candidates = [{"ticker": t, "composite_score": s} for t, s in seen.items()]

    rows: Dict[str, Dict] = {}
    for c in candidates:
        tk = (c.get("ticker") or "").upper()
        if not tk or tk in approved:
            continue
        score = c.get("composite_score")
        if score is None:
            score = c.get("score")
        if tk in excluded:
            reason = "excluded — low conviction"
        elif score is not None and score < min_score:
            reason = f"below score gate ({score:.0f} < {min_score:.0f})"
        elif tk in held:
            reason = "already held"
        else:
            reason = "passed by manager"
        rows[tk] = {"ticker": tk, "score": score, "reason": reason}

    # Excluded names that never made the candidate list still count as close calls.
    for tk in excluded:
        if tk and tk not in approved and tk not in rows:
            rows[tk] = {"ticker": tk, "score": None, "reason": "excluded — low conviction"}

    out = list(rows.values())
    out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return out


def _sell_watch(final_state: Dict, settings, positions: Dict[str, Dict]) -> List[Dict]:
    """Open positions approaching an exit trigger (pre-market proximity read).

    Combines two sources: anything the risk agent already flagged
    (`exits_recommended`), and a proximity scan over current holdings — distance
    to the trailing stop, closeness to max hold, and below the 50-day MA. Each is
    best-effort and guarded so a bad ticker never breaks the brief.
    """
    concl = (final_state or {}).get("conclusions") or {}
    risk = concl.get("risk") or {}
    flagged = {str(t).upper() for t in (risk.get("exits_recommended") or [])}

    rows: Dict[str, Dict] = {}
    for tk in flagged:
        rows[tk] = {"ticker": tk, "reason": "flagged by risk", "rank": 0}

    if not positions:  # nothing to scan; avoid importing price/signal providers
        return list(rows.values())

    try:
        from src.data.price import PriceProvider
        from src.strategy.signals import SignalGenerator
        price_provider = PriceProvider()
        sig = SignalGenerator(settings=settings, price_provider=price_provider)
        max_hold = int(getattr(settings, "max_hold_days", 60) or 60)
        now = datetime.now(timezone.utc)

        for tkr, p in positions.items():
            tk = (p.get("ticker") or tkr or "").upper()
            if not tk or tk in rows:
                continue
            reasons: List[str] = []
            rank = 9.0
            try:
                price = price_provider.get_latest_price(tk)
            except Exception:
                price = None
            stop = p.get("current_stop")
            if price and stop:
                pct_above = (price - stop) / price * 100
                if 0 <= pct_above <= _STOP_PROXIMITY_PCT:
                    reasons.append(f"{pct_above:.1f}% above stop")
                    rank = min(rank, pct_above)
            # Approaching max hold.
            ed = p.get("entry_date")
            if ed:
                try:
                    dt = datetime.fromisoformat(str(ed).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    cal_days = (now - dt).days
                    trading_days = int(cal_days * 5 / 7)
                    if trading_days >= max_hold - _MAX_HOLD_BUFFER_DAYS:
                        reasons.append(f"approaching max hold ({trading_days}/{max_hold}d)")
                        rank = min(rank, 1.0)
                except Exception:
                    pass
            # Below the 50-day MA (end-of-day exit signal).
            try:
                entry = p.get("entry_price") or price or 0
                if sig.check_ma_exit(tk, entry):
                    reasons.append("below 50-day MA")
                    rank = min(rank, 2.0)
            except Exception:
                pass
            if reasons:
                rows[tk] = {"ticker": tk, "reason": "; ".join(reasons), "rank": rank}
    except Exception as e:
        logger.debug("sell-watch proximity scan failed: %s", e)

    out = list(rows.values())
    out.sort(key=lambda r: r.get("rank", 9.0))
    return out


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

    positions = _load_positions(settings)
    held = {(p.get("ticker") or k or "").upper() for k, p in positions.items()}

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
        "close_calls": _close_calls(final_state, settings, held),
        "sell_watch": _sell_watch(final_state, settings, positions),
    }


def _fmt_score(s) -> str:
    try:
        return f"{float(s):.0f}"
    except (TypeError, ValueError):
        return "—"


def _format_sections(ctx: Dict) -> str:
    """Deterministic bullet sections appended under the Claude lede."""
    lines: List[str] = []

    picks = ctx.get("picks") or []
    if picks:
        body = ", ".join(f"{p['ticker']} ({_fmt_score(p.get('score'))})" for p in picks if p.get("ticker"))
        lines.append(f"🎯 Bought: {body}")
    else:
        lines.append("🎯 Bought: no new entries today")

    cc = ctx.get("close_calls") or []
    if cc:
        shown = cc[:_SECTION_CAP]
        body = "; ".join(
            f"{r['ticker']} ({_fmt_score(r.get('score'))}) — {r['reason']}" for r in shown
        )
        more = len(cc) - len(shown)
        if more > 0:
            body += f"; +{more} more"
        lines.append(f"👀 Close calls: {body}")

    sw = ctx.get("sell_watch") or []
    if sw:
        shown = sw[:_SECTION_CAP]
        body = "; ".join(f"{r['ticker']} — {r['reason']}" for r in shown)
        more = len(sw) - len(shown)
        if more > 0:
            body += f"; +{more} more"
        lines.append(f"⚠️ Watching to sell: {body}")

    return "\n".join(lines)


def _digest_summarizer(settings) -> Callable[[Dict], str]:
    """A tool-free Claude call: writes the interpretive lede, then code appends
    the deterministic bullet sections."""
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
            "You write the LEDE of the trader's MORNING MARKET BRIEF, produced "
            "right after the morning screen. In 2-3 plain-language sentences, "
            "interpret: (1) the market regime + VIX and what it implies for risk "
            "today; (2) the portfolio posture — open positions, equity, cash to "
            "deploy; and (3) the day's stance (are we entering, holding, or "
            "defensive) and who/what we're watching for news. Do NOT list "
            "individual tickers or scores — those are shown in bullet sections "
            "below your lede. Use the specific numbers given. No preamble, no "
            'markdown. Output ONLY JSON: {"summary": "..."}'
        )
        conclusion, _ = run_agent(
            ctx, role="MorningDigest", system_prompt=prompt,
            task_message=f"This morning's data:\n{event.get('context')}",
            conclusion_type=EventSummary, max_turns=1, needs_tools=False,
            model=getattr(settings, "event_resolver_model", None) or None,
        )
        lede = (conclusion.summary if conclusion else "") or "Morning market brief."
        sections = _format_sections(event.get("context") or {})
        return f"{lede}\n\n{sections}".strip()
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
        max_sentences=None,  # multi-section brief; the lede is already bounded
    )
