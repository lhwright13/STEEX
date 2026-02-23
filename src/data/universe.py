"""Stock universe management - S&P 500, small-caps, and filtering."""

import json
from io import StringIO
from pathlib import Path
from typing import List, Optional, Set

import pandas as pd
import requests
import yfinance as yf

from .base import DataProvider

# Cache file for historical insider data
INSIDER_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "cache" / "historical_insiders.json"


class Universe(DataProvider):
    """Manages the stock universe for screening."""

    default_ttl = 24 * 3600  # 24 hours

    # Static S&P 500 list (updated periodically)
    # This avoids external dependencies for the base list
    _SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

    def __init__(self, cache_enabled: bool = True):
        """Initialize universe provider."""
        super().__init__(cache_enabled)
        self._sp500_cache: Optional[List[str]] = None

    def fetch(self, *args, **kwargs) -> pd.DataFrame:
        """Fetch S&P 500 list as DataFrame."""
        tickers = self.get_sp500()
        return pd.DataFrame({"ticker": tickers})

    def get_sp500(self, refresh: bool = False) -> List[str]:
        """Get list of S&P 500 tickers.

        Args:
            refresh: Force refresh from Wikipedia

        Returns:
            List of ticker symbols
        """
        if self._sp500_cache and not refresh:
            return self._sp500_cache

        cache_key = "sp500_list"
        cached = self._get_from_cache(cache_key)
        if cached and not refresh:
            self._sp500_cache = cached
            return cached

        try:
            headers = {
                "User-Agent": "STEEX/1.0 (Stock Trading Analysis Tool)"
            }
            response = requests.get(self._SP500_URL, headers=headers, timeout=10)
            response.raise_for_status()
            tables = pd.read_html(StringIO(response.text))
            # First table contains S&P 500 constituents
            df = tables[0]
            # Ticker column is usually "Symbol"
            tickers = df["Symbol"].tolist()
            # Clean tickers (some have dots that need conversion)
            tickers = [t.replace(".", "-") for t in tickers]
            self._sp500_cache = tickers
            self._set_cache(cache_key, tickers)
            return tickers
        except Exception:
            # Fallback to a static list of major S&P 500 components
            return self._get_fallback_list()

    def _get_fallback_list(self) -> List[str]:
        """Return a fallback list of major stocks if Wikipedia fails."""
        return [
            "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
            "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK",
            "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "WMT", "MCD", "CSCO",
            "TMO", "ACN", "ABT", "DHR", "NEE", "VZ", "ADBE", "NKE", "CRM",
            "TXN", "PM", "WFC", "BMY", "RTX", "UPS", "QCOM", "MS", "ORCL",
            "HON", "UNP", "T", "IBM", "LOW", "INTC", "BA", "GE", "CAT", "AMGN",
            "SPGI", "DE", "INTU", "AXP", "SBUX", "PLD", "ELV", "BKNG", "GILD",
            "MDLZ", "ADI", "SYK", "ISRG", "AMD", "MMC", "LMT", "TJX", "BLK",
            "ADP", "CVS", "VRTX", "AMT", "CB", "REGN", "ZTS", "CI", "TMUS",
            "SCHW", "PGR", "SO", "DUK", "MO", "LRCX", "CME", "ETN", "BDX",
            "BSX", "NOC", "ICE", "SHW", "PNC", "EQIX", "CL", "EOG", "AON",
        ]

    def filter_by_price_volume(
        self,
        tickers: List[str],
        min_price: float = 5.0,
        min_volume: int = 500_000,
    ) -> List[str]:
        """Filter tickers by minimum price and volume.

        Args:
            tickers: List of tickers to filter
            min_price: Minimum stock price
            min_volume: Minimum average daily volume

        Returns:
            Filtered list of tickers
        """
        passed = []
        # Batch fetch to reduce API calls
        batch_size = 100
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            try:
                data = yf.download(
                    batch,
                    period="5d",
                    progress=False,
                    threads=True,
                )
                if data.empty:
                    continue

                for ticker in batch:
                    try:
                        if len(batch) > 1:
                            close = data["Close"][ticker].iloc[-1]
                            volume = data["Volume"][ticker].mean()
                        else:
                            close = data["Close"].iloc[-1]
                            volume = data["Volume"].mean()

                        if (
                            pd.notna(close)
                            and pd.notna(volume)
                            and close >= min_price
                            and volume >= min_volume
                        ):
                            passed.append(ticker)
                    except (KeyError, IndexError):
                        continue
            except Exception:
                continue

        return passed

    def get_filtered_universe(
        self,
        min_price: float = 5.0,
        min_volume: int = 500_000,
        exclude: Optional[Set[str]] = None,
    ) -> List[str]:
        """Get filtered S&P 500 universe.

        Args:
            min_price: Minimum stock price
            min_volume: Minimum average daily volume
            exclude: Set of tickers to exclude

        Returns:
            List of filtered tickers
        """
        tickers = self.get_sp500()

        if exclude:
            tickers = [t for t in tickers if t not in exclude]

        return self.filter_by_price_volume(tickers, min_price, min_volume)

    def get_insider_activity_tickers(self, days_back: int = 30) -> List[str]:
        """Get tickers with recent insider buying activity.

        Args:
            days_back: Only include activity from last N days

        Returns:
            List of unique tickers with insider purchases
        """
        if not INSIDER_CACHE_FILE.exists():
            return []

        try:
            with open(INSIDER_CACHE_FILE) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=days_back)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        tickers = set()
        for date_str, transactions in cache.get("dates", {}).items():
            if date_str >= cutoff_str:
                for tx in transactions:
                    ticker = tx.get("ticker")
                    if ticker and ticker not in ("N/A", "NONE", ""):
                        tickers.add(ticker.upper())

        return sorted(list(tickers))

    def get_expanded_universe(
        self,
        include_sp500: bool = True,
        include_insider_activity: bool = True,
        insider_days_back: int = 30,
    ) -> List[str]:
        """Get expanded universe including S&P 500 and insider activity stocks.

        Args:
            include_sp500: Include S&P 500 stocks
            include_insider_activity: Include stocks with recent insider buying
            insider_days_back: Days to look back for insider activity

        Returns:
            Combined list of unique tickers
        """
        tickers = set()

        if include_sp500:
            tickers.update(self.get_sp500())

        if include_insider_activity:
            tickers.update(self.get_insider_activity_tickers(insider_days_back))

        return sorted(list(tickers))

    def get_insider_cluster_tickers(
        self, min_insiders: int = 2, days_back: int = 30
    ) -> List[str]:
        """Get tickers with cluster buying (multiple insiders).

        Args:
            min_insiders: Minimum unique insiders buying
            days_back: Days to look back

        Returns:
            List of tickers with cluster buys
        """
        if not INSIDER_CACHE_FILE.exists():
            return []

        try:
            with open(INSIDER_CACHE_FILE) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

        from datetime import datetime, timedelta
        from collections import defaultdict

        cutoff = datetime.now() - timedelta(days=days_back)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        # Count unique insiders per ticker
        ticker_insiders = defaultdict(set)
        for date_str, transactions in cache.get("dates", {}).items():
            if date_str >= cutoff_str:
                for tx in transactions:
                    ticker = tx.get("ticker")
                    insider = tx.get("insider_name")
                    if ticker and insider and ticker not in ("N/A", "NONE", ""):
                        ticker_insiders[ticker.upper()].add(insider)

        # Filter by minimum insiders
        cluster_tickers = [
            ticker for ticker, insiders in ticker_insiders.items()
            if len(insiders) >= min_insiders
        ]

        return sorted(cluster_tickers)
