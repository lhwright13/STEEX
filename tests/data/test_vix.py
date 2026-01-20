"""Tests for VIX data provider."""

import pytest

from src.data.vix import VixProvider


class TestVixProvider:
    """Tests for VixProvider class."""

    def test_init(self):
        """Test provider initialization."""
        provider = VixProvider()
        assert provider.VIX_TICKER == "^VIX"

    def test_get_current_with_mock(self, mock_vix_provider):
        """Test getting current VIX level."""
        vix = mock_vix_provider.get_current()
        assert vix is not None
        assert isinstance(vix, float)
        assert 10 <= vix <= 80  # Reasonable VIX range

    def test_is_elevated(self, mock_vix_provider):
        """Test elevated VIX check."""
        # Set up mock to return specific value
        mock_vix_provider.get_current = lambda: 35.0

        assert mock_vix_provider.is_elevated(30) is True
        assert mock_vix_provider.is_elevated(40) is False

    def test_is_spike(self, mock_vix_provider):
        """Test VIX spike detection."""
        mock_vix_provider.get_current = lambda: 45.0

        assert mock_vix_provider.is_spike(40) is True
        assert mock_vix_provider.is_spike(50) is False

    def test_fetch_returns_dataframe(self, mock_vix_provider, sample_vix_data):
        """Test fetch returns DataFrame with expected columns."""
        df = mock_vix_provider.fetch(days=30)

        assert not df.empty
        assert "Close" in df.columns


@pytest.mark.integration
class TestVixProviderIntegration:
    """Integration tests for VIX provider."""

    def test_fetch_real_vix(self):
        """Test fetching real VIX data."""
        provider = VixProvider()
        df = provider.fetch(days=10)

        if not df.empty:
            assert "Close" in df.columns
            # VIX should be positive
            assert (df["Close"] > 0).all()
