"""Build training dataset for PySR symbolic regression.

Usage:
    python scripts/build_pysr_dataset.py --start 2024-01-01 --end 2025-12-31
    python scripts/build_pysr_dataset.py --start 2025-06-01 --end 2025-12-31 --freq 10
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from src.data.price import PriceProvider
from src.data.universe import Universe
from src.ml.dataset import DatasetBuilder
from src.ml.features import FeatureBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Build PySR training dataset")
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--freq",
        type=int,
        default=None,
        help="Sampling frequency in days (default: from settings)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: data/ml/datasets/dataset_YYYYMMDD)",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated tickers (default: S&P 500)",
    )
    args = parser.parse_args()

    settings = get_settings()
    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")
    freq = args.freq or settings.pysr_sample_frequency_days

    # Get tickers
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        universe = Universe()
        tickers = universe.get_sp500()

    logger.info(f"Building dataset: {len(tickers)} tickers, {start_date} to {end_date}")
    logger.info(f"Sample frequency: every {freq} days")

    # Build dataset
    price_provider = PriceProvider()
    feature_builder = FeatureBuilder(price_provider=price_provider)
    dataset_builder = DatasetBuilder(
        feature_builder=feature_builder,
        price_provider=price_provider,
    )

    dataset = dataset_builder.build_dataset(
        tickers=tickers,
        start_date=start_date,
        end_date=end_date,
        sample_freq_days=freq,
    )

    # Save dataset
    if args.output:
        output_dir = args.output
    else:
        date_str = datetime.now().strftime("%Y%m%d")
        output_dir = str(Path(settings.pysr_dataset_dir) / f"dataset_{date_str}")

    dataset_builder.save_dataset(dataset, output_dir)

    # Also create a "latest" symlink
    latest_path = Path(settings.pysr_dataset_dir) / "latest"
    if latest_path.is_symlink():
        latest_path.unlink()
    elif latest_path.exists():
        import shutil
        shutil.rmtree(latest_path)

    try:
        latest_path.symlink_to(Path(output_dir).resolve())
        logger.info(f"Created 'latest' symlink -> {output_dir}")
    except OSError:
        logger.warning("Could not create 'latest' symlink")

    logger.info(
        f"Dataset saved: {len(dataset.features)} samples, "
        f"{len(dataset.feature_names)} features"
    )


if __name__ == "__main__":
    main()
