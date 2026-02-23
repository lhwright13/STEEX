"""Stock-specific sentiment provider using free APIs and VADER NLP."""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

from .base import DataProvider


# Financial-specific word adjustments for VADER
# Positive words get boosted, negative words get penalized
FINANCIAL_LEXICON_UPDATES = {
    # Bullish terms (increase positive sentiment)
    "beat": 2.5,
    "beats": 2.5,
    "upgrade": 2.5,
    "upgraded": 2.5,
    "outperform": 2.0,
    "outperforms": 2.0,
    "bullish": 2.5,
    "surge": 2.0,
    "surges": 2.0,
    "soar": 2.0,
    "soars": 2.0,
    "rally": 2.0,
    "rallies": 2.0,
    "breakout": 2.0,
    "record": 1.5,
    "profit": 1.5,
    "profits": 1.5,
    "growth": 1.5,
    "expanding": 1.5,
    "dividend": 1.5,
    "buyback": 1.5,
    "acquisition": 1.0,
    "merger": 1.0,
    "innovation": 1.5,
    "breakthrough": 2.0,
    # Bearish terms (increase negative sentiment)
    "miss": -2.5,
    "misses": -2.5,
    "downgrade": -2.5,
    "downgraded": -2.5,
    "underperform": -2.0,
    "underperforms": -2.0,
    "bearish": -2.5,
    "crash": -2.5,
    "crashes": -2.5,
    "plunge": -2.5,
    "plunges": -2.5,
    "recession": -2.5,
    "bankruptcy": -3.0,
    "layoff": -2.0,
    "layoffs": -2.0,
    "lawsuit": -2.0,
    "fraud": -3.0,
    "scandal": -2.5,
    "investigation": -1.5,
    "default": -2.5,
    "debt": -1.0,
    "loss": -1.5,
    "losses": -1.5,
    "decline": -1.5,
    "declines": -1.5,
    "warning": -2.0,
    "concern": -1.0,
    "concerns": -1.0,
    "risk": -1.0,
    "risks": -1.0,
}


@dataclass
class SentimentResult:
    """Sentiment analysis result for a ticker."""

    ticker: str
    score: float  # -100 to +100 (bearish to bullish)
    normalized_score: float  # 0 to 100 for ranking
    news_count: int
    avg_relevance: float
    sentiment_label: str  # "Bearish", "Neutral", "Bullish"
    headlines: List[str]
    sources: Dict[str, float]  # source -> score
    timestamp: datetime


class SentimentProvider(DataProvider):
    """Fetches stock-specific sentiment using VADER NLP and APIs.

    Strategy:
    1. VADER NLP as primary (local, free, unlimited) - processes headlines
    2. Finnhub for news fetching (60 req/min free tier)
    3. Alpha Vantage as backup (25 req/day)

    VADER runs locally and provides unlimited sentiment analysis.
    APIs are only used to fetch news headlines for VADER to analyze.
    """

    default_ttl = 6 * 3600  # 6 hours

    # API endpoints
    ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
    FINNHUB_URL = "https://finnhub.io/api/v1"

    # Cache TTL in seconds (1 hour for sentiment data)
    CACHE_TTL = 3600

    def __init__(
        self,
        alpha_vantage_key: Optional[str] = None,
        finnhub_key: Optional[str] = None,
        cache_enabled: bool = True,
    ):
        """Initialize sentiment provider.

        Args:
            alpha_vantage_key: Alpha Vantage API key (or env ALPHA_VANTAGE_API_KEY)
            finnhub_key: Finnhub API key (or env FINNHUB_API_KEY)
            cache_enabled: Whether to cache results
        """
        super().__init__(cache_enabled)
        self.alpha_vantage_key = alpha_vantage_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        self.finnhub_key = finnhub_key or os.environ.get("FINNHUB_API_KEY", "")
        self._cache_timestamps: Dict[str, datetime] = {}

        # Initialize VADER with financial lexicon updates
        self.vader = None
        if VADER_AVAILABLE:
            self.vader = SentimentIntensityAnalyzer()
            # Add financial-specific terms to VADER lexicon
            self.vader.lexicon.update(FINANCIAL_LEXICON_UPDATES)

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cache entry is still valid."""
        if key not in self._cache_timestamps:
            return False
        age = (datetime.now() - self._cache_timestamps[key]).total_seconds()
        return age < self.CACHE_TTL

    def _set_cache_with_timestamp(self, key: str, value: Any) -> None:
        """Set cache with timestamp tracking."""
        self._set_cache(key, value)
        self._cache_timestamps[key] = datetime.now()

    def fetch(self, ticker: str) -> SentimentResult:
        """Fetch sentiment for a single ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            SentimentResult with sentiment data
        """
        return self.get_sentiment(ticker)

    def get_sentiment(self, ticker: str) -> SentimentResult:
        """Get sentiment for a single ticker.

        Tries Finnhub first (more generous free tier), falls back to Alpha Vantage.

        Args:
            ticker: Stock ticker symbol

        Returns:
            SentimentResult with sentiment data
        """
        cache_key = f"sentiment:{ticker}"

        # Check cache
        if self._is_cache_valid(cache_key):
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached

        result = None

        # Try Finnhub first (more generous free tier)
        if self.finnhub_key:
            result = self._fetch_finnhub_sentiment(ticker)

        # Fall back to Alpha Vantage if Finnhub fails
        if result is None and self.alpha_vantage_key:
            result = self._fetch_alpha_vantage_sentiment(ticker)

        # Return neutral if no API keys or both fail
        if result is None:
            result = self._neutral_sentiment(ticker)

        self._set_cache_with_timestamp(cache_key, result)
        return result

    def get_sentiment_batch(
        self,
        tickers: List[str],
        delay_ms: int = 200,
    ) -> Dict[str, SentimentResult]:
        """Get sentiment for multiple tickers.

        Args:
            tickers: List of ticker symbols
            delay_ms: Delay between API calls in milliseconds

        Returns:
            Dict mapping ticker to SentimentResult
        """
        results = {}

        for ticker in tickers:
            results[ticker] = self.get_sentiment(ticker)
            # Rate limiting
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)

        return results

    def _fetch_finnhub_sentiment(self, ticker: str) -> Optional[SentimentResult]:
        """Fetch sentiment from Finnhub news API.

        Args:
            ticker: Stock ticker symbol

        Returns:
            SentimentResult or None if failed
        """
        try:
            # Get company news from last 7 days
            to_date = datetime.now()
            from_date = to_date - timedelta(days=7)

            url = f"{self.FINNHUB_URL}/company-news"
            params = {
                "symbol": ticker,
                "from": from_date.strftime("%Y-%m-%d"),
                "to": to_date.strftime("%Y-%m-%d"),
                "token": self.finnhub_key,
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            news = response.json()

            if not news:
                return self._neutral_sentiment(ticker)

            # Finnhub news doesn't have sentiment scores built-in
            # We'll use a simple headline analysis approach
            headlines = [item.get("headline", "") for item in news[:10]]
            sources = {}

            # Count news by source
            for item in news:
                source = item.get("source", "unknown")
                sources[source] = sources.get(source, 0) + 1

            # Simple sentiment estimation based on headline keywords
            score = self._estimate_headline_sentiment(headlines)

            return SentimentResult(
                ticker=ticker,
                score=score,
                normalized_score=self._normalize_score(score),
                news_count=len(news),
                avg_relevance=0.8,  # Finnhub returns relevant news
                sentiment_label=self._score_to_label(score),
                headlines=headlines[:5],
                sources=sources,
                timestamp=datetime.now(),
            )

        except Exception:
            return None

    def _fetch_alpha_vantage_sentiment(self, ticker: str) -> Optional[SentimentResult]:
        """Fetch sentiment from Alpha Vantage news sentiment API.

        Args:
            ticker: Stock ticker symbol

        Returns:
            SentimentResult or None if failed
        """
        try:
            params = {
                "function": "NEWS_SENTIMENT",
                "tickers": ticker,
                "apikey": self.alpha_vantage_key,
                "limit": 50,
            }

            response = requests.get(self.ALPHA_VANTAGE_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            # Check for API limit message
            if "Note" in data or "Information" in data:
                return None

            feed = data.get("feed", [])
            if not feed:
                return self._neutral_sentiment(ticker)

            # Extract sentiment scores for this ticker
            scores = []
            relevances = []
            headlines = []
            sources = {}

            for item in feed:
                # Get ticker-specific sentiment
                ticker_sentiments = item.get("ticker_sentiment", [])
                for ts in ticker_sentiments:
                    if ts.get("ticker") == ticker:
                        score = float(ts.get("ticker_sentiment_score", 0))
                        relevance = float(ts.get("relevance_score", 0))
                        scores.append(score)
                        relevances.append(relevance)
                        break

                headlines.append(item.get("title", ""))
                source = item.get("source", "unknown")
                sources[source] = sources.get(source, 0) + 1

            if not scores:
                return self._neutral_sentiment(ticker)

            # Alpha Vantage scores are -1 to 1, convert to -100 to 100
            avg_score = sum(scores) / len(scores) * 100
            avg_relevance = sum(relevances) / len(relevances) if relevances else 0.5

            return SentimentResult(
                ticker=ticker,
                score=avg_score,
                normalized_score=self._normalize_score(avg_score),
                news_count=len(feed),
                avg_relevance=avg_relevance,
                sentiment_label=self._score_to_label(avg_score),
                headlines=headlines[:5],
                sources=sources,
                timestamp=datetime.now(),
            )

        except Exception:
            return None

    def _analyze_with_vader(self, headlines: List[str]) -> float:
        """Analyze sentiment from headlines using VADER NLP.

        VADER (Valence Aware Dictionary and sEntiment Reasoner) is tuned for
        social media but works well on financial news with our lexicon updates.

        Args:
            headlines: List of news headlines

        Returns:
            Sentiment score from -100 to 100
        """
        if not headlines:
            return 0.0

        if self.vader is None:
            # Fall back to keyword analysis if VADER unavailable
            return self._estimate_headline_sentiment_keywords(headlines)

        # Analyze each headline and aggregate
        compound_scores = []
        for headline in headlines:
            if headline.strip():
                scores = self.vader.polarity_scores(headline)
                compound_scores.append(scores['compound'])

        if not compound_scores:
            return 0.0

        # VADER compound score is -1 to 1, scale to -100 to 100
        avg_compound = sum(compound_scores) / len(compound_scores)
        return avg_compound * 100

    def _estimate_headline_sentiment_keywords(self, headlines: List[str]) -> float:
        """Fallback keyword-based sentiment when VADER unavailable.

        Args:
            headlines: List of news headlines

        Returns:
            Sentiment score from -100 to 100
        """
        if not headlines:
            return 0.0

        # Bullish keywords
        bullish = [
            "surge", "soar", "jump", "rally", "gain", "rise", "beat", "exceed",
            "upgrade", "buy", "bullish", "growth", "profit", "record", "high",
            "strong", "positive", "outperform", "boost", "expand", "success",
        ]

        # Bearish keywords
        bearish = [
            "fall", "drop", "plunge", "crash", "decline", "loss", "miss", "cut",
            "downgrade", "sell", "bearish", "weak", "negative", "underperform",
            "warning", "concern", "risk", "trouble", "fail", "lawsuit", "fraud",
        ]

        total_score = 0
        for headline in headlines:
            headline_lower = headline.lower()
            bullish_count = sum(1 for word in bullish if word in headline_lower)
            bearish_count = sum(1 for word in bearish if word in headline_lower)
            total_score += (bullish_count - bearish_count) * 10

        # Normalize to -100 to 100 range
        max_possible = len(headlines) * 30  # Assume max 3 keywords per headline
        if max_possible > 0:
            normalized = (total_score / max_possible) * 100
            return max(-100, min(100, normalized))

        return 0.0

    def _estimate_headline_sentiment(self, headlines: List[str]) -> float:
        """Estimate sentiment from headlines - uses VADER if available.

        Args:
            headlines: List of news headlines

        Returns:
            Sentiment score from -100 to 100
        """
        return self._analyze_with_vader(headlines)

    def _neutral_sentiment(self, ticker: str) -> SentimentResult:
        """Return neutral sentiment result.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Neutral SentimentResult
        """
        return SentimentResult(
            ticker=ticker,
            score=0.0,
            normalized_score=50.0,
            news_count=0,
            avg_relevance=0.0,
            sentiment_label="Neutral",
            headlines=[],
            sources={},
            timestamp=datetime.now(),
        )

    @staticmethod
    def _normalize_score(score: float) -> float:
        """Normalize score from -100/+100 to 0-100 scale.

        Args:
            score: Score from -100 to 100

        Returns:
            Normalized score from 0 to 100
        """
        return (score + 100) / 2

    @staticmethod
    def _score_to_label(score: float) -> str:
        """Convert score to sentiment label.

        Args:
            score: Score from -100 to 100

        Returns:
            Sentiment label string
        """
        if score <= -35:
            return "Bearish"
        elif score <= -15:
            return "Somewhat Bearish"
        elif score < 15:
            return "Neutral"
        elif score < 35:
            return "Somewhat Bullish"
        else:
            return "Bullish"
