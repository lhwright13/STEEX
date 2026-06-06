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
