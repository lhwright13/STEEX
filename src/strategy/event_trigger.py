"""News event-trigger core: ingest -> relevance -> guardrails -> auto-buy.

Deterministic fast-path that reacts to breaking news on a watchlist. On an
actionable bullish headline it sizes a small fixed-size position and executes
it immediately (reusing QuantManager.execute_entries, which also places the
protective server-side stop). The orchestrator then dispatches a review agent
on each fill; this module does not call the agent itself.

Safety model (auto-buy, so guardrails are mandatory):
  - crisis regime / entries-not-allowed -> skip everything (kill-switch)
  - market must be open
  - bullish sentiment >= threshold (VADER on the headline)
  - not already held, not within cooldown, daily cap not hit
  - valid live quote
  - tiny fixed event_position_pct, capped by available cash (minus reserve)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("steex.event_trigger")


class EventTrigger:
    """Runs one event-scan pass and returns a structured result."""

    def __init__(self, manager, settings, event_source, sentiment_provider):
        self.mgr = manager
        self.settings = settings
        self.source = event_source
        self.sentiment = sentiment_provider
        self._trades_path = Path(settings.data_dir) / "events" / "event_trades.json"
        self._trades_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- event-trade ledger (cooldown + daily cap) -------------------------
    def _load_trades(self) -> List[Dict]:
        try:
            if self._trades_path.exists():
                with open(self._trades_path) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("event_trades load failed: %s", e)
        return []

    def _save_trades(self, trades: List[Dict]) -> None:
        try:
            with open(self._trades_path, "w") as f:
                json.dump(trades[-500:], f)
        except Exception as e:
            logger.debug("event_trades save failed: %s", e)

    def _count_today(self, trades: List[Dict], now: datetime) -> int:
        today = now.date().isoformat()
        return sum(1 for t in trades if (t.get("ts", "")[:10] == today))

    def _last_trade_ts(self, trades: List[Dict], ticker: str) -> str:
        stamps = [t["ts"] for t in trades if t.get("ticker") == ticker and t.get("ts")]
        return max(stamps) if stamps else ""

    # ---- guardrails --------------------------------------------------------
    def _market_open(self) -> bool:
        broker = getattr(self.mgr, "broker", None)
        if broker is None:
            return False
        try:
            return bool(broker.get_clock().get("is_open"))
        except Exception:
            return False

    # ---- main pass ---------------------------------------------------------
    def run(self, dry_run: bool = True) -> Dict:
        now = datetime.now(timezone.utc)
        result: Dict = {
            "scanned": 0, "actionable": [], "skipped": [], "executed": [],
            "regime": None, "dry_run": dry_run, "timestamp": now.isoformat(),
        }

        # Kill-switch: regime first, before any work.
        regime = self.mgr.get_regime()
        result["regime"] = regime.get("name")
        if regime.get("name") == "crisis" or not regime.get("entries_allowed", True):
            result["skipped"].append({"reason": f"regime {regime.get('name')} blocks entries"})
            return result

        if not dry_run and not self._market_open():
            result["skipped"].append({"reason": "market closed"})
            return result

        events = self.source.poll()
        result["scanned"] = len(events)
        if not events:
            return result

        trades = self._load_trades()
        threshold = self.settings.event_sentiment_threshold
        cooldown_min = self.settings.event_cooldown_minutes
        daily_cap = self.settings.max_event_trades_per_day

        for ev in events:
            ticker = ev.ticker
            score = self.sentiment._analyze_with_vader([ev.headline])

            # Relevance: bullish enough?
            if score < threshold:
                result["skipped"].append({"ticker": ticker, "reason": f"sentiment {score:.0f} < {threshold}", "headline": ev.headline})
                continue

            # Daily cap
            if self._count_today(trades, now) >= daily_cap:
                result["skipped"].append({"ticker": ticker, "reason": "daily event cap reached"})
                break

            # Already held?
            if self.mgr.position_manager.get_position(ticker) is not None:
                result["skipped"].append({"ticker": ticker, "reason": "already held"})
                continue

            # Cooldown
            last = self._last_trade_ts(trades, ticker)
            if last:
                try:
                    age_min = (now - datetime.fromisoformat(last)).total_seconds() / 60
                    if age_min < cooldown_min:
                        result["skipped"].append({"ticker": ticker, "reason": f"cooldown ({age_min:.0f}<{cooldown_min}m)"})
                        continue
                except Exception:
                    pass

            # Quote sanity
            price = self.mgr.price_provider.get_latest_price(ticker)
            if not price or price <= 0:
                result["skipped"].append({"ticker": ticker, "reason": "no quote"})
                continue

            # Size a small fixed event position, capped by deployable cash.
            portfolio_value = self.mgr._get_portfolio_value()
            cash = self.mgr._get_cash()
            reserve = portfolio_value * self.settings.min_cash_reserve_pct
            deployable = max(0.0, cash - reserve)
            target = portfolio_value * self.settings.event_position_pct
            shares = int(min(target, deployable) / price)
            if shares < 1:
                result["skipped"].append({"ticker": ticker, "reason": "size < 1 share / insufficient cash"})
                continue

            stop_price = round(price * (1 - self.settings.initial_stop_pct), 2)
            actionable = {
                "ticker": ticker,
                "price": price,
                "shares": shares,
                "cost": round(price * shares, 2),
                "stop": stop_price,
                "score": round(score, 1),
                "reasons": [f"EVENT: {ev.headline}"],
                "event": {"id": ev.id, "headline": ev.headline, "url": ev.url,
                          "source": ev.source, "published_at": ev.published_at,
                          "sentiment": round(score, 1)},
            }
            result["actionable"].append(actionable)

            # Execute (reuses broker.buy + server stop inside execute_entries).
            executed = self.mgr.execute_entries([actionable], dry_run=dry_run, auto_confirm=True)
            if executed:
                trades.append({"ticker": ticker, "ts": now.isoformat(),
                               "headline": ev.headline, "dry_run": dry_run})
                result["executed"].append(actionable)

        self._save_trades(trades)
        return result
