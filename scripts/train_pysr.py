"""Train PySR symbolic regression models with walk-forward validation.

Usage:
    python scripts/train_pysr.py --dataset data/ml/datasets/latest --horizons 21d
    python scripts/train_pysr.py --dataset data/ml/datasets/latest --horizons 5d,21d,63d
    python scripts/train_pysr.py --dataset data/ml/datasets/latest --horizons 21d --timeout 300
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings, get_settings
from src.ml.dataset import DatasetBuilder
from src.ml.trainer import PySRTrainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Train PySR models")
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="Path to dataset directory",
    )
    parser.add_argument(
        "--horizons",
        type=str,
        default="21d",
        help="Comma-separated horizons to train (default: 21d)",
    )
    parser.add_argument(
        "--folds",
        type=int,
        default=None,
        help="Number of walk-forward folds (default: from settings)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="PySR timeout in seconds (default: from settings)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory for models (default: from settings)",
    )
    args = parser.parse_args()

    settings = get_settings()

    # Apply timeout override if specified
    if args.timeout is not None:
        settings = Settings(pysr_timeout=args.timeout)

    horizons = [h.strip() for h in args.horizons.split(",")]
    output_dir = args.output or settings.pysr_model_dir

    # Load dataset
    logger.info(f"Loading dataset from {args.dataset}")
    dataset_builder = DatasetBuilder()
    dataset = dataset_builder.load_dataset(args.dataset)

    logger.info(
        f"Dataset: {len(dataset.features)} samples, "
        f"{len(dataset.feature_names)} features, "
        f"horizons: {list(dataset.returns.keys())}"
    )

    # Train for each horizon
    trainer = PySRTrainer(settings)
    date_str = datetime.now().strftime("%Y%m%d")

    for horizon in horizons:
        if horizon not in dataset.returns:
            logger.error(
                f"Horizon '{horizon}' not in dataset. "
                f"Available: {list(dataset.returns.keys())}"
            )
            continue

        logger.info(f"Training for horizon: {horizon}")
        result = trainer.train_walk_forward(
            dataset=dataset,
            horizon=horizon,
            n_folds=args.folds,
        )

        logger.info(
            f"Walk-forward results for {horizon}: "
            f"mean val R2={result.mean_val_r_squared:.4f} "
            f"(+/- {result.std_val_r_squared:.4f}), "
            f"{len(result.folds)} folds"
        )

        if result.final_model:
            model_path = str(
                Path(output_dir) / f"pysr_{horizon}_{date_str}.json"
            )
            trainer.save_model(result.final_model, model_path)

            eq = result.final_model.selected_equation
            if eq:
                logger.info(
                    f"Selected equation (complexity={eq.complexity}): {eq.expression}"
                )
        else:
            logger.warning(f"No model produced for horizon {horizon}")

    logger.info("Training complete")


if __name__ == "__main__":
    main()
