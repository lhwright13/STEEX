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

    def _tradable(self, ticker: str) -> bool:
        """Gate on Alpaca tradability — this replaces the watchlist as the
        universe filter, so any real US-listed name (incl. small caps) qualifies
        while garbage/private/non-US mentions are dropped."""
        broker = getattr(self.mgr, "broker", None)
        if broker is None:
            return True  # no broker (e.g. dry-run without keys) — don't block
        try:
            asset = broker.get_asset(ticker)
            return bool(asset and asset.tradable)
        except Exception:
            return False

    # ---- main pass ---------------------------------------------------------
    def run(self, dry_run: bool = True, resolver=None) -> Dict:
        """Run one scan.

        `resolver(event) -> EventTickerResolution|None` turns a free-text post
        (event.ticker == "") into a ticker + bullish/confidence judgement. Events
        that already carry a ticker (e.g. Finnhub watchlist news) skip the
        resolver and use VADER sentiment instead.
        """
        now = datetime.now(timezone.utc)
        result: Dict = {
            "scanned": 0, "actionable": [], "skipped": [], "executed": [],
            "records": [],  # P1-5: per-post verdict + guardrail + timings
            "regime": None, "dry_run": dry_run, "timestamp": now.isoformat(),
        }
        detected_at = now.isoformat()

        def _record(ev, outcome, stop_reason, classification,
                    ticker=None, verdict=None, score=None):
            """Persist what happened to one scanned post: the resolver verdict, the
            guardrail it stopped at, and timings. Feeds the funnel (P3-4),
            near-misses (P4-1) and latency (P4-2). `classification` is one of
            executed | near_miss | noise."""
            result["records"].append({
                "id": ev.id,
                "headline": ev.headline[:300],
                "source": ev.source,
                "figure": getattr(ev, "figure", None),
                "published_at": ev.published_at,
                "detected_at": detected_at,
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "outcome": outcome,
                "classification": classification,
                "stop_reason": stop_reason,
                "ticker": ticker,
                "score": score,
                "verdict": verdict,
            })

        # Kill switch: a disarmed event path does no work at all (dry runs still
        # scan so you can watch it without arming).
        if not dry_run:
            from .control import event_armed
            if not event_armed(self.settings.data_dir):
                result["skipped"].append({"reason": "event trigger disarmed (kill switch)"})
                return result

        # Regime kill-switch next, before any polling.
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

        min_conf = getattr(self.settings, "event_min_confidence", 0.7)

        for ev in events:
            ticker = ev.ticker
            event_meta = {"id": ev.id, "headline": ev.headline, "url": ev.url,
                          "source": ev.source, "published_at": ev.published_at}

            verdict = None  # set for resolver-path (untickered) posts
            if ticker:
                # Pre-tickered (e.g. Finnhub watchlist news): VADER relevance.
                score = self.sentiment._analyze_with_vader([ev.headline])
                if score < threshold:
                    reason = f"sentiment {score:.0f} < {threshold}"
                    result["skipped"].append({"ticker": ticker, "reason": reason, "headline": ev.headline})
                    _record(ev, "skipped", reason, "noise", ticker=ticker, score=score)
                    continue
                event_meta["sentiment"] = round(score, 1)
            else:
                # Free-text post (e.g. Truth Social): resolve company -> ticker via LLM.
                if resolver is None:
                    result["skipped"].append({"reason": "no resolver for untickered event", "headline": ev.headline})
                    _record(ev, "skipped", "no resolver for untickered event", "noise")
                    continue
                res = resolver(ev)
                verdict = {
                    "mentions_company": bool(getattr(res, "mentions_company", False)) if res else False,
                    "ticker": getattr(res, "ticker", None) if res else None,
                    "is_bullish": bool(getattr(res, "is_bullish", False)) if res else False,
                    "confidence": getattr(res, "confidence", None) if res else None,
                    "company": getattr(res, "company_name", None) if res else None,
                    "reasoning": getattr(res, "reasoning", None) if res else None,
                }
                if not res or not getattr(res, "mentions_company", False) or not getattr(res, "is_bullish", False):
                    result["skipped"].append({"reason": "not a bullish company signal", "headline": ev.headline[:120]})
                    _record(ev, "skipped", "not a bullish company signal", "noise", verdict=verdict)
                    continue
                if not res.ticker or res.confidence < min_conf:
                    reason = f"low confidence {res.confidence:.2f} < {min_conf}"
                    result["skipped"].append({"ticker": res.ticker, "reason": reason, "headline": ev.headline[:120]})
                    _record(ev, "skipped", reason, "near_miss", ticker=res.ticker,
                            verdict=verdict, score=round(res.confidence * 100, 1))
                    continue
                ticker = res.ticker.upper()
                score = round(res.confidence * 100, 1)
                event_meta.update({"company": res.company_name, "confidence": res.confidence,
                                   "resolver_reasoning": res.reasoning})

            # Past the relevance filter: a real candidate. Any guardrail that blocks
            # it now is a near_miss (named a tradable bullish name, just gated out).
            # Tradability gate (replaces the watchlist): must be a real US-listed name.
            if not self._tradable(ticker):
                result["skipped"].append({"ticker": ticker, "reason": "not tradable on Alpaca"})
                _record(ev, "skipped", "not tradable on Alpaca", "near_miss", ticker=ticker, verdict=verdict, score=score)
                continue

            # Daily cap
            if self._count_today(trades, now) >= daily_cap:
                result["skipped"].append({"ticker": ticker, "reason": "daily event cap reached"})
                _record(ev, "skipped", "daily event cap reached", "near_miss", ticker=ticker, verdict=verdict, score=score)
                break

            # Already held?
            if self.mgr.position_manager.get_position(ticker) is not None:
                result["skipped"].append({"ticker": ticker, "reason": "already held"})
                _record(ev, "skipped", "already held", "near_miss", ticker=ticker, verdict=verdict, score=score)
                continue

            # Cooldown
            last = self._last_trade_ts(trades, ticker)
            if last:
                try:
                    age_min = (now - datetime.fromisoformat(last)).total_seconds() / 60
                    if age_min < cooldown_min:
                        reason = f"cooldown ({age_min:.0f}<{cooldown_min}m)"
                        result["skipped"].append({"ticker": ticker, "reason": reason})
                        _record(ev, "skipped", reason, "near_miss", ticker=ticker, verdict=verdict, score=score)
                        continue
                except Exception:
                    pass

            # Quote sanity
            price = self.mgr.price_provider.get_latest_price(ticker)
            if not price or price <= 0:
                result["skipped"].append({"ticker": ticker, "reason": "no quote"})
                _record(ev, "skipped", "no quote", "near_miss", ticker=ticker, verdict=verdict, score=score)
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
                _record(ev, "skipped", "size < 1 share / insufficient cash", "near_miss", ticker=ticker, verdict=verdict, score=score)
                continue

            stop_price = round(price * (1 - self.settings.initial_stop_pct), 2)
            actionable = {
                "ticker": ticker,
                "price": price,
                "shares": shares,
                "cost": round(price * shares, 2),
                "stop": stop_price,
                "score": round(score, 1),
                "reasons": [f"EVENT: {ev.headline[:200]}"],
                "event": event_meta,
            }
            result["actionable"].append(actionable)

            # Execute (reuses broker.buy + server stop inside execute_entries).
            executed = self.mgr.execute_entries([actionable], dry_run=dry_run, auto_confirm=True)
            if executed:
                result["executed"].append(actionable)
                _record(ev, "executed", None, "executed", ticker=ticker, verdict=verdict, score=score)
                # Only real fills go in the cooldown/daily-cap ledger — recording
                # dry-run "trades" would wrongly throttle the first live session.
                if not dry_run:
                    trades.append({"ticker": ticker, "ts": now.isoformat(),
                                   "headline": ev.headline})
            else:
                result["skipped"].append({"ticker": ticker, "reason": "execution returned no fill"})
                _record(ev, "skipped", "execution returned no fill", "near_miss", ticker=ticker, verdict=verdict, score=score)

        self._save_trades(trades)
        return result
