"""Dataset builder for PySR training data."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..data.price import PriceProvider
from .features import FeatureBuilder
from .models import TrainingDataset

logger = logging.getLogger(__name__)


class DatasetBuilder:
    """Walks through historical dates collecting features and forward returns."""

    def __init__(
        self,
        feature_builder: Optional[FeatureBuilder] = None,
        price_provider: Optional[PriceProvider] = None,
    ):
        self.feature_builder = feature_builder or FeatureBuilder()
        self.price_provider = price_provider or self.feature_builder.price_provider

    def _compute_forward_returns(
        self,
        ticker: str,
        date: datetime,
        horizons: Dict[str, int],
    ) -> Dict[str, Optional[float]]:
        """Compute forward returns for a ticker from a given date.

        Args:
            ticker: Stock ticker
            date: Starting date
            horizons: Dict of horizon_name -> trading_days

        Returns:
            Dict of horizon_name -> forward return (or None if unavailable)
        """
        max_horizon = max(horizons.values())
        calendar_days = int(max_horizon * 2) + 30

        try:
            df = self.price_provider.get_ohlcv(
                ticker,
                start=date - timedelta(days=5),
                end=date + timedelta(days=calendar_days),
            )
        except Exception:
            return {h: None for h in horizons}

        if df.empty:
            return {h: None for h in horizons}

        # Find the price on or closest to the reference date
        date_ts = pd.Timestamp(date)
        if date_ts.tzinfo is None and df.index.tzinfo is not None:
            date_ts = date_ts.tz_localize(df.index.tzinfo)
        elif date_ts.tzinfo is not None and df.index.tzinfo is None:
            date_ts = date_ts.tz_localize(None)

        mask = df.index <= date_ts
        if not mask.any():
            return {h: None for h in horizons}

        base_idx = df.index[mask][-1]
        base_loc = df.index.get_loc(base_idx)
        base_price = df["Close"].iloc[base_loc]

        if base_price <= 0:
            return {h: None for h in horizons}

        returns = {}
        for name, days in horizons.items():
            future_loc = base_loc + days
            if future_loc < len(df):
                future_price = df["Close"].iloc[future_loc]
                returns[name] = (future_price / base_price) - 1.0
            else:
                returns[name] = None

        return returns

    def build_dataset(
        self,
        tickers: List[str],
        start_date: datetime,
        end_date: datetime,
        sample_freq_days: int = 5,
        horizons: Optional[Dict[str, int]] = None,
    ) -> TrainingDataset:
        """Build training dataset from historical data.

        For each date at sample_freq_days intervals: compute features for all
        tickers, compute forward returns. Last horizon days have NaN returns
        (no future data) which are dropped from training.

        Args:
            tickers: List of tickers to include
            start_date: Start of data collection period
            end_date: End of data collection period
            sample_freq_days: Days between samples
            horizons: Forward return horizons (default: 5d, 21d, 63d)

        Returns:
            TrainingDataset with features and forward returns
        """
        if horizons is None:
            horizons = {"5d": 5, "21d": 21, "63d": 63}

        all_features = []
        all_returns: Dict[str, list] = {h: [] for h in horizons}
        all_indices = []

        current = start_date
        sample_count = 0

        while current <= end_date:
            logger.info(f"Building features for {current.strftime('%Y-%m-%d')}")

            for ticker in tickers:
                try:
                    features = self.feature_builder.build_feature_vector(ticker, current)
                    fwd_returns = self._compute_forward_returns(ticker, current, horizons)

                    all_features.append(features)
                    for h in horizons:
                        all_returns[h].append(fwd_returns.get(h))
                    all_indices.append(f"{ticker}_{current.strftime('%Y%m%d')}")

                    sample_count += 1
                except Exception:
                    logger.debug(f"Skipping {ticker} on {current.strftime('%Y-%m-%d')}")
                    continue

            current += timedelta(days=sample_freq_days)

        if not all_features:
            return TrainingDataset(
                features=pd.DataFrame(),
                returns={},
                feature_names=self.feature_builder.get_feature_names(),
                start_date=start_date,
                end_date=end_date,
                tickers=tickers,
            )

        feature_df = pd.DataFrame(all_features, index=all_indices)
        feature_names = self.feature_builder.get_feature_names()
        existing = [c for c in feature_names if c in feature_df.columns]
        feature_df = feature_df[existing]

        returns_series = {}
        for h in horizons:
            returns_series[h] = pd.Series(all_returns[h], index=all_indices, dtype=float)

        logger.info(
            f"Built dataset: {len(feature_df)} samples, "
            f"{len(existing)} features, "
            f"{len(tickers)} tickers"
        )

        return TrainingDataset(
            features=feature_df,
            returns=returns_series,
            feature_names=existing,
            start_date=start_date,
            end_date=end_date,
            tickers=tickers,
            metadata={
                "sample_freq_days": sample_freq_days,
                "horizons": horizons,
                "total_samples": len(feature_df),
            },
        )

    def save_dataset(self, dataset: TrainingDataset, path: str) -> None:
        """Save dataset to disk as parquet files.

        Storage layout:
            path/features.parquet
            path/returns_{horizon}.parquet
            path/metadata.json

        Args:
            dataset: TrainingDataset to save
            path: Directory path
        """
        out_dir = Path(path)
        out_dir.mkdir(parents=True, exist_ok=True)

        dataset.features.to_parquet(out_dir / "features.parquet")

        for horizon, series in dataset.returns.items():
            series.to_frame(name=horizon).to_parquet(
                out_dir / f"returns_{horizon}.parquet"
            )

        metadata = {
            "feature_names": dataset.feature_names,
            "start_date": dataset.start_date.isoformat() if dataset.start_date else None,
            "end_date": dataset.end_date.isoformat() if dataset.end_date else None,
            "tickers": dataset.tickers,
            "horizons": list(dataset.returns.keys()),
            **dataset.metadata,
        }
        with open(out_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Saved dataset to {out_dir}")

    def load_dataset(self, path: str) -> TrainingDataset:
        """Load dataset from disk.

        Args:
            path: Directory path containing parquet files

        Returns:
            TrainingDataset
        """
        in_dir = Path(path)

        features = pd.read_parquet(in_dir / "features.parquet")

        with open(in_dir / "metadata.json") as f:
            metadata = json.load(f)

        returns = {}
        for horizon in metadata.get("horizons", []):
            returns_file = in_dir / f"returns_{horizon}.parquet"
            if returns_file.exists():
                df = pd.read_parquet(returns_file)
                returns[horizon] = df.iloc[:, 0]

        start_date = None
        end_date = None
        if metadata.get("start_date"):
            start_date = datetime.fromisoformat(metadata["start_date"])
        if metadata.get("end_date"):
            end_date = datetime.fromisoformat(metadata["end_date"])

        return TrainingDataset(
            features=features,
            returns=returns,
            feature_names=metadata.get("feature_names", list(features.columns)),
            start_date=start_date,
            end_date=end_date,
            tickers=metadata.get("tickers", []),
            metadata=metadata,
        )
