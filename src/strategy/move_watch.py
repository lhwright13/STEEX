"""Big-move detector for held positions (P1-3).

Notification-only: flags when a holding *jumps* and emits a structured event for
the P1-2 summary stage to explain + push to the user. It does NOT trade.

"Jump" is measured against a per-ticker REFERENCE price (the price at the last
scan), not the entry price — otherwise a long-held winner (e.g. DELL +119%) would
trip every scan forever. The first time we see a ticker we just record its
reference (no alert); subsequent scans alert when it moves beyond the threshold
since that reference, then advance the reference. A per-ticker cooldown stops a
continued drift from re-alerting every scan.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("steex.move_watch")


class MoveWatcher:
    """Runs one big-move scan over current holdings and returns the events."""

    def __init__(self, manager, settings):
        self.mgr = manager
        self.settings = settings
        self._ledger_path = Path(settings.data_dir) / "events" / "big_moves.json"
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, dict]:
        try:
            if self._ledger_path.exists():
                return json.load(open(self._ledger_path))
        except Exception as e:
            logger.debug("big_moves ledger load failed: %s", e)
        return {}

    def _save(self, ledger: Dict[str, dict]) -> None:
        try:
            json.dump(ledger, open(self._ledger_path, "w"))
        except Exception as e:
            logger.debug("big_moves ledger save failed: %s", e)

    def scan(self) -> List[dict]:
        """Return big-move events since the last scan. Advances the references."""
        if not getattr(self.settings, "big_move_enabled", True):
            return []

        threshold = self.settings.big_move_threshold_pct
        cooldown_min = self.settings.big_move_cooldown_minutes
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        ledger = self._load()
        events: List[dict] = []

        for pos in self.mgr.position_manager.get_all_positions():
            price = self.mgr.price_provider.get_latest_price(pos.ticker)
            # NaN guard: `not price`/`price <= 0` both let NaN through (nan is
            # truthy and nan<=0 is False), so a data-feed outage that returns NaN
            # would compute move=NaN and — since abs(nan) < threshold is False —
            # fire a garbage "nan%" alert for every holding. Require a finite,
            # positive price.
            if price is None or not math.isfinite(price) or price <= 0:
                continue

            entry = ledger.get(pos.ticker)
            ref = entry.get("ref_price") if entry else None
            if not ref or not math.isfinite(ref) or ref <= 0:
                # First sighting (or a bad/legacy reference) — (re)set the
                # reference to this clean price, don't alert.
                ledger[pos.ticker] = {"ref_price": price, "ref_ts": now_iso,
                                      "last_alert_ts": (entry or {}).get("last_alert_ts", "")}
                continue

            move = (price - ref) / ref
            if not math.isfinite(move) or abs(move) < threshold:
                continue

            # Cooldown since the last alert for this ticker.
            last_alert = entry.get("last_alert_ts") or ""
            if last_alert:
                try:
                    age_min = (now - datetime.fromisoformat(last_alert)).total_seconds() / 60
                    if age_min < cooldown_min:
                        continue
                except Exception:
                    pass

            pnl = pos.calculate_pnl(price)
            events.append({
                "ticker": pos.ticker,
                "price": round(price, 2),
                "ref_price": round(ref, 2),
                "move_pct": round(move * 100, 1),
                "direction": "up" if move > 0 else "down",
                "shares": pos.shares,
                "pnl_pct": round(pnl["pnl_pct"] * 100, 1),  # since entry, for context
                "ts": now_iso,
            })
            # Advance the reference to the new level and stamp the alert.
            ledger[pos.ticker] = {"ref_price": price, "ref_ts": now_iso,
                                  "last_alert_ts": now_iso}

        # Drop ledger entries for tickers no longer held.
        held = {p.ticker for p in self.mgr.position_manager.get_all_positions()}
        ledger = {k: v for k, v in ledger.items() if k in held}
        self._save(ledger)
        return events
