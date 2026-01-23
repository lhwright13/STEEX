#!/usr/bin/env python3
"""
Find stocks with promising signals for tomorrow's trading.

This script:
1. Runs Stage 1 and Stage 2 screening (momentum stocks)
2. Fetches recent insider activity from SEC Form 4 filings
3. Cross-references to find actionable opportunities
4. Identifies stocks close to passing all criteria
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Change to project root and add to path
project_root = Path(__file__).parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from src.data.universe import Universe
from src.data.price import PriceProvider
from src.indicators.momentum import MomentumCalculator
from src.indicators.technical import TechnicalIndicators
from src.sec.scanners.insider import InsiderScanner
from src.sec.scanners.signals import find_cluster_buys, calculate_cluster_score
from config.settings import get_settings

# Cache file for historical insider data
INSIDER_CACHE_FILE = Path(__file__).parent.parent / "data" / "cache" / "historical_insiders.json"


def load_cached_insiders(days_back: int = 30) -> list:
    """Load insider transactions from cache file."""
    if not INSIDER_CACHE_FILE.exists():
        return []

    try:
        with open(INSIDER_CACHE_FILE) as f:
            cache = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    cutoff = datetime.now() - timedelta(days=days_back)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    transactions = []
    for date_str, txs in cache.get("dates", {}).items():
        if date_str >= cutoff_str:
            for tx in txs:
                tx["_filing_date"] = date_str
                transactions.append(tx)

    return transactions


def analyze_insider_activity(transactions: list, min_value: float = 100000) -> dict:
    """Analyze insider transactions and group by ticker."""
    by_ticker = defaultdict(list)

    for tx in transactions:
        ticker = tx.get("ticker")
        if not ticker or ticker in ("N/A", "NONE", ""):
            continue
        ticker = ticker.upper()
        by_ticker[ticker].append(tx)

    results = {}
    for ticker, txs in by_ticker.items():
        total_value = sum(t.get("total_value", 0) for t in txs)
        unique_buyers = len(set(t.get("insider_name") for t in txs))

        # Check for CEO/CFO buys
        ceo_cfo_buys = []
        officer_buys = []
        director_buys = []
        large_buys = []

        for t in txs:
            role = t.get("role", "").upper()
            value = t.get("total_value", 0)

            if "CEO" in role or "CFO" in role or "CHIEF EXECUTIVE" in role or "CHIEF FINANCIAL" in role:
                ceo_cfo_buys.append(t)
            elif t.get("is_officer"):
                officer_buys.append(t)
            elif t.get("is_director"):
                director_buys.append(t)

            if value >= min_value:
                large_buys.append(t)

        # Calculate a simple score
        score = 0
        score += unique_buyers * 10
        score += len(ceo_cfo_buys) * 50
        score += len(officer_buys) * 30
        score += len(director_buys) * 15

        if total_value > 1_000_000:
            score += 60
        elif total_value > 500_000:
            score += 40
        elif total_value > 100_000:
            score += 20

        if unique_buyers >= 3:
            score += 25

        results[ticker] = {
            "ticker": ticker,
            "company_name": txs[0].get("company_name", ""),
            "total_value": total_value,
            "unique_buyers": unique_buyers,
            "ceo_cfo_buys": ceo_cfo_buys,
            "officer_buys": officer_buys,
            "director_buys": director_buys,
            "large_buys": large_buys,
            "transactions": txs,
            "score": min(score, 100),
            "is_cluster": unique_buyers >= 3,
            "has_ceo_cfo": len(ceo_cfo_buys) > 0,
            "has_large_buy": len(large_buys) > 0,
        }

    return results


def get_momentum_data(tickers: list, settings) -> dict:
    """Get momentum and technical data for tickers."""
    price_provider = PriceProvider()
    momentum_calc = MomentumCalculator(price_provider)
    technical = TechnicalIndicators(price_provider)

    print(f"  Fetching momentum data for {len(tickers)} tickers...")

    # Get 6-month momentum and percentiles
    momentum_data = momentum_calc.get_momentum_percentiles(
        tickers,
        lookback_days=settings.momentum_lookback_days,
    )

    # Get 1-month momentum
    print("  Fetching short-term momentum...")
    short_momentum = momentum_calc.get_momentum_batch(
        tickers,
        lookback_days=settings.short_momentum_days,
    )

    results = {}
    for ticker in tickers:
        data = momentum_data.get(ticker, {})
        if not data:
            continue

        momentum_6m = data.get("momentum", 0)
        momentum_1m = short_momentum.get(ticker, 0)
        percentile = data.get("percentile", 0)

        # Check MA alignment
        alignment = technical.check_trend_alignment(
            ticker,
            short_ma=settings.ma_short,
            long_ma=settings.ma_long,
        )

        # Get current price
        price = price_provider.get_latest_price(ticker)

        results[ticker] = {
            "ticker": ticker,
            "price": price,
            "momentum_6m": momentum_6m,
            "momentum_1m": momentum_1m,
            "percentile": percentile,
            "above_ma_50": alignment["above_short_ma"],
            "above_ma_200": alignment["above_long_ma"],
            "aligned": alignment["aligned"],
        }

    return results


def check_stage_criteria(momentum_data: dict, settings) -> dict:
    """Check which stage criteria each stock passes."""
    for ticker, data in momentum_data.items():
        passes_momentum_6m = data["momentum_6m"] >= settings.momentum_min_return
        passes_momentum_1m = data["momentum_1m"] >= 0.05
        passes_percentile = data["percentile"] <= settings.overextension_percentile
        passes_ma = data["aligned"]

        data["passes_momentum_6m"] = passes_momentum_6m
        data["passes_momentum_1m"] = passes_momentum_1m
        data["passes_percentile"] = passes_percentile
        data["passes_ma"] = passes_ma
        data["passes_stage_2"] = passes_momentum_6m and passes_momentum_1m and passes_percentile and passes_ma

    return momentum_data


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def format_pct(value: float) -> str:
    """Format a decimal as percentage."""
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def format_currency(value: float) -> str:
    """Format currency value."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def main():
    print("=" * 70)
    print("PROMISING SIGNALS SCANNER")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    settings = get_settings()

    # Step 1: Load cached insider data
    print_section("STEP 1: LOADING INSIDER DATA")
    cached_insiders = load_cached_insiders(days_back=30)
    print(f"Loaded {len(cached_insiders)} insider transactions from cache")

    insider_analysis = analyze_insider_activity(cached_insiders)
    print(f"Found insider activity for {len(insider_analysis)} unique tickers")

    # Step 2: Also fetch fresh insider data from SEC
    print_section("STEP 2: FETCHING FRESH SEC DATA")
    try:
        scanner = InsiderScanner()
        fresh_purchases = scanner.scan(
            days_back=7,
            max_filings=200,
            use_daily_index=True,
            verbose=True,
        )
        print(f"Found {len(fresh_purchases)} recent purchases")

        # Find cluster buys
        clusters = find_cluster_buys(fresh_purchases, min_insiders=2)
        print(f"Found {len(clusters)} cluster buy signals")
    except Exception as e:
        print(f"Error fetching fresh SEC data: {e}")
        fresh_purchases = []
        clusters = {}

    # Step 3: Get S&P 500 universe and run Stage 1/2 screening
    print_section("STEP 3: RUNNING MOMENTUM SCREENING")
    universe = Universe()
    sp500 = universe.get_sp500()
    print(f"S&P 500 universe: {len(sp500)} stocks")

    # Filter by price/volume (Stage 1 simplified)
    print("  Filtering by price and volume...")
    stage_1 = universe.filter_by_price_volume(
        sp500,
        min_price=settings.min_price,
        min_volume=settings.min_volume,
    )
    print(f"  Stage 1 passed: {len(stage_1)} stocks")

    # Get momentum data for Stage 1 passed stocks
    momentum_data = get_momentum_data(stage_1, settings)
    momentum_data = check_stage_criteria(momentum_data, settings)

    stage_2_passed = [t for t, d in momentum_data.items() if d.get("passes_stage_2")]
    print(f"  Stage 2 passed: {len(stage_2_passed)} stocks")

    # Step 4: Cross-reference momentum stocks with insider activity
    print_section("STEP 4: CROSS-REFERENCING DATA")

    # Category A: Stocks with good momentum but lacking insider activity
    momentum_no_insider = []
    for ticker in stage_2_passed:
        if ticker not in insider_analysis:
            data = momentum_data[ticker]
            momentum_no_insider.append(data)

    # Category B: Stocks that pass Stage 2 AND have insider activity
    full_candidates = []
    for ticker in stage_2_passed:
        if ticker in insider_analysis:
            insider = insider_analysis[ticker]
            momentum = momentum_data[ticker]

            # Check if insider activity is significant
            passes_insider = (
                insider["is_cluster"] or
                insider["has_ceo_cfo"] or
                insider["has_large_buy"]
            )

            if passes_insider:
                full_candidates.append({
                    "ticker": ticker,
                    "momentum": momentum,
                    "insider": insider,
                })

    # Category C: Stocks with strong insider activity but momentum slightly below threshold
    near_pass_momentum = []
    for ticker, insider in insider_analysis.items():
        if ticker not in momentum_data:
            continue

        momentum = momentum_data[ticker]

        # Check if close to passing Stage 2
        if not momentum.get("passes_stage_2"):
            # Close if: 6m momentum >= 10% (vs 15% threshold) or 1m >= 3% (vs 5%)
            close_6m = momentum["momentum_6m"] >= 0.10
            close_1m = momentum["momentum_1m"] >= 0.03
            has_trend = momentum["above_ma_50"] or momentum["above_ma_200"]

            if close_6m and close_1m and has_trend:
                # Has decent insider activity?
                if insider["score"] >= 30 or insider["has_ceo_cfo"] or insider["has_large_buy"]:
                    near_pass_momentum.append({
                        "ticker": ticker,
                        "momentum": momentum,
                        "insider": insider,
                    })

    # Category D: Strong insider signals on ANY stock worth watching
    strong_insider_signals = []
    for ticker, insider in insider_analysis.items():
        # Very strong signals: cluster, CEO/CFO, or large value
        if insider["score"] >= 50 or insider["has_ceo_cfo"] or insider["total_value"] >= 500000:
            # Get momentum if available
            momentum = momentum_data.get(ticker)
            strong_insider_signals.append({
                "ticker": ticker,
                "insider": insider,
                "momentum": momentum,
                "in_sp500": ticker in sp500,
            })

    # Sort by insider score
    strong_insider_signals.sort(key=lambda x: x["insider"]["score"], reverse=True)

    # Print Results
    print_section("RESULTS: FULL CANDIDATES (Momentum + Insider)")
    if full_candidates:
        for c in sorted(full_candidates, key=lambda x: x["insider"]["score"], reverse=True):
            ticker = c["ticker"]
            m = c["momentum"]
            i = c["insider"]

            print(f"\n{ticker} - {i['company_name']}")
            print(f"  Price: ${m['price']:.2f}" if m['price'] else "  Price: N/A")
            print(f"  Momentum 6M: {format_pct(m['momentum_6m'])} | 1M: {format_pct(m['momentum_1m'])}")
            print(f"  Above MA50: {m['above_ma_50']} | MA200: {m['above_ma_200']}")
            print(f"  Insider Score: {i['score']}/100 | Buyers: {i['unique_buyers']} | Value: {format_currency(i['total_value'])}")
            if i['has_ceo_cfo']:
                print(f"  [CEO/CFO BUY]")
            if i['is_cluster']:
                print(f"  [CLUSTER BUY: {i['unique_buyers']} insiders]")
    else:
        print("\nNo stocks currently pass both momentum AND insider criteria.")

    print_section("RESULTS: MOMENTUM STOCKS NEEDING INSIDER CONFIRMATION")
    print(f"({len(momentum_no_insider)} stocks pass Stage 2 but lack insider activity)")

    # Show top momentum stocks without insider activity
    momentum_no_insider.sort(key=lambda x: x.get("momentum_6m", 0), reverse=True)
    for m in momentum_no_insider[:15]:
        print(f"  {m['ticker']}: 6M={format_pct(m['momentum_6m'])}, 1M={format_pct(m['momentum_1m'])}, Price=${m.get('price', 0):.2f}")

    print_section("RESULTS: STRONG INSIDER SIGNALS TO WATCH")
    print(f"(Sorted by insider score, may have varying momentum)")

    shown = 0
    for s in strong_insider_signals[:20]:
        ticker = s["ticker"]
        i = s["insider"]
        m = s.get("momentum")

        print(f"\n{ticker} - {i['company_name']}")
        print(f"  Insider Score: {i['score']}/100 | Buyers: {i['unique_buyers']} | Value: {format_currency(i['total_value'])}")

        if i['has_ceo_cfo']:
            for tx in i['ceo_cfo_buys'][:2]:
                print(f"    CEO/CFO: {tx.get('insider_name')} - {format_currency(tx.get('total_value', 0))}")

        if i['large_buys']:
            for tx in i['large_buys'][:2]:
                role = tx.get('role', 'Insider')
                print(f"    Large buy: {tx.get('insider_name')} ({role}) - {format_currency(tx.get('total_value', 0))}")

        if m:
            status = "PASSES Stage 2" if m.get("passes_stage_2") else "Does NOT pass Stage 2"
            print(f"  Momentum: 6M={format_pct(m.get('momentum_6m', 0))}, 1M={format_pct(m.get('momentum_1m', 0))} [{status}]")
        else:
            print(f"  Momentum: Not in S&P 500 or no data")

        print(f"  In S&P 500: {s['in_sp500']}")
        shown += 1

    print_section("RESULTS: NEAR-PASS CANDIDATES")
    print("(Good insider activity but momentum slightly below threshold)")

    near_pass_momentum.sort(key=lambda x: x["insider"]["score"], reverse=True)
    for c in near_pass_momentum[:10]:
        ticker = c["ticker"]
        m = c["momentum"]
        i = c["insider"]

        print(f"\n{ticker} - {i['company_name']}")
        print(f"  Momentum 6M: {format_pct(m['momentum_6m'])} (need 15%) | 1M: {format_pct(m['momentum_1m'])} (need 5%)")
        print(f"  Above MA50: {m['above_ma_50']} | MA200: {m['above_ma_200']}")
        print(f"  Insider Score: {i['score']}/100 | Value: {format_currency(i['total_value'])}")

        # What's missing?
        missing = []
        if not m.get("passes_momentum_6m"):
            missing.append(f"6M momentum ({format_pct(m['momentum_6m'])} vs 15% needed)")
        if not m.get("passes_momentum_1m"):
            missing.append(f"1M momentum ({format_pct(m['momentum_1m'])} vs 5% needed)")
        if not m.get("passes_ma"):
            missing.append("MA alignment")

        if missing:
            print(f"  Missing: {', '.join(missing)}")

    # Summary
    print_section("SUMMARY FOR TOMORROW'S TRADING")
    print(f"Full candidates (momentum + insider): {len(full_candidates)}")
    print(f"Momentum stocks awaiting insider confirmation: {len(momentum_no_insider)}")
    print(f"Strong insider signals (any momentum): {len([s for s in strong_insider_signals if s['insider']['score'] >= 50])}")
    print(f"Near-pass candidates: {len(near_pass_momentum)}")

    if full_candidates:
        print("\nTOP PICKS (full criteria met):")
        for c in sorted(full_candidates, key=lambda x: x["insider"]["score"], reverse=True)[:5]:
            print(f"  - {c['ticker']}")

    print("\n" + "=" * 70)
    print("Scan complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
