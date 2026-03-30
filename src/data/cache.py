"""SQLite-backed persistent cache for data providers."""

import os
import pickle
import sqlite3
import time
import threading
from pathlib import Path
from typing import Any, Optional

# How long SQLite waits for a lock before raising OperationalError
_SQLITE_TIMEOUT = 30  # seconds
# How many times to retry a failed DB operation before giving up
_DB_RETRY_ATTEMPTS = 3
_DB_RETRY_DELAY = 0.5  # seconds between retries


class DBCache:
    """SQLite-backed persistent cache for data providers.

    Stores pickled Python objects (DataFrames, dicts, dataclasses, etc.)
    with per-entry TTL expiration. Thread-safe via a per-instance lock
    around the shared connection.  Multiple concurrent processes are
    supported via WAL journal mode and a 30-second busy timeout.
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
        self._conn = self._open_connection()
        self._conn.executescript(self._SCHEMA)

    def _open_connection(self) -> sqlite3.Connection:
        """Open (or re-open) the SQLite connection with safe defaults."""
        conn = sqlite3.connect(
            self._db_path,
            timeout=_SQLITE_TIMEOUT,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        # Tell SQLite to wait up to 30 s when the DB is busy instead of
        # immediately raising "database is locked".
        conn.execute(f"PRAGMA busy_timeout={_SQLITE_TIMEOUT * 1000}")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _execute_with_retry(self, fn):
        """Call fn(conn) with automatic retry on OperationalError."""
        last_exc = None
        for attempt in range(_DB_RETRY_ATTEMPTS):
            try:
                with self._lock:
                    return fn(self._conn)
            except sqlite3.OperationalError as e:
                last_exc = e
                # Attempt to recover a broken connection
                try:
                    self._conn.close()
                except Exception:
                    pass
                try:
                    self._conn = self._open_connection()
                    self._conn.executescript(self._SCHEMA)
                except Exception:
                    pass
                if attempt < _DB_RETRY_ATTEMPTS - 1:
                    time.sleep(_DB_RETRY_DELAY * (attempt + 1))
        raise last_exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return cached value if it exists and has not expired, else None."""
        now = time.time()
        self._maybe_prune(now)

        try:
            row = self._execute_with_retry(
                lambda conn: conn.execute(
                    "SELECT value, fetched_at, ttl_seconds FROM cache WHERE key = ?",
                    (key,),
                ).fetchone()
            )
        except sqlite3.OperationalError:
            self._misses += 1
            return None

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

        def _write(conn):
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl_seconds) "
                "VALUES (?, ?, ?, ?)",
                (key, blob, now, ttl_seconds),
            )
            conn.commit()

        self._execute_with_retry(_write)

    def clear(self, pattern: Optional[str] = None) -> int:
        """Delete all entries, or only those whose key starts with *pattern*.

        Returns the number of rows deleted.
        """
        def _clear(conn):
            if pattern is None:
                cur = conn.execute("DELETE FROM cache")
            else:
                cur = conn.execute(
                    "DELETE FROM cache WHERE key LIKE ?", (pattern + "%",)
                )
            conn.commit()
            return cur.rowcount

        return self._execute_with_retry(_clear)

    def prune(self) -> int:
        """Remove expired entries. Returns number of rows deleted."""
        now = time.time()

        def _prune(conn):
            cur = conn.execute(
                "DELETE FROM cache WHERE (? - fetched_at) > ttl_seconds", (now,)
            )
            conn.commit()
            return cur.rowcount

        deleted = self._execute_with_retry(_prune)
        self._last_prune = now
        return deleted

    def stats(self) -> dict:
        """Return hit/miss counts and database size on disk."""
        try:
            row_count = self._execute_with_retry(
                lambda conn: conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            )
        except sqlite3.OperationalError:
            row_count = -1

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
        try:
            rows = self._execute_with_retry(
                lambda conn: conn.execute(
                    "SELECT value, fetched_at, ttl_seconds FROM cache "
                    "WHERE key LIKE ? AND (? - fetched_at) <= ttl_seconds "
                    "LIMIT 1",
                    (prefix + "%", now),
                ).fetchall()
            )
        except sqlite3.OperationalError:
            return None

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
