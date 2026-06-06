"""EventsMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class EventsMixin:
    def get_event_activity(self, limit: int = 10) -> Dict[str, Any]:
        """Recent event-trigger scans: executed trades and review verdicts.

        Reads event_scan run logs (newest first) and surfaces any trades that
        actually fired plus the post-trade review verdicts, so the dashboard
        can show what the news fast-path has been doing.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"events": [], "last_scan": None, "timestamp": datetime.utcnow().isoformat() + "Z"}

        events = []
        last_scan = None
        scanned_files = 0
        for run_file in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
            if len(events) >= limit or scanned_files >= 500:
                break
            data = self._load_json(run_file)
            if not data or data.get("mode") != "event_scan":
                continue
            scanned_files += 1
            scan = (data.get("conclusions") or {}).get("event_scan") or {}
            if last_scan is None:
                last_scan = {
                    "completed_at": data.get("completed_at"),
                    "regime": scan.get("regime"),
                    "scanned": scan.get("scanned", 0),
                }
            reviews = {r.get("ticker"): r for r in (data.get("event_reviews") or [])}
            for trade in scan.get("executed", []) or []:
                ev = trade.get("event", {})
                rv = reviews.get(trade.get("ticker"), {})
                events.append({
                    "ticker": trade.get("ticker"),
                    "price": trade.get("price"),
                    "shares": trade.get("shares"),
                    "headline": ev.get("headline"),
                    "source": ev.get("source"),
                    "url": ev.get("url"),
                    "confidence": ev.get("confidence"),
                    "published_at": ev.get("published_at"),
                    "verdict": rv.get("verdict"),
                    "verdict_reason": rv.get("reasoning"),
                    "run_id": data.get("run_id"),
                })

        return {
            "events": events,
            "last_scan": last_scan,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ---- P3-5: configured figures (dropdown source) ----------------------

    def _configured_figures(self) -> List[Dict[str, Any]]:
        """The watched figures, mirroring the orchestrator's source construction.

        Names here MUST equal what records are tagged with (``ev.figure`` =
        the figure's ``name``), so the P3-5 dropdown filter actually matches.
        In the legacy single-account case the orchestrator synthesizes the name
        ``"realDonaldTrump"`` — replicate that exactly.
        """
        figs = [
            {"name": f.get("name"), "account_id": f.get("account_id"),
             "platform": f.get("platform"), "enabled": bool(f.get("enabled", True))}
            for f in (self.settings.event_figures or [])
            if isinstance(f, dict) and f.get("name")
        ]
        if not figs:
            figs = [{
                "name": "realDonaldTrump", "platform": "truth_social",
                "account_id": getattr(self.settings, "event_truth_social_account_id", None),
                "enabled": True,
            }]
        return figs

    def get_event_figures(self) -> Dict[str, Any]:
        """Watched figures for the P3-5 dropdown (name == record figure tag)."""
        return {"figures": self._configured_figures()}

    # ---- P3-4: event-trigger panel aggregate -----------------------------

    def get_event_aggregate(self, figure: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
        """Watching feed + "why no trade?" funnel + armed strip (P3-4).

        Rolls up the per-post verdict records (P1-5) across recent event_scan
        run logs (``data/runs/*.jsonl``) — the canonical source chosen for the
        funnel (plan open-decision #6) — into three views:

          * ``feed``   - most recent posts, each with a verdict chip, clickable
                         to the post + resolver reasoning + guardrail hit
          * ``funnel`` - seen -> named -> bullish -> passed guardrails ->
                         executed, for today and the trailing week, plus a
                         drop-reason breakdown
          * ``status`` - armed flag, watched figure(s), trades today X/cap,
                         last-poll age, active cooldowns

        ``figure`` (P3-5) optionally restricts every view to one figure's posts.
        """
        now = datetime.now(timezone.utc)
        runs_dir = self.data_dir / "runs"

        records: List[Dict[str, Any]] = []   # deduped, newest-first
        seen_keys = set()
        last_scan = None
        scanned_files = 0

        if runs_dir.exists():
            for run_file in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
                if scanned_files >= 500 or len(records) >= 800:
                    break
                data = self._load_json(run_file)
                if not data or data.get("mode") != "event_scan":
                    continue
                scanned_files += 1
                scan = (data.get("conclusions") or {}).get("event_scan") or {}
                if last_scan is None:
                    last_scan = {
                        "completed_at": data.get("completed_at"),
                        "regime": scan.get("regime"),
                        "scanned": scan.get("scanned", 0),
                    }
                for rec in scan.get("records") or []:
                    key = rec.get("id") or f"{(rec.get('headline') or '')[:40]}|{rec.get('decided_at')}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    if figure and rec.get("figure") != figure:
                        continue
                    records.append(rec)

        funnel = {
            "today": self._event_funnel([r for r in records if self._rec_within(r, now, 1)]),
            "week": self._event_funnel([r for r in records if self._rec_within(r, now, 7)]),
        }
        feed = [self._event_feed_item(r) for r in records[:limit]]
        status = self._event_status(records, last_scan, now, figure)
        # P4-1: near-misses — named a bullish company, blocked by a guardrail.
        near_misses = [
            self._near_miss_item(r) for r in records
            if r.get("classification") == "near_miss"
        ][:limit]
        # P4-2: reaction latency from the P1-5 timings.
        latency = self._event_latency(records, limit)
        # P4-3: live event-trigger knobs (read-only; edited via the agent).
        config = self._event_config()

        return {
            "feed": feed,
            "funnel": funnel,
            "status": status,
            "near_misses": near_misses,
            "latency": latency,
            "config": config,
            "last_scan": last_scan,
            "figure": figure,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    @staticmethod
    def _near_miss_item(rec: Dict[str, Any]) -> Dict[str, Any]:
        """A blocked-but-bullish post, with the exact guardrail and would-be action."""
        verdict = rec.get("verdict") or {}
        return {
            "id": rec.get("id"),
            "headline": rec.get("headline"),
            "ticker": rec.get("ticker"),
            "confidence": verdict.get("confidence"),
            "score": rec.get("score"),
            "guardrail": rec.get("stop_reason"),       # the exact block
            "reasoning": verdict.get("reasoning"),
            "figure": rec.get("figure"),
            "published_at": rec.get("published_at"),
        }

    @staticmethod
    def _delta_seconds(a, b):
        def parse(t):
            try:
                return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            except Exception:
                return None
        da, db = parse(a), parse(b)
        if not da or not db:
            return None
        return round((db - da).total_seconds(), 1)

    @staticmethod
    def _percentile(values, pct):
        if not values:
            return None
        s = sorted(values)
        k = (len(s) - 1) * pct
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)

    def _event_latency(self, records, limit) -> Dict[str, Any]:
        """Per-event latency (published -> detected -> decided) + median/p90 (P4-2)."""
        events, totals, detects = [], [], []
        for r in records:
            detect_s = self._delta_seconds(r.get("published_at"), r.get("detected_at"))
            decide_s = self._delta_seconds(r.get("detected_at"), r.get("decided_at"))
            total_s = self._delta_seconds(r.get("published_at"), r.get("decided_at"))
            if total_s is None:
                continue
            events.append({
                "ticker": r.get("ticker"), "headline": r.get("headline"),
                "detect_s": detect_s, "decide_s": decide_s, "total_s": total_s,
                "outcome": r.get("outcome"), "published_at": r.get("published_at"),
            })
            totals.append(total_s)
            if detect_s is not None:
                detects.append(detect_s)
        return {
            "events": events[:limit],
            "count": len(totals),
            "median_total_s": self._percentile(totals, 0.5),
            "p90_total_s": self._percentile(totals, 0.9),
            "median_detect_s": self._percentile(detects, 0.5),
        }

    def _event_config(self) -> Dict[str, Any]:
        """The event-trigger knobs, read-only (P4-3). Edited via the agent, not here."""
        s = self.settings
        def pct(v):
            return f"{round(v * 100, 1)}%"
        params = [
            {"key": "event_position_pct", "label": "Position size",
             "value": pct(getattr(s, "event_position_pct", 0)),
             "help": "Fraction of portfolio deployed per event trade (kept small — these are fast, news-driven bets)."},
            {"key": "max_event_trades_per_day", "label": "Daily cap",
             "value": getattr(s, "max_event_trades_per_day", 0),
             "help": "Hard limit on event trades per day. Once hit, further signals are logged as near-misses, not traded."},
            {"key": "event_cooldown_minutes", "label": "Cooldown",
             "value": f"{getattr(s, 'event_cooldown_minutes', 0)} min",
             "help": "Minimum minutes between event trades in the same ticker, to avoid stacking on one news cycle."},
            {"key": "event_min_confidence", "label": "Min confidence",
             "value": f"{getattr(s, 'event_min_confidence', 0):.2f}",
             "help": "Minimum LLM confidence (0-1) that a post is a bullish, correctly-resolved ticker before it can trade."},
            {"key": "event_sentiment_threshold", "label": "Sentiment threshold",
             "value": getattr(s, "event_sentiment_threshold", 0),
             "help": "VADER sentiment floor for pre-tickered (watchlist) news on the non-resolver path."},
            {"key": "initial_stop_pct", "label": "Stop",
             "value": pct(getattr(s, "initial_stop_pct", 0)),
             "help": "Protective stop placed below the entry on every event fill."},
        ]
        return {"params": params, "editable_via": "agent"}

    # ---- P3-6: event-trade cards -----------------------------------------

    def get_event_trade_cards(self, limit: int = 20, day: Optional[str] = None) -> Dict[str, Any]:
        """Event-trade cards (P3-6): each fired event trade as a rich card.

        Joins the ``event_trade`` user_updates (the triggering post, entry, stop,
        review verdict — one data source, shared by Today's Events and the event
        panel) to the live holding for current price + unrealized P&L. A trade
        whose position is closed simply has ``live = None``.
        """
        from src.notify import user_updates as uu
        updates = uu.read_updates(self.data_dir, limit=200, types=["event_trade"], day=day)

        pos_by_ticker: Dict[str, Any] = {}
        try:
            for p in (self.get_portfolio_holdings().get("positions") or []):
                if p.get("ticker"):
                    pos_by_ticker[p["ticker"]] = p
        except Exception as e:  # P&L is a nice-to-have; never break the card list
            logger.debug("event-trade-card holdings join skipped: %s", e)

        cards = []
        for u in updates[:limit]:
            p = u.payload or {}
            review = p.get("review") or {}
            ticker = p.get("ticker")
            pos = pos_by_ticker.get(ticker)
            live = None
            if pos:
                live = {
                    "price": pos.get("current_price"),
                    "market_value": pos.get("market_value"),
                    "unrealized_pnl": pos.get("unrealized_pnl"),
                    "unrealized_pct": pos.get("unrealized_pct"),
                    "stop": pos.get("current_stop"),
                    "held": True,
                }
            cards.append({
                "id": u.id, "ts": u.ts, "ticker": ticker,
                "headline": p.get("headline"), "figure": p.get("figure"),
                "source": p.get("source"), "shares": p.get("shares"),
                "entry_price": p.get("price"), "stop": p.get("stop"),
                "review_verdict": review.get("verdict"),
                "review_reasoning": review.get("reasoning"),
                "links": [l.model_dump() for l in (u.links or [])],
                "live": live,
            })
        return {
            "cards": cards, "count": len(cards),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    @staticmethod
    def _rec_time(rec: Dict[str, Any]):
        ts = rec.get("decided_at") or rec.get("detected_at") or rec.get("published_at")
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None

    def _rec_within(self, rec: Dict[str, Any], now: datetime, days: int) -> bool:
        d = self._rec_time(rec)
        return bool(d) and (now - d).total_seconds() <= days * 86400

    @staticmethod
    def _event_funnel(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The seen->executed funnel for one window, with drop reasons.

        Stage mapping to P1-5 record fields:
          seen     = every recorded post
          named    = a ticker was resolved (or the post was pre-tickered)
          bullish  = passed the relevance/bullish filter (classification is
                     near_miss or executed; 'noise' failed here)
          passed   = cleared every guardrail (i.e. executed)
          executed = the order actually filled
        """
        seen = len(recs)
        named = sum(1 for r in recs if r.get("ticker"))
        bullish = sum(1 for r in recs if r.get("classification") in ("near_miss", "executed"))
        executed = sum(1 for r in recs if r.get("outcome") == "executed")
        drops: Dict[str, int] = {}
        for r in recs:
            if r.get("outcome") != "executed":
                reason = r.get("stop_reason") or "unknown"
                drops[reason] = drops.get(reason, 0) + 1
        return {
            "stages": [
                {"key": "seen", "label": "Seen", "count": seen},
                {"key": "named", "label": "Named a company", "count": named},
                {"key": "bullish", "label": "Bullish signal", "count": bullish},
                {"key": "passed", "label": "Passed guardrails", "count": executed},
                {"key": "executed", "label": "Executed", "count": executed},
            ],
            "drop_reasons": sorted(
                ({"reason": k, "count": v} for k, v in drops.items()),
                key=lambda x: -x["count"],
            ),
        }

    @staticmethod
    def _event_feed_item(rec: Dict[str, Any]) -> Dict[str, Any]:
        verdict = rec.get("verdict") or {}
        if rec.get("outcome") == "executed":
            chip = {"kind": "traded", "text": f"TRADED → {rec.get('ticker') or ''}".strip()}
        elif rec.get("classification") == "near_miss":
            chip = {"kind": "blocked", "text": "SIGNAL · blocked"}
        else:
            chip = {"kind": "noise", "text": "skipped"}
        return {
            "id": rec.get("id"),
            "headline": rec.get("headline"),
            "ticker": rec.get("ticker"),
            "score": rec.get("score"),
            "confidence": verdict.get("confidence"),
            "classification": rec.get("classification"),
            "outcome": rec.get("outcome"),
            "stop_reason": rec.get("stop_reason"),
            "reasoning": verdict.get("reasoning"),
            "company": verdict.get("company"),
            "figure": rec.get("figure"),
            "source": rec.get("source"),
            "published_at": rec.get("published_at"),
            "decided_at": rec.get("decided_at"),
            "chip": chip,
        }

    def _event_status(self, records, last_scan, now, figure) -> Dict[str, Any]:
        try:
            controls = self.get_controls()
        except Exception:
            controls = {}
        figs = [f["name"] for f in self._configured_figures() if f.get("enabled", True)]
        if figure:
            figs = [f for f in figs if f == figure] or [figure]

        cap = getattr(self.settings, "max_event_trades_per_day", 0)
        trades_today = sum(
            1 for r in records
            if r.get("outcome") == "executed" and self._rec_within(r, now, 1)
        )

        last_poll_seconds = None
        if last_scan and last_scan.get("completed_at"):
            last_poll_seconds = self._elapsed_seconds(last_scan["completed_at"])

        cooldown_min = getattr(self.settings, "event_cooldown_minutes", 0)
        cooldowns: Dict[str, int] = {}
        for r in records:
            if r.get("outcome") != "executed" or not r.get("ticker"):
                continue
            d = self._rec_time(r)
            if not d:
                continue
            remaining = cooldown_min - (now - d).total_seconds() / 60.0
            if remaining > 0:
                cooldowns[r["ticker"]] = max(cooldowns.get(r["ticker"], 0), round(remaining))

        return {
            "armed": bool(controls.get("event_armed", True)),
            "trading_armed": bool(controls.get("trading_armed", True)),
            "figures": figs,
            "trades_today": trades_today,
            "cap": cap,
            "last_poll_seconds": last_poll_seconds,
            "regime": (last_scan or {}).get("regime"),
            "cooldowns": [{"ticker": k, "expires_in_min": v} for k, v in cooldowns.items()],
        }
