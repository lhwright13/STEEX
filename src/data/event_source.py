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
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("steex.event_source")

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Truth Social `content` is HTML; flatten it to plain text."""
    text = _TAG_RE.sub(" ", html or "")
    # collapse whitespace and decode the few entities that show up
    text = (text.replace("&amp;", "&").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()

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


class TruthSocialEventSource(EventSource):
    """Poll a public Truth Social account's posts directly (no API key).

    Truth Social is Mastodon-based; a prominent account's posts are publicly
    readable at /api/v1/accounts/{id}/statuses. Trump's account is
    107780257626128497. We hit it directly (works from a residential IP — the
    open-source archives only need a proxy because datacenter IPs get blocked).

    Posts arrive with NO ticker — the company is named in free text ("go out
    and buy a Dell"), so `ticker` is left empty here and resolved downstream by
    an LLM. Dedupe + lookback use a cursor file, same pattern as NewsEventSource.
    """

    _UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    def __init__(self, account_id, data_dir, lookback_hours: int = 24, http_get=None):
        self.account_id = str(account_id)
        self.lookback_hours = lookback_hours
        self._events_dir = Path(data_dir) / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._cursor_path = self._events_dir / "truth_cursor.json"
        # Injectable fetcher for tests; defaults to a real requests GET.
        self._http_get = http_get or self._default_get

    def _default_get(self, url: str) -> list:
        import requests
        r = requests.get(url, headers={"User-Agent": self._UA}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def _load_cursor(self) -> dict:
        try:
            if self._cursor_path.exists():
                with open(self._cursor_path) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("truth cursor load failed: %s", e)
        return {"last_id": "", "seen_ids": []}

    def _save_cursor(self, cursor: dict) -> None:
        try:
            cursor["seen_ids"] = cursor.get("seen_ids", [])[-_MAX_SEEN_IDS:]
            with open(self._cursor_path, "w") as f:
                json.dump(cursor, f)
        except Exception as e:
            logger.debug("truth cursor save failed: %s", e)

    def poll(self) -> List[NewsEvent]:
        url = (f"https://truthsocial.com/api/v1/accounts/{self.account_id}"
               f"/statuses?limit=20&exclude_replies=true")
        try:
            posts = self._http_get(url)
        except Exception as e:
            logger.warning("Truth Social poll failed: %s", e)
            return []

        cursor = self._load_cursor()
        seen = set(cursor.get("seen_ids", []))
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.lookback_hours)

        fresh: List[NewsEvent] = []
        for p in posts:
            pid = str(p.get("id") or "")
            if not pid or pid in seen:
                continue
            text = _strip_html(p.get("content", ""))
            if not text:
                seen.add(pid)
                continue
            created = p.get("created_at", "")
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created_dt < cutoff:
                    seen.add(pid)
                    continue
            except Exception:
                created_dt = now
            fresh.append(NewsEvent(
                id=pid,
                ticker="",  # resolved downstream by the LLM
                headline=text[:600],
                url=p.get("url", ""),
                published_at=created_dt.isoformat(),
                source="truth_social",
                summary=text[:1000],
            ))
            seen.add(pid)

        cursor["seen_ids"] = list(seen)
        if fresh:
            cursor["last_id"] = fresh[0].id
        self._save_cursor(cursor)
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
