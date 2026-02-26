#!/usr/bin/env python3
"""Run signal research analysis.

Requires walk-forward data (feature matrix with forward returns).
Run a walk-forward backtest first to generate the feature matrix,
or provide pre-computed features via --features flag.

Usage:
    python scripts/run_signal_research.py --start 2024-01-01 --end 2025-01-01
    python scripts/run_signal_research.py --features features.json
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
from src.research.signal_tester import SignalResearcher


def main():
    parser = argparse.ArgumentParser(description="STEEX Signal Research")
    parser.add_argument(
        "--start", type=str, help="Start date (YYYY-MM-DD) for generating features",
    )
    parser.add_argument(
        "--end", type=str, help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--features", type=str, help="Path to pre-computed features JSON",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Signal generation interval in days",
    )
    parser.add_argument(
        "--output", type=str, help="Output file for results",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    console = Console()
    settings = get_settings()

    # Load or generate feature matrix
    feature_matrix = None

    if args.features:
        features_path = Path(args.features)
        if not features_path.exists():
            console.print(f"[red]Features file not found: {args.features}[/red]")
            sys.exit(1)
        with open(features_path) as f:
            feature_matrix = json.load(f)
        console.print(f"Loaded {len(feature_matrix)} feature observations")

    elif args.start and args.end:
        try:
            start_date = datetime.strptime(args.start, "%Y-%m-%d")
            end_date = datetime.strptime(args.end, "%Y-%m-%d")
        except ValueError as e:
            console.print(f"[red]Invalid date: {e}[/red]")
            sys.exit(1)

        console.print(f"\n[bold]Generating feature matrix...[/bold]")
        console.print(f"Period: {args.start} to {args.end}")

        from src.backtest.walkforward import WalkForwardBacktester
        backtester = WalkForwardBacktester(settings=settings)

        if args.interval:
            settings.walkforward_signal_interval = args.interval

        with console.status("Running pipeline for historical dates..."):
            _, feature_matrix = backtester.generate_historical_signals(
                start=start_date,
                end=end_date,
            )

        console.print(f"Generated {len(feature_matrix)} observations")
    else:
        console.print("[red]Provide either --start/--end or --features[/red]")
        sys.exit(1)

    if not feature_matrix:
        console.print("[yellow]No feature data available.[/yellow]")
        sys.exit(0)

    # Run signal research
    console.print(f"\n[bold]STEEX Signal Research[/bold]")
    console.print("-" * 60)

    researcher = SignalResearcher(settings=settings)
    report = researcher.run_full_analysis(feature_matrix)

    # Hypothesis test results
    if report.hypotheses:
        console.print(f"\n[bold]Signal Hypothesis Tests[/bold]")
        table = Table(box=box.SIMPLE)
        table.add_column("Signal")
        table.add_column("IC", justify="right")
        table.add_column("t-stat", justify="right")
        table.add_column("p-value", justify="right")
        table.add_column("Sig?")
        table.add_column("Win Rate", justify="right")
        table.add_column("Samples", justify="right")

        for h in report.hypotheses:
            sig_str = "[green]YES[/green]" if h.is_significant else "[red]NO[/red]"
            table.add_row(
                h.signal_name.replace("_score", ""),
                f"{h.information_coefficient:+.3f}",
                f"{h.t_statistic:.2f}",
                f"{h.p_value:.4f}",
                sig_str,
                f"{h.win_rate_when_strong:.1%}",
                str(h.sample_size),
            )
        console.print(table)

    # Redundant pairs
    if report.redundant_pairs:
        console.print(f"\n[bold]Redundant Signal Pairs[/bold] (corr > {settings.research_redundancy_threshold})")
        for s1, s2 in report.redundant_pairs:
            key = f"{s1}|{s2}"
            corr = report.correlations.get(key, 0)
            console.print(f"  {s1} <-> {s2}: {corr:.3f}")
    else:
        console.print("\nNo redundant signal pairs found.")

    # Recommended weights
    if report.recommended_weights:
        console.print(f"\n[bold]Recommended Weights (ridge regression)[/bold]")
        table = Table(box=box.SIMPLE)
        table.add_column("Signal")
        table.add_column("Current Weight", justify="right")
        table.add_column("Recommended", justify="right")

        current_weights = {
            "momentum_score": settings.weight_momentum,
            "insider_score": settings.weight_insider,
            "volume_score": settings.weight_volume,
            "sentiment_score": settings.weight_sentiment,
            "fundamental_score": settings.weight_fundamental,
            "options_score": settings.weight_options,
            "pysr_score": settings.weight_pysr,
        }

        for signal, weight in sorted(report.recommended_weights.items(), key=lambda x: -x[1]):
            current = current_weights.get(signal, 0)
            table.add_row(
                signal.replace("_score", ""),
                f"{current:.2f}",
                f"{weight:.2f}",
            )
        console.print(table)

    # Save results
    if args.output:
        output_path = Path(args.output)
        output_data = {
            "hypotheses": [
                {
                    "signal": h.signal_name,
                    "ic": h.information_coefficient,
                    "t_stat": h.t_statistic,
                    "p_value": h.p_value,
                    "significant": h.is_significant,
                    "win_rate": h.win_rate_when_strong,
                    "samples": h.sample_size,
                }
                for h in report.hypotheses
            ],
            "redundant_pairs": report.redundant_pairs,
            "recommended_weights": report.recommended_weights,
        }
        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2)
        console.print(f"\n[green]Results saved to {output_path}[/green]")

    console.print()


if __name__ == "__main__":
    main()
