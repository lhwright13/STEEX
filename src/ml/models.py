"""Data models for PySR symbolic regression integration."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class FeatureRow:
    """Single ticker/date feature vector."""

    ticker: str
    date: datetime
    features: Dict[str, float] = field(default_factory=dict)


@dataclass
class TrainingDataset:
    """Feature matrix and forward returns for training."""

    features: pd.DataFrame  # rows = ticker/date, cols = feature names
    returns: Dict[str, pd.Series]  # horizon -> forward returns series
    feature_names: List[str] = field(default_factory=list)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tickers: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class DiscoveredEquation:
    """One equation from PySR's Pareto front."""

    expression: str
    complexity: int
    loss: float
    r_squared: float
    feature_names: List[str] = field(default_factory=list)
    latex: str = ""
    validation_r_squared: Optional[float] = None


@dataclass
class PySRModel:
    """Trained model artifact."""

    equations: List[DiscoveredEquation] = field(default_factory=list)
    selected_index: int = 0
    horizon: str = "21d"
    training_window: str = ""
    feature_names: List[str] = field(default_factory=list)
    feature_stats: Dict[str, Dict[str, float]] = field(default_factory=dict)
    trained_date: str = ""
    train_r_squared: float = 0.0
    val_r_squared: float = 0.0

    @property
    def selected_equation(self) -> Optional[DiscoveredEquation]:
        if 0 <= self.selected_index < len(self.equations):
            return self.equations[self.selected_index]
        return None


@dataclass
class WalkForwardFold:
    """One fold's train/val split and results."""

    fold_index: int
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    train_size: int = 0
    val_size: int = 0
    equations: List[DiscoveredEquation] = field(default_factory=list)
    selected_index: int = 0
    train_r_squared: float = 0.0
    val_r_squared: float = 0.0


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward output."""

    folds: List[WalkForwardFold] = field(default_factory=list)
    horizon: str = "21d"
    mean_val_r_squared: float = 0.0
    std_val_r_squared: float = 0.0
    final_model: Optional[PySRModel] = None


@dataclass
class PredictionResult:
    """Prediction for one ticker."""

    ticker: str
    predicted_return_5d: Optional[float] = None
    predicted_return_21d: Optional[float] = None
    predicted_return_63d: Optional[float] = None
    pysr_score: float = 50.0  # 0-100 cross-sectional percentile
    confidence: float = 0.0  # Mahalanobis distance from training distribution
    equation_used: str = ""
    prediction_date: Optional[datetime] = None
