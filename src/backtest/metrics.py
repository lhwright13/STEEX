"""Performance metrics for backtesting."""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def calculate_sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252,
) -> float:
    """Calculate Sharpe ratio from returns series.

    Args:
        returns: Series of period returns
        risk_free_rate: Annual risk-free rate
        periods_per_year: Trading periods per year

    Returns:
        Annualized Sharpe ratio
    """
    if returns.empty or returns.std() == 0:
        return 0.0

    excess_returns = returns - (risk_free_rate / periods_per_year)
    return np.sqrt(periods_per_year) * excess_returns.mean() / returns.std()


def calculate_sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252,
) -> float:
    """Calculate Sortino ratio (downside deviation only).

    Args:
        returns: Series of period returns
        risk_free_rate: Annual risk-free rate
        periods_per_year: Trading periods per year

    Returns:
        Annualized Sortino ratio
    """
    if returns.empty:
        return 0.0

    excess_returns = returns - (risk_free_rate / periods_per_year)
    downside_returns = returns[returns < 0]

    if downside_returns.empty or downside_returns.std() == 0:
        return float("inf") if excess_returns.mean() > 0 else 0.0

    downside_std = downside_returns.std()
    return np.sqrt(periods_per_year) * excess_returns.mean() / downside_std


def calculate_max_drawdown(equity_curve: pd.Series) -> Dict:
    """Calculate maximum drawdown from equity curve.

    Args:
        equity_curve: Series of portfolio values

    Returns:
        Dict with max_drawdown, peak_date, trough_date, recovery_date
    """
    if equity_curve.empty:
        return {
            "max_drawdown": 0,
            "max_drawdown_pct": 0,
            "peak_date": None,
            "trough_date": None,
            "recovery_date": None,
        }

    # Calculate running maximum
    running_max = equity_curve.cummax()

    # Calculate drawdown
    drawdown = (equity_curve - running_max) / running_max

    # Find max drawdown
    max_dd_idx = drawdown.idxmin()
    max_dd = drawdown.loc[max_dd_idx]

    # Find peak before max drawdown
    peak_idx = running_max.loc[:max_dd_idx].idxmax()
    peak_value = equity_curve.loc[peak_idx]

    # Find trough
    trough_value = equity_curve.loc[max_dd_idx]

    # Find recovery (if any)
    recovery_idx = None
    if max_dd_idx is not None:
        post_trough = equity_curve.loc[max_dd_idx:]
        recovered = post_trough[post_trough >= peak_value]
        if not recovered.empty:
            recovery_idx = recovered.index[0]

    return {
        "max_drawdown": peak_value - trough_value,
        "max_drawdown_pct": abs(max_dd),
        "peak_date": peak_idx,
        "trough_date": max_dd_idx,
        "recovery_date": recovery_idx,
    }


def calculate_cagr(
    starting_value: float,
    ending_value: float,
    years: float,
) -> float:
    """Calculate Compound Annual Growth Rate.

    Args:
        starting_value: Initial portfolio value
        ending_value: Final portfolio value
        years: Number of years

    Returns:
        CAGR as decimal
    """
    if starting_value <= 0 or years <= 0:
        return 0.0

    return (ending_value / starting_value) ** (1 / years) - 1


def calculate_calmar_ratio(
    cagr: float,
    max_drawdown_pct: float,
) -> float:
    """Calculate Calmar ratio (CAGR / Max Drawdown).

    Args:
        cagr: Compound annual growth rate
        max_drawdown_pct: Maximum drawdown as decimal

    Returns:
        Calmar ratio
    """
    if max_drawdown_pct == 0:
        return float("inf") if cagr > 0 else 0.0
    return cagr / max_drawdown_pct


def calculate_metrics(
    trades: List,
    equity_curve: pd.DataFrame,
    risk_free_rate: float = 0.02,
) -> Dict:
    """Calculate comprehensive performance metrics.

    Args:
        trades: List of BacktestTrade objects
        equity_curve: DataFrame with equity values
        risk_free_rate: Annual risk-free rate

    Returns:
        Dict with all performance metrics
    """
    metrics = {
        "total_trades": len(trades),
        "winners": 0,
        "losers": 0,
        "win_rate": 0,
        "avg_winner": 0,
        "avg_loser": 0,
        "profit_factor": 0,
        "total_pnl": 0,
        "avg_pnl": 0,
        "avg_hold_days": 0,
        "sharpe_ratio": 0,
        "sortino_ratio": 0,
        "max_drawdown_pct": 0,
        "cagr": 0,
        "calmar_ratio": 0,
    }

    if not trades:
        return metrics

    # Trade statistics
    pnls = [t.pnl for t in trades if t.pnl is not None]
    pnl_pcts = [t.pnl_pct for t in trades if t.pnl_pct is not None]

    if not pnls:
        return metrics

    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    winner_pcts = [p for p in pnl_pcts if p > 0]
    loser_pcts = [p for p in pnl_pcts if p <= 0]

    metrics["winners"] = len(winners)
    metrics["losers"] = len(losers)
    metrics["win_rate"] = len(winners) / len(pnls) if pnls else 0
    metrics["total_pnl"] = sum(pnls)
    metrics["avg_pnl"] = sum(pnls) / len(pnls) if pnls else 0

    if winner_pcts:
        metrics["avg_winner"] = sum(winner_pcts) / len(winner_pcts)
    if loser_pcts:
        metrics["avg_loser"] = sum(loser_pcts) / len(loser_pcts)

    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    metrics["profit_factor"] = (
        gross_profit / gross_loss if gross_loss > 0 else float("inf")
    )

    # Hold time
    hold_days = []
    for t in trades:
        if t.exit_date and t.entry_date:
            days = (t.exit_date - t.entry_date).days
            hold_days.append(days)
    metrics["avg_hold_days"] = sum(hold_days) / len(hold_days) if hold_days else 0

    # Equity curve metrics
    if not equity_curve.empty and "equity" in equity_curve.columns:
        equity_series = equity_curve["equity"]

        # Calculate daily returns
        returns = equity_series.pct_change().dropna()

        if not returns.empty:
            metrics["sharpe_ratio"] = calculate_sharpe_ratio(returns, risk_free_rate)
            metrics["sortino_ratio"] = calculate_sortino_ratio(returns, risk_free_rate)

        # Max drawdown
        dd_info = calculate_max_drawdown(equity_series)
        metrics["max_drawdown_pct"] = dd_info["max_drawdown_pct"]
        metrics["peak_date"] = dd_info["peak_date"]
        metrics["trough_date"] = dd_info["trough_date"]

        # CAGR
        if len(equity_curve) > 1:
            start_value = equity_series.iloc[0]
            end_value = equity_series.iloc[-1]
            days = (equity_curve.index[-1] - equity_curve.index[0]).days
            years = days / 365.25

            if years > 0:
                metrics["cagr"] = calculate_cagr(start_value, end_value, years)
                metrics["calmar_ratio"] = calculate_calmar_ratio(
                    metrics["cagr"], metrics["max_drawdown_pct"]
                )

    return metrics


def calculate_benchmark_comparison(
    strategy_equity: pd.Series,
    benchmark_prices: pd.Series,
    starting_capital: float,
) -> Dict:
    """Compare strategy performance to benchmark.

    Args:
        strategy_equity: Strategy equity curve
        benchmark_prices: Benchmark price series (e.g., SPY)
        starting_capital: Initial capital

    Returns:
        Dict with comparison metrics
    """
    if strategy_equity.empty or benchmark_prices.empty:
        return {
            "strategy_return": 0,
            "benchmark_return": 0,
            "alpha": 0,
            "beta": 0,
            "correlation": 0,
        }

    # Align dates
    common_dates = strategy_equity.index.intersection(benchmark_prices.index)
    if len(common_dates) < 2:
        return {
            "strategy_return": 0,
            "benchmark_return": 0,
            "alpha": 0,
            "beta": 0,
            "correlation": 0,
        }

    strategy_aligned = strategy_equity.loc[common_dates]
    benchmark_aligned = benchmark_prices.loc[common_dates]

    # Calculate returns
    strategy_returns = strategy_aligned.pct_change().dropna()
    benchmark_returns = benchmark_aligned.pct_change().dropna()

    # Total returns
    strategy_total = (strategy_aligned.iloc[-1] / strategy_aligned.iloc[0]) - 1
    benchmark_shares = starting_capital / benchmark_aligned.iloc[0]
    benchmark_equity = benchmark_shares * benchmark_aligned
    benchmark_total = (benchmark_equity.iloc[-1] / benchmark_equity.iloc[0]) - 1

    # Correlation
    correlation = strategy_returns.corr(benchmark_returns)

    # Beta (covariance / variance of benchmark)
    covariance = strategy_returns.cov(benchmark_returns)
    benchmark_variance = benchmark_returns.var()
    beta = covariance / benchmark_variance if benchmark_variance > 0 else 0

    # Alpha (annualized)
    periods = len(strategy_returns)
    if periods > 0:
        strategy_annual = strategy_total * (252 / periods)
        benchmark_annual = benchmark_total * (252 / periods)
        alpha = strategy_annual - (beta * benchmark_annual)
    else:
        alpha = 0

    return {
        "strategy_return": strategy_total,
        "benchmark_return": benchmark_total,
        "alpha": alpha,
        "beta": beta,
        "correlation": correlation,
    }


def format_metrics_report(metrics: Dict) -> str:
    """Format metrics as a readable report.

    Args:
        metrics: Dict of performance metrics

    Returns:
        Formatted string report
    """
    lines = [
        "=" * 50,
        "BACKTEST PERFORMANCE REPORT",
        "=" * 50,
        "",
        "TRADE STATISTICS",
        "-" * 30,
        f"Total Trades:     {metrics.get('total_trades', 0)}",
        f"Winners:          {metrics.get('winners', 0)}",
        f"Losers:           {metrics.get('losers', 0)}",
        f"Win Rate:         {metrics.get('win_rate', 0) * 100:.1f}%",
        f"Avg Winner:       {metrics.get('avg_winner', 0) * 100:+.1f}%",
        f"Avg Loser:        {metrics.get('avg_loser', 0) * 100:+.1f}%",
        f"Profit Factor:    {metrics.get('profit_factor', 0):.2f}",
        f"Avg Hold Days:    {metrics.get('avg_hold_days', 0):.1f}",
        "",
        "RISK METRICS",
        "-" * 30,
        f"Sharpe Ratio:     {metrics.get('sharpe_ratio', 0):.2f}",
        f"Sortino Ratio:    {metrics.get('sortino_ratio', 0):.2f}",
        f"Max Drawdown:     {metrics.get('max_drawdown_pct', 0) * 100:.1f}%",
        f"CAGR:             {metrics.get('cagr', 0) * 100:.1f}%",
        f"Calmar Ratio:     {metrics.get('calmar_ratio', 0):.2f}",
        "",
        "RETURNS",
        "-" * 30,
        f"Total P&L:        ${metrics.get('total_pnl', 0):,.2f}",
        "",
        "=" * 50,
    ]

    return "\n".join(lines)
