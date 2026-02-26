#!/usr/bin/env python3
"""Run post-mortem analysis on completed trades.

Usage:
    python scripts/run_postmortem.py
    python scripts/run_postmortem.py --days 90
    python scripts/run_postmortem.py --save
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich import box

from config.settings import get_settings
from src.portfolio.postmortem import PostMortemAnalyzer


def main():
    parser = argparse.ArgumentParser(description="STEEX Post-Mortem Analysis")
    parser.add_argument(
        "--days", type=int, default=None,
        help="Lookback days (overrides settings)",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save report to data/postmortem/",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show individual trade analyses",
    )

    args = parser.parse_args()

    console = Console()
    settings = get_settings()

    if args.days is not None:
        settings.postmortem_lookback_days = args.days

    lookback = settings.postmortem_lookback_days
    start = datetime.now() - timedelta(days=lookback)

    console.print(f"\n[bold]STEEX Post-Mortem Analysis[/bold]")
    console.print(f"Period: last {lookback} days")
    console.print("-" * 50)

    analyzer = PostMortemAnalyzer(settings=settings)
    report = analyzer.generate_report(start, datetime.now())

    if report.trades_analyzed == 0:
        console.print("[yellow]No trades found in the period.[/yellow]")
        sys.exit(0)

    console.print(f"\nTrades analyzed: {report.trades_analyzed}")
    console.print(f"Score-return correlation: {report.score_correlation:.3f}")
    console.print(f"Avg missed upside after exit: {report.avg_missed_upside:.1%}")

    # Loss breakdown
    if report.loss_breakdown:
        console.print(f"\n[bold]Loss Categories[/bold]")
        table = Table(box=box.SIMPLE)
        table.add_column("Category")
        table.add_column("Count", justify="right")

        for cat, count in sorted(report.loss_breakdown.items(), key=lambda x: -x[1]):
            table.add_row(cat, str(count))
        console.print(table)

    # Patterns
    if report.patterns:
        console.print(f"\n[bold]Patterns Detected[/bold]")
        for p in report.patterns:
            console.print(f"  {p['pattern']}: {p['count']} occurrences")

    # Recommendations
    if report.recommendations:
        console.print(f"\n[bold]Recommendations[/bold]")
        for rec in report.recommendations:
            console.print(f"  - {rec}")

    # Verbose: individual trade analyses
    if args.verbose and report.analyses:
        console.print(f"\n[bold]Trade Details[/bold]")
        table = Table(box=box.SIMPLE)
        table.add_column("Ticker")
        table.add_column("P&L", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Accuracy")
        table.add_column("Loss Type")
        table.add_column("Regime")
        table.add_column("Missed", justify="right")

        for a in report.analyses[:30]:
            pnl_color = "green" if a.trade.pnl_pct > 0 else "red"
            table.add_row(
                a.trade.ticker,
                f"[{pnl_color}]{a.trade.pnl_pct * 100:+.1f}%[/{pnl_color}]",
                f"{a.trade.score:.0f}",
                a.score_accuracy,
                a.loss_category or "-",
                a.regime_at_entry,
                f"{a.missed_upside:.1%}" if a.missed_upside > 0 else "-",
            )
        console.print(table)

    # Save
    if args.save:
        filepath = analyzer.save_knowledge(report)
        console.print(f"\n[green]Report saved to {filepath}[/green]")

    console.print()


if __name__ == "__main__":
    main()
