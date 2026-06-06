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

        return {
            "feed": feed,
            "funnel": funnel,
            "status": status,
            "last_scan": last_scan,
            "figure": figure,
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
        figs = [
            f.get("name") for f in (self.settings.event_figures or [])
            if isinstance(f, dict) and f.get("enabled", True) and f.get("name")
        ]
        if not figs:
            figs = ["Donald Trump (@realDonaldTrump)"]
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
