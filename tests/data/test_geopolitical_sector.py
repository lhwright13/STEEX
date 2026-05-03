"""Tests for the sector lookup cascade in src.data.geopolitical.

The cascade resolves in this order:
    SECTOR_MAPPING (hot path) -> DBCache -> yfinance -> "unknown"

Failures in the yfinance path must return "unknown" without raising and
must write a short-TTL negative cache entry so repeated screens don't
hammer the API.
"""

from unittest.mock import MagicMock, patch

import pytest

import src.data.geopolitical as geopolitical_mod
from src.data.cache import DBCache
from src.data.geopolitical import SECTOR_MAPPING, get_ticker_sector


@pytest.fixture
def sector_env(tmp_path):
    """Swap in a fresh DBCache and fake settings for the sector singleton."""
    cache = DBCache(db_path=str(tmp_path / "sector_cache.db"))

    fake_settings = MagicMock()
    fake_settings.cache_db_path = str(tmp_path / "sector_cache.db")
    fake_settings.sector_lookup_yfinance_enabled = True

    original = (
        geopolitical_mod._sector_cache_instance,
        geopolitical_mod._sector_cache_settings,
        geopolitical_mod._sector_cache_initialized,
    )

    geopolitical_mod._sector_cache_instance = cache
    geopolitical_mod._sector_cache_settings = fake_settings
    geopolitical_mod._sector_cache_initialized = True

    yield cache, fake_settings

    (
        geopolitical_mod._sector_cache_instance,
        geopolitical_mod._sector_cache_settings,
        geopolitical_mod._sector_cache_initialized,
    ) = original
    cache.close()


class TestSectorLookupCascade:
    """End-to-end tests for get_ticker_sector()."""

    def test_hardcoded_ticker_uses_fast_path(self, sector_env):
        """Tickers in SECTOR_MAPPING must never invoke yfinance."""
        assert "AAPL" in SECTOR_MAPPING
        with patch.object(geopolitical_mod, "_fetch_sector_yfinance") as mock_fetch:
            sector = get_ticker_sector("AAPL")
        assert sector == "technology"
        mock_fetch.assert_not_called()

    def test_unknown_ticker_fetches_yfinance_and_caches(self, sector_env):
        """First call hits yfinance; second call hits the cache."""
        cache, _ = sector_env
        assert "STT" not in SECTOR_MAPPING

        with patch.object(
            geopolitical_mod, "_fetch_sector_yfinance", return_value="financials"
        ) as mock_fetch:
            first = get_ticker_sector("STT")
            second = get_ticker_sector("STT")

        assert first == "financials"
        assert second == "financials"
        assert mock_fetch.call_count == 1
        assert cache.get("sector:STT") == "financials"

    def test_yfinance_failure_returns_unknown_with_negative_cache(self, sector_env):
        """yfinance returning None must produce 'unknown' and record it in cache."""
        cache, _ = sector_env
        assert "ZZZ" not in SECTOR_MAPPING

        with patch.object(
            geopolitical_mod, "_fetch_sector_yfinance", return_value=None
        ):
            sector = get_ticker_sector("ZZZ")

        assert sector == "unknown"
        assert cache.get("sector:ZZZ") == "unknown"

    def test_kill_switch_off_skips_yfinance(self, sector_env):
        """When sector_lookup_yfinance_enabled is False, yfinance is not called."""
        _, settings = sector_env
        settings.sector_lookup_yfinance_enabled = False
        assert "STT" not in SECTOR_MAPPING

        with patch.object(geopolitical_mod, "_fetch_sector_yfinance") as mock_fetch:
            sector = get_ticker_sector("STT")

        assert sector == "unknown"
        mock_fetch.assert_not_called()


class TestFetchSectorYfinance:
    """Unit tests for the low-level yfinance helper."""

    def test_normalizes_known_label(self):
        fake_ticker = MagicMock()
        fake_ticker.info = {"sector": "Financial Services"}
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = geopolitical_mod._fetch_sector_yfinance("STT")
        assert result == "financials"

    def test_returns_none_on_missing_sector(self):
        fake_ticker = MagicMock()
        fake_ticker.info = {"industry": "Asset Management"}
        with patch("yfinance.Ticker", return_value=fake_ticker):
            result = geopolitical_mod._fetch_sector_yfinance("STT")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")):
            result = geopolitical_mod._fetch_sector_yfinance("STT")
        assert result is None
