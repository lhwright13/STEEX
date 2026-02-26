"""Walk-forward backtester that replays the full screening pipeline historically.

Unlike BacktestEngine which takes pre-generated signals, this replays
the FULL screening pipeline for each date using HistoricalPriceProvider
to prevent lookahead bias.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config.settings import Settings, get_settings
from ..data.historical import HistoricalPriceProvider
from ..data.price import PriceProvider
from ..data.vix import VixProvider
from ..indicators.momentum import MomentumCalculator
from ..indicators.technical import TechnicalIndicators
from ..strategy.screener import StockScreener
from ..strategy.ranking import StockRanker
from .engine import BacktestEngine, BacktestResult
from .metrics import calculate_metrics

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardConfig:
    """Configuration for a single walk-forward fold."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass
class WalkForwardFoldResult:
    """Results from a single walk-forward fold."""

    config: WalkForwardConfig
    in_sample: BacktestResult
    out_of_sample: BacktestResult
    signals_generated: int


@dataclass
class RegimeMetrics:
    """Performance metrics segmented by regime."""

    regime_name: str
    trade_count: int
    win_rate: float
    avg_return: float
    sharpe: float


class WalkForwardBacktester:
    """Replays the full screening pipeline historically via walk-forward windows.

    Core idea: for each reference date, create a HistoricalPriceProvider
    truncated to that date, inject it into the screener stack, run the
    full pipeline, and collect both signals and the feature matrix.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        price_provider: Optional[PriceProvider] = None,
        vix_provider: Optional[VixProvider] = None,
    ):
        self.settings = settings or get_settings()
        self.price_provider = price_provider or PriceProvider()
        self.vix_provider = vix_provider or VixProvider()

    def prefetch_data(
        self,
        tickers: List[str],
        start: datetime,
        end: datetime,
    ) -> Dict[str, pd.DataFrame]:
        """Bulk fetch all price data upfront.

        Args:
            tickers: List of ticker symbols
            start: Earliest date needed (include MA buffer)
            end: Latest date needed

        Returns:
            Dict mapping ticker to full OHLCV DataFrame
        """
        # Add buffer for MA calculations (200 trading days ~= 300 calendar days)
        buffer_start = start - timedelta(days=400)
        logger.info(
            "Prefetching %d tickers from %s to %s",
            len(tickers), buffer_start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
        )
        return self.price_provider.get_ohlcv_batch(
            tickers, start=buffer_start, end=end
        )

    def run_pipeline_for_date(
        self,
        reference_date: datetime,
        price_cache: Dict[str, pd.DataFrame],
        settings: Optional[Settings] = None,
        universe: Optional[List[str]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """Run the full screening pipeline as of a historical date.

        Creates a HistoricalPriceProvider for this date, injects it into
        the screener stack, runs the pipeline, and extracts both signals
        and the feature matrix.

        Args:
            reference_date: The "as-of" date
            price_cache: Pre-fetched price data
            settings: Optional settings override
            universe: Custom universe of tickers

        Returns:
            (signals, feature_matrix) where:
            - signals: List of {date, ticker, score}
            - feature_matrix: List of {date, ticker, score, momentum_score, ...}
        """
        settings = settings or self.settings

        # Create historical price provider for this date
        hist_provider = HistoricalPriceProvider(reference_date, price_cache)
        momentum = MomentumCalculator(hist_provider)
        technical = TechnicalIndicators(hist_provider)

        # Build screener with historical provider injected
        screener = StockScreener(
            settings=settings,
            price_provider=hist_provider,
            momentum_calc=momentum,
            technical=technical,
        )
        ranker = StockRanker(settings)

        # Run pipeline
        try:
            pipeline = screener.run_pipeline(
                reference_date=reference_date,
                custom_universe=universe,
            )
        except Exception as e:
            logger.warning("Pipeline failed for %s: %s", reference_date, e)
            return [], []

        if not pipeline.final_candidates:
            return [], []

        # Rank and extract signals + features
        ranked = ranker.rank_stocks(pipeline.final_candidates)
        signals = []
        features = []

        for stock in ranked:
            signal = {
                "date": reference_date,
                "ticker": stock.ticker,
                "score": stock.composite_score,
            }
            signals.append(signal)

            feature = {
                "date": reference_date,
                "ticker": stock.ticker,
                "composite_score": stock.composite_score,
                "momentum_score": stock.momentum_score,
                "insider_score": stock.insider_score,
                "volume_score": stock.volume_score,
                "sentiment_score": stock.sentiment_score,
                "fundamental_score": stock.fundamental_score,
                "options_score": stock.options_score,
                "pysr_score": stock.pysr_score,
            }
            features.append(feature)

        return signals, features

    def generate_historical_signals(
        self,
        start: datetime,
        end: datetime,
        interval_days: Optional[int] = None,
        price_cache: Optional[Dict[str, pd.DataFrame]] = None,
        universe: Optional[List[str]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """Generate signals by running the pipeline at regular intervals.

        Args:
            start: Start date
            end: End date
            interval_days: Days between signal generations
            price_cache: Pre-fetched data (will be fetched if None)
            universe: Custom universe

        Returns:
            (all_signals, all_features)
        """
        interval = interval_days or self.settings.walkforward_signal_interval

        if price_cache is None:
            # Fetch universe if not provided
            if universe is None:
                from ..data.universe import Universe
                universe = Universe().get_tickers()
            price_cache = self.prefetch_data(universe, start, end)

        all_signals = []
        all_features = []
        current = start

        while current <= end:
            # Skip weekends
            if current.weekday() < 5:
                sigs, feats = self.run_pipeline_for_date(
                    current, price_cache, universe=universe,
                )
                all_signals.extend(sigs)
                all_features.extend(feats)
                logger.info(
                    "%s: %d signals generated",
                    current.strftime("%Y-%m-%d"), len(sigs),
                )

            current += timedelta(days=interval)

        return all_signals, all_features

    def run_walk_forward(
        self,
        folds: Optional[List[WalkForwardConfig]] = None,
        starting_capital: float = 10000,
        universe: Optional[List[str]] = None,
    ) -> List[WalkForwardFoldResult]:
        """Run walk-forward backtest across multiple folds.

        For each fold: generate signals in train+test windows,
        run BacktestEngine on each window separately, return
        in-sample vs out-of-sample comparison.

        Args:
            folds: List of fold configs (auto-generated if None)
            starting_capital: Starting capital per fold
            universe: Custom universe of tickers

        Returns:
            List of fold results
        """
        if folds is None:
            folds = self._generate_folds()

        # Get universe
        if universe is None:
            from ..data.universe import Universe
            universe = Universe().get_tickers()

        # Prefetch data for entire period
        earliest = min(f.train_start for f in folds)
        latest = max(f.test_end for f in folds)
        price_cache = self.prefetch_data(universe, earliest, latest)

        results = []
        engine = BacktestEngine(
            settings=self.settings,
            price_provider=self.price_provider,
            vix_provider=self.vix_provider,
        )

        for i, fold_config in enumerate(folds):
            logger.info(
                "Fold %d/%d: train %s-%s, test %s-%s",
                i + 1, len(folds),
                fold_config.train_start.strftime("%Y-%m-%d"),
                fold_config.train_end.strftime("%Y-%m-%d"),
                fold_config.test_start.strftime("%Y-%m-%d"),
                fold_config.test_end.strftime("%Y-%m-%d"),
            )

            # Generate signals for entire fold period
            signals, _ = self.generate_historical_signals(
                start=fold_config.train_start,
                end=fold_config.test_end,
                price_cache=price_cache,
                universe=universe,
            )

            if not signals:
                logger.warning("No signals generated for fold %d", i + 1)
                continue

            # Split signals into in-sample and out-of-sample
            train_signals = [
                s for s in signals
                if fold_config.train_start <= s["date"] <= fold_config.train_end
            ]
            test_signals = [
                s for s in signals
                if fold_config.test_start <= s["date"] <= fold_config.test_end
            ]

            # Run backtest on each window
            in_sample = engine.run(
                signals=train_signals,
                start_date=fold_config.train_start,
                end_date=fold_config.train_end,
                starting_capital=starting_capital,
            )

            out_of_sample = engine.run(
                signals=test_signals,
                start_date=fold_config.test_start,
                end_date=fold_config.test_end,
                starting_capital=starting_capital,
            )

            results.append(WalkForwardFoldResult(
                config=fold_config,
                in_sample=in_sample,
                out_of_sample=out_of_sample,
                signals_generated=len(signals),
            ))

        return results

    def compare_parameters(
        self,
        param_sets: List[Dict],
        test_start: datetime,
        test_end: datetime,
        universe: Optional[List[str]] = None,
        starting_capital: float = 10000,
    ) -> Dict:
        """Run the same period with different Settings overrides.

        Args:
            param_sets: List of dicts with parameter overrides
            test_start: Test period start
            test_end: Test period end
            universe: Custom universe
            starting_capital: Starting capital

        Returns:
            Dict with comparison results per parameter set
        """
        if universe is None:
            from ..data.universe import Universe
            universe = Universe().get_tickers()

        price_cache = self.prefetch_data(universe, test_start, test_end)

        engine = BacktestEngine(
            price_provider=self.price_provider,
            vix_provider=self.vix_provider,
        )

        comparisons = {}
        for i, params in enumerate(param_sets):
            label = params.pop("label", f"set_{i}")

            # Create settings with overrides
            override_settings = Settings(**params)
            signals, features = self.generate_historical_signals(
                start=test_start,
                end=test_end,
                price_cache=price_cache,
                universe=universe,
            )

            result = engine.run(
                signals=signals,
                start_date=test_start,
                end_date=test_end,
                starting_capital=starting_capital,
            )

            comparisons[label] = {
                "total_return": result.total_return_pct,
                "trades": len(result.trades),
                "sharpe": result.metrics.get("sharpe_ratio", 0),
                "max_drawdown": result.metrics.get("max_drawdown_pct", 0),
                "win_rate": result.metrics.get("win_rate", 0),
                "profit_factor": result.metrics.get("profit_factor", 0),
            }

        return comparisons

    def segment_by_regime(
        self,
        result: BacktestResult,
        vix_data: Optional[pd.DataFrame] = None,
    ) -> List[RegimeMetrics]:
        """Break backtest metrics by VIX regime bucket.

        Args:
            result: BacktestResult to segment
            vix_data: VIX historical data

        Returns:
            List of RegimeMetrics per regime
        """
        if vix_data is None:
            vix_data = self.vix_provider.fetch(
                start=result.start_date - timedelta(days=30),
                end=result.end_date,
            )

        regime_trades: Dict[str, List] = {}

        for trade in result.trades:
            if trade.entry_date is None:
                continue

            # Find VIX on entry date
            vix = self._get_vix_for_date(vix_data, trade.entry_date)
            if vix is None:
                regime_name = "unknown"
            elif vix < 20:
                regime_name = "low_vol"
            elif vix < 30:
                regime_name = "normal"
            elif vix < 40:
                regime_name = "elevated"
            else:
                regime_name = "crisis"

            if regime_name not in regime_trades:
                regime_trades[regime_name] = []
            regime_trades[regime_name].append(trade)

        metrics_list = []
        for name, trades in regime_trades.items():
            pnl_pcts = [t.pnl_pct for t in trades if t.pnl_pct is not None]
            if not pnl_pcts:
                continue

            returns_series = pd.Series(pnl_pcts)
            winners = [p for p in pnl_pcts if p > 0]

            from .metrics import calculate_sharpe_ratio
            sharpe = calculate_sharpe_ratio(returns_series) if len(returns_series) > 1 else 0.0

            metrics_list.append(RegimeMetrics(
                regime_name=name,
                trade_count=len(trades),
                win_rate=len(winners) / len(pnl_pcts) if pnl_pcts else 0,
                avg_return=sum(pnl_pcts) / len(pnl_pcts),
                sharpe=sharpe,
            ))

        return sorted(metrics_list, key=lambda m: m.trade_count, reverse=True)

    def _generate_folds(self) -> List[WalkForwardConfig]:
        """Auto-generate walk-forward fold configurations."""
        n_folds = self.settings.walkforward_folds
        train_months = self.settings.walkforward_train_months
        test_months = self.settings.walkforward_test_months
        total_months = train_months + test_months

        folds = []
        # Work backwards from roughly "now"
        end = datetime.now() - timedelta(days=30)  # Buffer

        for i in range(n_folds):
            test_end = end - timedelta(days=i * test_months * 30)
            test_start = test_end - timedelta(days=test_months * 30)
            train_end = test_start - timedelta(days=1)
            train_start = train_end - timedelta(days=train_months * 30)

            folds.append(WalkForwardConfig(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            ))

        return list(reversed(folds))

    @staticmethod
    def _get_vix_for_date(vix_data: pd.DataFrame, date: datetime) -> Optional[float]:
        """Get VIX level for a date."""
        if vix_data.empty:
            return None
        try:
            idx = vix_data.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            mask = idx <= date
            if not mask.any():
                return None
            closest = idx[mask][-1]
            return vix_data.loc[closest, "Close"]
        except (KeyError, IndexError):
            return None
