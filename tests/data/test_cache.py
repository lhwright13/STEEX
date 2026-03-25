"""Tests for DBCache TTL, L1/L2 lookup, and corrupted data recovery (E5).

The DBCache is a SQLite-backed persistent cache with per-entry TTL.
"""

import os
import pickle
import sqlite3
import time

import pytest

from src.data.cache import DBCache


@pytest.fixture
def cache(tmp_path):
    """Create a fresh DBCache in a temp directory."""
    db_path = str(tmp_path / "test_cache.db")
    c = DBCache(db_path=db_path)
    yield c
    c.close()


class TestTTLExpiration:
    """Items must expire after their TTL elapses."""

    def test_item_returned_before_ttl(self, cache):
        cache.set("key1", {"data": 42}, ttl_seconds=60)
        result = cache.get("key1")
        assert result == {"data": 42}

    def test_item_expires_after_ttl(self, cache):
        """An item with a tiny TTL should be None after the TTL passes."""
        cache.set("key_short", "hello", ttl_seconds=0.01)
        time.sleep(0.05)
        result = cache.get("key_short")
        assert result is None

    def test_different_ttls_per_key(self, cache):
        """Each key has its own TTL; one can expire while another survives."""
        cache.set("short", 1, ttl_seconds=0.01)
        cache.set("long", 2, ttl_seconds=60)
        time.sleep(0.05)

        assert cache.get("short") is None
        assert cache.get("long") == 2

    def test_overwrite_resets_ttl(self, cache):
        """Overwriting a key resets its fetched_at timestamp."""
        cache.set("k", "v1", ttl_seconds=0.01)
        time.sleep(0.05)
        assert cache.get("k") is None

        cache.set("k", "v2", ttl_seconds=60)
        assert cache.get("k") == "v2"


class TestLookup:
    """Test get, find_by_prefix, and cache stats (hit/miss tracking)."""

    def test_miss_returns_none(self, cache):
        assert cache.get("nonexistent") is None

    def test_hit_miss_counters(self, cache):
        cache.set("x", 1, ttl_seconds=60)
        cache.get("x")       # hit
        cache.get("missing")  # miss

        stats = cache.stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1

    def test_find_by_prefix(self, cache):
        cache.set("prices:AAPL:daily", [100, 101], ttl_seconds=60)
        cache.set("prices:MSFT:daily", [200, 201], ttl_seconds=60)

        result = cache.find_by_prefix("prices:AAPL")
        assert result == [100, 101]

    def test_find_by_prefix_expired(self, cache):
        """find_by_prefix should not return expired entries."""
        cache.set("old:key", "stale", ttl_seconds=0.01)
        time.sleep(0.05)
        assert cache.find_by_prefix("old:") is None

    def test_clear_by_pattern(self, cache):
        cache.set("a:1", 1, ttl_seconds=60)
        cache.set("a:2", 2, ttl_seconds=60)
        cache.set("b:1", 3, ttl_seconds=60)

        deleted = cache.clear(pattern="a:")
        assert deleted == 2
        assert cache.get("a:1") is None
        assert cache.get("b:1") == 3

    def test_prune_removes_expired(self, cache):
        cache.set("gone", "bye", ttl_seconds=0.01)
        cache.set("stay", "hi", ttl_seconds=60)
        time.sleep(0.05)

        pruned = cache.prune()
        assert pruned >= 1
        assert cache.get("stay") == "hi"


class TestCorruptedDataRecovery:
    """Corrupted or unpicklable blobs should return None, not crash."""

    def test_corrupted_blob_returns_none(self, tmp_path):
        """Manually inject a corrupt blob; get() should return None."""
        db_path = str(tmp_path / "corrupt.db")
        cache = DBCache(db_path=db_path)

        # Insert a corrupt blob directly
        now = time.time()
        with cache._lock:
            cache._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl_seconds) "
                "VALUES (?, ?, ?, ?)",
                ("bad_key", b"not_valid_pickle_data", now, 9999),
            )
            cache._conn.commit()

        result = cache.get("bad_key")
        assert result is None
        cache.close()

    def test_empty_db_returns_none(self, tmp_path):
        """A freshly created cache returns None for any key."""
        db_path = str(tmp_path / "empty.db")
        cache = DBCache(db_path=db_path)
        assert cache.get("anything") is None
        assert cache.find_by_prefix("any") is None
        cache.close()
