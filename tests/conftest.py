"""Shared test fixtures."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def reset_db_cache():
    from src.data.base import DataProvider
    original = DataProvider._db_cache
    DataProvider._db_cache = None
    yield
    DataProvider._db_cache = original


@pytest.fixture
def sample_ohlcv_data():
    """Generate sample OHLCV data for testing."""
    dates = pd.date_range(end=datetime.now(), periods=250, freq="B")
    base_price = 100

    # Generate realistic price movement
    import numpy as np

    np.random.seed(42)
    returns = np.random.randn(250) * 0.02
    prices = base_price * np.cumprod(1 + returns)

    df = pd.DataFrame(
        {
            "Open": prices * (1 + np.random.randn(250) * 0.005),
            "High": prices * (1 + np.abs(np.random.randn(250) * 0.01)),
            "Low": prices * (1 - np.abs(np.random.randn(250) * 0.01)),
            "Close": prices,
            "Volume": np.random.randint(500000, 5000000, 250),
            "Adj_Close": prices,
        },
        index=dates,
    )
    return df


@pytest.fixture
def sample_vix_data():
    """Generate sample VIX data."""
    dates = pd.date_range(end=datetime.now(), periods=252, freq="B")

    import numpy as np

    np.random.seed(42)
    vix_values = 20 + np.random.randn(252) * 5
    vix_values = np.clip(vix_values, 10, 80)

    return pd.DataFrame(
        {
            "Open": vix_values * 0.98,
            "High": vix_values * 1.05,
            "Low": vix_values * 0.95,
            "Close": vix_values,
            "Volume": np.random.randint(1000000, 10000000, 252),
        },
        index=dates,
    )


@pytest.fixture
def mock_price_provider(sample_ohlcv_data):
    """Create a mock price provider."""
    from src.data.price import PriceProvider

    provider = PriceProvider(cache_enabled=False)

    def mock_get_ohlcv(ticker, **kwargs):
        return sample_ohlcv_data.copy()

    def mock_get_ohlcv_batch(tickers, **kwargs):
        return {t: sample_ohlcv_data.copy() for t in tickers}

    def mock_get_latest_price(ticker):
        return sample_ohlcv_data["Close"].iloc[-1]

    provider.get_ohlcv = mock_get_ohlcv
    provider.get_ohlcv_batch = mock_get_ohlcv_batch
    provider.get_latest_price = mock_get_latest_price

    return provider


@pytest.fixture
def mock_vix_provider(sample_vix_data):
    """Create a mock VIX provider."""
    from src.data.vix import VixProvider

    provider = VixProvider(cache_enabled=False)

    def mock_fetch(**kwargs):
        return sample_vix_data.copy()

    def mock_get_current():
        return sample_vix_data["Close"].iloc[-1]

    provider.fetch = mock_fetch
    provider.get_current = mock_get_current

    return provider


@pytest.fixture
def sample_insider_transactions():
    """Generate sample insider transactions."""
    from src.sec.models import InsiderTransaction

    return [
        InsiderTransaction(
            ticker="AAPL",
            company_name="Apple Inc.",
            company_cik="0000320193",
            insider_name="John Smith",
            insider_cik="0001234567",
            is_director=True,
            is_officer=True,
            is_ten_percent_owner=False,
            officer_title="CEO",
            transaction_date="2024-01-10",
            transaction_code="P",
            acquired_disposed="A",
            shares=10000,
            price_per_share=150.0,
            total_value=1500000.0,
            shares_owned_after=50000,
            filing_date="2024-01-12",
            filing_url="https://www.sec.gov/...",
        ),
        InsiderTransaction(
            ticker="AAPL",
            company_name="Apple Inc.",
            company_cik="0000320193",
            insider_name="Jane Doe",
            insider_cik="0001234568",
            is_director=True,
            is_officer=False,
            is_ten_percent_owner=False,
            officer_title="",
            transaction_date="2024-01-11",
            transaction_code="P",
            acquired_disposed="A",
            shares=5000,
            price_per_share=151.0,
            total_value=755000.0,
            shares_owned_after=20000,
            filing_date="2024-01-13",
            filing_url="https://www.sec.gov/...",
        ),
    ]


@pytest.fixture
def test_settings():
    """Create test settings."""
    from config.settings import Settings

    return Settings(
        momentum_lookback_days=126,
        short_momentum_days=21,
        momentum_min_return=0.10,
        ma_short=50,
        ma_long=200,
        insider_lookback_days=30,
        min_cluster_buyers=3,
        min_purchase_value=100000,
        min_price=5.0,
        min_volume=500000,
        max_positions=20,
        daily_picks=2,
        position_size_pct=0.05,
        initial_stop_pct=0.07,
        max_hold_days=60,
        vix_caution_level=30,
        vix_exit_level=40,
    )
