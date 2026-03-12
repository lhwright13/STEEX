"""Signal research and hypothesis testing.

Consumes the feature matrix from WalkForwardBacktester to test whether
each scoring factor actually predicts forward returns. Uses t-tests,
Spearman rank correlation (information coefficient), and cross-validated
ridge regression for weight optimization.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from config.settings import Settings, get_settings
from ..data.price import PriceProvider


SIGNAL_COLUMNS = [
    "momentum_score",
    "insider_score",
    "volume_score",
    "sentiment_score",
    "fundamental_score",
    "options_score",
    "pysr_score",
]


@dataclass
class HypothesisResult:
    """Result of testing a single signal hypothesis."""

    signal_name: str
    sample_size: int
    mean_forward_return: float
    t_statistic: float
    p_value: float
    is_significant: bool
    information_coefficient: float  # Spearman rank correlation of signal vs return
    win_rate_when_strong: float     # win rate when signal > median


@dataclass
class SignalResearchReport:
    """Aggregated signal research results."""

    hypotheses: List[HypothesisResult]
    correlations: Dict[str, float]         # signal pair key -> correlation
    redundant_pairs: List[Tuple[str, str]]
    recommended_weights: Dict[str, float]


class SignalResearcher:
    """Tests whether each scoring factor predicts forward returns.

    Uses the feature matrix (signal values per stock per date) plus
    forward returns to run t-tests, compute information coefficients,
    and optimize scoring weights via ridge regression.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        price_provider: Optional[PriceProvider] = None,
    ):
        self.settings = settings or get_settings()
        self.price_provider = price_provider or PriceProvider()

    def compute_forward_returns(
        self,
        feature_matrix: List[Dict],
        price_cache: Optional[Dict[str, pd.DataFrame]] = None,
        forward_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Compute forward returns for each signal observation.

        Args:
            feature_matrix: List of dicts with date, ticker, and signal scores
            price_cache: Pre-fetched price data
            forward_days: Forward return horizon in trading days

        Returns:
            DataFrame with signal columns plus forward_return column
        """
        fwd_days = forward_days or self.settings.research_forward_return_days

        rows = []
        for obs in feature_matrix:
            ticker = obs["ticker"]
            date = obs["date"]

            if price_cache and ticker in price_cache:
                df = price_cache[ticker]
            else:
                # Fetch enough data to compute forward return
                from datetime import timedelta
                df = self.price_provider.get_ohlcv(
                    ticker,
                    start=date - timedelta(days=10),
                    end=date + timedelta(days=int(fwd_days * 1.5) + 10),
                )

            if df.empty:
                continue

            # Strip timezone to avoid tz-naive/aware comparison issues
            if df.index.tz is not None:
                df = df.copy()
                df.index = df.index.tz_localize(None)

            idx = df.index
            compare_date = pd.Timestamp(date).tz_localize(None) if hasattr(pd.Timestamp(date), 'tz') else pd.Timestamp(date)

            # Find entry price (on signal date)
            entry_mask = idx <= compare_date
            if not entry_mask.any():
                continue
            entry_price = df.loc[idx[entry_mask][-1], "Close"]

            # Find forward price
            future_mask = idx > compare_date
            future_dates = idx[future_mask]
            if len(future_dates) < fwd_days:
                continue

            forward_price = df.loc[future_dates[fwd_days - 1], "Close"]
            forward_return = (forward_price - entry_price) / entry_price

            row = dict(obs)
            row["forward_return"] = forward_return
            rows.append(row)

        return pd.DataFrame(rows)

    def test_hypothesis(
        self,
        signal_values: pd.Series,
        forward_returns: pd.Series,
        signal_name: str,
    ) -> HypothesisResult:
        """Test whether a signal predicts forward returns.

        Uses a one-sample t-test on returns when signal is above median,
        and Spearman rank correlation for information coefficient.

        Args:
            signal_values: Series of signal scores
            forward_returns: Series of forward returns (aligned)
            signal_name: Name of the signal being tested

        Returns:
            HypothesisResult
        """
        # Align and drop NaN
        mask = signal_values.notna() & forward_returns.notna()
        signals = signal_values[mask]
        returns = forward_returns[mask]

        n = len(signals)
        if n < self.settings.research_min_sample_size:
            return HypothesisResult(
                signal_name=signal_name,
                sample_size=n,
                mean_forward_return=0.0,
                t_statistic=0.0,
                p_value=1.0,
                is_significant=False,
                information_coefficient=0.0,
                win_rate_when_strong=0.0,
            )

        # Information coefficient (Spearman rank correlation)
        ic, _ = stats.spearmanr(signals, returns)
        if np.isnan(ic):
            ic = 0.0

        # Split into above/below median
        median_val = signals.median()
        strong_mask = signals > median_val
        strong_returns = returns[strong_mask]

        # T-test: are returns when signal is strong significantly > 0?
        if len(strong_returns) > 2:
            t_stat, p_val = stats.ttest_1samp(strong_returns, 0)
        else:
            t_stat, p_val = 0.0, 1.0

        win_rate = (strong_returns > 0).mean() if len(strong_returns) > 0 else 0.0

        return HypothesisResult(
            signal_name=signal_name,
            sample_size=n,
            mean_forward_return=strong_returns.mean() if len(strong_returns) > 0 else 0.0,
            t_statistic=t_stat,
            p_value=p_val,
            is_significant=p_val < self.settings.research_significance_level,
            information_coefficient=ic,
            win_rate_when_strong=win_rate,
        )

    def test_all_factors(
        self,
        feature_df: pd.DataFrame,
    ) -> List[HypothesisResult]:
        """Test all signal factors against forward returns.

        Args:
            feature_df: DataFrame with signal columns and forward_return

        Returns:
            List of HypothesisResult for each signal
        """
        if "forward_return" not in feature_df.columns:
            return []

        results = []
        for col in SIGNAL_COLUMNS:
            if col not in feature_df.columns:
                continue

            result = self.test_hypothesis(
                feature_df[col],
                feature_df["forward_return"],
                col,
            )
            results.append(result)

        return sorted(results, key=lambda r: abs(r.information_coefficient), reverse=True)

    def find_redundant_signals(
        self,
        feature_df: pd.DataFrame,
    ) -> Tuple[Dict[str, float], List[Tuple[str, str]]]:
        """Find pairs of signals with high correlation (redundancy).

        Args:
            feature_df: DataFrame with signal columns

        Returns:
            (correlations dict, list of redundant pairs)
        """
        threshold = self.settings.research_redundancy_threshold
        available = [c for c in SIGNAL_COLUMNS if c in feature_df.columns]

        if len(available) < 2:
            return {}, []

        corr_matrix = feature_df[available].corr(method="spearman")

        correlations = {}
        redundant = []

        for i, c1 in enumerate(available):
            for c2 in available[i + 1:]:
                corr_val = corr_matrix.loc[c1, c2]
                key = f"{c1}|{c2}"
                correlations[key] = corr_val

                if abs(corr_val) > threshold:
                    redundant.append((c1, c2))

        return correlations, redundant

    def optimize_weights(
        self,
        feature_df: pd.DataFrame,
        n_folds: int = 5,
    ) -> Dict[str, float]:
        """Walk-forward ridge regression for weight optimization.

        Fits a regularized linear model predicting forward returns
        from signal scores, using walk-forward cross-validation.

        Args:
            feature_df: DataFrame with signal columns and forward_return
            n_folds: Number of cross-validation folds

        Returns:
            Dict mapping signal name to recommended weight
        """
        available = [c for c in SIGNAL_COLUMNS if c in feature_df.columns]
        if len(available) < 2 or "forward_return" not in feature_df.columns:
            return {}

        X = feature_df[available].fillna(50.0)
        y = feature_df["forward_return"]

        # Drop rows with NaN target
        mask = y.notna()
        X = X[mask]
        y = y[mask]

        if len(X) < self.settings.research_min_sample_size * 2:
            return {}

        # Standardize
        X_mean = X.mean()
        X_std = X.std().replace(0, 1)
        X_norm = (X - X_mean) / X_std

        # Simple ridge regression (no sklearn dependency)
        alpha = 1.0  # Regularization strength
        fold_size = len(X_norm) // n_folds
        coefs_all = []

        for fold in range(n_folds):
            test_start = fold * fold_size
            test_end = test_start + fold_size

            train_X = pd.concat([X_norm.iloc[:test_start], X_norm.iloc[test_end:]])
            train_y = pd.concat([y.iloc[:test_start], y.iloc[test_end:]])

            if len(train_X) < 10:
                continue

            # Ridge regression: (X'X + alpha*I)^-1 X'y
            XtX = train_X.T @ train_X
            Xty = train_X.T @ train_y
            I = np.eye(len(available))

            try:
                coefs = np.linalg.solve(XtX.values + alpha * I, Xty.values)
                coefs_all.append(coefs)
            except np.linalg.LinAlgError:
                continue

        if not coefs_all:
            return {}

        # Average coefficients across folds
        avg_coefs = np.mean(coefs_all, axis=0)

        # Convert to positive weights (use abs value, then normalize)
        abs_coefs = np.abs(avg_coefs)
        total = abs_coefs.sum()
        if total == 0:
            return {name: 1.0 / len(available) for name in available}

        weights = {name: float(c / total) for name, c in zip(available, abs_coefs)}
        return weights

    def run_full_analysis(
        self,
        feature_matrix: List[Dict],
        price_cache: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> SignalResearchReport:
        """Run complete signal research analysis.

        Args:
            feature_matrix: List of feature observations
            price_cache: Pre-fetched price data

        Returns:
            SignalResearchReport
        """
        # Compute forward returns
        feature_df = self.compute_forward_returns(feature_matrix, price_cache)

        if feature_df.empty:
            return SignalResearchReport(
                hypotheses=[],
                correlations={},
                redundant_pairs=[],
                recommended_weights={},
            )

        # Test all factors
        hypotheses = self.test_all_factors(feature_df)

        # Find redundancy
        correlations, redundant = self.find_redundant_signals(feature_df)

        # Optimize weights
        weights = self.optimize_weights(feature_df)

        return SignalResearchReport(
            hypotheses=hypotheses,
            correlations=correlations,
            redundant_pairs=redundant,
            recommended_weights=weights,
        )
