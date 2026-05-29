"""Event ingestion for the news-driven event-trigger subsystem.

Polls breaking news for a watchlist of tickers and yields deduplicated
NewsEvent records. The EventSource abstraction lets a future streaming source
(websocket/push) replace the poller without touching the trigger logic — both
just implement `poll() -> list[NewsEvent]`.

The MVP NewsEventSource reuses the single Finnhub company-news code path on
`SentimentProvider.fetch_company_news` (src/data/sentiment.py) — no new HTTP.
A persisted cursor (data/events/seen_cursor.json) tracks the last-seen article
timestamp per ticker plus a rolling set of seen article ids so the same
headline never triggers twice across runs.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("steex.event_source")

# Cap the rolling seen-id set so the cursor file can't grow without bound.
_MAX_SEEN_IDS = 5000


@dataclass
class NewsEvent:
    """A single news article about a watchlist company."""

    id: str
    ticker: str
    headline: str
    url: str
    published_at: str  # ISO 8601 UTC
    source: str
    summary: str = ""


class EventSource(ABC):
    """Abstract source of market-moving events."""

    @abstractmethod
    def poll(self) -> List[NewsEvent]:
        """Return new, deduplicated events since the last poll."""
        ...


class NewsEventSource(EventSource):
    """Finnhub company-news poller over a fixed watchlist.

    Args:
        watchlist: tickers to watch.
        data_dir: project data dir (cursor persisted under data/events/).
        sentiment_provider: a SentimentProvider (reused for fetch_company_news).
        lookback_days: how far back to query on each poll (cursor trims the rest).
    """

    def __init__(self, watchlist, data_dir, sentiment_provider, lookback_days: int = 7):
        self.watchlist = [t.upper() for t in (watchlist or [])]
        self.lookback_days = lookback_days
        self.sentiment = sentiment_provider
        self._events_dir = Path(data_dir) / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._cursor_path = self._events_dir / "seen_cursor.json"

    # ---- cursor persistence ------------------------------------------------
    def _load_cursor(self) -> dict:
        try:
            if self._cursor_path.exists():
                with open(self._cursor_path) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("cursor load failed: %s", e)
        return {"last_ts": {}, "seen_ids": []}

    def _save_cursor(self, cursor: dict) -> None:
        try:
            cursor["seen_ids"] = cursor.get("seen_ids", [])[-_MAX_SEEN_IDS:]
            with open(self._cursor_path, "w") as f:
                json.dump(cursor, f)
        except Exception as e:
            logger.debug("cursor save failed: %s", e)

    # ---- polling -----------------------------------------------------------
    def poll(self) -> List[NewsEvent]:
        if not self.watchlist:
            return []
        cursor = self._load_cursor()
        last_ts = cursor.get("last_ts", {})
        seen_ids = set(cursor.get("seen_ids", []))

        now = datetime.now(timezone.utc)
        frm = (now - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
        to = now.strftime("%Y-%m-%d")

        fresh: List[NewsEvent] = []
        for ticker in self.watchlist:
            articles = self.sentiment.fetch_company_news(ticker, frm, to)
            cutoff = last_ts.get(ticker, 0)
            newest = cutoff
            for a in articles:
                ts = int(a.get("datetime", 0) or 0)
                aid = str(a.get("id") or a.get("url") or "")
                if not aid or aid in seen_ids:
                    continue
                if ts <= cutoff:
                    continue
                headline = (a.get("headline") or "").strip()
                if not headline:
                    continue
                fresh.append(NewsEvent(
                    id=aid,
                    ticker=ticker,
                    headline=headline,
                    url=a.get("url", ""),
                    published_at=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else now.isoformat(),
                    source=a.get("source", "finnhub"),
                    summary=(a.get("summary") or "")[:500],
                ))
                seen_ids.add(aid)
                newest = max(newest, ts)
            if newest > cutoff:
                last_ts[ticker] = newest

        cursor["last_ts"] = last_ts
        cursor["seen_ids"] = list(seen_ids)
        self._save_cursor(cursor)

        # Oldest-first so trades execute in chronological order.
        fresh.sort(key=lambda e: e.published_at)
        return fresh


class StreamingEventSource(EventSource):
    """Stub for a future push/websocket source.

    A real implementation would register a websocket callback that appends
    NewsEvent objects to `self._buffer`; `poll()` then drains and returns them.
    The trigger loop is identical regardless of source, so swapping this in is
    a config change only.
    """

    def __init__(self):
        self._buffer: List[NewsEvent] = []

    def push(self, event: NewsEvent) -> None:
        self._buffer.append(event)

    def poll(self) -> List[NewsEvent]:
        drained, self._buffer = self._buffer, []
        return drained
