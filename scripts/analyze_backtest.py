#!/usr/bin/env python3
"""Enhanced backtest analysis and reporting."""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config.settings import get_settings
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import calculate_benchmark_comparison
from src.data.price import PriceProvider


def analyze_monthly_performance(trades, equity_curve):
    """Analyze performance by month."""
    monthly = defaultdict(lambda: {"trades": 0, "winners": 0, "pnl": 0})

    for trade in trades:
        if trade.exit_date and trade.pnl is not None:
            month_key = trade.exit_date.strftime("%Y-%m")
            monthly[month_key]["trades"] += 1
            monthly[month_key]["pnl"] += trade.pnl
            if trade.pnl > 0:
                monthly[month_key]["winners"] += 1

    return dict(sorted(monthly.items()))


def analyze_win_loss_streaks(trades):
    """Analyze winning and losing streaks."""
    streaks = []
    current_streak = 0
    current_type = None

    for trade in sorted(trades, key=lambda t: t.exit_date or datetime.min):
        if trade.pnl is None:
            continue

        is_winner = trade.pnl > 0

        if current_type is None:
            current_type = is_winner
            current_streak = 1
        elif is_winner == current_type:
            current_streak += 1
        else:
            streaks.append(("win" if current_type else "loss", current_streak))
            current_type = is_winner
            current_streak = 1

    if current_streak > 0:
        streaks.append(("win" if current_type else "loss", current_streak))

    max_win_streak = max((s[1] for s in streaks if s[0] == "win"), default=0)
    max_loss_streak = max((s[1] for s in streaks if s[0] == "loss"), default=0)

    return {
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "streaks": streaks,
    }


def analyze_trade_duration(trades):
    """Analyze trade duration distribution."""
    durations = []
    for trade in trades:
        if trade.entry_date and trade.exit_date:
            days = (trade.exit_date - trade.entry_date).days
            durations.append(days)

    if not durations:
        return {}

    return {
        "min_days": min(durations),
        "max_days": max(durations),
        "avg_days": sum(durations) / len(durations),
        "median_days": sorted(durations)[len(durations) // 2],
        "distribution": {
            "0-7 days": len([d for d in durations if d <= 7]),
            "8-14 days": len([d for d in durations if 8 <= d <= 14]),
            "15-30 days": len([d for d in durations if 15 <= d <= 30]),
            "31-60 days": len([d for d in durations if 31 <= d <= 60]),
            "60+ days": len([d for d in durations if d > 60]),
        },
    }


def analyze_exit_reasons(trades):
    """Analyze exit reasons and their performance."""
    by_reason = defaultdict(lambda: {"count": 0, "pnl": 0, "winners": 0, "pnl_pcts": []})

    for trade in trades:
        reason = trade.exit_reason or "unknown"
        by_reason[reason]["count"] += 1
        by_reason[reason]["pnl"] += trade.pnl or 0
        if trade.pnl and trade.pnl > 0:
            by_reason[reason]["winners"] += 1
        if trade.pnl_pct is not None:
            by_reason[reason]["pnl_pcts"].append(trade.pnl_pct)

    # Calculate averages
    for reason, data in by_reason.items():
        data["win_rate"] = data["winners"] / data["count"] if data["count"] > 0 else 0
        data["avg_pnl_pct"] = (
            sum(data["pnl_pcts"]) / len(data["pnl_pcts"]) if data["pnl_pcts"] else 0
        )

    return dict(by_reason)


def get_best_worst_trades(trades, n=5):
    """Get best and worst trades by P&L percentage."""
    sorted_trades = sorted(
        [t for t in trades if t.pnl_pct is not None],
        key=lambda t: t.pnl_pct,
        reverse=True,
    )

    return {
        "best": sorted_trades[:n],
        "worst": sorted_trades[-n:][::-1],
    }


def analyze_drawdowns(equity_curve):
    """Analyze all drawdowns in the equity curve."""
    if equity_curve.empty or "equity" not in equity_curve.columns:
        return []

    equity = equity_curve["equity"]
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max

    # Find drawdown periods
    in_drawdown = False
    drawdowns = []
    current_dd = {}

    for date, dd in drawdown.items():
        if dd < 0 and not in_drawdown:
            # Start of drawdown
            in_drawdown = True
            current_dd = {
                "start": date,
                "peak_value": running_max[date],
            }
        elif dd < 0 and in_drawdown:
            # Continue drawdown
            pass
        elif dd >= 0 and in_drawdown:
            # End of drawdown
            in_drawdown = False
            current_dd["end"] = date
            current_dd["trough_value"] = equity[date]
            current_dd["max_dd"] = abs(drawdown[current_dd["start"]:date].min())
            drawdowns.append(current_dd)
            current_dd = {}

    # Sort by magnitude
    drawdowns.sort(key=lambda x: x.get("max_dd", 0), reverse=True)
    return drawdowns[:5]  # Top 5 drawdowns


def fetch_spy_data(start_date, end_date):
    """Fetch SPY benchmark data."""
    provider = PriceProvider()
    # Add buffer to ensure we get data for the full period
    buffer_start = start_date - pd.Timedelta(days=7)
    buffer_end = end_date + pd.Timedelta(days=7)
    df = provider.get_ohlcv("SPY", start=buffer_start, end=buffer_end)
    if df.empty:
        return pd.Series()
    # Ensure timezone-naive index for comparison
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df["Close"]


def print_analysis_report(result, console):
    """Print comprehensive analysis report."""

    # Monthly Performance
    monthly = analyze_monthly_performance(result.trades, result.equity_curve)
    if monthly:
        table = Table(title="Monthly Performance", box=box.ROUNDED)
        table.add_column("Month", style="cyan")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("P&L", justify="right")

        for month, data in monthly.items():
            win_rate = f"{data['winners']/data['trades']*100:.0f}%" if data["trades"] > 0 else "N/A"
            pnl_style = "green" if data["pnl"] > 0 else "red"
            table.add_row(
                month,
                str(data["trades"]),
                win_rate,
                f"[{pnl_style}]${data['pnl']:+,.2f}[/{pnl_style}]",
            )

        console.print(table)
        console.print()

    # Win/Loss Streaks
    streaks = analyze_win_loss_streaks(result.trades)
    console.print(Panel(
        f"Max Win Streak: [green]{streaks['max_win_streak']}[/green]\n"
        f"Max Loss Streak: [red]{streaks['max_loss_streak']}[/red]",
        title="Win/Loss Streaks",
    ))
    console.print()

    # Trade Duration
    duration = analyze_trade_duration(result.trades)
    if duration:
        table = Table(title="Trade Duration Distribution", box=box.ROUNDED)
        table.add_column("Duration", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Percentage", justify="right")

        total = sum(duration["distribution"].values())
        for period, count in duration["distribution"].items():
            pct = count / total * 100 if total > 0 else 0
            table.add_row(period, str(count), f"{pct:.1f}%")

        console.print(table)
        console.print(f"Average: {duration['avg_days']:.1f} days | "
                     f"Median: {duration['median_days']} days | "
                     f"Range: {duration['min_days']}-{duration['max_days']} days")
        console.print()

    # Exit Reason Analysis
    exit_analysis = analyze_exit_reasons(result.trades)
    if exit_analysis:
        table = Table(title="Exit Reason Analysis", box=box.ROUNDED)
        table.add_column("Reason", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("Avg Return", justify="right")
        table.add_column("Total P&L", justify="right")

        for reason, data in sorted(exit_analysis.items(), key=lambda x: x[1]["pnl"], reverse=True):
            pnl_style = "green" if data["pnl"] > 0 else "red"
            avg_style = "green" if data["avg_pnl_pct"] > 0 else "red"
            table.add_row(
                reason,
                str(data["count"]),
                f"{data['win_rate']*100:.0f}%",
                f"[{avg_style}]{data['avg_pnl_pct']*100:+.1f}%[/{avg_style}]",
                f"[{pnl_style}]${data['pnl']:+,.2f}[/{pnl_style}]",
            )

        console.print(table)
        console.print()

    # Best/Worst Trades
    best_worst = get_best_worst_trades(result.trades)

    table = Table(title="Top 5 Best Trades", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Entry", style="dim")
    table.add_column("Exit", style="dim")
    table.add_column("Return", justify="right", style="green")
    table.add_column("P&L", justify="right", style="green")

    for trade in best_worst["best"]:
        table.add_row(
            trade.ticker,
            trade.entry_date.strftime("%Y-%m-%d"),
            trade.exit_date.strftime("%Y-%m-%d") if trade.exit_date else "-",
            f"{trade.pnl_pct*100:+.1f}%",
            f"${trade.pnl:+,.2f}",
        )

    console.print(table)
    console.print()

    table = Table(title="Top 5 Worst Trades", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Entry", style="dim")
    table.add_column("Exit", style="dim")
    table.add_column("Return", justify="right", style="red")
    table.add_column("P&L", justify="right", style="red")
    table.add_column("Exit Reason", style="dim")

    for trade in best_worst["worst"]:
        table.add_row(
            trade.ticker,
            trade.entry_date.strftime("%Y-%m-%d"),
            trade.exit_date.strftime("%Y-%m-%d") if trade.exit_date else "-",
            f"{trade.pnl_pct*100:+.1f}%",
            f"${trade.pnl:+,.2f}",
            trade.exit_reason or "-",
        )

    console.print(table)
    console.print()

    # Benchmark Comparison
    if not result.equity_curve.empty:
        console.print("[bold]Fetching SPY benchmark data...[/bold]")
        spy_prices = fetch_spy_data(result.start_date, result.end_date)

        if not spy_prices.empty:
            # Ensure equity curve index is timezone-naive
            equity_series = result.equity_curve["equity"].copy()
            if hasattr(equity_series.index, 'tz') and equity_series.index.tz is not None:
                equity_series.index = equity_series.index.tz_localize(None)

            # Calculate SPY return directly
            spy_start_price = spy_prices.iloc[0]
            spy_end_price = spy_prices.iloc[-1]
            spy_return = (spy_end_price - spy_start_price) / spy_start_price

            comparison = calculate_benchmark_comparison(
                equity_series,
                spy_prices,
                result.starting_capital,
            )
            # Override with direct calculation if benchmark_comparison fails
            if comparison["benchmark_return"] == 0:
                comparison["benchmark_return"] = spy_return

            alpha = result.total_return_pct - (comparison["benchmark_return"] * 100)

            console.print(Panel(
                f"Strategy Return: [{'green' if result.total_return > 0 else 'red'}]"
                f"{result.total_return_pct:+.1f}%[/]\n"
                f"SPY Return:      [{'green' if comparison['benchmark_return'] > 0 else 'red'}]"
                f"{comparison['benchmark_return']*100:+.1f}%[/]\n"
                f"Alpha:           [{'green' if alpha > 0 else 'red'}]{alpha:+.1f}%[/]\n"
                f"Beta:            {comparison['beta']:.2f}\n"
                f"Correlation:     {comparison['correlation']:.2f}",
                title="Benchmark Comparison (vs SPY)",
            ))
            console.print()

    # Recommendations
    recommendations = []

    # Analyze exit reasons for recommendations
    if "stop_loss" in exit_analysis and exit_analysis["stop_loss"]["count"] > 3:
        recommendations.append(
            "- High stop-loss exits. Consider widening stops or improving entry timing."
        )

    if "below_ma" in exit_analysis and exit_analysis["below_ma"]["win_rate"] < 0.3:
        recommendations.append(
            "- 'Below MA' exits have low win rate. Consider removing or adjusting this exit."
        )

    if "dead_money" in exit_analysis and exit_analysis["dead_money"]["count"] > 5:
        recommendations.append(
            "- Many 'dead money' exits. Consider tightening entry criteria for momentum."
        )

    if streaks["max_loss_streak"] >= 4:
        recommendations.append(
            f"- Max loss streak of {streaks['max_loss_streak']}. Consider position sizing reduction after losses."
        )

    if duration and duration["avg_days"] < 10:
        recommendations.append(
            "- Short average hold time. Winners may be cut too early."
        )

    if recommendations:
        console.print(Panel(
            "\n".join(recommendations),
            title="Recommendations",
            style="yellow",
        ))


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Analyze backtest results")
    parser.add_argument(
        "--start",
        type=str,
        default="2025-08-25",
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2026-01-19",
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--signals",
        type=str,
        default="data/mis_backtest_signals_v2.json",
        help="Path to signals file",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10000,
        help="Starting capital",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for JSON results",
    )

    args = parser.parse_args()
    console = Console()

    # Parse dates
    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")

    console.print(f"\n[bold]Enhanced Backtest Analysis[/bold]")
    console.print(f"Period: {args.start} to {args.end}")
    console.print(f"Signals: {args.signals}")
    console.print("-" * 50)

    # Load signals
    signals_path = Path(args.signals)
    if not signals_path.exists():
        console.print(f"[red]Signals file not found: {args.signals}[/red]")
        sys.exit(1)

    with open(signals_path) as f:
        signals = json.load(f)

    console.print(f"Loaded {len(signals)} signals\n")

    # Clear settings cache
    get_settings.cache_clear()

    # Run backtest
    console.print("[bold]Running backtest...[/bold]\n")
    engine = BacktestEngine()
    result = engine.run(
        signals=signals,
        start_date=start_date,
        end_date=end_date,
        starting_capital=args.capital,
    )

    # Print summary metrics
    console.print(Panel(
        f"Total Return: [{'green' if result.total_return > 0 else 'red'}]"
        f"{result.total_return_pct:+.1f}%[/]\n"
        f"Total Trades: {len(result.trades)}\n"
        f"Win Rate: {result.metrics.get('win_rate', 0)*100:.1f}%\n"
        f"Profit Factor: {result.metrics.get('profit_factor', 0):.2f}\n"
        f"Sharpe Ratio: {result.metrics.get('sharpe_ratio', 0):.2f}\n"
        f"Max Drawdown: {result.metrics.get('max_drawdown_pct', 0)*100:.1f}%",
        title="Summary Metrics",
    ))
    console.print()

    # Print detailed analysis
    print_analysis_report(result, console)

    # Save results if output specified
    if args.output:
        output_data = {
            "summary": {
                "total_return_pct": result.total_return_pct,
                "total_trades": len(result.trades),
                "win_rate": result.metrics.get("win_rate", 0),
                "profit_factor": result.metrics.get("profit_factor", 0),
                "sharpe_ratio": result.metrics.get("sharpe_ratio", 0),
                "max_drawdown_pct": result.metrics.get("max_drawdown_pct", 0),
            },
            "monthly": analyze_monthly_performance(result.trades, result.equity_curve),
            "exit_reasons": {
                k: {key: v for key, v in val.items() if key != "pnl_pcts"}
                for k, val in analyze_exit_reasons(result.trades).items()
            },
            "streaks": analyze_win_loss_streaks(result.trades),
            "duration": analyze_trade_duration(result.trades),
        }

        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)

        console.print(f"\n[green]Analysis saved to {args.output}[/green]")


if __name__ == "__main__":
    main()
