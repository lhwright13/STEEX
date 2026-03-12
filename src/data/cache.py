"""SQLite-backed persistent cache for data providers."""

import os
import pickle
import sqlite3
import time
import threading
from pathlib import Path
from typing import Any, Optional


class DBCache:
    """SQLite-backed persistent cache for data providers.

    Stores pickled Python objects (DataFrames, dicts, dataclasses, etc.)
    with per-entry TTL expiration. Thread-safe via a per-instance lock
    around the shared connection.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value BLOB,
            fetched_at REAL,
            ttl_seconds REAL
        );
        CREATE INDEX IF NOT EXISTS idx_cache_fetched ON cache(fetched_at);
    """

    _PRUNE_INTERVAL = 300  # seconds between automatic prune sweeps

    def __init__(self, db_path: str = "data/cache.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._last_prune = 0.0

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self._SCHEMA)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return cached value if it exists and has not expired, else None."""
        now = time.time()
        self._maybe_prune(now)

        with self._lock:
            row = self._conn.execute(
                "SELECT value, fetched_at, ttl_seconds FROM cache WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            self._misses += 1
            return None

        blob, fetched_at, ttl = row
        if now - fetched_at > ttl:
            self._misses += 1
            return None

        self._hits += 1
        try:
            return pickle.loads(blob)
        except Exception:
            self._misses += 1
            return None

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store a pickled value with the given TTL."""
        blob = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl_seconds) "
                "VALUES (?, ?, ?, ?)",
                (key, blob, now, ttl_seconds),
            )
            self._conn.commit()

    def clear(self, pattern: Optional[str] = None) -> int:
        """Delete all entries, or only those whose key starts with *pattern*.

        Returns the number of rows deleted.
        """
        with self._lock:
            if pattern is None:
                cur = self._conn.execute("DELETE FROM cache")
            else:
                cur = self._conn.execute(
                    "DELETE FROM cache WHERE key LIKE ?", (pattern + "%",)
                )
            self._conn.commit()
            return cur.rowcount

    def prune(self) -> int:
        """Remove expired entries. Returns number of rows deleted."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM cache WHERE (? - fetched_at) > ttl_seconds", (now,)
            )
            self._conn.commit()
            self._last_prune = now
            return cur.rowcount

    def stats(self) -> dict:
        """Return hit/miss counts and database size on disk."""
        with self._lock:
            row_count = self._conn.execute(
                "SELECT COUNT(*) FROM cache"
            ).fetchone()[0]

        try:
            size_bytes = os.path.getsize(self._db_path)
        except OSError:
            size_bytes = 0

        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": row_count,
            "size_bytes": size_bytes,
            "db_path": self._db_path,
        }

    def find_by_prefix(self, prefix: str) -> Optional[Any]:
        """Return the first valid (non-expired) cached value whose key starts with prefix."""
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT value, fetched_at, ttl_seconds FROM cache "
                "WHERE key LIKE ? AND (? - fetched_at) <= ttl_seconds "
                "LIMIT 1",
                (prefix + "%", now),
            ).fetchall()

        for blob, _fetched_at, _ttl in rows:
            try:
                return pickle.loads(blob)
            except Exception:
                continue
        return None

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _maybe_prune(self, now: float) -> None:
        """Prune expired rows if enough time has elapsed since the last sweep."""
        if now - self._last_prune > self._PRUNE_INTERVAL:
            self.prune()
