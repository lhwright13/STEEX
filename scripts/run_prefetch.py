#!/usr/bin/env python3
"""Standalone data prefetcher - warms caches before pipeline execution.

Usage:
    python scripts/run_prefetch.py              # Prefetch all data for S&P 500
    python scripts/run_prefetch.py --stats      # Show cache stats after prefetch

Cron example (run 30 min before market open):
    0 9 * * 1-5 cd /path/to/STEEX && venv/bin/python scripts/run_prefetch.py
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings
from src.data.prefetch import DataPrefetcher
from src.data.universe import Universe


def main():
    parser = argparse.ArgumentParser(
        description="STEEX Data Prefetcher - Warm caches before pipeline"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show cache stats after prefetch",
    )
    parser.add_argument(
        "--prices-only",
        action="store_true",
        help="Only prefetch price data",
    )
    args = parser.parse_args()

    settings = get_settings()

    print("STEEX Data Prefetcher")
    print("=" * 40)

    # Get universe
    universe = Universe()
    tickers = universe.get_sp500()
    print(f"Universe: {len(tickers)} tickers")

    # Run prefetcher
    prefetcher = DataPrefetcher(settings=settings, universe=universe)

    if args.prices_only:
        start = time.time()
        count = prefetcher._prefetch_prices(tickers, days=settings.prefetch_price_days)
        elapsed = time.time() - start
        print(f"Prices prefetched: {count} tickers in {elapsed:.1f}s")
    else:
        report = prefetcher.prefetch_all(tickers)
        print(f"\nPrefetch Report:")
        print(f"  Prices:       {report.prices_fetched}")
        print(f"  Earnings:     {report.earnings_fetched}")
        print(f"  Sentiment:    {report.sentiment_fetched}")
        print(f"  Fundamentals: {report.fundamentals_fetched}")
        print(f"  Duration:     {report.duration_seconds}s")
        if report.errors:
            print(f"  Errors:       {len(report.errors)}")
            for err in report.errors:
                print(f"    - {err}")

    # Show cache stats
    if args.stats:
        from src.data.cache import DBCache
        cache = DBCache(settings.cache_db_path)
        stats = cache.stats()
        print(f"\nCache Stats:")
        print(f"  Entries: {stats['entries']}")
        print(f"  Size:    {stats['size_bytes'] / 1024 / 1024:.1f} MB")
        print(f"  Hits:    {stats['hits']}")
        print(f"  Misses:  {stats['misses']}")
        cache.close()


if __name__ == "__main__":
    main()
