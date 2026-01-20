#!/usr/bin/env python3
"""Daily stock scanner - runs the full screening pipeline."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import get_settings
from src.data.vix import VixProvider
from src.strategy.ranking import StockRanker
from src.strategy.screener import StockScreener


def main():
    """Run the daily stock scanner."""
    parser = argparse.ArgumentParser(description="Daily stock scanner")
    parser.add_argument(
        "--date",
        type=str,
        help="Date to scan (YYYY-MM-DD), defaults to today",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Number of top picks to show (defaults to settings.daily_picks)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output",
    )
    parser.add_argument(
        "--all-candidates",
        action="store_true",
        help="Show all candidates, not just top picks",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show stocks passing each stage (for debugging)",
    )
    parser.add_argument(
        "--skip-insider",
        action="store_true",
        help="Skip insider filter to see momentum picks (testing only)",
    )

    args = parser.parse_args()

    console = Console()
    settings = get_settings()

    # Parse date
    if args.date:
        try:
            reference_date = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            console.print(f"[red]Invalid date format: {args.date}[/red]")
            console.print("Use YYYY-MM-DD format")
            sys.exit(1)
    else:
        reference_date = datetime.now()

    console.print(f"\n[bold]MIS Daily Scanner[/bold]")
    console.print(f"Date: {reference_date.strftime('%Y-%m-%d')}")
    console.print("-" * 40)

    # Check VIX
    vix = VixProvider()
    vix_level = vix.get_current()
    if vix_level:
        if vix_level > settings.vix_exit_level:
            console.print(f"[bold red]VIX: {vix_level:.1f} - SPIKE![/bold red]")
            console.print("[yellow]Consider exiting 50% of positions[/yellow]")
        elif vix_level > settings.vix_caution_level:
            console.print(f"[bold yellow]VIX: {vix_level:.1f} - ELEVATED[/bold yellow]")
            console.print("[yellow]Tightening stops recommended[/yellow]")
        else:
            console.print(f"VIX: {vix_level:.1f}")
    console.print()

    # Run screener
    console.print("[bold]Running screening pipeline...[/bold]")

    with console.status("Stage 1: Universe filter..."):
        screener = StockScreener()

    with console.status("Running full pipeline..."):
        result = screener.run_pipeline(reference_date)

    # Show pipeline results
    console.print(f"\n[bold]Screening Results[/bold]")
    console.print(f"Universe size:        {result.universe_size}")
    console.print(f"Stage 1 (filters):    {result.stage_1_passed}")
    console.print(f"Stage 2 (momentum):   {result.stage_2_passed}")
    console.print(f"Stage 3 (insider):    {result.stage_3_passed}")
    console.print(f"Stage 4 (sentiment):  {result.stage_4_passed}")
    console.print()

    # Debug: show stocks at each stage
    if args.debug:
        console.print("\n[bold]Stage 2 Momentum Stocks:[/bold]")
        momentum_stocks = [
            (t, r) for t, r in result.all_results.items()
            if "stage_2" in r.passed_stages
        ]
        momentum_stocks.sort(key=lambda x: x[1].momentum_6m or 0, reverse=True)

        for ticker, sr in momentum_stocks[:15]:
            mom_6m = f"{(sr.momentum_6m or 0) * 100:+.1f}%"
            mom_1m = f"{(sr.momentum_1m or 0) * 100:+.1f}%"
            insider_info = f"Insiders: {sr.insider_buyers}" if sr.insider_buyers else "No insider buys"
            console.print(f"  {ticker:6} | 6M: {mom_6m:>7} | 1M: {mom_1m:>7} | {insider_info}")

        if len(momentum_stocks) > 15:
            console.print(f"  ... and {len(momentum_stocks) - 15} more")
        console.print()

    # Skip insider filter for testing
    candidates = result.final_candidates
    if args.skip_insider and not candidates:
        console.print("[yellow]--skip-insider: Using Stage 2 momentum stocks[/yellow]")
        candidates = [
            r for r in result.all_results.values()
            if "stage_2" in r.passed_stages
        ]

    if not candidates:
        console.print("[yellow]No candidates found matching all criteria.[/yellow]")
        sys.exit(0)

    # Rank candidates
    ranker = StockRanker()
    n_picks = args.top or settings.daily_picks

    if args.all_candidates:
        ranked = ranker.rank_stocks(candidates)
    else:
        ranked = ranker.get_top_picks(candidates, n_picks)

    # Display results
    table = Table(title=f"Top {len(ranked)} Picks")
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Ticker", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("6M Return", justify="right")
    table.add_column("Insiders", justify="right")
    table.add_column("Insider $", justify="right")
    table.add_column("Reasons")

    for pick in ranked:
        summary = ranker.format_pick_summary(pick)
        reasons_str = ", ".join(summary["reasons"][:3])

        table.add_row(
            str(summary["rank"]),
            summary["ticker"],
            str(summary["score"]),
            summary["momentum_6m"],
            str(summary["insider_buyers"]),
            summary["insider_value"],
            reasons_str,
        )

    console.print(table)

    # Verbose output
    if args.verbose and ranked:
        console.print("\n[bold]Detailed Pick Information[/bold]")
        console.print("-" * 50)

        for pick in ranked[:3]:
            sr = pick.screening_result
            console.print(f"\n[bold cyan]{pick.ticker}[/bold cyan]")
            console.print(f"  Composite Score: {pick.composite_score:.1f}")
            console.print(f"  Momentum Score:  {pick.momentum_score:.1f}")
            console.print(f"  Insider Score:   {pick.insider_score:.1f}")
            console.print(f"  Volume Score:    {pick.volume_score:.1f}")
            console.print(f"  6M Momentum:     {(sr.momentum_6m or 0) * 100:.1f}%")
            console.print(f"  1M Momentum:     {(sr.momentum_1m or 0) * 100:.1f}%")
            console.print(f"  Above 50 MA:     {sr.above_ma_50}")
            console.print(f"  Above 200 MA:    {sr.above_ma_200}")
            console.print(f"  Insider Buyers:  {sr.insider_buyers}")
            console.print(f"  Insider Value:   ${sr.total_insider_value:,.0f}")

    console.print()


if __name__ == "__main__":
    main()
