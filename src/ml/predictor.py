"""PySR prediction pipeline - loads trained models, produces predictions."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from config.settings import Settings, get_settings

from .equations import equation_to_python
from .features import FeatureBuilder
from .models import DiscoveredEquation, PredictionResult, PySRModel
from .trainer import PySRTrainer

logger = logging.getLogger(__name__)


class PySRPredictor:
    """Loads trained PySR models and produces predictions."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        feature_builder: Optional[FeatureBuilder] = None,
    ):
        self.settings = settings or get_settings()
        self.feature_builder = feature_builder or FeatureBuilder()
        self._models: Dict[str, PySRModel] = {}
        self._predict_fns: Dict[str, callable] = {}
        self._loaded = False

    def load_models(self) -> bool:
        """Load most recent model per horizon from model_dir.

        Returns:
            True if at least one model was loaded
        """
        model_dir = Path(self.settings.pysr_model_dir)
        if not model_dir.exists():
            logger.debug(f"Model directory does not exist: {model_dir}")
            return False

        trainer = PySRTrainer(self.settings)
        model_files = sorted(model_dir.glob("pysr_*.json"), reverse=True)

        # Group by horizon, keep most recent
        loaded_horizons: Dict[str, Path] = {}
        for f in model_files:
            # Filename format: pysr_{horizon}_{date}.json
            parts = f.stem.split("_")
            if len(parts) >= 3:
                horizon = parts[1]
                if horizon not in loaded_horizons:
                    loaded_horizons[horizon] = f

        for horizon, path in loaded_horizons.items():
            try:
                model = trainer.load_model(str(path))
                self._models[horizon] = model

                # Pre-compile prediction function
                eq = model.selected_equation
                if eq:
                    self._predict_fns[horizon] = equation_to_python(
                        eq.expression, model.feature_names
                    )
                    logger.info(
                        f"Loaded {horizon} model from {path.name} "
                        f"(R2={model.val_r_squared:.4f})"
                    )
            except Exception as e:
                logger.warning(f"Failed to load model {path}: {e}")

        self._loaded = True
        return len(self._models) > 0

    def is_available(self) -> bool:
        """Check if at least one model is loaded."""
        if not self._loaded:
            self.load_models()
        return len(self._models) > 0

    def predict_ticker(
        self,
        ticker: str,
        date: Optional[datetime] = None,
    ) -> PredictionResult:
        """Produce prediction for a single ticker.

        Args:
            ticker: Stock ticker symbol
            date: Reference date (defaults to now)

        Returns:
            PredictionResult with predicted returns and score
        """
        if not self.is_available():
            return PredictionResult(ticker=ticker)

        # Build feature vector
        raw_features = self.feature_builder.build_feature_vector(ticker, date)

        result = PredictionResult(
            ticker=ticker,
            prediction_date=date or datetime.now(),
        )

        # Predict for each loaded horizon
        for horizon, model in self._models.items():
            predict_fn = self._predict_fns.get(horizon)
            if predict_fn is None:
                continue

            # Normalize features using stored stats
            normalized = {}
            for name in model.feature_names:
                raw_val = raw_features.get(name, 0.0)
                if np.isnan(raw_val):
                    raw_val = 0.0

                stats = model.feature_stats.get(name, {"mean": 0.0, "std": 1.0})
                mean = stats["mean"]
                std = stats["std"]
                if std == 0:
                    std = 1.0

                normalized[name] = (raw_val - mean) / std

            # Evaluate equation
            try:
                predicted = predict_fn(normalized)
            except Exception:
                predicted = 0.0

            if horizon in ("1d", "5d"):
                result.predicted_return_5d = predicted
            elif horizon == "21d":
                result.predicted_return_21d = predicted
            elif horizon == "63d":
                result.predicted_return_63d = predicted

            # Use equation from the primary horizon for display
            eq = model.selected_equation
            if eq and not result.equation_used:
                result.equation_used = eq.expression

        # Compute confidence based on distance from training distribution
        result.confidence = self._compute_confidence(raw_features)

        return result

    def _compute_confidence(self, features: Dict[str, float]) -> float:
        """Estimate confidence based on proximity to training distribution.

        Uses a simplified approach: count how many features are within 2
        standard deviations of training mean. Higher = more confident.

        Args:
            features: Raw feature dict

        Returns:
            Confidence score 0-1
        """
        # Use 21d model stats if available, else first available
        model = self._models.get("21d")
        if model is None and self._models:
            model = next(iter(self._models.values()))
        if model is None:
            return 0.0

        in_range = 0
        total = 0

        for name in model.feature_names:
            stats = model.feature_stats.get(name)
            if stats is None:
                continue

            val = features.get(name, np.nan)
            if np.isnan(val):
                continue

            total += 1
            z = abs((val - stats["mean"]) / max(stats["std"], 1e-10))
            if z <= 2.0:
                in_range += 1

        return in_range / max(total, 1)

    def predict_batch(
        self,
        tickers: List[str],
        date: Optional[datetime] = None,
    ) -> Dict[str, PredictionResult]:
        """Predict for multiple tickers.

        Args:
            tickers: List of ticker symbols
            date: Reference date

        Returns:
            Dict mapping ticker to PredictionResult
        """
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = self.predict_ticker(ticker, date)
            except Exception:
                logger.debug(f"Prediction failed for {ticker}")
                results[ticker] = PredictionResult(ticker=ticker)
        return results

    def compute_pysr_score(
        self,
        predictions: Dict[str, PredictionResult],
    ) -> Dict[str, float]:
        """Convert raw predictions to cross-sectional percentile scores (0-100).

        Uses 21d predicted return as the primary signal. Falls back to
        5d or 63d if 21d is not available.

        Args:
            predictions: Dict of ticker -> PredictionResult

        Returns:
            Dict mapping ticker to percentile score (0-100)
        """
        # Extract the primary predicted return for each ticker
        values = {}
        for ticker, pred in predictions.items():
            val = pred.predicted_return_21d
            if val is None:
                val = pred.predicted_return_5d
            if val is None:
                val = pred.predicted_return_63d
            if val is not None:
                values[ticker] = val

        if not values:
            return {t: 50.0 for t in predictions}

        # Percentile rank
        sorted_items = sorted(values.items(), key=lambda x: x[1])
        n = len(sorted_items)
        scores = {}
        for rank, (ticker, _) in enumerate(sorted_items):
            scores[ticker] = ((rank + 1) / n) * 100

        # Fill missing with neutral
        for ticker in predictions:
            if ticker not in scores:
                scores[ticker] = 50.0

        return scores

    def get_active_equations(self) -> Dict[str, DiscoveredEquation]:
        """Get the selected equation for each loaded horizon.

        Returns:
            Dict mapping horizon to DiscoveredEquation
        """
        result = {}
        for horizon, model in self._models.items():
            eq = model.selected_equation
            if eq:
                result[horizon] = eq
        return result
