#!/usr/bin/env python3
"""
Fetch historical insider trading data from SEC EDGAR.

This script downloads and caches Form 4 filings for backtesting purposes.
Data is stored in a local JSON file for fast access.

Usage:
    python scripts/fetch_historical_insiders.py              # Last 6 months
    python scripts/fetch_historical_insiders.py --days 365   # Last year
    python scripts/fetch_historical_insiders.py --rebuild    # Force rebuild cache
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from sec.client import EdgarClient
from sec.scanners.insider import InsiderScanner
from sec.models import InsiderTransaction

console = Console()

# Cache file location
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_FILE = CACHE_DIR / "historical_insiders.json"


def load_cache() -> Dict:
    """Load existing cache if available."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"dates": {}, "last_updated": None}
    return {"dates": {}, "last_updated": None}


def save_cache(cache: Dict):
    """Save cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache["last_updated"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, default=str)


def transaction_to_dict(tx: InsiderTransaction) -> Dict:
    """Convert transaction to serializable dict."""
    # Determine role from flags
    role = tx.officer_title or ""
    if tx.is_officer and not role:
        role = "Officer"
    elif tx.is_director:
        role = "Director"
    elif tx.is_ten_percent_owner:
        role = "10% Owner"

    return {
        "ticker": tx.ticker,
        "company_name": tx.company_name,
        "insider_name": tx.insider_name,
        "role": role,
        "is_officer": tx.is_officer,
        "is_director": tx.is_director,
        "is_ten_percent_owner": tx.is_ten_percent_owner,
        "transaction_code": tx.transaction_code,
        "shares": tx.shares,
        "price_per_share": tx.price_per_share,
        "total_value": tx.total_value,
        "transaction_date": tx.transaction_date if isinstance(tx.transaction_date, str) else (tx.transaction_date.isoformat() if tx.transaction_date else None),
        "filing_date": tx.filing_date if isinstance(tx.filing_date, str) else (tx.filing_date.isoformat() if tx.filing_date else None),
    }


def fetch_day(scanner: InsiderScanner, date: datetime, max_filings: int = 200) -> List[Dict]:
    """Fetch insider transactions for a single day."""
    try:
        transactions = scanner.scan(
            days_back=1,
            max_filings=max_filings,
            use_daily_index=True,
            verbose=False,
            reference_date=date,
        )
        return [transaction_to_dict(tx) for tx in transactions]
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to fetch {date.strftime('%Y-%m-%d')}: {e}[/yellow]")
        return []


def get_trading_days(start_date: datetime, end_date: datetime) -> List[datetime]:
    """Get list of trading days (weekdays) between dates."""
    days = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            days.append(current)
        current += timedelta(days=1)
    return days


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical insider trading data from SEC EDGAR"
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=180,
        help="Number of days to fetch (default: 180)",
    )
    parser.add_argument(
        "--max-filings", "-m",
        type=int,
        default=200,
        help="Max filings per day (default: 200)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild cache from scratch",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show verbose output",
    )

    args = parser.parse_args()

    console.print("\n[bold]Historical Insider Data Fetcher[/bold]")
    console.print("=" * 50)

    # Load or create cache
    if args.rebuild:
        cache = {"dates": {}, "last_updated": None}
        console.print("[yellow]Rebuilding cache from scratch[/yellow]")
    else:
        cache = load_cache()
        if cache["last_updated"]:
            console.print(f"Cache last updated: {cache['last_updated']}")
            console.print(f"Cached days: {len(cache['dates'])}")

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    console.print(f"\nDate range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

    # Get trading days
    trading_days = get_trading_days(start_date, end_date)
    console.print(f"Trading days to process: {len(trading_days)}")

    # Find days we need to fetch
    days_to_fetch = []
    for day in trading_days:
        day_key = day.strftime("%Y-%m-%d")
        if day_key not in cache["dates"]:
            days_to_fetch.append(day)

    console.print(f"Days already cached: {len(trading_days) - len(days_to_fetch)}")
    console.print(f"Days to fetch: {len(days_to_fetch)}")

    if not days_to_fetch:
        console.print("\n[green]Cache is up to date![/green]")
    else:
        console.print(f"\n[bold]Fetching {len(days_to_fetch)} days of data...[/bold]")
        console.print("[dim]SEC rate limit: 10 requests/second - this may take a while[/dim]")

        scanner = InsiderScanner()
        total_transactions = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching...", total=len(days_to_fetch))

            for i, day in enumerate(days_to_fetch):
                day_key = day.strftime("%Y-%m-%d")
                progress.update(task, description=f"Fetching {day_key}")

                transactions = fetch_day(scanner, day, args.max_filings)
                cache["dates"][day_key] = transactions
                total_transactions += len(transactions)

                if args.verbose and transactions:
                    console.print(f"  {day_key}: {len(transactions)} purchases")

                progress.advance(task)

                # Save periodically
                if (i + 1) % 10 == 0:
                    save_cache(cache)

                # Small delay to be nice to SEC servers
                time.sleep(0.2)

        save_cache(cache)
        console.print(f"\n[green]Fetched {total_transactions} transactions from {len(days_to_fetch)} days[/green]")

    # Summary statistics
    console.print("\n[bold]Cache Summary[/bold]")
    console.print("-" * 30)

    total_tx = sum(len(txs) for txs in cache["dates"].values())
    console.print(f"Total cached days: {len(cache['dates'])}")
    console.print(f"Total transactions: {total_tx}")

    # Count by ticker
    ticker_counts = {}
    for day_txs in cache["dates"].values():
        for tx in day_txs:
            ticker = tx.get("ticker", "UNKNOWN")
            ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

    console.print(f"Unique tickers: {len(ticker_counts)}")

    # Top tickers
    if ticker_counts:
        top_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        console.print("\nTop 10 tickers by purchase count:")
        for ticker, count in top_tickers:
            console.print(f"  {ticker}: {count}")

    console.print(f"\nCache saved to: {CACHE_FILE}")
    console.print()


if __name__ == "__main__":
    main()
