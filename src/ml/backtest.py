"""Historical backtesting for PySR symbolic regression equations.

Computes predicted vs actual returns over a historical window by:
1. Downloading OHLCV price data and VIX history
2. Computing price-derivable features (momentum, technical) as rolling series
3. Holding fundamental/sentiment/options features constant (current snapshot)
4. Normalizing with the model's stored feature_stats
5. Evaluating the discovered equation at each date
6. Comparing against actual realized forward returns
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from .equations import equation_to_python
from .models import PySRModel

logger = logging.getLogger(__name__)


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI from a closing price series."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Compute ATR as percentage of close."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean() / close


def compute_historical_features(
    ohlcv: pd.DataFrame,
    vix_df: Optional[pd.DataFrame] = None,
    fundamental_snapshot: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Compute feature time series from daily OHLCV data.

    Price-derivable features are computed as rolling calculations.
    Fundamental/sentiment/options features use a static snapshot (held constant).
    VIX features are computed from ^VIX history if provided.

    Args:
        ohlcv: DataFrame with Open, High, Low, Close, Volume columns
        vix_df: Optional DataFrame with Close column for ^VIX
        fundamental_snapshot: Optional dict of static fundamental features

    Returns:
        DataFrame indexed by date with one column per feature
    """
    close = ohlcv["Close"]
    high = ohlcv["High"]
    low = ohlcv["Low"]
    volume = ohlcv["Volume"]

    features = pd.DataFrame(index=ohlcv.index)

    # -- Momentum --
    features["momentum_6m"] = close / close.shift(126) - 1
    features["momentum_1m"] = close / close.shift(21) - 1
    # Single ticker: percentile is meaningless, use 0.5
    features["momentum_percentile"] = 0.5

    # -- Technical --
    features["above_ma_50"] = (close > close.rolling(50).mean()).astype(float)
    features["above_ma_200"] = (close > close.rolling(200).mean()).astype(float)
    features["rsi_14"] = _compute_rsi(close, 14)
    features["atr_pct"] = _compute_atr_pct(high, low, close, 14)
    features["volume_surge"] = volume / volume.rolling(20).mean()

    # -- Fundamental (static snapshot) --
    fund = fundamental_snapshot or {}
    features["pe_ratio"] = fund.get("pe_ratio", 0.0)
    features["peg_ratio"] = fund.get("peg_ratio", 0.0)
    features["roe"] = fund.get("roe", 0.0)
    features["debt_to_equity"] = fund.get("debt_to_equity", 0.0)
    features["revenue_growth"] = fund.get("revenue_growth", 0.0)
    features["fundamental_score"] = fund.get("fundamental_score", 50.0)

    # -- Sentiment (neutral default) --
    features["sentiment_score"] = 50.0

    # -- VIX --
    if vix_df is not None and not vix_df.empty:
        # Align VIX to the same index
        vix_close = vix_df["Close"].reindex(ohlcv.index, method="ffill")
        features["vix_current"] = vix_close
        features["vix_percentile"] = vix_close.rolling(252, min_periods=20).rank(pct=True)
        features["momentum_x_vix"] = features["momentum_6m"] * features["vix_current"]
    else:
        features["vix_current"] = 20.0
        features["vix_percentile"] = 0.5
        features["momentum_x_vix"] = features["momentum_6m"] * 20.0

    # -- Options (neutral default) --
    features["put_call_oi_ratio"] = 0.85
    features["iv_skew"] = 0.0
    features["options_score"] = 50.0

    # -- Derived --
    features["rsi_deviation"] = features["rsi_14"] - 50.0

    return features


def fetch_fundamental_snapshot(ticker: str) -> Dict[str, float]:
    """Fetch current fundamental data for a ticker using yfinance.

    Returns a dict of feature name -> value for the static fundamental features.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        return {
            "pe_ratio": info.get("trailingPE") or info.get("forwardPE") or 0.0,
            "peg_ratio": info.get("pegRatio") or 0.0,
            "roe": info.get("returnOnEquity") or 0.0,
            "debt_to_equity": info.get("debtToEquity") or 0.0,
            "revenue_growth": info.get("revenueGrowth") or 0.0,
            "fundamental_score": 50.0,  # Default neutral
        }
    except Exception:
        return {}


def run_backtest(
    model: PySRModel,
    ticker: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    horizon_days: int = 1,
    equation_index: Optional[int] = None,
) -> pd.DataFrame:
    """Run a historical backtest of a PySR equation on a single ticker.

    Downloads price history, computes features at each date, applies the
    equation, and compares against actual forward returns.

    Args:
        model: Trained PySRModel with equation and feature_stats
        ticker: Stock ticker symbol
        start_date: Backtest start (default: 1 year ago)
        end_date: Backtest end (default: today)
        horizon_days: Forward return horizon in trading days (default: 1)
        equation_index: Which equation to use (default: model.selected_index)

    Returns:
        DataFrame with columns:
            - predicted_return: equation output (raw, not normalized)
            - actual_return: realized forward return
            - close: closing price
    """
    if end_date is None:
        end_date = datetime.now()
    if start_date is None:
        start_date = end_date - timedelta(days=365)

    eq_idx = equation_index if equation_index is not None else model.selected_index
    eq = model.equations[eq_idx]
    predict_fn = equation_to_python(eq.expression, model.feature_names)

    # Need extra history before start_date for rolling indicators (200-day MA needs 200 prior days)
    fetch_start = start_date - timedelta(days=300)

    # Download price data
    logger.info(f"Fetching {ticker} price data from {fetch_start.date()} to {end_date.date()}")
    stock = yf.Ticker(ticker)
    ohlcv = stock.history(start=fetch_start, end=end_date, auto_adjust=False)
    if ohlcv.empty:
        logger.error(f"No price data for {ticker}")
        return pd.DataFrame()

    # Download VIX
    try:
        vix_ticker = yf.Ticker("^VIX")
        vix_df = vix_ticker.history(start=fetch_start, end=end_date, auto_adjust=False)
    except Exception:
        vix_df = None

    # Fetch fundamentals snapshot
    fund_snapshot = fetch_fundamental_snapshot(ticker)

    # Compute features
    all_features = compute_historical_features(ohlcv, vix_df, fund_snapshot)

    # Compute actual forward returns
    close = ohlcv["Close"]
    actual_returns = close.shift(-horizon_days) / close - 1

    # Trim to backtest window
    if hasattr(ohlcv.index, 'tz') and ohlcv.index.tz is not None:
        start_ts = pd.Timestamp(start_date, tz=ohlcv.index.tz)
        end_ts = pd.Timestamp(end_date, tz=ohlcv.index.tz)
    else:
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)

    mask = (all_features.index >= start_ts) & (all_features.index <= end_ts)
    all_features = all_features[mask]
    actual_returns = actual_returns[mask]
    close = close[mask]

    # Drop rows with NaN features (early dates missing rolling lookback)
    valid_mask = all_features.notna().all(axis=1)
    all_features = all_features[valid_mask]
    actual_returns = actual_returns[valid_mask]
    close = close[valid_mask]

    if all_features.empty:
        logger.error("No valid feature rows after filtering")
        return pd.DataFrame()

    # Normalize features using stored stats and evaluate equation
    predicted_returns = []
    for date_idx in all_features.index:
        row = all_features.loc[date_idx]
        normalized = {}
        for name in model.feature_names:
            raw_val = row.get(name, 0.0)
            if pd.isna(raw_val):
                raw_val = 0.0
            stats = model.feature_stats.get(name, {"mean": 0.0, "std": 1.0})
            mean = stats["mean"]
            std = stats["std"]
            if std == 0 or np.isnan(std):
                std = 1.0
            normalized[name] = (raw_val - mean) / std

        try:
            pred = predict_fn(normalized)
        except Exception:
            pred = 0.0

        predicted_returns.append(pred)

    result = pd.DataFrame({
        "predicted_return": predicted_returns,
        "actual_return": actual_returns.values,
        "close": close.values,
    }, index=all_features.index)

    # Drop rows where actual return is NaN (last horizon_days rows)
    result = result.dropna(subset=["actual_return"])

    logger.info(
        f"Backtest complete: {len(result)} data points, "
        f"correlation={result['predicted_return'].corr(result['actual_return']):.4f}"
    )

    return result


def compute_strategy_returns(
    backtest_df: pd.DataFrame,
    long_threshold: float = 0.0,
    short_enabled: bool = False,
) -> pd.DataFrame:
    """Compute cumulative returns for a model-following strategy vs buy-and-hold.

    Strategy: go long when predicted_return > long_threshold, flat/short otherwise.

    Args:
        backtest_df: Output from run_backtest()
        long_threshold: Minimum predicted return to go long
        short_enabled: If True, go short when predicted < -long_threshold

    Returns:
        DataFrame with columns:
            - strategy_return: daily return of the strategy
            - buyhold_return: daily return of buy-and-hold
            - strategy_cumulative: cumulative strategy return
            - buyhold_cumulative: cumulative buy-and-hold return
    """
    df = backtest_df.copy()

    # Signal: 1 = long, 0 = flat, -1 = short
    signal = pd.Series(0.0, index=df.index)
    signal[df["predicted_return"] > long_threshold] = 1.0
    if short_enabled:
        signal[df["predicted_return"] < -long_threshold] = -1.0

    df["signal"] = signal
    df["strategy_return"] = signal * df["actual_return"]
    df["buyhold_return"] = df["actual_return"]

    df["strategy_cumulative"] = (1 + df["strategy_return"]).cumprod() - 1
    df["buyhold_cumulative"] = (1 + df["buyhold_return"]).cumprod() - 1

    return df
