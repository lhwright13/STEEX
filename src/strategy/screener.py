"""5-stage stock screening pipeline."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..data.calendar import EarningsCalendar
from ..data.price import PriceProvider
from ..data.universe import Universe
from ..data.sentiment import SentimentProvider, SentimentResult
from ..data.geopolitical import GeopoliticalSentimentProvider, get_ticker_sector
from ..data.fundamentals import FundamentalsProvider, FundamentalData
from ..data.options import OptionsProvider, OptionsData
from ..indicators.momentum import MomentumCalculator
from ..indicators.technical import TechnicalIndicators
from ..sec.scanners.insider import InsiderScanner
from ..sec.models import InsiderTransaction
from ..sec.scanners.signals import calculate_cluster_score, find_cluster_buys
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Cache file for historical insider data
INSIDER_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "cache" / "historical_insiders.json"


@dataclass
class ScreeningResult:
    """Result of screening pipeline."""

    ticker: str
    passed_stages: List[str] = field(default_factory=list)
    failed_stage: Optional[str] = None
    momentum_6m: Optional[float] = None
    momentum_1m: Optional[float] = None
    momentum_percentile: Optional[float] = None
    above_ma_50: bool = False
    above_ma_200: bool = False
    insider_score: float = 0
    insider_buyers: int = 0
    total_insider_value: float = 0
    volume_surge: Optional[float] = None
    has_earnings_soon: bool = False
    sentiment_score: Optional[float] = None  # 0-100 combined sentiment
    sentiment_label: Optional[str] = None  # "Bearish", "Neutral", "Bullish"
    sector: Optional[str] = None  # Sector for geopolitical impact
    # Fundamental data
    fundamental_score: Optional[float] = None  # 0-100 fundamental score
    pe_ratio: Optional[float] = None
    peg_ratio: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    revenue_growth: Optional[float] = None
    # Options data
    options_score: Optional[float] = None  # 0-100 options sentiment score
    put_call_ratio: Optional[float] = None
    iv_rank: Optional[float] = None
    # PySR symbolic regression data
    pysr_score: Optional[float] = None
    pysr_predicted_return: Optional[float] = None
    pysr_equation: Optional[str] = None
    pysr_confidence: Optional[float] = None


@dataclass
class ScreeningPipelineResult:
    """Full pipeline results."""

    date: datetime
    universe_size: int
    stage_1_passed: int
    stage_2_passed: int
    stage_3_passed: int
    stage_4_passed: int
    stage_5_passed: int = 0  # Fundamental filter (optional stage)
    final_candidates: List[ScreeningResult] = field(default_factory=list)
    all_results: Dict[str, ScreeningResult] = field(default_factory=dict)


class StockScreener:
    """Multi-stage stock screening pipeline."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        universe: Optional[Universe] = None,
        price_provider: Optional[PriceProvider] = None,
        momentum_calc: Optional[MomentumCalculator] = None,
        technical: Optional[TechnicalIndicators] = None,
        earnings_cal: Optional[EarningsCalendar] = None,
        insider_scanner: Optional[InsiderScanner] = None,
        sentiment_provider: Optional[SentimentProvider] = None,
        geopolitical_provider: Optional[GeopoliticalSentimentProvider] = None,
        fundamentals_provider: Optional[FundamentalsProvider] = None,
        options_provider: Optional[OptionsProvider] = None,
        pysr_predictor=None,
    ):
        """Initialize screener with dependencies.

        Args:
            settings: Configuration settings
            universe: Universe provider
            price_provider: Price data provider
            momentum_calc: Momentum calculator
            technical: Technical indicators calculator
            earnings_cal: Earnings calendar provider
            insider_scanner: Insider trading scanner
            sentiment_provider: Stock-specific sentiment provider
            geopolitical_provider: Geopolitical/macro sentiment provider
            fundamentals_provider: Fundamental analysis provider
            options_provider: Options intelligence provider
            pysr_predictor: PySR predictor instance (optional)
        """
        self.settings = settings or get_settings()
        self.universe = universe or Universe()
        self.price_provider = price_provider or PriceProvider()
        self.momentum = momentum_calc or MomentumCalculator(self.price_provider)
        self.technical = technical or TechnicalIndicators(self.price_provider)
        self.earnings = earnings_cal or EarningsCalendar()
        self.insider_scanner = insider_scanner or InsiderScanner()
        self.sentiment_provider = sentiment_provider or SentimentProvider()
        self.geopolitical_provider = geopolitical_provider or GeopoliticalSentimentProvider()
        self.fundamentals_provider = fundamentals_provider or FundamentalsProvider()
        self.options_provider = options_provider or OptionsProvider()
        self.pysr_predictor = pysr_predictor

    def _load_cached_insider_transactions(
        self, days_back: int = 30
    ) -> List[InsiderTransaction]:
        """Load insider transactions from cache file.

        Args:
            days_back: Only include transactions from last N days

        Returns:
            List of InsiderTransaction objects
        """
        if not INSIDER_CACHE_FILE.exists():
            return []

        try:
            with open(INSIDER_CACHE_FILE) as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

        cutoff = datetime.now() - timedelta(days=days_back)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        transactions = []
        for date_str, txs in cache.get("dates", {}).items():
            if date_str >= cutoff_str:
                for tx in txs:
                    ticker = tx.get("ticker")
                    if not ticker or ticker in ("N/A", "NONE", ""):
                        continue

                    transactions.append(
                        InsiderTransaction(
                            ticker=ticker,
                            company_name=tx.get("company_name", ""),
                            company_cik="",
                            insider_name=tx.get("insider_name", ""),
                            insider_cik=tx.get("insider_name", ""),  # Use name as ID
                            is_director=tx.get("is_director", False),
                            is_officer=tx.get("is_officer", False),
                            is_ten_percent_owner=tx.get("is_ten_percent_owner", False),
                            officer_title=tx.get("role", ""),
                            transaction_date=tx.get("transaction_date", ""),
                            transaction_code=tx.get("transaction_code", "P"),
                            acquired_disposed="A",
                            shares=tx.get("shares", 0),
                            price_per_share=tx.get("price_per_share", 0),
                            total_value=tx.get("total_value", 0),
                            shares_owned_after=0,
                            filing_date=tx.get("filing_date", ""),
                            filing_url="",
                        )
                    )

        return transactions

    def stage_1_universe_filter(
        self,
        tickers: Optional[List[str]] = None,
        reference_date: Optional[datetime] = None,
    ) -> List[str]:
        """Stage 1: Filter by price, volume, and earnings.

        Args:
            tickers: Input tickers (defaults to S&P 500)
            reference_date: Date for earnings check

        Returns:
            Filtered list of tickers
        """
        if tickers is None:
            tickers = self.universe.get_sp500()

        # Filter by price and volume
        filtered = self.universe.filter_by_price_volume(
            tickers,
            min_price=self.settings.min_price,
            min_volume=self.settings.min_volume,
        )

        # Filter out stocks with upcoming earnings
        filtered = self.earnings.filter_earnings_blackout(
            filtered,
            blackout_days=self.settings.earnings_blackout_days,
            reference_date=reference_date,
        )

        return filtered

    def stage_2_momentum_filter(
        self,
        tickers: List[str],
    ) -> tuple[List[str], Dict[str, Dict]]:
        """Stage 2: Filter by momentum criteria.

        Args:
            tickers: Input tickers

        Returns:
            Tuple of (filtered tickers, momentum data)
        """
        # Get 6-month momentum and percentiles
        momentum_data = self.momentum.get_momentum_percentiles(
            tickers,
            lookback_days=self.settings.momentum_lookback_days,
        )

        # Get 1-month momentum
        short_momentum = self.momentum.get_momentum_batch(
            tickers,
            lookback_days=self.settings.short_momentum_days,
        )

        # Combine data
        for ticker in momentum_data:
            momentum_data[ticker]["momentum_1m"] = short_momentum.get(ticker, 0)

        passed = []
        for ticker, data in momentum_data.items():
            momentum_6m = data.get("momentum", 0)
            momentum_1m = data.get("momentum_1m", 0)
            percentile = data.get("percentile", 0)

            # Check momentum conditions
            if (
                momentum_6m >= self.settings.momentum_min_return
                and momentum_1m >= self.settings.short_momentum_min_return
                and (not self.settings.overextension_filter_enabled or percentile <= self.settings.overextension_percentile)
            ):
                # Check MA alignment
                alignment = self.technical.check_trend_alignment(
                    ticker,
                    short_ma=self.settings.ma_short,
                    long_ma=self.settings.ma_long,
                )
                data["above_ma_50"] = alignment["above_short_ma"]
                data["above_ma_200"] = alignment["above_long_ma"]

                if self.settings.require_dual_ma:
                    ma_ok = alignment["aligned"]
                else:
                    ma_ok = alignment["above_short_ma"]

                if ma_ok:
                    passed.append(ticker)

        return passed, momentum_data

    def stage_3_insider_enrich(
        self,
        tickers: List[str],
        lookback_days: Optional[int] = None,
        all_transactions: Optional[List] = None,
    ) -> tuple[List[str], Dict[str, Dict]]:
        """Stage 3: Enrich with insider activity data (no longer a hard filter).

        All tickers pass through - insider activity boosts ranking score
        rather than being a gate. Stocks with insider buying will rank
        higher due to the insider weight in composite scoring.

        Args:
            tickers: Input tickers
            lookback_days: Days to look back for insider activity
            all_transactions: Pre-fetched transactions (optional)

        Returns:
            Tuple of (all tickers, insider data for scoring)
        """
        lookback = lookback_days or self.settings.insider_lookback_days
        insider_data = {}

        # Get all recent insider transactions if not provided
        if all_transactions is None:
            try:
                all_transactions = self.insider_scanner.scan(
                    days_back=lookback,
                    max_filings=1000,
                    verbose=False,
                )
            except Exception:
                logger.warning("Insider scan failed for stage 3, using empty transactions", exc_info=True)
                all_transactions = []

        # Group transactions by ticker
        transactions_by_ticker: Dict[str, List] = {}
        for tx in all_transactions:
            if tx.ticker:
                ticker_upper = tx.ticker.upper()
                if ticker_upper not in transactions_by_ticker:
                    transactions_by_ticker[ticker_upper] = []
                transactions_by_ticker[ticker_upper].append(tx)

        for ticker in tickers:
            ticker_upper = ticker.upper()
            transactions = transactions_by_ticker.get(ticker_upper, [])

            if not transactions:
                insider_data[ticker] = {
                    "score": 0,
                    "buyers": 0,
                    "total_value": 0,
                    "has_insider_activity": False,
                }
                continue

            # Filter to purchases only
            purchases = [t for t in transactions if t.is_purchase]

            if not purchases:
                insider_data[ticker] = {
                    "score": 0,
                    "buyers": 0,
                    "total_value": 0,
                    "has_insider_activity": False,
                }
                continue

            # Calculate cluster score
            score_data = calculate_cluster_score(purchases)

            unique_buyers = len(set(t.insider_cik for t in purchases))
            total_value = sum(t.total_value for t in purchases)

            # Check for strong insider signals (for display/ranking boost)
            has_ceo_cfo = any(
                t.officer_title
                and ("CEO" in t.officer_title.upper() or "CFO" in t.officer_title.upper())
                for t in purchases
            )
            has_cluster = unique_buyers >= self.settings.min_cluster_buyers
            has_high_value = total_value >= self.settings.min_purchase_value

            insider_data[ticker] = {
                "score": score_data["score"],
                "buyers": unique_buyers,
                "total_value": total_value,
                "factors": score_data.get("factors", {}),
                "has_insider_activity": True,
                "has_strong_signal": has_ceo_cfo or has_cluster or has_high_value,
            }

        # All tickers pass through - insider is now a scoring boost, not a gate
        return tickers, insider_data

    def stage_4_sentiment_filter(
        self,
        tickers: List[str],
    ) -> tuple[List[str], Dict[str, Dict]]:
        """Stage 4: Filter by combined sentiment (stock-specific + geopolitical).

        Combines:
        1. Stock-specific news sentiment (Alpha Vantage/Finnhub)
        2. Sector-level geopolitical sentiment (GDELT)

        Args:
            tickers: Input tickers

        Returns:
            Tuple of (filtered tickers, sentiment data)
        """
        if not self.settings.sentiment_enabled:
            # Return all tickers with neutral sentiment if disabled
            return tickers, {t: {"score": 50, "label": "Neutral"} for t in tickers}

        sentiment_data = {}
        passed = []

        # Get macro sentiment once (affects all tickers via sector)
        macro_sentiment = None
        if self.settings.geopolitical_enabled:
            try:
                macro_sentiment = self.geopolitical_provider.get_macro_sentiment()
            except Exception:
                logger.debug("Failed to fetch macro/geopolitical sentiment", exc_info=True)
                macro_sentiment = None

        for ticker in tickers:
            try:
                # Get stock-specific sentiment
                stock_sentiment = self.sentiment_provider.get_sentiment(ticker)
                stock_score = stock_sentiment.normalized_score  # 0-100

                # Get sector-based geopolitical modifier
                geo_score = 50.0  # Neutral default
                sector = get_ticker_sector(ticker)

                if macro_sentiment and sector != "unknown":
                    sector_sent = macro_sentiment.sector_sentiments.get(sector)
                    if sector_sent:
                        geo_score = sector_sent.final_score

                # Combine scores with configurable weights
                # Default: 60% stock-specific, 40% geopolitical
                combined_score = (
                    self.settings.sentiment_stock_weight * stock_score
                    + self.settings.geopolitical_weight * geo_score
                )

                # Determine label
                if combined_score < 35:
                    label = "Bearish"
                elif combined_score < 45:
                    label = "Somewhat Bearish"
                elif combined_score < 55:
                    label = "Neutral"
                elif combined_score < 65:
                    label = "Somewhat Bullish"
                else:
                    label = "Bullish"

                sentiment_data[ticker] = {
                    "score": combined_score,
                    "stock_score": stock_score,
                    "geo_score": geo_score,
                    "sector": sector,
                    "label": label,
                    "headlines": stock_sentiment.headlines[:3],
                }

                # Pass if sentiment meets minimum threshold
                if combined_score >= self.settings.sentiment_min_score:
                    passed.append(ticker)

            except Exception:
                # On error, give neutral score and pass through
                logger.debug("Sentiment analysis failed for %s, using neutral score", ticker, exc_info=True)
                sentiment_data[ticker] = {
                    "score": 50,
                    "stock_score": 50,
                    "geo_score": 50,
                    "sector": "unknown",
                    "label": "Neutral",
                    "headlines": [],
                }
                passed.append(ticker)

        return passed, sentiment_data

    def stage_5_fundamental_filter(
        self,
        tickers: List[str],
    ) -> tuple[List[str], Dict[str, Dict]]:
        """Stage 5: Filter by fundamental analysis.

        Uses fundamental metrics like P/E, ROE, debt levels to filter
        out speculative or poor quality stocks.

        Args:
            tickers: Input tickers

        Returns:
            Tuple of (filtered tickers, fundamental data)
        """
        if not self.settings.fundamental_enabled:
            # Return all tickers with neutral scores if disabled
            return tickers, {t: {"score": 50.0, "passed": True} for t in tickers}

        fundamental_data = {}
        passed = []

        for ticker in tickers:
            try:
                data = self.fundamentals_provider.get_fundamentals(ticker)

                fundamental_data[ticker] = {
                    "score": data.fundamental_score,
                    "pe_ratio": data.pe_ratio,
                    "peg_ratio": data.peg_ratio,
                    "roe": data.return_on_equity,
                    "debt_to_equity": data.debt_to_equity,
                    "revenue_growth": data.revenue_growth,
                    "profit_margin": data.profit_margin,
                }

                # Check fundamental filters
                passes, reason = self.fundamentals_provider.passes_fundamental_filter(
                    data,
                    max_pe=self.settings.fundamental_max_pe,
                    min_roe=self.settings.fundamental_min_roe,
                    max_debt_equity=self.settings.fundamental_max_debt_equity,
                )

                fundamental_data[ticker]["passed"] = passes
                fundamental_data[ticker]["reason"] = reason

                if passes:
                    passed.append(ticker)

            except Exception:
                # On error, pass through with neutral score
                logger.debug("Fundamental analysis failed for %s, using neutral score", ticker, exc_info=True)
                fundamental_data[ticker] = {
                    "score": 50.0,
                    "passed": True,
                    "reason": "Data unavailable",
                }
                passed.append(ticker)

        return passed, fundamental_data

    def run_pipeline(
        self,
        reference_date: Optional[datetime] = None,
        custom_universe: Optional[List[str]] = None,
    ) -> ScreeningPipelineResult:
        """Run the full 5-stage screening pipeline.

        Args:
            reference_date: Date for the screening
            custom_universe: Custom list of tickers (defaults to S&P 500)

        Returns:
            ScreeningPipelineResult with all stages
        """
        date = reference_date or datetime.now()
        all_results: Dict[str, ScreeningResult] = {}

        # Get initial universe
        universe = custom_universe if custom_universe is not None else self.universe.get_sp500()
        universe_size = len(universe)

        # Initialize results for all tickers
        for ticker in universe:
            all_results[ticker] = ScreeningResult(ticker=ticker)

        # Stage 1: Universe filter
        stage_1 = self.stage_1_universe_filter(universe, reference_date)
        for ticker in stage_1:
            all_results[ticker].passed_stages.append("stage_1")
        for ticker in set(universe) - set(stage_1):
            all_results[ticker].failed_stage = "stage_1"

        # Stage 2: Momentum filter
        stage_2, momentum_data = self.stage_2_momentum_filter(stage_1)
        for ticker in stage_1:
            if ticker in momentum_data:
                data = momentum_data[ticker]
                all_results[ticker].momentum_6m = data.get("momentum")
                all_results[ticker].momentum_1m = data.get("momentum_1m")
                all_results[ticker].momentum_percentile = data.get("percentile")
                all_results[ticker].above_ma_50 = data.get("above_ma_50", False)
                all_results[ticker].above_ma_200 = data.get("above_ma_200", False)

        for ticker in stage_2:
            all_results[ticker].passed_stages.append("stage_2")
        for ticker in set(stage_1) - set(stage_2):
            if all_results[ticker].failed_stage is None:
                all_results[ticker].failed_stage = "stage_2"

        # Stage 3: Insider enrichment (no longer a hard filter)
        # All momentum stocks pass through - insider activity boosts ranking
        cached_transactions = self._load_cached_insider_transactions(
            days_back=self.settings.insider_lookback_days
        )
        stage_3, insider_data = self.stage_3_insider_enrich(
            stage_2,
            all_transactions=cached_transactions if cached_transactions else None,
        )
        for ticker in stage_2:
            if ticker in insider_data:
                data = insider_data[ticker]
                all_results[ticker].insider_score = data.get("score", 0)
                all_results[ticker].insider_buyers = data.get("buyers", 0)
                all_results[ticker].total_insider_value = data.get("total_value", 0)

        for ticker in stage_3:
            all_results[ticker].passed_stages.append("stage_3")
        # No tickers fail stage_3 anymore - it's enrichment, not filtering

        # Stage 4: Sentiment filter
        stage_4, sentiment_data = self.stage_4_sentiment_filter(stage_3)
        for ticker in stage_3:
            if ticker in sentiment_data:
                data = sentiment_data[ticker]
                all_results[ticker].sentiment_score = data.get("score")
                all_results[ticker].sentiment_label = data.get("label")
                all_results[ticker].sector = data.get("sector")

        for ticker in stage_4:
            all_results[ticker].passed_stages.append("stage_4")
        for ticker in set(stage_3) - set(stage_4):
            if all_results[ticker].failed_stage is None:
                all_results[ticker].failed_stage = "stage_4"

        # Stage 5: Fundamental filter
        stage_5, fundamental_data = self.stage_5_fundamental_filter(stage_4)
        for ticker in stage_4:
            if ticker in fundamental_data:
                data = fundamental_data[ticker]
                all_results[ticker].fundamental_score = data.get("score")
                all_results[ticker].pe_ratio = data.get("pe_ratio")
                all_results[ticker].peg_ratio = data.get("peg_ratio")
                all_results[ticker].roe = data.get("roe")
                all_results[ticker].debt_to_equity = data.get("debt_to_equity")
                all_results[ticker].revenue_growth = data.get("revenue_growth")

        for ticker in stage_5:
            all_results[ticker].passed_stages.append("stage_5")
        for ticker in set(stage_4) - set(stage_5):
            if all_results[ticker].failed_stage is None:
                all_results[ticker].failed_stage = "stage_5"

        # Get volume surge for final candidates
        if stage_5:
            volume_data = self.technical.get_volume_surge_batch(stage_5)
            for ticker in stage_5:
                all_results[ticker].volume_surge = volume_data.get(ticker)

        # Get options data for final candidates (enrichment, not filtering)
        if stage_5 and self.settings.options_enabled:
            for ticker in stage_5:
                try:
                    options_data = self.options_provider.get_options_sentiment(ticker)
                    all_results[ticker].options_score = options_data.options_score
                    all_results[ticker].put_call_ratio = options_data.put_call_oi_ratio
                    all_results[ticker].iv_rank = options_data.avg_call_iv
                except Exception:
                    # Options data is enrichment, not critical
                    logger.debug("Options data fetch failed for %s, using neutral score", ticker, exc_info=True)
                    all_results[ticker].options_score = 50.0

        # PySR enrichment (if enabled and available)
        if stage_5 and self.settings.pysr_enabled and self.pysr_predictor:
            if self.pysr_predictor.is_available():
                try:
                    predictions = self.pysr_predictor.predict_batch(stage_5)
                    scores = self.pysr_predictor.compute_pysr_score(predictions)
                    for ticker in stage_5:
                        if ticker in predictions:
                            all_results[ticker].pysr_score = scores.get(ticker, 50.0)
                            all_results[ticker].pysr_predicted_return = predictions[ticker].predicted_return_21d
                            all_results[ticker].pysr_equation = predictions[ticker].equation_used
                            all_results[ticker].pysr_confidence = predictions[ticker].confidence
                except Exception:
                    logger.debug("PySR prediction failed for batch, skipping enrichment", exc_info=True)

        # Final candidates
        final_candidates = [all_results[t] for t in stage_5]

        return ScreeningPipelineResult(
            date=date,
            universe_size=universe_size,
            stage_1_passed=len(stage_1),
            stage_2_passed=len(stage_2),
            stage_3_passed=len(stage_3),
            stage_4_passed=len(stage_4),
            stage_5_passed=len(stage_5),
            final_candidates=final_candidates,
            all_results=all_results,
        )

    def get_candidates(
        self,
        reference_date: Optional[datetime] = None,
    ) -> List[ScreeningResult]:
        """Get list of candidate stocks from screening.

        Args:
            reference_date: Date for the screening

        Returns:
            List of ScreeningResult for candidates
        """
        result = self.run_pipeline(reference_date)
        return result.final_candidates
