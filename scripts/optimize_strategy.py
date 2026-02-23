#!/usr/bin/env python3
"""Parameter optimization for the backtest engine.

Sweeps key execution parameters across a grid, runs the backtest for each
combination, and ranks results by risk-adjusted metrics.

Usage:
    python scripts/optimize_strategy.py
    python scripts/optimize_strategy.py --signals data/mis_backtest_signals_v2.json
    python scripts/optimize_strategy.py --capital 50000 --top 20
"""

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich import box

from config.settings import Settings, get_settings
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import format_metrics_report

console = Console()


# ---------------------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------------------

PARAM_GRID = {
    # Stop-loss parameters
    "initial_stop_pct": [0.08, 0.10, 0.12, 0.15, 0.18],
    # Trailing stop distances (after 10%/20%/30% gain)
    "trail_stop_10": [0.08, 0.10, 0.12, 0.15],
    "trail_stop_20": [0.12, 0.15, 0.18],
    "trail_stop_30": [0.15, 0.18, 0.22],
    # Hold time rules
    "max_hold_days": [30, 45, 60, 90],
    "dead_money_days": [7, 10, 15, 20],
    # Position sizing
    "position_size_pct": [0.04, 0.05, 0.06, 0.08],
    "max_positions": [10, 15, 20],
    # Cooling off
    "cooling_off_days": [7, 14, 21],
}

# Focused grid for fast sweep - only the most impactful parameters
FOCUSED_GRID = {
    "initial_stop_pct": [0.08, 0.10, 0.12, 0.15, 0.18],
    "max_hold_days": [30, 45, 60, 90],
    "dead_money_days": [7, 10, 15, 20, 999],  # 999 = effectively disabled
    "position_size_pct": [0.04, 0.05, 0.06, 0.08, 0.10],
    "max_positions": [10, 15, 20],
}

# Trailing stop grid - run separately since it has multiple coupled params
TRAIL_GRID = {
    "trail_stop_10": [0.06, 0.08, 0.10, 0.12, 0.15],
    "trail_stop_20": [0.10, 0.12, 0.15, 0.18],
    "trail_stop_30": [0.12, 0.15, 0.18, 0.22, 0.25],
}


def make_settings(overrides: dict) -> Settings:
    """Create a Settings instance with parameter overrides."""
    get_settings.cache_clear()
    s = Settings()
    for key, val in overrides.items():
        setattr(s, key, val)
    return s


def run_single_backtest(
    signals: list,
    start_date: datetime,
    end_date: datetime,
    capital: float,
    overrides: dict,
) -> dict:
    """Run a single backtest with parameter overrides."""
    settings = make_settings(overrides)
    engine = BacktestEngine(settings=settings)
    result = engine.run(
        signals=signals,
        start_date=start_date,
        end_date=end_date,
        starting_capital=capital,
    )

    return {
        "params": overrides,
        "total_return_pct": result.total_return_pct,
        "total_trades": len(result.trades),
        "win_rate": result.metrics.get("win_rate", 0),
        "profit_factor": result.metrics.get("profit_factor", 0),
        "sharpe_ratio": result.metrics.get("sharpe_ratio", 0),
        "sortino_ratio": result.metrics.get("sortino_ratio", 0),
        "max_drawdown_pct": result.metrics.get("max_drawdown_pct", 0),
        "cagr": result.metrics.get("cagr", 0),
        "calmar_ratio": result.metrics.get("calmar_ratio", 0),
        "avg_hold_days": result.metrics.get("avg_hold_days", 0),
        "avg_winner": result.metrics.get("avg_winner", 0),
        "avg_loser": result.metrics.get("avg_loser", 0),
        "ending_capital": result.ending_capital,
        "total_pnl": result.metrics.get("total_pnl", 0),
    }


def sweep_grid(
    grid: dict,
    signals: list,
    start_date: datetime,
    end_date: datetime,
    capital: float,
    base_overrides: dict = None,
) -> list:
    """Sweep a parameter grid and return sorted results."""
    base_overrides = base_overrides or {}
    keys = list(grid.keys())
    values = list(grid.values())
    combos = list(product(*values))

    console.print(f"Sweeping {len(combos)} combinations across {len(keys)} parameters")

    results = []
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Optimizing...", total=len(combos))

        for combo in combos:
            overrides = {**base_overrides}
            for k, v in zip(keys, combo):
                overrides[k] = v

            # Validate: trailing stops should be increasing
            t10 = overrides.get("trail_stop_10")
            t20 = overrides.get("trail_stop_20")
            t30 = overrides.get("trail_stop_30")
            if t10 and t20 and t10 > t20:
                progress.advance(task)
                continue
            if t20 and t30 and t20 > t30:
                progress.advance(task)
                continue

            try:
                result = run_single_backtest(
                    signals, start_date, end_date, capital, overrides
                )
                results.append(result)
            except Exception as e:
                console.print(f"  [dim]Error with {overrides}: {e}[/dim]")

            progress.advance(task)

    return results


def print_top_results(results: list, sort_key: str, title: str, n: int = 10):
    """Print top N results sorted by a given key."""
    if sort_key == "sharpe_ratio":
        # Filter out extreme Sharpe from few trades
        filtered = [r for r in results if r["total_trades"] >= 5]
    else:
        filtered = results

    if not filtered:
        filtered = results

    reverse = sort_key != "max_drawdown_pct"
    sorted_results = sorted(filtered, key=lambda r: r.get(sort_key, 0), reverse=reverse)

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("#", style="dim", justify="right")
    table.add_column("Return", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Win%", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("MaxDD", justify="right")
    table.add_column("Parameters")

    for i, r in enumerate(sorted_results[:n], 1):
        ret_color = "green" if r["total_return_pct"] > 0 else "red"
        params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        table.add_row(
            str(i),
            f"[{ret_color}]{r['total_return_pct']:+.1f}%[/{ret_color}]",
            str(r["total_trades"]),
            f"{r['win_rate'] * 100:.0f}%",
            f"{r['profit_factor']:.2f}",
            f"{r['sharpe_ratio']:.2f}",
            f"{r['max_drawdown_pct'] * 100:.1f}%",
            params_str,
        )

    console.print(table)
    console.print()


def find_parameter_importance(results: list) -> dict:
    """Analyze which parameters have the most impact on returns."""
    importance = {}

    # Get all parameter keys
    all_params = set()
    for r in results:
        all_params.update(r["params"].keys())

    for param in all_params:
        # Group results by this parameter's value
        by_value = {}
        for r in results:
            val = r["params"].get(param)
            if val is not None:
                if val not in by_value:
                    by_value[val] = []
                by_value[val].append(r["total_return_pct"])

        if len(by_value) < 2:
            continue

        # Calculate mean return for each value
        means = {v: sum(rets) / len(rets) for v, rets in by_value.items()}
        spread = max(means.values()) - min(means.values())
        best_val = max(means, key=means.get)

        importance[param] = {
            "spread": spread,
            "best_value": best_val,
            "best_return": means[best_val],
            "all_means": {str(k): round(v, 2) for k, v in sorted(means.items())},
        }

    # Sort by impact
    return dict(sorted(importance.items(), key=lambda x: x[1]["spread"], reverse=True))


def main():
    parser = argparse.ArgumentParser(description="Optimize strategy parameters via backtest sweep")
    parser.add_argument("--signals", type=str, default="data/mis_backtest_signals_v2.json")
    parser.add_argument("--start", type=str, default="2025-08-25")
    parser.add_argument("--end", type=str, default="2026-01-19")
    parser.add_argument("--capital", type=float, default=50000)
    parser.add_argument("--top", type=int, default=15, help="Number of top results to show")
    parser.add_argument("--full", action="store_true", help="Run full grid (slow)")
    parser.add_argument("--output", type=str, default="data/optimization_results.json")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")

    # Load signals
    with open(args.signals) as f:
        signals = json.load(f)

    console.print(Panel.fit(
        f"[bold]Strategy Parameter Optimization[/bold]\n"
        f"Period: {args.start} to {args.end} | Capital: ${args.capital:,.0f}\n"
        f"Signals: {len(signals)} | Mode: {'Full' if args.full else 'Focused'}",
        border_style="blue",
    ))

    # -----------------------------------------------------------------------
    # Phase 1: Core parameter sweep
    # -----------------------------------------------------------------------
    console.print("\n[bold]Phase 1: Core Parameter Sweep[/bold]")
    grid = PARAM_GRID if args.full else FOCUSED_GRID
    core_results = sweep_grid(grid, signals, start_date, end_date, args.capital)

    print_top_results(core_results, "total_return_pct", "Top by Total Return", args.top)
    print_top_results(core_results, "sharpe_ratio", "Top by Sharpe Ratio", args.top)
    print_top_results(core_results, "profit_factor", "Top by Profit Factor", args.top)

    # Parameter importance
    importance = find_parameter_importance(core_results)
    if importance:
        console.print("[bold]Parameter Importance (by return spread):[/bold]")
        table = Table(box=box.SIMPLE)
        table.add_column("Parameter")
        table.add_column("Impact", justify="right")
        table.add_column("Best Value")
        table.add_column("Value -> Avg Return")

        for param, info in list(importance.items())[:10]:
            table.add_row(
                param,
                f"{info['spread']:.1f}%",
                str(info['best_value']),
                str(info['all_means']),
            )

        console.print(table)
        console.print()

    # -----------------------------------------------------------------------
    # Phase 2: Trailing stop sweep (using best core params)
    # -----------------------------------------------------------------------
    console.print("[bold]Phase 2: Trailing Stop Optimization[/bold]")

    # Get best core params (by Sharpe, with min 5 trades)
    viable = [r for r in core_results if r["total_trades"] >= 5]
    if viable:
        best_core = max(viable, key=lambda r: r["sharpe_ratio"])
        base_params = best_core["params"]
        console.print(f"Using best core params: {base_params}")
    else:
        base_params = {}

    trail_results = sweep_grid(
        TRAIL_GRID, signals, start_date, end_date, args.capital, base_overrides=base_params
    )

    print_top_results(trail_results, "total_return_pct", "Top Trailing Stop Configs by Return", args.top)
    print_top_results(trail_results, "sharpe_ratio", "Top Trailing Stop Configs by Sharpe", args.top)

    # -----------------------------------------------------------------------
    # Phase 3: Best overall combination
    # -----------------------------------------------------------------------
    console.print("[bold]Phase 3: Best Overall Configuration[/bold]")

    all_results = core_results + trail_results
    viable_all = [r for r in all_results if r["total_trades"] >= 5]

    if viable_all:
        # Rank by composite score: normalize return, sharpe, and drawdown
        for r in viable_all:
            # Higher is better for all three
            dd = r["max_drawdown_pct"]
            r["composite"] = (
                r["total_return_pct"] * 0.3
                + r["sharpe_ratio"] * 10 * 0.4
                + (1 - dd) * 100 * 0.15
                + r["win_rate"] * 100 * 0.15
            )

        best_composite = sorted(viable_all, key=lambda r: r["composite"], reverse=True)

        print_top_results(best_composite, "composite", "Top by Composite Score (Return + Sharpe + Low DD + Win Rate)", args.top)

        winner = best_composite[0]
        console.print(Panel(
            f"Return:       [green]{winner['total_return_pct']:+.1f}%[/green]\n"
            f"Trades:       {winner['total_trades']}\n"
            f"Win Rate:     {winner['win_rate'] * 100:.1f}%\n"
            f"Profit Factor: {winner['profit_factor']:.2f}\n"
            f"Sharpe Ratio: {winner['sharpe_ratio']:.2f}\n"
            f"Max Drawdown: {winner['max_drawdown_pct'] * 100:.1f}%\n"
            f"Avg Hold:     {winner['avg_hold_days']:.0f} days\n"
            f"\nParameters:\n" + "\n".join(
                f"  {k}: {v}" for k, v in winner["params"].items()
            ),
            title="RECOMMENDED CONFIGURATION",
            border_style="green",
        ))

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    save_data = {
        "run_date": datetime.now().isoformat(),
        "config": {
            "start": args.start,
            "end": args.end,
            "capital": args.capital,
            "signals_count": len(signals),
        },
        "core_results_count": len(core_results),
        "trail_results_count": len(trail_results),
        "parameter_importance": importance,
        "top_by_return": sorted(
            all_results, key=lambda r: r["total_return_pct"], reverse=True
        )[:20],
        "top_by_sharpe": sorted(
            [r for r in all_results if r["total_trades"] >= 5],
            key=lambda r: r["sharpe_ratio"],
            reverse=True,
        )[:20],
        "recommended": winner["params"] if viable_all else {},
    }

    with open(output_path, "w") as f:
        json.dump(save_data, f, indent=2, default=str)

    console.print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
