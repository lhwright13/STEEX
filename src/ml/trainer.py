"""PySR walk-forward training pipeline."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import Settings, get_settings

from .models import (
    DiscoveredEquation,
    PySRModel,
    TrainingDataset,
    WalkForwardFold,
    WalkForwardResult,
)

logger = logging.getLogger(__name__)


class PySRTrainer:
    """Walk-forward training with PySR symbolic regression."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def _run_pysr(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        feature_names: List[str],
    ) -> List[DiscoveredEquation]:
        """Run PySR on training data and extract Pareto front.

        Args:
            X_train: Feature matrix
            y_train: Target values (forward returns)
            feature_names: Column names for features

        Returns:
            List of DiscoveredEquation from the Pareto front
        """
        try:
            from pysr import PySRRegressor
        except ImportError:
            raise ImportError(
                "pysr is required for training. Install with: pip install pysr"
            )

        model = PySRRegressor(
            niterations=self.settings.pysr_niterations,
            binary_operators=["+", "-", "*", "/"],
            unary_operators=["abs", "square", "sqrt", "log"],
            populations=self.settings.pysr_populations,
            maxsize=self.settings.pysr_max_complexity,
            parsimony=self.settings.pysr_parsimony,
            timeout_in_seconds=self.settings.pysr_timeout,
            temp_equation_file=True,
            verbosity=1,
        )

        X_np = X_train.values
        y_np = y_train.values

        logger.info(
            f"Running PySR: {X_np.shape[0]} samples, {X_np.shape[1]} features"
        )
        model.fit(X_np, y_np, variable_names=feature_names)

        equations = []
        if hasattr(model, "equations_") and model.equations_ is not None:
            eq_df = model.equations_
            for _, row in eq_df.iterrows():
                eq = DiscoveredEquation(
                    expression=str(row.get("equation", "")),
                    complexity=int(row.get("complexity", 0)),
                    loss=float(row.get("loss", 999)),
                    r_squared=1.0 - float(row.get("loss", 1.0)),
                    feature_names=feature_names,
                    latex=str(row.get("equation", "")),
                )
                equations.append(eq)

        if not equations:
            logger.warning("PySR returned no equations")

        return equations

    def _evaluate_equations(
        self,
        equations: List[DiscoveredEquation],
        X_val: pd.DataFrame,
        y_val: pd.Series,
        feature_names: List[str],
    ) -> List[float]:
        """Evaluate equations on validation set, return R-squared scores.

        Args:
            equations: List of equations to evaluate
            X_val: Validation features
            y_val: Validation targets
            feature_names: Feature names

        Returns:
            List of validation R-squared values (one per equation)
        """
        from .equations import equation_to_python

        y_mean = y_val.mean()
        ss_tot = ((y_val - y_mean) ** 2).sum()

        val_scores = []
        for eq in equations:
            try:
                predict_fn = equation_to_python(eq.expression, feature_names)
                predictions = []
                for _, row in X_val.iterrows():
                    feat_dict = {name: row[name] for name in feature_names}
                    predictions.append(predict_fn(feat_dict))

                preds = np.array(predictions)
                ss_res = ((y_val.values - preds) ** 2).sum()

                if ss_tot > 0:
                    r2 = 1.0 - ss_res / ss_tot
                else:
                    r2 = 0.0

                val_scores.append(r2)
            except Exception:
                val_scores.append(-999.0)

        return val_scores

    def _select_best(
        self,
        equations: List[DiscoveredEquation],
        val_scores: List[float],
    ) -> int:
        """Select best equation: highest val R-squared under complexity cap.

        Args:
            equations: Pareto front equations
            val_scores: Validation R-squared per equation

        Returns:
            Index of selected equation
        """
        max_complexity = self.settings.pysr_max_selected_complexity
        best_idx = 0
        best_r2 = -999.0

        for i, (eq, r2) in enumerate(zip(equations, val_scores)):
            if eq.complexity <= max_complexity and r2 > best_r2:
                best_r2 = r2
                best_idx = i

        return best_idx

    def train_walk_forward(
        self,
        dataset: TrainingDataset,
        horizon: str = "21d",
        n_folds: Optional[int] = None,
        train_months: Optional[int] = None,
        val_months: Optional[int] = None,
    ) -> WalkForwardResult:
        """Run walk-forward validation with PySR.

        For each fold: split by time (train_end < val_start), run PySR on
        training data, evaluate Pareto equations on validation, pick best.

        Args:
            dataset: Training dataset with features and returns
            horizon: Return horizon to train on (e.g., "21d")
            n_folds: Number of folds (default from settings)
            train_months: Training window months (default from settings)
            val_months: Validation window months (default from settings)

        Returns:
            WalkForwardResult with per-fold results and final model
        """
        n_folds = n_folds or self.settings.pysr_walk_forward_folds
        train_months = train_months or self.settings.pysr_train_months
        val_months = val_months or self.settings.pysr_val_months

        if horizon not in dataset.returns:
            raise ValueError(
                f"Horizon '{horizon}' not in dataset. "
                f"Available: {list(dataset.returns.keys())}"
            )

        features = dataset.features
        returns = dataset.returns[horizon]

        # Parse dates from index (format: TICKER_YYYYMMDD)
        dates = []
        for idx in features.index:
            parts = idx.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    dates.append(datetime.strptime(parts[1], "%Y%m%d"))
                except ValueError:
                    dates.append(None)
            else:
                dates.append(None)

        date_series = pd.Series(dates, index=features.index)

        # Drop rows without valid dates or returns
        valid_mask = date_series.notna() & returns.notna()
        features = features[valid_mask]
        returns = returns[valid_mask]
        date_series = date_series[valid_mask]

        if len(features) == 0:
            logger.error("No valid samples after filtering")
            return WalkForwardResult(horizon=horizon)

        # Normalize features
        from .features import FeatureBuilder

        builder = FeatureBuilder()
        features_norm, feature_stats = builder.normalize_features(features)
        feature_names = list(features_norm.columns)

        # Compute fold boundaries
        min_date = date_series.min()
        max_date = date_series.max()
        total_days = (max_date - min_date).days

        fold_step_days = total_days // (n_folds + 1)
        train_days = train_months * 30
        val_days = val_months * 30

        folds = []
        last_fold_equations = []
        last_fold_best_idx = 0

        for fold_i in range(n_folds):
            val_end = max_date - timedelta(days=fold_step_days * (n_folds - fold_i - 1))
            val_start = val_end - timedelta(days=val_days)
            train_end = val_start - timedelta(days=1)
            train_start = train_end - timedelta(days=train_days)

            # Enforce no overlap: val_start > train_end
            if val_start <= train_end:
                val_start = train_end + timedelta(days=1)

            train_mask = (date_series >= train_start) & (date_series <= train_end)
            val_mask = (date_series >= val_start) & (date_series <= val_end)

            X_train = features_norm[train_mask]
            y_train = returns[train_mask]
            X_val = features_norm[val_mask]
            y_val = returns[val_mask]

            if len(X_train) < 10 or len(X_val) < 5:
                logger.warning(
                    f"Fold {fold_i}: insufficient data "
                    f"(train={len(X_train)}, val={len(X_val)}), skipping"
                )
                continue

            logger.info(
                f"Fold {fold_i}: train={len(X_train)} samples "
                f"({train_start.strftime('%Y-%m-%d')} to {train_end.strftime('%Y-%m-%d')}), "
                f"val={len(X_val)} samples "
                f"({val_start.strftime('%Y-%m-%d')} to {val_end.strftime('%Y-%m-%d')})"
            )

            # Train PySR
            equations = self._run_pysr(X_train, y_train, feature_names)

            if not equations:
                logger.warning(f"Fold {fold_i}: no equations discovered")
                continue

            # Evaluate on validation
            val_scores = self._evaluate_equations(equations, X_val, y_val, feature_names)
            best_idx = self._select_best(equations, val_scores)

            # Update validation R-squared on equations
            for i, r2 in enumerate(val_scores):
                equations[i].validation_r_squared = r2

            # Compute train R-squared for the selected equation
            train_scores = self._evaluate_equations(
                [equations[best_idx]], X_train, y_train, feature_names
            )
            train_r2 = train_scores[0] if train_scores else 0.0
            val_r2 = val_scores[best_idx]

            fold = WalkForwardFold(
                fold_index=fold_i,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
                train_size=len(X_train),
                val_size=len(X_val),
                equations=equations,
                selected_index=best_idx,
                train_r_squared=train_r2,
                val_r_squared=val_r2,
            )
            folds.append(fold)

            last_fold_equations = equations
            last_fold_best_idx = best_idx

            logger.info(
                f"Fold {fold_i}: selected equation #{best_idx} "
                f"(complexity={equations[best_idx].complexity}), "
                f"train R2={train_r2:.4f}, val R2={val_r2:.4f}"
            )

        # Build final model from last fold (most recent data)
        val_r2_values = [f.val_r_squared for f in folds]
        mean_val_r2 = np.mean(val_r2_values) if val_r2_values else 0.0
        std_val_r2 = np.std(val_r2_values) if val_r2_values else 0.0

        final_model = None
        if last_fold_equations:
            final_model = PySRModel(
                equations=last_fold_equations,
                selected_index=last_fold_best_idx,
                horizon=horizon,
                training_window=f"{train_months}m",
                feature_names=feature_names,
                feature_stats=feature_stats,
                trained_date=datetime.now().strftime("%Y-%m-%d"),
                train_r_squared=folds[-1].train_r_squared if folds else 0.0,
                val_r_squared=folds[-1].val_r_squared if folds else 0.0,
            )

        return WalkForwardResult(
            folds=folds,
            horizon=horizon,
            mean_val_r_squared=mean_val_r2,
            std_val_r_squared=std_val_r2,
            final_model=final_model,
        )

    def save_model(self, model: PySRModel, path: str) -> None:
        """Save trained model to JSON.

        Args:
            model: PySRModel to save
            path: File path for the JSON output
        """
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "horizon": model.horizon,
            "selected_index": model.selected_index,
            "training_window": model.training_window,
            "feature_names": model.feature_names,
            "feature_stats": model.feature_stats,
            "trained_date": model.trained_date,
            "train_r_squared": model.train_r_squared,
            "val_r_squared": model.val_r_squared,
            "equations": [
                {
                    "expression": eq.expression,
                    "complexity": eq.complexity,
                    "loss": eq.loss,
                    "r_squared": eq.r_squared,
                    "feature_names": eq.feature_names,
                    "latex": eq.latex,
                    "validation_r_squared": eq.validation_r_squared,
                }
                for eq in model.equations
            ],
        }

        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved model to {out_path}")

    def load_model(self, path: str) -> PySRModel:
        """Load trained model from JSON.

        Args:
            path: File path to the JSON model

        Returns:
            PySRModel
        """
        with open(path) as f:
            data = json.load(f)

        equations = []
        for eq_data in data.get("equations", []):
            equations.append(
                DiscoveredEquation(
                    expression=eq_data["expression"],
                    complexity=eq_data["complexity"],
                    loss=eq_data["loss"],
                    r_squared=eq_data["r_squared"],
                    feature_names=eq_data.get("feature_names", []),
                    latex=eq_data.get("latex", ""),
                    validation_r_squared=eq_data.get("validation_r_squared"),
                )
            )

        return PySRModel(
            equations=equations,
            selected_index=data.get("selected_index", 0),
            horizon=data.get("horizon", "21d"),
            training_window=data.get("training_window", ""),
            feature_names=data.get("feature_names", []),
            feature_stats=data.get("feature_stats", {}),
            trained_date=data.get("trained_date", ""),
            train_r_squared=data.get("train_r_squared", 0.0),
            val_r_squared=data.get("val_r_squared", 0.0),
        )
