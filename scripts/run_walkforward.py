#!/usr/bin/env python3
"""Run walk-forward backtest on the STEEX strategy.

Usage:
    python scripts/run_walkforward.py --start 2024-01-01 --end 2025-01-01 --folds 4
    python scripts/run_walkforward.py --start 2024-01-01 --end 2025-01-01 --interval 14
    python scripts/run_walkforward.py --start 2024-01-01 --end 2025-01-01 --output results.json
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich import box

from config.settings import get_settings
from src.backtest.walkforward import WalkForwardBacktester, WalkForwardConfig
from src.backtest.metrics import format_metrics_report


def main():
    parser = argparse.ArgumentParser(description="STEEX Walk-Forward Backtest")
    parser.add_argument(
        "--start", type=str, required=True, help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", type=str, required=True, help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--folds", type=int, default=None,
        help="Number of walk-forward folds (overrides settings)",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Days between signal generations (overrides settings)",
    )
    parser.add_argument(
        "--capital", type=float, default=10000,
        help="Starting capital per fold (default: 10000)",
    )
    parser.add_argument(
        "--output", type=str, help="Output file for results JSON",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging",
    )
    parser.add_argument(
        "--train-months", type=int, default=None,
        help="Training window in months",
    )
    parser.add_argument(
        "--test-months", type=int, default=None,
        help="Test window in months",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    console = Console()
    settings = get_settings()

    # Apply overrides
    if args.folds is not None:
        settings.walkforward_folds = args.folds
    if args.interval is not None:
        settings.walkforward_signal_interval = args.interval
    if args.train_months is not None:
        settings.walkforward_train_months = args.train_months
    if args.test_months is not None:
        settings.walkforward_test_months = args.test_months

    # Parse dates
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as e:
        console.print(f"[red]Invalid date format: {e}[/red]")
        sys.exit(1)

    if start_date >= end_date:
        console.print("[red]Start date must be before end date[/red]")
        sys.exit(1)

    console.print(f"\n[bold]STEEX Walk-Forward Backtest[/bold]")
    console.print(f"Period: {args.start} to {args.end}")
    console.print(f"Train: {settings.walkforward_train_months}mo | Test: {settings.walkforward_test_months}mo")
    console.print(f"Folds: {settings.walkforward_folds} | Signal interval: {settings.walkforward_signal_interval}d")
    console.print(f"Starting Capital: ${args.capital:,.0f}")
    console.print("-" * 60)

    # Build fold configs from the provided date range
    train_days = settings.walkforward_train_months * 30
    test_days = settings.walkforward_test_months * 30
    total_days = (end_date - start_date).days

    if total_days < train_days + test_days:
        console.print("[red]Date range too short for train+test window[/red]")
        sys.exit(1)

    folds = []
    fold_start = start_date
    for i in range(settings.walkforward_folds):
        train_end = fold_start + __import__("datetime").timedelta(days=train_days)
        test_start = train_end + __import__("datetime").timedelta(days=1)
        test_end = test_start + __import__("datetime").timedelta(days=test_days)

        if test_end > end_date:
            test_end = end_date

        if train_end >= end_date:
            break

        folds.append(WalkForwardConfig(
            train_start=fold_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        ))
        fold_start = test_start  # Rolling window

    if not folds:
        console.print("[red]Could not generate any folds from date range[/red]")
        sys.exit(1)

    console.print(f"\nGenerated {len(folds)} folds:")
    for i, f in enumerate(folds):
        console.print(
            f"  Fold {i+1}: Train {f.train_start:%Y-%m-%d} to {f.train_end:%Y-%m-%d} | "
            f"Test {f.test_start:%Y-%m-%d} to {f.test_end:%Y-%m-%d}"
        )

    # Run walk-forward
    backtester = WalkForwardBacktester(settings=settings)

    console.print("\n[bold]Running walk-forward backtest...[/bold]")
    with console.status("Generating signals and running backtests..."):
        results = backtester.run_walk_forward(
            folds=folds,
            starting_capital=args.capital,
        )

    if not results:
        console.print("[yellow]No results produced.[/yellow]")
        sys.exit(0)

    # Display results
    console.print(f"\n[bold]Results ({len(results)} folds)[/bold]")
    console.print("=" * 60)

    table = Table(box=box.SIMPLE)
    table.add_column("Fold", style="bold")
    table.add_column("IS Return", justify="right")
    table.add_column("OOS Return", justify="right")
    table.add_column("IS Sharpe", justify="right")
    table.add_column("OOS Sharpe", justify="right")
    table.add_column("IS Trades", justify="right")
    table.add_column("OOS Trades", justify="right")
    table.add_column("Signals", justify="right")

    for i, fold in enumerate(results):
        is_ret = fold.in_sample.total_return_pct
        oos_ret = fold.out_of_sample.total_return_pct
        is_color = "green" if is_ret > 0 else "red"
        oos_color = "green" if oos_ret > 0 else "red"

        table.add_row(
            f"Fold {i+1}",
            f"[{is_color}]{is_ret:+.1f}%[/{is_color}]",
            f"[{oos_color}]{oos_ret:+.1f}%[/{oos_color}]",
            f"{fold.in_sample.metrics.get('sharpe_ratio', 0):.2f}",
            f"{fold.out_of_sample.metrics.get('sharpe_ratio', 0):.2f}",
            str(len(fold.in_sample.trades)),
            str(len(fold.out_of_sample.trades)),
            str(fold.signals_generated),
        )

    console.print(table)

    # Aggregate OOS results
    total_oos_trades = sum(len(f.out_of_sample.trades) for f in results)
    avg_oos_return = sum(f.out_of_sample.total_return_pct for f in results) / len(results)
    avg_oos_sharpe = sum(
        f.out_of_sample.metrics.get("sharpe_ratio", 0) for f in results
    ) / len(results)

    console.print(f"\n[bold]Aggregate Out-of-Sample[/bold]")
    console.print(f"Avg OOS Return:  {avg_oos_return:+.1f}%")
    console.print(f"Avg OOS Sharpe:  {avg_oos_sharpe:.2f}")
    console.print(f"Total OOS Trades: {total_oos_trades}")

    # Regime segmentation on the last fold
    if results:
        last = results[-1]
        regime_metrics = backtester.segment_by_regime(last.out_of_sample)
        if regime_metrics:
            console.print(f"\n[bold]Regime Breakdown (Last Fold OOS)[/bold]")
            regime_table = Table(box=box.SIMPLE)
            regime_table.add_column("Regime")
            regime_table.add_column("Trades", justify="right")
            regime_table.add_column("Win Rate", justify="right")
            regime_table.add_column("Avg Return", justify="right")
            regime_table.add_column("Sharpe", justify="right")

            for rm in regime_metrics:
                regime_table.add_row(
                    rm.regime_name,
                    str(rm.trade_count),
                    f"{rm.win_rate:.0%}",
                    f"{rm.avg_return:.1%}",
                    f"{rm.sharpe:.2f}",
                )
            console.print(regime_table)

    # Save results
    if args.output:
        output_path = Path(args.output)
        output_data = {
            "start_date": args.start,
            "end_date": args.end,
            "settings": {
                "train_months": settings.walkforward_train_months,
                "test_months": settings.walkforward_test_months,
                "folds": len(results),
                "signal_interval": settings.walkforward_signal_interval,
            },
            "folds": [
                {
                    "train": f"{f.config.train_start:%Y-%m-%d} to {f.config.train_end:%Y-%m-%d}",
                    "test": f"{f.config.test_start:%Y-%m-%d} to {f.config.test_end:%Y-%m-%d}",
                    "in_sample_return": f.in_sample.total_return_pct,
                    "out_of_sample_return": f.out_of_sample.total_return_pct,
                    "in_sample_sharpe": f.in_sample.metrics.get("sharpe_ratio", 0),
                    "out_of_sample_sharpe": f.out_of_sample.metrics.get("sharpe_ratio", 0),
                    "in_sample_trades": len(f.in_sample.trades),
                    "out_of_sample_trades": len(f.out_of_sample.trades),
                    "signals_generated": f.signals_generated,
                }
                for f in results
            ],
            "aggregate": {
                "avg_oos_return": avg_oos_return,
                "avg_oos_sharpe": avg_oos_sharpe,
                "total_oos_trades": total_oos_trades,
            },
        }
        with open(output_path, "w") as fp:
            json.dump(output_data, fp, indent=2, default=str)
        console.print(f"\n[green]Results saved to {output_path}[/green]")

    console.print()


if __name__ == "__main__":
    main()
