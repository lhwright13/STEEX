"""Abstract base class for data providers."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Optional

import pandas as pd

from .cache import DBCache


class DataProvider(ABC):
    """Abstract base class for all data providers.

    Provides a two-level cache:
      L1 - in-memory dict (fast, lost between sessions)
      L2 - SQLite via DBCache (persistent across sessions)

    Subclasses can set ``default_ttl`` (seconds) as a class attribute to
    control how long their data lives in the persistent cache.
    """

    # Shared across all provider instances; lazy-initialized on first use.
    _db_cache: ClassVar[Optional[DBCache]] = None
    _db_cache_enabled: ClassVar[bool] = True

    # Subclasses override this to set their own default TTL (seconds).
    # 4 hours is a safe default for intraday-ish data.
    default_ttl: ClassVar[float] = 4 * 3600

    def __init__(self, cache_enabled: bool = True):
        """Initialize provider with optional caching.

        Args:
            cache_enabled: Whether to cache data to avoid repeated API calls
        """
        self.cache_enabled = cache_enabled
        self._cache: dict[str, Any] = {}
        self._init_db_cache()

    @classmethod
    def _init_db_cache(cls) -> None:
        """Lazy-initialize the shared DBCache from settings."""
        if cls._db_cache is not None or not cls._db_cache_enabled:
            return
        try:
            from config.settings import get_settings
            settings = get_settings()
            if settings.cache_enabled:
                cls._db_cache = DBCache(settings.cache_db_path)
            else:
                cls._db_cache_enabled = False
        except Exception:
            cls._db_cache_enabled = False

    def _get_cache_key(self, *args, **kwargs) -> str:
        """Generate cache key from arguments."""
        return f"{self.__class__.__name__}:{args}:{kwargs}"

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get item from cache if available.

        Checks the in-memory L1 cache first, then falls through to
        the persistent L2 SQLite cache.
        """
        if not self.cache_enabled:
            return None

        # L1: in-memory
        value = self._cache.get(key)
        if value is not None:
            return value

        # L2: persistent SQLite
        if self._db_cache is not None:
            value = self._db_cache.get(key)
            if value is not None:
                # Promote to L1 so the next lookup is instant
                self._cache[key] = value
                return value

        return None

    def _set_cache(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store item in both L1 (in-memory) and L2 (SQLite) caches."""
        if not self.cache_enabled:
            return

        # L1
        self._cache[key] = value

        # L2
        if self._db_cache is not None:
            ttl = ttl if ttl is not None else self.default_ttl
            try:
                self._db_cache.set(key, value, ttl)
            except Exception:
                pass  # don't let cache failures break the provider

    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        self._cache.clear()

    @abstractmethod
    def fetch(self, *args, **kwargs) -> pd.DataFrame:
        """Fetch data from the source.

        Returns:
            DataFrame containing the fetched data
        """
        pass

    @staticmethod
    def trading_days_ago(days: int, from_date: Optional[datetime] = None) -> datetime:
        """Calculate date N trading days ago (approximate).

        This uses a simple approximation: trading_days * 1.4 calendar days.

        Args:
            days: Number of trading days
            from_date: Reference date (defaults to today)

        Returns:
            Approximate date that was N trading days ago
        """
        from_date = from_date or datetime.now()
        calendar_days = int(days * 1.4)  # Approximate conversion
        return from_date - pd.Timedelta(days=calendar_days)
