"""Quick PySR demo: build dataset, train, and verify.

Builds a small dataset from ~30 large-cap S&P 500 stocks over a 1-week
window with 1-day forward returns, trains PySR with a short timeout,
and saves the model.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from config.settings import Settings
from src.data.price import PriceProvider
from src.ml.dataset import DatasetBuilder
from src.ml.features import FeatureBuilder
from src.ml.trainer import PySRTrainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Representative large-cap tickers (reliable data from yfinance)
DEMO_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "JNJ", "UNH", "XOM", "PG", "MA", "HD", "CVX", "MRK",
    "ABBV", "PEP", "KO", "COST", "AVGO", "LLY", "WMT", "MCD", "CSCO",
    "ACN", "TMO", "ABT",
]

MODEL_DIR = str(PROJECT_ROOT / "data" / "ml" / "models")
DATASET_DIR = str(PROJECT_ROOT / "data" / "ml" / "datasets" / "demo")


def build_demo_dataset():
    """Build a small dataset: current features + 1d forward returns."""
    logger.info(f"Building demo dataset for {len(DEMO_TICKERS)} tickers")

    price_provider = PriceProvider()
    feature_builder = FeatureBuilder(price_provider=price_provider)

    # Collect current features for all tickers
    logger.info("Collecting features...")
    feature_matrix = feature_builder.build_feature_matrix(DEMO_TICKERS)
    logger.info(f"Got features for {len(feature_matrix)} tickers: {list(feature_matrix.columns)}")

    if feature_matrix.empty:
        logger.error("No features collected")
        return None

    # Get 2 weeks of price history to compute forward returns at multiple points
    logger.info("Collecting price history for forward returns...")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=21)

    all_rows = []
    all_returns_1d = []
    all_indices = []

    # Sample at 5 historical points (roughly every 2-3 trading days)
    sample_offsets = [10, 8, 6, 4, 2]  # trading days ago

    for offset in sample_offsets:
        for ticker in DEMO_TICKERS:
            try:
                df = price_provider.get_ohlcv(ticker, days=30)
                if df.empty or len(df) < offset + 2:
                    continue

                # Forward return: price[t+1] / price[t] - 1
                price_t = df["Close"].iloc[-(offset + 1)]
                price_t1 = df["Close"].iloc[-offset]

                if price_t > 0:
                    fwd_return = (price_t1 / price_t) - 1.0
                else:
                    continue

                # Use current features (approximation for demo)
                if ticker in feature_matrix.index:
                    row = feature_matrix.loc[ticker].to_dict()
                    idx = f"{ticker}_{offset:02d}"
                    all_rows.append(row)
                    all_returns_1d.append(fwd_return)
                    all_indices.append(idx)
            except Exception:
                continue

    if not all_rows:
        logger.error("No samples collected")
        return None

    features_df = pd.DataFrame(all_rows, index=all_indices)
    returns_series = pd.Series(all_returns_1d, index=all_indices, dtype=float)

    # Add some noise to features across samples to avoid degeneracy
    # (since we're reusing current features for multiple time points)
    np.random.seed(42)
    for col in features_df.columns:
        std = features_df[col].std()
        if std > 0:
            noise = np.random.normal(0, std * 0.05, len(features_df))
            features_df[col] = features_df[col] + noise

    logger.info(f"Dataset: {len(features_df)} samples, {len(features_df.columns)} features")
    logger.info(f"Return stats: mean={returns_series.mean():.4f}, std={returns_series.std():.4f}")

    from src.ml.models import TrainingDataset
    dataset = TrainingDataset(
        features=features_df,
        returns={"1d": returns_series},
        feature_names=list(features_df.columns),
        start_date=start_date,
        end_date=end_date,
        tickers=DEMO_TICKERS,
        metadata={"horizons": {"1d": 1}, "demo": True},
    )

    # Save
    builder = DatasetBuilder()
    builder.save_dataset(dataset, DATASET_DIR)
    logger.info(f"Saved dataset to {DATASET_DIR}")

    return dataset


def train_demo_model(dataset):
    """Train PySR with a short timeout on the demo dataset."""
    settings = Settings(
        pysr_enabled=True,
        pysr_model_dir=MODEL_DIR,
        pysr_niterations=20,
        pysr_max_complexity=15,
        pysr_max_selected_complexity=12,
        pysr_populations=10,
        pysr_parsimony=0.005,
        pysr_timeout=120,
        pysr_train_months=1,
        pysr_val_months=1,
        pysr_walk_forward_folds=2,
    )

    trainer = PySRTrainer(settings)

    # Since our demo dataset doesn't have real temporal structure in the index,
    # we'll do a simple train/test split instead of walk-forward
    features = dataset.features
    returns = dataset.returns["1d"]

    # Drop NaN returns
    valid = returns.dropna()
    features = features.loc[valid.index]
    returns = valid

    # Normalize
    fb = FeatureBuilder()
    features_norm, feature_stats = fb.normalize_features(features)
    feature_names = list(features_norm.columns)

    # 80/20 split
    n = len(features_norm)
    split = int(n * 0.8)
    X_train = features_norm.iloc[:split]
    y_train = returns.iloc[:split]
    X_val = features_norm.iloc[split:]
    y_val = returns.iloc[split:]

    logger.info(f"Training PySR: {len(X_train)} train, {len(X_val)} val samples")
    logger.info(f"Features: {feature_names}")
    logger.info("This will take ~2 minutes...")

    # Run PySR
    equations = trainer._run_pysr(X_train, y_train, feature_names)
    logger.info(f"Discovered {len(equations)} equations on Pareto front")

    if not equations:
        logger.error("No equations discovered")
        return None

    # Evaluate on validation
    val_scores = trainer._evaluate_equations(equations, X_val, y_val, feature_names)
    best_idx = trainer._select_best(equations, val_scores)

    for i, (eq, vs) in enumerate(zip(equations, val_scores)):
        marker = " <-- SELECTED" if i == best_idx else ""
        logger.info(
            f"  [{i}] complexity={eq.complexity}, "
            f"train_R2={eq.r_squared:.4f}, val_R2={vs:.4f}: "
            f"{eq.expression}{marker}"
        )

    # Build and save model
    from src.ml.models import PySRModel
    model = PySRModel(
        equations=equations,
        selected_index=best_idx,
        horizon="1d",
        training_window="demo",
        feature_names=feature_names,
        feature_stats=feature_stats,
        trained_date=datetime.now().strftime("%Y-%m-%d"),
        train_r_squared=equations[best_idx].r_squared,
        val_r_squared=val_scores[best_idx],
    )

    model_path = f"{MODEL_DIR}/pysr_1d_{datetime.now().strftime('%Y%m%d')}.json"
    trainer.save_model(model, model_path)
    logger.info(f"Model saved to {model_path}")

    selected = equations[best_idx]
    logger.info(f"Selected equation: {selected.expression}")
    logger.info(f"  Complexity: {selected.complexity}")
    logger.info(f"  Train R2: {selected.r_squared:.4f}")
    logger.info(f"  Val R2: {val_scores[best_idx]:.4f}")

    return model


def main():
    logger.info("=" * 60)
    logger.info("PySR Demo - Building dataset and training")
    logger.info("=" * 60)

    # Step 1: Build dataset
    dataset = build_demo_dataset()
    if dataset is None:
        logger.error("Failed to build dataset")
        return

    # Step 2: Train
    model = train_demo_model(dataset)
    if model is None:
        logger.error("Failed to train model")
        return

    logger.info("")
    logger.info("=" * 60)
    logger.info("Demo complete! Next steps:")
    logger.info("  1. Enable PySR in config/config.yaml:")
    logger.info("     pysr_enabled: true")
    logger.info("     weight_pysr: 0.10")
    logger.info("  2. Start dashboard: flask --app dashboard.app run")
    logger.info("  3. Navigate to Alpha Discovery page")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
