#!/usr/bin/env python3
"""Compare backtest performance with and without overextension filter.

This script generates signals with two different overextension_percentile settings:
- BASELINE: 0.95 (filters out top 5% as overextended)
- TEST: 1.0 (no filter - includes all stocks)

Then runs backtests on both signal sets to compare performance.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

from config.settings import Settings, get_settings
from src.backtest.engine import BacktestEngine
from src.data.price import PriceProvider
from src.indicators.momentum import MomentumCalculator
from src.indicators.technical import TechnicalIndicators
from src.sec.models import InsiderTransaction
from src.sec.scanners.signals import calculate_cluster_score


# Historical insider cache
INSIDER_CACHE = Path(__file__).parent.parent / "data" / "cache" / "historical_insiders.json"


def load_historical_insiders() -> dict:
    """Load historical insider data from cache."""
    if not INSIDER_CACHE.exists():
        raise FileNotFoundError(f"Historical insider cache not found: {INSIDER_CACHE}")

    with open(INSIDER_CACHE) as f:
        return json.load(f)


def get_transactions_for_period(
    insider_data: dict,
    end_date: datetime,
    lookback_days: int = 30,
) -> list:
    """Get insider transactions for a lookback period ending on end_date."""
    start_date = end_date - timedelta(days=lookback_days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    transactions = []
    for date_str, txs in insider_data.get("dates", {}).items():
        if start_str <= date_str <= end_str:
            for tx in txs:
                ticker = tx.get("ticker")
                if not ticker or ticker in ("N/A", "NONE", ""):
                    continue

                # Only purchases
                if tx.get("transaction_code") != "P":
                    continue

                transactions.append(
                    InsiderTransaction(
                        ticker=ticker,
                        company_name=tx.get("company_name", ""),
                        company_cik="",
                        insider_name=tx.get("insider_name", ""),
                        insider_cik=tx.get("insider_name", ""),
                        is_director=tx.get("is_director", False),
                        is_officer=tx.get("is_officer", False),
                        is_ten_percent_owner=tx.get("is_ten_percent_owner", False),
                        officer_title=tx.get("role", ""),
                        transaction_date=tx.get("transaction_date", ""),
                        transaction_code=tx.get("transaction_code", "P"),
                        acquired_disposed="A",
                        shares=tx.get("shares", 0),
                        price_per_share=tx.get("price_per_share", 0),
                        total_value=tx.get("total_value", 0),
                        shares_owned_after=0,
                        filing_date=tx.get("filing_date", ""),
                        filing_url="",
                    )
                )

    return transactions


def screen_candidates_with_overextension(
    transactions: list,
    price_provider: PriceProvider,
    momentum_calc: MomentumCalculator,
    technical: TechnicalIndicators,
    settings: Settings,
    reference_date: datetime,
    all_tickers_for_percentile: list,
) -> list:
    """Screen candidates with overextension percentile filter.

    The overextension filter compares a stock's momentum percentile against
    all stocks in the universe. Stocks in the top N% are excluded as "overextended".
    """

    # Group transactions by ticker
    by_ticker = defaultdict(list)
    for tx in transactions:
        by_ticker[tx.ticker.upper()].append(tx)

    # Calculate momentum percentiles for all candidate tickers for relative comparison
    candidate_tickers = list(by_ticker.keys())
    momentum_percentiles = momentum_calc.get_momentum_percentiles(
        candidate_tickers,
        lookback_days=settings.momentum_lookback_days
    )

    candidates = []

    for ticker, txs in by_ticker.items():
        # Calculate insider score
        score_data = calculate_cluster_score(txs)
        score = score_data["score"]
        factors = score_data.get("factors", {})

        unique_buyers = factors.get("unique_insiders", len(set(t.insider_cik for t in txs)))
        total_value = factors.get("total_value", sum(t.total_value for t in txs))

        # Check insider criteria
        has_ceo_cfo = factors.get("ceo_cfo_count", 0) > 0
        if not has_ceo_cfo:
            has_ceo_cfo = any(
                t.officer_title and ("CEO" in t.officer_title.upper() or "CFO" in t.officer_title.upper())
                for t in txs
            )
        has_cluster = unique_buyers >= settings.min_cluster_buyers
        has_high_value = total_value >= settings.min_purchase_value

        if not (has_ceo_cfo or has_cluster or has_high_value):
            continue

        # Get momentum data
        try:
            momentum_6m = momentum_calc.get_momentum(
                ticker, lookback_days=settings.momentum_lookback_days
            )
            momentum_1m = momentum_calc.get_momentum(
                ticker, lookback_days=settings.short_momentum_days
            )
        except Exception:
            continue

        if momentum_6m is None or momentum_1m is None:
            continue

        # Apply momentum filter
        if momentum_6m < settings.momentum_min_return:
            continue
        if momentum_1m < 0.05:  # Stricter 1-month filter
            continue

        # Apply overextension percentile filter
        percentile_data = momentum_percentiles.get(ticker, {})
        percentile = percentile_data.get("percentile", 0)

        # This is the key filter: if percentile > overextension_percentile, skip
        if percentile > settings.overextension_percentile:
            continue

        # Check MA alignment
        alignment = technical.check_trend_alignment(
            ticker,
            short_ma=settings.ma_short,
            long_ma=settings.ma_long,
        )

        if not alignment["aligned"]:
            continue

        candidates.append({
            "date": reference_date.strftime("%Y-%m-%d"),
            "ticker": ticker,
            "score": score,
            "momentum_6m": round(momentum_6m * 100, 1),
            "momentum_1m": round(momentum_1m * 100, 1),
            "percentile": round(percentile * 100, 1),
            "insider_buyers": unique_buyers,
            "insider_value": total_value,
            "has_ceo_cfo": has_ceo_cfo,
        })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Return top N per day
    return candidates[:settings.daily_picks]


def generate_signals_with_settings(
    start_date: datetime,
    end_date: datetime,
    settings: Settings,
    insider_data: dict,
    interval_days: int = 7,
    console: Console = None,
) -> list:
    """Generate signals for backtest period with specific settings."""

    # Initialize providers
    price_provider = PriceProvider()
    momentum_calc = MomentumCalculator(price_provider)
    technical = TechnicalIndicators(price_provider)

    all_signals = []
    current_date = start_date

    while current_date <= end_date:
        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        # Get transactions for lookback period
        transactions = get_transactions_for_period(
            insider_data,
            current_date,
            lookback_days=settings.insider_lookback_days,
        )

        if transactions:
            # Get all candidate tickers for percentile calculation
            all_tickers = list(set(t.ticker.upper() for t in transactions))

            # Screen candidates with overextension filter
            candidates = screen_candidates_with_overextension(
                transactions,
                price_provider,
                momentum_calc,
                technical,
                settings,
                current_date,
                all_tickers,
            )

            for candidate in candidates:
                all_signals.append({
                    "date": candidate["date"],
                    "ticker": candidate["ticker"],
                    "score": candidate["score"],
                })

        current_date += timedelta(days=interval_days)

    return all_signals


def run_backtest_with_settings(
    signals: list,
    start_date: datetime,
    end_date: datetime,
    settings: Settings,
    starting_capital: float = 10000,
) -> dict:
    """Run backtest with specific settings and return metrics."""
    engine = BacktestEngine(settings=settings)
    result = engine.run(
        signals=signals,
        start_date=start_date,
        end_date=end_date,
        starting_capital=starting_capital,
    )

    return {
        "total_return": result.total_return_pct,
        "cagr": result.metrics.get("cagr", 0) * 100,
        "sharpe_ratio": result.metrics.get("sharpe_ratio", 0),
        "max_drawdown": result.metrics.get("max_drawdown_pct", 0) * 100,
        "win_rate": result.metrics.get("win_rate", 0) * 100,
        "total_trades": len(result.trades),
        "profit_factor": result.metrics.get("profit_factor", 0),
        "avg_hold_days": result.metrics.get("avg_hold_days", 0),
        "ending_capital": result.ending_capital,
        "total_pnl": result.metrics.get("total_pnl", 0),
    }


def main():
    """Run comparison between baseline and test configurations."""
    console = Console()

    # Configuration
    START_DATE = "2024-06-01"
    END_DATE = "2025-12-31"
    STARTING_CAPITAL = 10000
    INTERVAL_DAYS = 7  # Weekly signal generation

    start_date = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_date = datetime.strptime(END_DATE, "%Y-%m-%d")

    console.print("\n[bold]Overextension Filter Comparison Backtest[/bold]")
    console.print(f"Period: {START_DATE} to {END_DATE}")
    console.print(f"Starting Capital: ${STARTING_CAPITAL:,.2f}")
    console.print("-" * 60)

    # Clear settings cache
    get_settings.cache_clear()

    # Load historical insider data
    console.print("\nLoading historical insider data...")
    try:
        insider_data = load_historical_insiders()
        console.print("[green]Insider data loaded[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("Please run fetch_historical_insiders.py first.")
        sys.exit(1)

    # Generate BASELINE signals: overextension_percentile = 0.95 (filter active)
    console.print("\n[bold cyan]Generating BASELINE signals (overextension_percentile = 0.95)...[/bold cyan]")
    baseline_settings = Settings(overextension_percentile=0.95)
    baseline_signals = generate_signals_with_settings(
        start_date, end_date, baseline_settings, insider_data, INTERVAL_DAYS, console
    )
    console.print(f"[green]Generated {len(baseline_signals)} baseline signals[/green]")

    # Generate TEST signals: overextension_percentile = 1.0 (no filter)
    console.print("\n[bold cyan]Generating TEST signals (overextension_percentile = 1.0 - no filter)...[/bold cyan]")
    test_settings = Settings(overextension_percentile=1.0)
    test_signals = generate_signals_with_settings(
        start_date, end_date, test_settings, insider_data, INTERVAL_DAYS, console
    )
    console.print(f"[green]Generated {len(test_signals)} test signals[/green]")

    # Show signal differences
    baseline_tickers = set(s["ticker"] for s in baseline_signals)
    test_tickers = set(s["ticker"] for s in test_signals)
    additional_tickers = test_tickers - baseline_tickers

    console.print(f"\nSignal Analysis:")
    console.print(f"  Baseline unique tickers: {len(baseline_tickers)}")
    console.print(f"  Test unique tickers: {len(test_tickers)}")
    console.print(f"  Additional tickers without filter: {len(additional_tickers)}")
    if additional_tickers:
        console.print(f"  Additional tickers: {', '.join(sorted(additional_tickers)[:10])}")
        if len(additional_tickers) > 10:
            console.print(f"    ... and {len(additional_tickers) - 10} more")

    # Run BASELINE backtest
    console.print("\n[bold cyan]Running BASELINE backtest...[/bold cyan]")
    baseline_results = run_backtest_with_settings(
        baseline_signals, start_date, end_date, baseline_settings, STARTING_CAPITAL
    )
    console.print("[green]Baseline backtest complete[/green]")

    # Run TEST backtest
    console.print("[bold cyan]Running TEST backtest...[/bold cyan]")
    test_results = run_backtest_with_settings(
        test_signals, start_date, end_date, test_settings, STARTING_CAPITAL
    )
    console.print("[green]Test backtest complete[/green]")
    console.print()

    # Results comparison table
    table = Table(title="Backtest Comparison Results", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("BASELINE\n(filter=0.95)", justify="right")
    table.add_column("TEST\n(no filter=1.0)", justify="right")
    table.add_column("Difference", justify="right")

    metrics = [
        ("Total Return", "total_return", "%", 1),
        ("CAGR", "cagr", "%", 1),
        ("Sharpe Ratio", "sharpe_ratio", "", 2),
        ("Max Drawdown", "max_drawdown", "%", 1),
        ("Win Rate", "win_rate", "%", 1),
        ("Total Trades", "total_trades", "", 0),
        ("Profit Factor", "profit_factor", "", 2),
        ("Avg Hold Days", "avg_hold_days", "", 1),
    ]

    for label, key, suffix, decimals in metrics:
        baseline_val = baseline_results[key]
        test_val = test_results[key]
        diff = test_val - baseline_val

        # Format based on decimals
        if decimals == 0:
            baseline_str = f"{baseline_val:.0f}{suffix}"
            test_str = f"{test_val:.0f}{suffix}"
            diff_str = f"{diff:+.0f}{suffix}"
        elif decimals == 1:
            baseline_str = f"{baseline_val:.1f}{suffix}"
            test_str = f"{test_val:.1f}{suffix}"
            diff_str = f"{diff:+.1f}{suffix}"
        else:
            baseline_str = f"{baseline_val:.2f}{suffix}"
            test_str = f"{test_val:.2f}{suffix}"
            diff_str = f"{diff:+.2f}{suffix}"

        # Color diff based on whether it's better (green) or worse (red)
        # For drawdown, lower is better; for everything else, higher is better
        if key == "max_drawdown":
            diff_style = "green" if diff < 0 else "red" if diff > 0 else "white"
        else:
            diff_style = "green" if diff > 0 else "red" if diff < 0 else "white"

        table.add_row(
            label,
            baseline_str,
            test_str,
            f"[{diff_style}]{diff_str}[/{diff_style}]",
        )

    console.print(table)
    console.print()

    # Summary
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Baseline Ending Capital: ${baseline_results['ending_capital']:,.2f}")
    console.print(f"  Test Ending Capital:     ${test_results['ending_capital']:,.2f}")
    console.print()

    # Recommendation
    console.print("[bold]Analysis & Recommendation:[/bold]")

    # Score the comparison
    baseline_score = 0
    test_score = 0

    # Compare key metrics
    if baseline_results["sharpe_ratio"] > test_results["sharpe_ratio"]:
        baseline_score += 2
    elif test_results["sharpe_ratio"] > baseline_results["sharpe_ratio"]:
        test_score += 2

    if baseline_results["total_return"] > test_results["total_return"]:
        baseline_score += 1
    elif test_results["total_return"] > baseline_results["total_return"]:
        test_score += 1

    if baseline_results["max_drawdown"] < test_results["max_drawdown"]:
        baseline_score += 2
    elif test_results["max_drawdown"] < baseline_results["max_drawdown"]:
        test_score += 2

    if baseline_results["win_rate"] > test_results["win_rate"]:
        baseline_score += 1
    elif test_results["win_rate"] > baseline_results["win_rate"]:
        test_score += 1

    if baseline_results["profit_factor"] > test_results["profit_factor"]:
        baseline_score += 1
    elif test_results["profit_factor"] > baseline_results["profit_factor"]:
        test_score += 1

    console.print(f"  Baseline Score: {baseline_score}")
    console.print(f"  Test Score:     {test_score}")
    console.print()

    if baseline_score > test_score:
        console.print("[green][bold]RECOMMENDATION: KEEP the overextension filter (0.95)[/bold][/green]")
        console.print("  The filter helps protect against overextended positions and")
        console.print("  provides better risk-adjusted returns.")
    elif test_score > baseline_score:
        console.print("[yellow][bold]RECOMMENDATION: REMOVE the overextension filter (use 1.0)[/bold][/yellow]")
        console.print("  Removing the filter allows more opportunities and provides")
        console.print("  better overall performance.")
    else:
        console.print("[white][bold]RECOMMENDATION: Either setting works - marginal difference[/bold][/white]")
        console.print("  Consider keeping the filter for conservative risk management,")
        console.print("  or removing it if you prefer more trading opportunities.")

    console.print()


if __name__ == "__main__":
    main()
