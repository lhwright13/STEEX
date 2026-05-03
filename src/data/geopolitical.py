"""Geopolitical and macro sentiment using GDELT Project data.

GDELT (Global Database of Events, Language, and Tone) provides:
- Real-time global news monitoring in 100+ languages
- Sentiment/tone analysis from -100 to +100
- Event categorization (conflicts, politics, economics)
- Free API access with no rate limits

This module maps global events to sector impacts for trading signals.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import requests

from .base import DataProvider


@dataclass
class GeopoliticalEvent:
    """A geopolitical event with sector impact."""

    event_type: str
    description: str
    tone: float  # -100 to +100
    affected_sectors: Dict[str, float]  # sector -> impact multiplier
    source_count: int
    timestamp: datetime


@dataclass
class SectorSentiment:
    """Aggregated sentiment for a sector."""

    sector: str
    base_tone: float  # Overall news tone
    event_modifier: float  # Adjustment from geopolitical events
    final_score: float  # Combined score (0-100)
    events: List[str]  # Relevant events affecting this sector
    confidence: float  # 0-1 based on data quality


@dataclass
class MacroSentimentResult:
    """Complete macro sentiment analysis result."""

    timestamp: datetime
    global_tone: float  # Overall global sentiment
    sector_sentiments: Dict[str, SectorSentiment]
    active_events: List[GeopoliticalEvent]
    risk_level: str  # "low", "medium", "high", "extreme"


# Sector classification for S&P 500 stocks
SECTOR_MAPPING = {
    # Technology
    "XLK": "technology",
    "AAPL": "technology", "MSFT": "technology", "NVDA": "technology",
    "GOOGL": "technology", "GOOG": "technology", "META": "technology",
    "AVGO": "technology", "ORCL": "technology", "CRM": "technology",
    "AMD": "technology", "ADBE": "technology", "CSCO": "technology",
    "INTC": "technology", "IBM": "technology", "NOW": "technology",

    # Defense/Aerospace
    "XAR": "defense",
    "RTX": "defense", "LMT": "defense", "NOC": "defense",
    "GD": "defense", "BA": "defense", "HII": "defense",
    "LHX": "defense", "TDG": "defense", "HWM": "defense",

    # Energy
    "XLE": "energy",
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    "SLB": "energy", "EOG": "energy", "MPC": "energy",
    "PSX": "energy", "VLO": "energy", "OXY": "energy",
    "HAL": "energy", "DVN": "energy", "HES": "energy",

    # Financials
    "XLF": "financials",
    "JPM": "financials", "BAC": "financials", "WFC": "financials",
    "GS": "financials", "MS": "financials", "C": "financials",
    "AXP": "financials", "BLK": "financials", "SCHW": "financials",
    "BX": "financials", "KKR": "financials", "COF": "financials",

    # Healthcare
    "XLV": "healthcare",
    "UNH": "healthcare", "JNJ": "healthcare", "LLY": "healthcare",
    "PFE": "healthcare", "ABBV": "healthcare", "MRK": "healthcare",
    "TMO": "healthcare", "ABT": "healthcare", "DHR": "healthcare",
    "BMY": "healthcare", "AMGN": "healthcare", "GILD": "healthcare",

    # Consumer Discretionary (Travel/Leisure subset)
    "XLY": "consumer_discretionary",
    "AMZN": "consumer_discretionary", "TSLA": "consumer_discretionary",
    "HD": "consumer_discretionary", "MCD": "consumer_discretionary",
    "NKE": "consumer_discretionary", "SBUX": "consumer_discretionary",
    "LOW": "consumer_discretionary", "TJX": "consumer_discretionary",

    # Travel/Leisure (subset - impacted by geopolitical)
    "DAL": "travel", "UAL": "travel", "LUV": "travel", "AAL": "travel",
    "MAR": "travel", "HLT": "travel", "H": "travel",
    "CCL": "travel", "RCL": "travel", "NCLH": "travel",
    "BKNG": "travel", "EXPE": "travel",

    # Industrials
    "XLI": "industrials",
    "CAT": "industrials", "DE": "industrials", "UNP": "industrials",
    "HON": "industrials", "UPS": "industrials", "GE": "industrials",
    "MMM": "industrials", "EMR": "industrials", "ITW": "industrials",

    # Materials
    "XLB": "materials",
    "LIN": "materials", "APD": "materials", "SHW": "materials",
    "FCX": "materials", "NEM": "materials", "NUE": "materials",
    "DOW": "materials", "DD": "materials", "ECL": "materials",

    # Utilities
    "XLU": "utilities",
    "NEE": "utilities", "SO": "utilities", "DUK": "utilities",
    "D": "utilities", "AEP": "utilities", "EXC": "utilities",

    # Real Estate
    "XLRE": "real_estate",
    "AMT": "real_estate", "PLD": "real_estate", "CCI": "real_estate",
    "EQIX": "real_estate", "PSA": "real_estate", "SPG": "real_estate",

    # Consumer Staples
    "XLP": "consumer_staples",
    "PG": "consumer_staples", "KO": "consumer_staples", "PEP": "consumer_staples",
    "WMT": "consumer_staples", "COST": "consumer_staples", "PM": "consumer_staples",

    # Communication Services
    "XLC": "communication",
    "NFLX": "communication", "DIS": "communication", "CMCSA": "communication",
    "VZ": "communication", "T": "communication", "TMUS": "communication",
}

# Event type to sector impact mapping
# Positive values = bullish for sector, negative = bearish
EVENT_SECTOR_IMPACTS = {
    "military_conflict": {
        "defense": 25,  # RTX, LMT go up
        "energy": 15,  # Oil disruption concerns
        "travel": -30,  # Airlines, hotels down
        "consumer_discretionary": -10,
        "financials": -5,
    },
    "war_escalation": {
        "defense": 35,
        "energy": 25,
        "materials": 10,  # Commodities
        "travel": -40,
        "consumer_discretionary": -15,
        "technology": -10,
    },
    "peace_deal": {
        "defense": -15,
        "travel": 20,
        "consumer_discretionary": 10,
        "energy": -10,
    },
    "oil_supply_disruption": {
        "energy": 30,
        "travel": -25,
        "industrials": -10,
        "consumer_discretionary": -10,
    },
    "trade_war": {
        "technology": -20,  # Chip restrictions etc
        "industrials": -15,
        "materials": -10,
        "consumer_staples": 5,  # Domestic focus
    },
    "tariff_increase": {
        "industrials": -15,
        "consumer_discretionary": -10,
        "materials": -10,
        "consumer_staples": 5,
    },
    "interest_rate_hike": {
        "financials": 15,
        "real_estate": -20,
        "technology": -15,
        "utilities": -10,
    },
    "interest_rate_cut": {
        "real_estate": 15,
        "technology": 10,
        "utilities": 10,
        "financials": -10,
    },
    "pandemic_outbreak": {
        "healthcare": 20,
        "technology": 10,  # Remote work
        "travel": -40,
        "consumer_discretionary": -25,
        "energy": -20,
    },
    "economic_recession": {
        "consumer_staples": 10,
        "utilities": 10,
        "healthcare": 5,
        "consumer_discretionary": -25,
        "financials": -20,
        "industrials": -15,
    },
    "currency_crisis": {
        "materials": 10,  # Commodities as hedge
        "financials": -15,
        "consumer_discretionary": -10,
    },
    "political_instability": {
        "defense": 10,
        "utilities": 5,
        "consumer_discretionary": -10,
        "travel": -15,
    },
    "natural_disaster": {
        "materials": 10,  # Rebuilding
        "industrials": 5,
        "travel": -15,
        "real_estate": -10,
    },
    "cyber_attack": {
        "technology": -10,  # Initial fear, but...
        # Cybersecurity stocks would go up if we tracked them separately
    },
    "regulatory_crackdown": {
        "technology": -15,
        "financials": -10,
        "healthcare": -10,
    },
}

# Keywords to detect event types in news
EVENT_KEYWORDS = {
    "military_conflict": [
        "military strike", "bombing", "troops deployed", "armed conflict",
        "missile", "invasion", "military operation", "airstrikes",
    ],
    "war_escalation": [
        "war", "warfare", "declares war", "escalation", "ground invasion",
        "nuclear threat", "mobilization", "martial law",
    ],
    "peace_deal": [
        "peace deal", "ceasefire", "peace agreement", "truce",
        "peace talks succeed", "end of conflict", "peace treaty",
    ],
    "oil_supply_disruption": [
        "oil supply", "opec cut", "pipeline attack", "refinery",
        "oil embargo", "fuel shortage", "energy crisis",
    ],
    "trade_war": [
        "trade war", "trade dispute", "trade tensions", "export ban",
        "import restrictions", "trade retaliation",
    ],
    "tariff_increase": [
        "tariff", "import duty", "trade barrier", "customs duty",
        "protectionist", "import tax",
    ],
    "interest_rate_hike": [
        "rate hike", "interest rate increase", "fed raises", "hawkish fed",
        "monetary tightening", "rate increase",
    ],
    "interest_rate_cut": [
        "rate cut", "interest rate decrease", "fed cuts", "dovish fed",
        "monetary easing", "rate reduction",
    ],
    "pandemic_outbreak": [
        "pandemic", "outbreak", "virus spread", "health emergency",
        "epidemic", "quarantine", "lockdown",
    ],
    "economic_recession": [
        "recession", "economic downturn", "gdp contraction", "economic crisis",
        "depression", "economic collapse",
    ],
    "currency_crisis": [
        "currency crisis", "devaluation", "currency collapse",
        "forex crisis", "monetary crisis",
    ],
    "political_instability": [
        "coup", "political crisis", "government collapse", "civil unrest",
        "regime change", "political turmoil", "protests",
    ],
    "natural_disaster": [
        "earthquake", "hurricane", "typhoon", "flood", "wildfire",
        "tsunami", "volcanic eruption", "natural disaster",
    ],
    "cyber_attack": [
        "cyber attack", "data breach", "hacking", "ransomware",
        "cybersecurity breach", "cyber warfare",
    ],
    "regulatory_crackdown": [
        "regulatory crackdown", "antitrust", "regulation", "fine",
        "investigation", "compliance", "enforcement action",
    ],
}


class GeopoliticalSentimentProvider(DataProvider):
    """Provides macro/geopolitical sentiment using GDELT.

    GDELT offers free unlimited API access for news sentiment analysis.
    """

    GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
    GDELT_TV_API = "https://api.gdeltproject.org/api/v2/tv/tv"

    # Cache TTL in seconds (30 minutes for macro data)
    CACHE_TTL = 1800

    def __init__(self, cache_enabled: bool = True):
        """Initialize geopolitical sentiment provider.

        Args:
            cache_enabled: Whether to cache results
        """
        super().__init__(cache_enabled)
        self._cache_timestamps: Dict[str, datetime] = {}

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

    def fetch(self, query: str = "global markets") -> MacroSentimentResult:
        """Fetch macro sentiment data.

        Args:
            query: Search query for GDELT

        Returns:
            MacroSentimentResult with global sentiment
        """
        return self.get_macro_sentiment()

    def get_macro_sentiment(self) -> MacroSentimentResult:
        """Get overall macro/geopolitical sentiment.

        Returns:
            MacroSentimentResult with sector sentiments and events
        """
        cache_key = "macro_sentiment"

        if self._is_cache_valid(cache_key):
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached

        # Fetch global news tone
        global_tone = self._fetch_global_tone()

        # Detect active geopolitical events
        active_events = self._detect_events()

        # Calculate sector sentiments
        sector_sentiments = self._calculate_sector_sentiments(global_tone, active_events)

        # Determine risk level
        risk_level = self._assess_risk_level(global_tone, active_events)

        result = MacroSentimentResult(
            timestamp=datetime.now(),
            global_tone=global_tone,
            sector_sentiments=sector_sentiments,
            active_events=active_events,
            risk_level=risk_level,
        )

        self._set_cache_with_timestamp(cache_key, result)
        return result

    def get_sector_sentiment(self, sector: str) -> SectorSentiment:
        """Get sentiment for a specific sector.

        Args:
            sector: Sector name (e.g., "defense", "energy")

        Returns:
            SectorSentiment for the sector
        """
        macro = self.get_macro_sentiment()
        return macro.sector_sentiments.get(
            sector,
            SectorSentiment(
                sector=sector,
                base_tone=0,
                event_modifier=0,
                final_score=50,
                events=[],
                confidence=0.5,
            ),
        )

    def get_ticker_sector(self, ticker: str) -> str:
        """Get sector for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Sector name or "unknown"
        """
        return SECTOR_MAPPING.get(ticker.upper(), "unknown")

    def get_sector_modifier(self, ticker: str) -> float:
        """Get sector-based sentiment modifier for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Modifier from -50 to +50 based on sector sentiment
        """
        sector = self.get_ticker_sector(ticker)
        if sector == "unknown":
            return 0.0

        sector_sent = self.get_sector_sentiment(sector)
        # Convert 0-100 score to -50 to +50 modifier
        return sector_sent.final_score - 50

    def _fetch_global_tone(self) -> float:
        """Fetch global news tone from GDELT.

        Returns:
            Global tone score from -100 to +100
        """
        try:
            # Query for financial/market news
            # Note: GDELT API has issues with OR queries, use simpler query
            params = {
                "query": "stock market",
                "mode": "ToneChart",
                "format": "json",
                "timespan": "1d",
            }

            response = requests.get(self.GDELT_DOC_API, params=params, timeout=15)
            response.raise_for_status()

            # GDELT sometimes returns empty response
            if not response.text:
                return 0.0

            data = response.json()

            # Extract average tone from response
            if "tonechart" in data:
                tones = data["tonechart"]
                if tones:
                    # Calculate weighted average tone
                    # GDELT returns "bin" for tone value (-10 to +10 typically)
                    total_tone = 0
                    total_count = 0
                    for item in tones:
                        tone = float(item.get("bin", 0))
                        count = int(item.get("count", 1))
                        total_tone += tone * count
                        total_count += count

                    if total_count > 0:
                        avg_tone = total_tone / total_count
                        # GDELT tone is typically -10 to +10, scale to -100 to +100
                        return max(-100, min(100, avg_tone * 10))

            return 0.0  # Neutral if no data

        except Exception:
            return 0.0  # Neutral on error

    def _detect_events(self) -> List[GeopoliticalEvent]:
        """Detect active geopolitical events from news.

        Returns:
            List of detected GeopoliticalEvent objects
        """
        events = []

        for event_type, keywords in EVENT_KEYWORDS.items():
            event = self._search_for_event(event_type, keywords)
            if event:
                events.append(event)

        return events

    def _search_for_event(
        self,
        event_type: str,
        keywords: List[str],
    ) -> Optional[GeopoliticalEvent]:
        """Search GDELT for a specific event type using multiple keywords.

        Args:
            event_type: Type of event to search for
            keywords: Keywords to search

        Returns:
            GeopoliticalEvent if detected, None otherwise
        """
        if not keywords:
            return None

        # Search multiple keywords independently and aggregate results
        # GDELT API has issues with OR queries, so we search each keyword
        all_articles = []
        all_tones = []
        seen_urls: Set[str] = set()

        # Try up to 3 keywords to limit API calls while improving coverage
        keywords_to_try = keywords[:3]

        for keyword in keywords_to_try:
            try:
                params = {
                    "query": keyword,
                    "mode": "ArtList",
                    "format": "json",
                    "maxrecords": 10,
                    "timespan": "3d",
                }

                response = requests.get(self.GDELT_DOC_API, params=params, timeout=10)
                response.raise_for_status()

                if not response.text:
                    continue

                data = response.json()
                articles = data.get("articles", [])

                for article in articles:
                    url = article.get("url", "")
                    # Deduplicate by URL
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_articles.append(article)
                        tone = article.get("tone", 0)
                        if isinstance(tone, (int, float)):
                            all_tones.append(tone)

            except Exception:
                continue

        # Lower threshold from 3 to 2 articles for detection
        if len(all_articles) < 2:
            return None

        # Calculate average tone from all collected articles
        avg_tone = sum(all_tones) / len(all_tones) if all_tones else 0
        # Scale GDELT tone (-10 to +10) to our scale (-100 to +100)
        scaled_tone = max(-100, min(100, avg_tone * 10))

        # Get sector impacts for this event type
        impacts = EVENT_SECTOR_IMPACTS.get(event_type, {})

        # Use the most recent article's title as description
        description = event_type
        if all_articles:
            description = all_articles[0].get("title", event_type)

        return GeopoliticalEvent(
            event_type=event_type,
            description=description,
            tone=scaled_tone,
            affected_sectors=impacts,
            source_count=len(all_articles),
            timestamp=datetime.now(),
        )

    def _calculate_sector_sentiments(
        self,
        global_tone: float,
        events: List[GeopoliticalEvent],
    ) -> Dict[str, SectorSentiment]:
        """Calculate sentiment for each sector.

        Args:
            global_tone: Overall global sentiment
            events: List of active events

        Returns:
            Dict mapping sector name to SectorSentiment
        """
        sectors = [
            "technology", "defense", "energy", "financials", "healthcare",
            "consumer_discretionary", "travel", "industrials", "materials",
            "utilities", "real_estate", "consumer_staples", "communication",
        ]

        results = {}

        for sector in sectors:
            # Start with global tone as base (scale from -100/+100 to 0-100)
            base_score = (global_tone + 100) / 2

            # Apply event modifiers
            event_modifier = 0
            relevant_events = []

            for event in events:
                impact = event.affected_sectors.get(sector, 0)
                if impact != 0:
                    event_modifier += impact
                    relevant_events.append(f"{event.event_type}: {impact:+d}")

            # Combine base and modifier, clamp to 0-100
            final_score = max(0, min(100, base_score + event_modifier))

            # Confidence based on data quality
            confidence = 0.7 if events else 0.5

            results[sector] = SectorSentiment(
                sector=sector,
                base_tone=global_tone,
                event_modifier=event_modifier,
                final_score=final_score,
                events=relevant_events,
                confidence=confidence,
            )

        return results

    def _assess_risk_level(
        self,
        global_tone: float,
        events: List[GeopoliticalEvent],
    ) -> str:
        """Assess overall geopolitical risk level.

        Args:
            global_tone: Overall global sentiment
            events: List of active events

        Returns:
            Risk level: "low", "medium", "high", or "extreme"
        """
        # Count high-impact events
        high_impact_types = {"war_escalation", "pandemic_outbreak", "economic_recession"}
        high_impact_count = sum(1 for e in events if e.event_type in high_impact_types)

        medium_impact_types = {"military_conflict", "trade_war", "currency_crisis"}
        medium_impact_count = sum(1 for e in events if e.event_type in medium_impact_types)

        # Determine risk level
        if high_impact_count >= 2 or global_tone < -50:
            return "extreme"
        elif high_impact_count >= 1 or medium_impact_count >= 2 or global_tone < -25:
            return "high"
        elif medium_impact_count >= 1 or global_tone < -10:
            return "medium"
        else:
            return "low"


# ---------------------------------------------------------------------------
# Sector lookup cascade: SECTOR_MAPPING → DBCache → yfinance → "unknown"
# ---------------------------------------------------------------------------

_SECTOR_CACHE_TTL_POSITIVE = 30 * 86400  # 30 days for successful lookups
_SECTOR_CACHE_TTL_NEGATIVE = 86400       # 1 day for "unknown" / failures

# yfinance's sector labels mapped onto STEEX internal sector names so the
# result can be compared directly against SECTOR_MAPPING values downstream.
_YFINANCE_SECTOR_NORMALIZATION: Dict[str, str] = {
    "Technology": "technology",
    "Financial Services": "financials",
    "Healthcare": "healthcare",
    "Consumer Cyclical": "consumer_discretionary",
    "Consumer Defensive": "consumer_staples",
    "Industrials": "industrials",
    "Communication Services": "communication",
    "Energy": "energy",
    "Basic Materials": "materials",
    "Real Estate": "real_estate",
    "Utilities": "utilities",
}

_sector_cache_instance: Optional[Any] = None
_sector_cache_settings: Optional[Any] = None
_sector_cache_initialized: bool = False


def _get_sector_cache():
    """Lazy singleton for the sector DBCache and settings reference."""
    global _sector_cache_instance, _sector_cache_settings, _sector_cache_initialized
    if _sector_cache_initialized:
        return _sector_cache_instance, _sector_cache_settings
    _sector_cache_initialized = True
    try:
        from config.settings import get_settings
        from .cache import DBCache
        _sector_cache_settings = get_settings()
        _sector_cache_instance = DBCache(db_path=_sector_cache_settings.cache_db_path)
    except Exception:
        _sector_cache_instance = None
        _sector_cache_settings = None
    return _sector_cache_instance, _sector_cache_settings


def _fetch_sector_yfinance(ticker: str) -> Optional[str]:
    """Query yfinance for a ticker's sector and normalize the label.

    Returns the STEEX-internal sector name on success, or None on any
    failure (network error, rate limit, missing field) so the caller can
    record a short-TTL negative cache entry.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        raw = info.get("sector") if isinstance(info, dict) else None
        if not raw:
            return None
        return _YFINANCE_SECTOR_NORMALIZATION.get(raw, "unknown")
    except Exception:
        return None


def get_ticker_sector(ticker: str) -> str:
    """Return the STEEX-internal sector name for a ticker.

    Resolution cascade:
      1. Hardcoded SECTOR_MAPPING (hot path for mega-caps, no external calls).
      2. DBCache lookup (`sector:<TICKER>`) — previously-resolved tickers.
      3. yfinance fallback (gated by ``sector_lookup_yfinance_enabled``).
      4. ``"unknown"`` with a short-TTL negative cache so we retry tomorrow.

    Any failure inside the cascade returns ``"unknown"`` and never raises.
    """
    key = ticker.upper()

    if key in SECTOR_MAPPING:
        return SECTOR_MAPPING[key]

    cache, settings = _get_sector_cache()

    if cache is not None:
        try:
            hit = cache.get(f"sector:{key}")
            if hit is not None:
                return hit
        except Exception:
            pass

    kill_switch_on = settings is None or getattr(
        settings, "sector_lookup_yfinance_enabled", True
    )
    if kill_switch_on:
        resolved = _fetch_sector_yfinance(ticker)
        if resolved and resolved != "unknown":
            if cache is not None:
                try:
                    cache.set(f"sector:{key}", resolved, _SECTOR_CACHE_TTL_POSITIVE)
                except Exception:
                    pass
            return resolved

    if cache is not None:
        try:
            cache.set(f"sector:{key}", "unknown", _SECTOR_CACHE_TTL_NEGATIVE)
        except Exception:
            pass
    return "unknown"
