"""Tests for universe module."""

import pytest

from src.data.universe import Universe


class TestUniverse:
    """Tests for Universe class."""

    def test_init(self):
        """Test universe initialization."""
        universe = Universe()
        assert universe.cache_enabled is True

    def test_get_sp500_returns_list(self):
        """Test that get_sp500 returns a list of tickers."""
        universe = Universe()
        # Use fallback list to avoid network call
        tickers = universe._get_fallback_list()

        assert isinstance(tickers, list)
        assert len(tickers) > 0
        assert all(isinstance(t, str) for t in tickers)
        # Check some known tickers are present
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_fallback_list_content(self):
        """Test fallback list contains major stocks."""
        universe = Universe()
        tickers = universe._get_fallback_list()

        major_stocks = ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]
        for stock in major_stocks:
            assert stock in tickers

    def test_fetch_returns_dataframe(self):
        """Test fetch method returns DataFrame."""
        universe = Universe()
        # Mock get_sp500 to use fallback
        universe.get_sp500 = lambda refresh=False: universe._get_fallback_list()

        df = universe.fetch()
        assert not df.empty
        assert "ticker" in df.columns


@pytest.mark.integration
class TestUniverseIntegration:
    """Integration tests for Universe."""

    def test_get_sp500_from_wikipedia(self):
        """Test fetching S&P 500 list from Wikipedia."""
        universe = Universe()
        tickers = universe.get_sp500(refresh=True)

        # Should have around 500 tickers
        assert len(tickers) >= 400
        assert len(tickers) <= 510
        assert "AAPL" in tickers
