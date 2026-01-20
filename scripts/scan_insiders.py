#!/usr/bin/env python3
"""
Scan SEC Form 4 filings for insider buying signals.

Usage:
    python scripts/scan_insiders.py           # Default scan (3 days, daily index)
    python scripts/scan_insiders.py --fast    # Quick scan (Atom feed only)
    python scripts/scan_insiders.py --days 7  # Custom lookback period
"""

import argparse
import sys
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sec.scanners.insider import InsiderScanner
from sec.scanners.signals import find_cluster_buys, calculate_cluster_score


def print_cluster_report(clusters: dict):
    """Print formatted cluster buy report."""
    if not clusters:
        print("\nNo cluster buys found.")
        return

    scored = []
    for ticker, transactions in clusters.items():
        score_data = calculate_cluster_score(transactions)
        scored.append((ticker, transactions, score_data))

    scored.sort(key=lambda x: x[2]["score"], reverse=True)

    print("\n" + "=" * 70)
    print("CLUSTER BUY SIGNALS")
    print("=" * 70)

    for ticker, transactions, score_data in scored:
        company = transactions[0].company_name
        score = score_data["score"]
        factors = score_data["factors"]

        print(f"\n{ticker} - {company}")
        print(f"  Score: {score}/100")
        print(f"  Insiders: {factors['unique_insiders']} | Value: ${factors['total_value']:,.0f}")
        print(f"  Directors: {factors['directors']} | Officers: {factors['officers']}")

        for t in transactions[:5]:  # Show first 5
            print(f"    {t.insider_name} ({t.role})")
            print(f"      {t.shares:,.0f} @ ${t.price_per_share:.2f} = ${t.total_value:,.0f}")


def print_top_purchases(transactions: list, limit: int = 10):
    """Print top purchases by value."""
    if not transactions:
        print("\nNo purchases found.")
        return

    sorted_tx = sorted(transactions, key=lambda x: x.total_value, reverse=True)

    print("\n" + "=" * 70)
    print(f"TOP {limit} PURCHASES")
    print("=" * 70)

    for t in sorted_tx[:limit]:
        print(f"\n{t.ticker} - {t.company_name}")
        print(f"  {t.insider_name} ({t.role})")
        print(f"  {t.shares:,.0f} shares @ ${t.price_per_share:.2f} = ${t.total_value:,.0f}")


def main():
    parser = argparse.ArgumentParser(
        description="Scan SEC Form 4 filings for insider buying signals"
    )
    parser.add_argument(
        "--days", "-d", type=int, default=3, help="Days to look back (default: 3)"
    )
    parser.add_argument(
        "--max", "-m", type=int, default=150, help="Max filings to process (default: 150)"
    )
    parser.add_argument(
        "--fast", "-f", action="store_true", help="Use Atom feed (faster, less data)"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress progress output"
    )

    args = parser.parse_args()

    print("=" * 70)
    print("INSIDER BUYING SCANNER")
    print("=" * 70)

    scanner = InsiderScanner()
    purchases = scanner.scan(
        days_back=args.days,
        max_filings=args.max,
        use_daily_index=not args.fast,
        verbose=not args.quiet,
    )

    print(f"\nFound {len(purchases)} insider purchases")

    # Find cluster buys
    clusters = find_cluster_buys(purchases, min_insiders=2)
    print(f"Found {len(clusters)} cluster buy signals")

    # Print reports
    print_cluster_report(clusters)
    print_top_purchases(purchases, limit=10)

    print("\n" + "=" * 70)
    print("Source: SEC EDGAR (free, no API key)")
    print("=" * 70)


if __name__ == "__main__":
    main()
