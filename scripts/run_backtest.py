#!/usr/bin/env python3
"""Run backtest on the MIS strategy."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import get_settings
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import format_metrics_report


def main():
    """Run backtest simulation."""
    parser = argparse.ArgumentParser(description="Run MIS strategy backtest")
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
        "--capital",
        type=float,
        default=10000,
        help="Starting capital (default: 10000)",
    )
    parser.add_argument(
        "--signals",
        type=str,
        help="Path to signals JSON file",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output",
    )

    args = parser.parse_args()

    console = Console()
    settings = get_settings()

    # Parse dates
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as e:
        console.print(f"[red]Invalid date format: {e}[/red]")
        console.print("Use YYYY-MM-DD format")
        sys.exit(1)

    if start_date >= end_date:
        console.print("[red]Start date must be before end date[/red]")
        sys.exit(1)

    console.print(f"\n[bold]MIS Strategy Backtest[/bold]")
    console.print(f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    console.print(f"Starting Capital: ${args.capital:,.2f}")
    console.print("-" * 50)

    # Load signals
    if args.signals:
        signals_path = Path(args.signals)
        if not signals_path.exists():
            console.print(f"[red]Signals file not found: {args.signals}[/red]")
            sys.exit(1)

        with open(signals_path) as f:
            signals = json.load(f)

        console.print(f"Loaded {len(signals)} signals from {args.signals}")
    else:
        console.print("[yellow]No signals file provided.[/yellow]")
        console.print("To run a backtest, provide a signals JSON file with entries like:")
        console.print('  [{"date": "2024-01-15", "ticker": "AAPL", "score": 75}, ...]')
        console.print("\nYou can generate signals by running the daily scanner on historical dates.")

        # Generate sample signals file format
        sample = [
            {"date": "2024-01-15", "ticker": "AAPL", "score": 75},
            {"date": "2024-01-16", "ticker": "MSFT", "score": 72},
        ]
        console.print("\nSample signals format:")
        console.print(json.dumps(sample, indent=2))
        sys.exit(0)

    # Run backtest
    console.print("\n[bold]Running backtest...[/bold]")

    engine = BacktestEngine()

    with console.status("Simulating trades..."):
        result = engine.run(
            signals=signals,
            start_date=start_date,
            end_date=end_date,
            starting_capital=args.capital,
        )

    # Display results
    console.print("\n" + format_metrics_report(result.metrics))

    # Summary
    console.print(f"\n[bold]Summary[/bold]")
    console.print(f"Starting Capital:  ${result.starting_capital:,.2f}")
    console.print(f"Ending Capital:    ${result.ending_capital:,.2f}")
    console.print(f"Total Return:      {result.total_return_pct:+.1f}%")
    console.print(f"Total Trades:      {len(result.trades)}")

    # Trade breakdown by exit reason
    if result.trades:
        exit_reasons = {}
        for trade in result.trades:
            reason = trade.exit_reason or "unknown"
            if reason not in exit_reasons:
                exit_reasons[reason] = {"count": 0, "pnl": 0}
            exit_reasons[reason]["count"] += 1
            exit_reasons[reason]["pnl"] += trade.pnl or 0

        console.print("\n[bold]Exit Reason Breakdown[/bold]")
        table = Table()
        table.add_column("Reason", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Total P&L", justify="right")

        for reason, data in sorted(exit_reasons.items()):
            pnl_str = f"${data['pnl']:+,.2f}"
            pnl_style = "green" if data["pnl"] > 0 else "red"
            table.add_row(
                reason,
                str(data["count"]),
                f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
            )

        console.print(table)

    # Verbose trade list
    if args.verbose and result.trades:
        console.print("\n[bold]Trade List (last 20)[/bold]")
        trade_table = Table()
        trade_table.add_column("Ticker")
        trade_table.add_column("Entry")
        trade_table.add_column("Exit")
        trade_table.add_column("P&L", justify="right")
        trade_table.add_column("Return", justify="right")
        trade_table.add_column("Reason")

        sorted_trades = sorted(
            result.trades,
            key=lambda t: t.exit_date or datetime.min,
            reverse=True,
        )

        for trade in sorted_trades[:20]:
            pnl = trade.pnl or 0
            pnl_pct = trade.pnl_pct or 0
            pnl_style = "green" if pnl > 0 else "red"

            trade_table.add_row(
                trade.ticker,
                trade.entry_date.strftime("%Y-%m-%d"),
                trade.exit_date.strftime("%Y-%m-%d") if trade.exit_date else "-",
                f"[{pnl_style}]${pnl:+,.2f}[/{pnl_style}]",
                f"[{pnl_style}]{pnl_pct * 100:+.1f}%[/{pnl_style}]",
                trade.exit_reason or "-",
            )

        console.print(trade_table)

    # Save results
    if args.output:
        output_path = Path(args.output)
        output_data = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "starting_capital": result.starting_capital,
            "ending_capital": result.ending_capital,
            "total_return_pct": result.total_return_pct,
            "metrics": result.metrics,
            "trades": [
                {
                    "ticker": t.ticker,
                    "entry_date": t.entry_date.isoformat(),
                    "exit_date": t.exit_date.isoformat() if t.exit_date else None,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "shares": t.shares,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "exit_reason": t.exit_reason,
                }
                for t in result.trades
            ],
        }

        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2, default=str)

        console.print(f"\n[green]Results saved to {output_path}[/green]")

    console.print()


if __name__ == "__main__":
    main()
