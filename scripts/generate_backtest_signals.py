#!/usr/bin/env python3
"""Generate backtest signals from historical insider data."""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config.settings import get_settings
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
) -> list[InsiderTransaction]:
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


def screen_candidates(
    transactions: list[InsiderTransaction],
    price_provider: PriceProvider,
    momentum_calc: MomentumCalculator,
    technical: TechnicalIndicators,
    settings,
    reference_date: datetime,
) -> list[dict]:
    """Screen candidates using momentum, technical, and insider filters."""

    # Group transactions by ticker
    by_ticker = defaultdict(list)
    for tx in transactions:
        by_ticker[tx.ticker.upper()].append(tx)

    candidates = []

    for ticker, txs in by_ticker.items():
        # Calculate insider score with new enhanced scoring
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

        # Apply stricter momentum filter
        if momentum_6m < settings.momentum_min_return:
            continue
        if momentum_1m < 0.05:  # Stricter 1-month filter
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
            "insider_buyers": unique_buyers,
            "insider_value": total_value,
            "has_ceo_cfo": has_ceo_cfo,
        })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Return top N per day
    return candidates[:settings.daily_picks]


def generate_signals(
    start_date: datetime,
    end_date: datetime,
    interval_days: int = 7,
) -> list[dict]:
    """Generate signals for backtest period."""

    console = Console()
    settings = get_settings()

    # Load historical data
    console.print("Loading historical insider data...")
    insider_data = load_historical_insiders()

    # Initialize providers
    price_provider = PriceProvider()
    momentum_calc = MomentumCalculator(price_provider)
    technical = TechnicalIndicators(price_provider)

    all_signals = []
    current_date = start_date

    # Calculate number of periods
    total_periods = ((end_date - start_date).days // interval_days) + 1

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating signals...", total=total_periods)

        while current_date <= end_date:
            progress.update(task, description=f"Processing {current_date.strftime('%Y-%m-%d')}...")

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
                # Screen candidates
                candidates = screen_candidates(
                    transactions,
                    price_provider,
                    momentum_calc,
                    technical,
                    settings,
                    current_date,
                )

                for candidate in candidates:
                    all_signals.append({
                        "date": candidate["date"],
                        "ticker": candidate["ticker"],
                        "score": candidate["score"],
                    })

            current_date += timedelta(days=interval_days)
            progress.advance(task)

    return all_signals


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate backtest signals from historical data")
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
        "--interval",
        type=int,
        default=7,
        help="Days between signal generation (default: 7 for weekly)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/mis_backtest_signals_v2.json",
        help="Output file path",
    )
    parser.add_argument(
        "--compare",
        type=str,
        help="Path to original signals file for comparison",
    )

    args = parser.parse_args()
    console = Console()

    # Parse dates
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as e:
        console.print(f"[red]Invalid date format: {e}[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Signal Generation[/bold]")
    console.print(f"Period: {args.start} to {args.end}")
    console.print(f"Interval: {args.interval} days")
    console.print("-" * 50)

    # Clear settings cache to ensure fresh config
    get_settings.cache_clear()
    settings = get_settings()

    console.print(f"\n[bold]Using Settings:[/bold]")
    console.print(f"  Momentum min return: {settings.momentum_min_return * 100}%")
    console.print(f"  Insider lookback: {settings.insider_lookback_days} days")
    console.print(f"  Min cluster buyers: {settings.min_cluster_buyers}")
    console.print(f"  Min purchase value: ${settings.min_purchase_value:,.0f}")
    console.print()

    # Generate signals
    signals = generate_signals(start_date, end_date, args.interval)

    # Save signals
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(signals, f, indent=2)

    console.print(f"\n[green]Generated {len(signals)} signals[/green]")
    console.print(f"Saved to: {output_path}")

    # Show unique tickers
    unique_tickers = set(s["ticker"] for s in signals)
    console.print(f"Unique tickers: {len(unique_tickers)}")

    # Show signals table
    if signals:
        table = Table(title="Generated Signals")
        table.add_column("Date")
        table.add_column("Ticker")
        table.add_column("Score", justify="right")

        for signal in signals[:20]:
            table.add_row(
                signal["date"],
                signal["ticker"],
                str(signal["score"]),
            )

        if len(signals) > 20:
            table.add_row("...", "...", "...")

        console.print(table)

    # Compare with original if provided
    if args.compare:
        compare_path = Path(args.compare)
        if compare_path.exists():
            with open(compare_path) as f:
                original = json.load(f)

            orig_tickers = set(s["ticker"] for s in original)
            new_tickers = set(s["ticker"] for s in signals)

            console.print(f"\n[bold]Comparison with Original[/bold]")
            console.print(f"Original signals: {len(original)}")
            console.print(f"New signals: {len(signals)}")
            console.print(f"Original tickers: {orig_tickers}")
            console.print(f"New tickers: {new_tickers}")
            console.print(f"Added: {new_tickers - orig_tickers}")
            console.print(f"Removed: {orig_tickers - new_tickers}")


if __name__ == "__main__":
    main()
