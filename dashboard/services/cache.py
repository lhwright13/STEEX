"""Caching utilities for the dashboard."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytz
import streamlit as st

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


ET = pytz.timezone("America/New_York")


def is_market_open() -> bool:
    """Check if US stock market is currently open."""
    now = datetime.now(ET)

    # Market hours: 9:30 AM - 4:00 PM ET, Monday-Friday
    if now.weekday() >= 5:  # Weekend
        return False

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now <= market_close


def get_market_status() -> dict:
    """Get current market status with details."""
    now = datetime.now(ET)

    if now.weekday() >= 5:
        return {
            "status": "closed",
            "reason": "Weekend",
            "next_open": "Monday 9:30 AM ET",
        }

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    pre_market_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    after_hours_end = now.replace(hour=20, minute=0, second=0, microsecond=0)

    if market_open <= now <= market_close:
        return {
            "status": "open",
            "reason": "Regular Trading Hours",
            "closes_at": "4:00 PM ET",
        }
    elif pre_market_start <= now < market_open:
        return {
            "status": "pre_market",
            "reason": "Pre-Market",
            "opens_at": "9:30 AM ET",
        }
    elif market_close < now <= after_hours_end:
        return {
            "status": "after_hours",
            "reason": "After Hours",
            "closes_at": "8:00 PM ET",
        }
    else:
        return {
            "status": "closed",
            "reason": "Market Closed",
            "next_open": "9:30 AM ET",
        }


def get_cache_ttl(cache_type: str) -> int:
    """Get cache TTL in seconds based on type and market status.

    Args:
        cache_type: Type of data being cached

    Returns:
        TTL in seconds
    """
    market_open = is_market_open()

    ttls = {
        "prices": 60 if market_open else 300,
        "vix": 300,
        "screening": 3600,
        "backtest": 86400,  # 24 hours
        "positions": 60 if market_open else 300,
        "trades": 3600,
    }

    return ttls.get(cache_type, 300)


@st.cache_data(ttl=60)
def get_cached_prices(tickers: tuple) -> dict:
    """Get cached price data for tickers.

    Args:
        tickers: Tuple of ticker symbols (tuple for hashability)

    Returns:
        Dict mapping ticker to current price
    """
    from src.data.price import PriceProvider

    provider = PriceProvider()
    prices = {}

    for ticker in tickers:
        price = provider.get_latest_price(ticker)
        if price is not None:
            prices[ticker] = price

    return prices


@st.cache_data(ttl=300)
def get_cached_vix() -> Optional[float]:
    """Get cached VIX value."""
    from src.data.vix import VixProvider

    provider = VixProvider()
    return provider.get_current()


@st.cache_data(ttl=3600)
def get_cached_screening_results():
    """Get cached screening results."""
    from src.strategy.screener import StockScreener

    screener = StockScreener()
    return screener.run_pipeline()


def clear_all_caches():
    """Clear all Streamlit caches."""
    st.cache_data.clear()
    st.cache_resource.clear()
