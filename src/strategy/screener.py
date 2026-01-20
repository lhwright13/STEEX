"""5-stage stock screening pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

from ..data.calendar import EarningsCalendar
from ..data.price import PriceProvider
from ..data.universe import Universe
from ..indicators.momentum import MomentumCalculator
from ..indicators.technical import TechnicalIndicators
from ..sec.scanners.insider import InsiderScanner
from ..sec.scanners.signals import calculate_cluster_score, find_cluster_buys
from config.settings import Settings, get_settings


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


@dataclass
class ScreeningPipelineResult:
    """Full pipeline results."""

    date: datetime
    universe_size: int
    stage_1_passed: int
    stage_2_passed: int
    stage_3_passed: int
    stage_4_passed: int
    final_candidates: List[ScreeningResult]
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
        """
        self.settings = settings or get_settings()
        self.universe = universe or Universe()
        self.price_provider = price_provider or PriceProvider()
        self.momentum = momentum_calc or MomentumCalculator(self.price_provider)
        self.technical = technical or TechnicalIndicators(self.price_provider)
        self.earnings = earnings_cal or EarningsCalendar()
        self.insider_scanner = insider_scanner or InsiderScanner()

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

            # Check all momentum conditions
            if (
                momentum_6m >= self.settings.momentum_min_return
                and momentum_1m > 0  # Not falling knife
                and percentile <= self.settings.overextension_percentile
            ):
                # Check MA alignment
                alignment = self.technical.check_trend_alignment(
                    ticker,
                    short_ma=self.settings.ma_short,
                    long_ma=self.settings.ma_long,
                )
                data["above_ma_50"] = alignment["above_short_ma"]
                data["above_ma_200"] = alignment["above_long_ma"]

                if alignment["aligned"]:
                    passed.append(ticker)

        return passed, momentum_data

    def stage_3_insider_filter(
        self,
        tickers: List[str],
        lookback_days: Optional[int] = None,
        all_transactions: Optional[List] = None,
    ) -> tuple[List[str], Dict[str, Dict]]:
        """Stage 3: Filter by insider activity.

        Args:
            tickers: Input tickers
            lookback_days: Days to look back for insider activity
            all_transactions: Pre-fetched transactions (optional)

        Returns:
            Tuple of (filtered tickers, insider data)
        """
        lookback = lookback_days or self.settings.insider_lookback_days
        insider_data = {}
        passed = []

        # Get all recent insider transactions if not provided
        if all_transactions is None:
            try:
                all_transactions = self.insider_scanner.scan(
                    days_back=lookback,
                    max_filings=1000,
                    verbose=False,
                )
            except Exception:
                all_transactions = []

        # Group transactions by ticker
        transactions_by_ticker: Dict[str, List] = {}
        for tx in all_transactions:
            if tx.ticker:
                ticker_upper = tx.ticker.upper()
                if ticker_upper not in transactions_by_ticker:
                    transactions_by_ticker[ticker_upper] = []
                transactions_by_ticker[ticker_upper].append(tx)

        # Filter tickers in our candidate list
        tickers_set = set(t.upper() for t in tickers)

        for ticker in tickers:
            ticker_upper = ticker.upper()
            transactions = transactions_by_ticker.get(ticker_upper, [])

            if not transactions:
                insider_data[ticker] = {
                    "score": 0,
                    "buyers": 0,
                    "total_value": 0,
                }
                continue

            # Filter to purchases only (already filtered in scanner, but double-check)
            purchases = [t for t in transactions if t.is_purchase]

            if not purchases:
                insider_data[ticker] = {
                    "score": 0,
                    "buyers": 0,
                    "total_value": 0,
                }
                continue

            # Calculate cluster score
            score_data = calculate_cluster_score(purchases)

            unique_buyers = len(set(t.insider_cik for t in purchases))
            total_value = sum(t.total_value for t in purchases)

            insider_data[ticker] = {
                "score": score_data["score"],
                "buyers": unique_buyers,
                "total_value": total_value,
                "factors": score_data.get("factors", {}),
            }

            # Check if passes insider criteria
            # Any of: CEO/CFO buy, 3+ buyers, high value purchase
            has_ceo_cfo = any(
                t.officer_title
                and ("CEO" in t.officer_title.upper() or "CFO" in t.officer_title.upper())
                for t in purchases
            )
            has_cluster = unique_buyers >= self.settings.min_cluster_buyers
            has_high_value = total_value >= self.settings.min_purchase_value

            if has_ceo_cfo or has_cluster or has_high_value:
                passed.append(ticker)

        return passed, insider_data

    def stage_4_sentiment_filter(
        self,
        tickers: List[str],
    ) -> List[str]:
        """Stage 4: Sentiment check (stub - returns all tickers).

        This stage is optional and can be implemented with sentiment data.

        Args:
            tickers: Input tickers

        Returns:
            Filtered tickers (currently passthrough)
        """
        # Sentiment filtering is optional enhancement
        # For now, pass all tickers through
        return tickers

    def run_pipeline(
        self,
        reference_date: Optional[datetime] = None,
    ) -> ScreeningPipelineResult:
        """Run the full 5-stage screening pipeline.

        Args:
            reference_date: Date for the screening

        Returns:
            ScreeningPipelineResult with all stages
        """
        date = reference_date or datetime.now()
        all_results: Dict[str, ScreeningResult] = {}

        # Get initial universe
        universe = self.universe.get_sp500()
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

        # Stage 3: Insider filter
        stage_3, insider_data = self.stage_3_insider_filter(stage_2)
        for ticker in stage_2:
            if ticker in insider_data:
                data = insider_data[ticker]
                all_results[ticker].insider_score = data.get("score", 0)
                all_results[ticker].insider_buyers = data.get("buyers", 0)
                all_results[ticker].total_insider_value = data.get("total_value", 0)

        for ticker in stage_3:
            all_results[ticker].passed_stages.append("stage_3")
        for ticker in set(stage_2) - set(stage_3):
            if all_results[ticker].failed_stage is None:
                all_results[ticker].failed_stage = "stage_3"

        # Stage 4: Sentiment filter
        stage_4 = self.stage_4_sentiment_filter(stage_3)
        for ticker in stage_4:
            all_results[ticker].passed_stages.append("stage_4")

        # Get volume surge for final candidates
        if stage_4:
            volume_data = self.technical.get_volume_surge_batch(stage_4)
            for ticker in stage_4:
                all_results[ticker].volume_surge = volume_data.get(ticker)

        # Final candidates
        final_candidates = [all_results[t] for t in stage_4]

        return ScreeningPipelineResult(
            date=date,
            universe_size=universe_size,
            stage_1_passed=len(stage_1),
            stage_2_passed=len(stage_2),
            stage_3_passed=len(stage_3),
            stage_4_passed=len(stage_4),
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
