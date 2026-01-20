"""Abstract base class for data providers."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

import pandas as pd


class DataProvider(ABC):
    """Abstract base class for all data providers."""

    def __init__(self, cache_enabled: bool = True):
        """Initialize provider with optional caching.

        Args:
            cache_enabled: Whether to cache data to avoid repeated API calls
        """
        self.cache_enabled = cache_enabled
        self._cache: dict[str, Any] = {}

    def _get_cache_key(self, *args, **kwargs) -> str:
        """Generate cache key from arguments."""
        return f"{self.__class__.__name__}:{args}:{kwargs}"

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get item from cache if available."""
        if not self.cache_enabled:
            return None
        return self._cache.get(key)

    def _set_cache(self, key: str, value: Any) -> None:
        """Store item in cache."""
        if self.cache_enabled:
            self._cache[key] = value

    def clear_cache(self) -> None:
        """Clear the cache."""
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
