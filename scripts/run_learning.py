#!/usr/bin/env python3
"""CLI entry point for the STEEX self-learning loop.

Usage:
    python scripts/run_learning.py                    # Full learning cycle
    python scripts/run_learning.py --dry-run          # Propose but don't apply
    python scripts/run_learning.py --phase postmortem  # Run specific phase only
    python scripts/run_learning.py --force-research   # Force signal research
    python scripts/run_learning.py --gaps             # Show knowledge gaps
    python scripts/run_learning.py --history          # Show config change history
    python scripts/run_learning.py --verbose          # Detailed output
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path so this script runs via direct invocation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config.settings import get_settings

console = Console()


def show_gaps():
    """Display knowledge gaps flagged for user review."""
    from src.learning.journal import LearningJournal

    journal = LearningJournal()
    gaps = journal.get_gaps()

    if not gaps:
        console.print("[green]No knowledge gaps flagged.[/green]")
        return

    console.print(Panel.fit(
        f"[bold]Knowledge Gaps ({len(gaps)} unresolved)[/bold]",
        border_style="yellow",
    ))

    table = Table(box=box.SIMPLE)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Type")
    table.add_column("Severity")
    table.add_column("Description")
    table.add_column("Flagged")

    for i, gap in enumerate(gaps):
        severity_color = {
            "high": "red", "medium": "yellow", "low": "dim",
        }.get(gap.get("severity", "medium"), "white")

        table.add_row(
            str(i),
            gap.get("gap_type", "unknown"),
            f"[{severity_color}]{gap.get('severity', 'medium')}[/{severity_color}]",
            gap.get("description", ""),
            gap.get("timestamp", "")[:16],
        )

    console.print(table)


def show_history():
    """Display recent config change history."""
    from src.learning.config_writer import ConfigWriter

    writer = ConfigWriter()
    history = writer.get_history(limit=20)

    if not history:
        console.print("[dim]No config changes recorded yet.[/dim]")
        return

    console.print(Panel.fit(
        f"[bold]Config Change History ({len(history)} entries)[/bold]",
        border_style="blue",
    ))

    for entry in history:
        timestamp = entry.get("timestamp", "")[:16]
        source = entry.get("source", "unknown")
        applied = entry.get("applied", {})

        console.print(f"\n[bold]{timestamp}[/bold] via {source}")
        if isinstance(applied, dict):
            for param, info in applied.items():
                if isinstance(info, dict):
                    console.print(
                        f"  {param}: {info.get('old', '?')} -> {info.get('new', '?')} "
                        f"(delta: {info.get('delta', '?'):+.4f})"
                    )
        warnings = entry.get("warnings", [])
        for w in warnings:
            console.print(f"  [yellow]{w}[/yellow]")


def run_learning(args):
    """Execute the learning loop."""
    settings = get_settings()

    if not settings.learning_enabled:
        console.print("[red]Learning loop is disabled in config.[/red]")
        console.print("Set learning_enabled: true in config/config.yaml to enable.")
        return

    from src.learning.loop import LearningLoop

    loop = LearningLoop(settings=settings)

    dry_run = args.dry_run or settings.learning_dry_run
    phases = [args.phase] if args.phase else None

    console.print(Panel.fit(
        "[bold]STEEX Learning Loop[/bold]\n"
        f"Mode: {'DRY RUN' if dry_run else 'LIVE'}"
        + (f" | Phase: {args.phase}" if args.phase else " | All phases")
        + (" | Force research" if args.force_research else ""),
        border_style="magenta",
    ))

    results = loop.run(
        dry_run=dry_run,
        force_research=args.force_research,
        phases=phases,
    )

    if results.get("error"):
        console.print(f"[red]Error: {results['error']}[/red]")
        return

    # Print results
    phases_run = results.get("phases_run", [])
    console.print(f"\nPhases completed: {', '.join(phases_run)}")

    # PostMortem summary
    pm = results.get("postmortem")
    if pm and not pm.get("error"):
        console.print(f"\n[bold]PostMortem:[/bold] {pm.get('trades_analyzed', 0)} trades analyzed")
        if pm.get("loss_breakdown"):
            for cat, count in pm["loss_breakdown"].items():
                console.print(f"  {cat}: {count}")
        console.print(f"  Score correlation: {pm.get('score_correlation', 0):.2f}")

    # Alpha Decay summary
    decay = results.get("alpha_decay")
    if decay and not decay.get("error"):
        degrading = decay.get("degrading", [])
        if degrading:
            console.print(f"\n[bold yellow]Alpha Decay:[/bold yellow] Degrading signals: {', '.join(degrading)}")
        else:
            console.print("\n[bold]Alpha Decay:[/bold] All signals healthy")

    # Signal Research summary
    research = results.get("signal_research")
    if research and not research.get("error"):
        console.print(f"\n[bold]Signal Research:[/bold] {research.get('feature_count', 0)} observations")
        weights = research.get("recommended_weights", {})
        if weights:
            console.print("  Recommended weights:")
            for k, v in sorted(weights.items()):
                console.print(f"    {k}: {v:.4f}")

    # OOS Validation summary
    oos = results.get("oos_validation")
    if oos:
        passed = oos.get("passed", False)
        color = "green" if passed else "red"
        console.print(
            f"\n[bold]OOS Validation:[/bold] [{color}]{'PASSED' if passed else 'FAILED'}[/{color}] "
            f"Sharpe={oos.get('sharpe', 0):.2f}, Win Rate={oos.get('win_rate', 0):.1%}"
        )

    # Apply summary
    apply_result = results.get("apply")
    if apply_result:
        if apply_result.get("dry_run"):
            console.print("\n[bold yellow]Apply:[/bold yellow] DRY RUN - changes not applied")
            proposal = apply_result.get("would_apply", {})
            changes = proposal.get("changes", {})
            if changes:
                for param, info in changes.items():
                    console.print(
                        f"  {param}: {info['current']} -> {info['validated']} "
                        f"(delta: {info['delta']:+.4f})"
                    )
        elif apply_result.get("applied"):
            console.print(f"\n[bold green]Applied {apply_result.get('count', 0)} config changes[/bold green]")

    # Gaps summary
    gaps = results.get("gaps", [])
    if gaps:
        console.print(f"\n[bold yellow]Gaps flagged: {len(gaps)}[/bold yellow]")
        for gap in gaps:
            console.print(f"  [{gap.get('severity', 'medium')}] {gap.get('description', '')}")

    console.print()


def main():
    parser = argparse.ArgumentParser(
        description="STEEX Self-Learning Loop - Continuous Parameter Optimization"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Propose changes without applying them",
    )
    parser.add_argument(
        "--phase",
        choices=[
            "postmortem", "alpha_decay", "signal_research",
            "oos_validation", "apply", "gaps",
        ],
        help="Run a specific phase only",
    )
    parser.add_argument(
        "--gaps",
        action="store_true",
        help="Show knowledge gaps flagged for review",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show config change history",
    )
    parser.add_argument(
        "--force-research",
        action="store_true",
        help="Force signal research even if no degradation detected",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Detailed output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.gaps:
        show_gaps()
    elif args.history:
        show_history()
    else:
        run_learning(args)


if __name__ == "__main__":
    main()
