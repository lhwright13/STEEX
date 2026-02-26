"""Feature engineering for PySR symbolic regression."""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data.fundamentals import FundamentalsProvider
from ..data.options import OptionsProvider
from ..data.price import PriceProvider
from ..data.sentiment import SentimentProvider
from ..data.vix import VixProvider
from ..indicators.momentum import MomentumCalculator
from ..indicators.technical import TechnicalIndicators

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    # Momentum
    "momentum_6m",
    "momentum_1m",
    "momentum_percentile",
    # Technical
    "above_ma_50",
    "above_ma_200",
    "rsi_14",
    "atr_pct",
    "volume_surge",
    # Fundamental
    "pe_ratio",
    "peg_ratio",
    "roe",
    "debt_to_equity",
    "revenue_growth",
    "fundamental_score",
    # Sentiment
    "sentiment_score",
    "vix_current",
    "vix_percentile",
    # Options
    "put_call_oi_ratio",
    "iv_skew",
    "options_score",
    # Derived interactions
    "momentum_x_vix",
    "rsi_deviation",
]


class FeatureBuilder:
    """Builds feature matrices from existing data providers."""

    def __init__(
        self,
        price_provider: Optional[PriceProvider] = None,
        fundamentals_provider: Optional[FundamentalsProvider] = None,
        sentiment_provider: Optional[SentimentProvider] = None,
        options_provider: Optional[OptionsProvider] = None,
        vix_provider: Optional[VixProvider] = None,
        momentum_calc: Optional[MomentumCalculator] = None,
        technical: Optional[TechnicalIndicators] = None,
    ):
        self._price = price_provider
        self._fundamentals = fundamentals_provider
        self._sentiment = sentiment_provider
        self._options = options_provider
        self._vix = vix_provider
        self._momentum = momentum_calc
        self._technical = technical

    @property
    def price_provider(self) -> PriceProvider:
        if self._price is None:
            self._price = PriceProvider()
        return self._price

    @property
    def fundamentals_provider(self) -> FundamentalsProvider:
        if self._fundamentals is None:
            self._fundamentals = FundamentalsProvider()
        return self._fundamentals

    @property
    def sentiment_provider(self) -> SentimentProvider:
        if self._sentiment is None:
            self._sentiment = SentimentProvider()
        return self._sentiment

    @property
    def options_provider(self) -> OptionsProvider:
        if self._options is None:
            self._options = OptionsProvider()
        return self._options

    @property
    def vix_provider(self) -> VixProvider:
        if self._vix is None:
            self._vix = VixProvider()
        return self._vix

    @property
    def momentum_calc(self) -> MomentumCalculator:
        if self._momentum is None:
            self._momentum = MomentumCalculator(self.price_provider)
        return self._momentum

    @property
    def technical(self) -> TechnicalIndicators:
        if self._technical is None:
            self._technical = TechnicalIndicators(self.price_provider)
        return self._technical

    def get_feature_names(self) -> List[str]:
        """Return canonical ordered list of feature names."""
        return list(FEATURE_NAMES)

    def build_feature_vector(
        self,
        ticker: str,
        date: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Compute feature vector for a single ticker.

        Args:
            ticker: Stock ticker symbol
            date: Reference date (defaults to now)

        Returns:
            Dict mapping feature name to value. Missing features are NaN.
        """
        features: Dict[str, float] = {}

        # Momentum features
        try:
            mom_6m = self.momentum_calc.get_momentum(ticker, 126)
            features["momentum_6m"] = mom_6m if mom_6m is not None else np.nan
        except Exception:
            features["momentum_6m"] = np.nan

        try:
            mom_1m = self.momentum_calc.get_momentum(ticker, 21)
            features["momentum_1m"] = mom_1m if mom_1m is not None else np.nan
        except Exception:
            features["momentum_1m"] = np.nan

        # Compute momentum percentile against the full universe
        try:
            from ..data.universe import Universe
            universe = Universe()
            universe_tickers = universe.get_sp500()
            if ticker not in universe_tickers:
                universe_tickers = universe_tickers + [ticker]
            percentiles = self.momentum_calc.get_momentum_percentiles(
                universe_tickers, lookback_days=126
            )
            if ticker in percentiles:
                features["momentum_percentile"] = percentiles[ticker]["percentile"]
            else:
                features["momentum_percentile"] = np.nan
        except Exception:
            features["momentum_percentile"] = np.nan

        # Technical features
        try:
            alignment = self.technical.check_trend_alignment(ticker)
            features["above_ma_50"] = 1.0 if alignment["above_short_ma"] else 0.0
            features["above_ma_200"] = 1.0 if alignment["above_long_ma"] else 0.0
        except Exception:
            features["above_ma_50"] = np.nan
            features["above_ma_200"] = np.nan

        try:
            rsi = self.technical.get_rsi(ticker)
            features["rsi_14"] = rsi if rsi is not None else np.nan
        except Exception:
            features["rsi_14"] = np.nan

        try:
            atr = self.technical.get_atr_percent(ticker)
            features["atr_pct"] = atr if atr is not None else np.nan
        except Exception:
            features["atr_pct"] = np.nan

        try:
            vol = self.technical.get_volume_surge(ticker)
            features["volume_surge"] = vol if vol is not None else np.nan
        except Exception:
            features["volume_surge"] = np.nan

        # Fundamental features
        try:
            fund = self.fundamentals_provider.get_fundamentals(ticker)
            features["pe_ratio"] = fund.pe_ratio if fund.pe_ratio is not None else np.nan
            features["peg_ratio"] = fund.peg_ratio if fund.peg_ratio is not None else np.nan
            features["roe"] = fund.return_on_equity if fund.return_on_equity is not None else np.nan
            features["debt_to_equity"] = fund.debt_to_equity if fund.debt_to_equity is not None else np.nan
            features["revenue_growth"] = fund.revenue_growth if fund.revenue_growth is not None else np.nan
            features["fundamental_score"] = fund.fundamental_score
        except Exception:
            for f in ["pe_ratio", "peg_ratio", "roe", "debt_to_equity", "revenue_growth"]:
                features[f] = np.nan
            features["fundamental_score"] = 50.0

        # Sentiment features
        try:
            sent = self.sentiment_provider.get_sentiment(ticker)
            features["sentiment_score"] = sent.normalized_score
        except Exception:
            features["sentiment_score"] = 50.0

        # VIX features
        try:
            vix = self.vix_provider.get_current()
            features["vix_current"] = vix if vix is not None else np.nan
        except Exception:
            features["vix_current"] = np.nan

        try:
            vix_pct = self.vix_provider.get_percentile()
            features["vix_percentile"] = vix_pct if vix_pct is not None else np.nan
        except Exception:
            features["vix_percentile"] = np.nan

        # Options features
        try:
            opts = self.options_provider.get_options_sentiment(ticker)
            features["put_call_oi_ratio"] = opts.put_call_oi_ratio if opts.put_call_oi_ratio is not None else np.nan
            features["iv_skew"] = opts.iv_skew if opts.iv_skew is not None else np.nan
            features["options_score"] = opts.options_score
        except Exception:
            features["put_call_oi_ratio"] = np.nan
            features["iv_skew"] = np.nan
            features["options_score"] = 50.0

        # Derived interaction features
        mom_6m_val = features.get("momentum_6m", np.nan)
        vix_val = features.get("vix_current", np.nan)
        rsi_val = features.get("rsi_14", np.nan)

        if not np.isnan(mom_6m_val) and not np.isnan(vix_val):
            features["momentum_x_vix"] = mom_6m_val * vix_val
        else:
            features["momentum_x_vix"] = np.nan

        if not np.isnan(rsi_val):
            features["rsi_deviation"] = rsi_val - 50.0
        else:
            features["rsi_deviation"] = np.nan

        return features

    def build_feature_matrix(
        self,
        tickers: List[str],
        date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Build feature matrix for multiple tickers.

        Args:
            tickers: List of ticker symbols
            date: Reference date

        Returns:
            DataFrame with tickers as index, features as columns
        """
        rows = []
        for ticker in tickers:
            try:
                features = self.build_feature_vector(ticker, date)
                features["ticker"] = ticker
                rows.append(features)
            except Exception:
                logger.warning(f"Failed to build features for {ticker}")
                continue

        if not rows:
            return pd.DataFrame(columns=["ticker"] + self.get_feature_names())

        df = pd.DataFrame(rows)
        df = df.set_index("ticker")

        # Fill momentum percentile using cross-sectional rank
        if "momentum_6m" in df.columns:
            valid = df["momentum_6m"].dropna()
            if len(valid) > 1:
                ranks = valid.rank(pct=True)
                df.loc[ranks.index, "momentum_percentile"] = ranks

        # Reorder columns to canonical order
        existing = [c for c in self.get_feature_names() if c in df.columns]
        return df[existing]

    def normalize_features(
        self,
        df: pd.DataFrame,
        stats: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Tuple[pd.DataFrame, Dict[str, Dict[str, float]]]:
        """Z-score normalize features.

        In training mode (stats=None), computes mean/std from data.
        In prediction mode (stats provided), applies stored statistics.
        NaN values are filled with cross-sectional median (no lookahead).

        Args:
            df: Feature DataFrame
            stats: Pre-computed {feature: {mean, std}} for prediction mode

        Returns:
            Tuple of (normalized DataFrame, stats dict)
        """
        result = df.copy()
        computed_stats: Dict[str, Dict[str, float]] = {}

        for col in result.columns:
            # Fill NaN with column median
            median_val = result[col].median()
            if pd.isna(median_val):
                median_val = 0.0
            result[col] = result[col].fillna(median_val)

            if stats is not None:
                # Prediction mode: use stored stats
                col_stats = stats.get(col, {"mean": 0.0, "std": 1.0})
                mean = col_stats["mean"]
                std = col_stats["std"]
            else:
                # Training mode: compute stats
                mean = result[col].mean()
                std = result[col].std()

            if std == 0 or np.isnan(std):
                std = 1.0

            computed_stats[col] = {"mean": mean, "std": std}
            result[col] = (result[col] - mean) / std

        return result, computed_stats
