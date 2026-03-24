#!/usr/bin/env python3
"""Analyze current holdings using STEEX screening and portfolio tools.

Runs each holding through fundamentals, momentum, sentiment, options,
correlation analysis, regime detection, and sector exposure checks.

Usage:
    venv/bin/python scripts/analyze_holdings.py
    venv/bin/python scripts/analyze_holdings.py --json  # Machine-readable output
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings
from src.data.fundamentals import FundamentalsProvider
from src.data.geopolitical import get_ticker_sector
from src.data.options import OptionsProvider
from src.data.price import PriceProvider
from src.data.sentiment import SentimentProvider
from src.indicators.momentum import MomentumCalculator
from src.indicators.technical import TechnicalIndicators
from src.portfolio.construction import PortfolioConstructor
from src.regime.detector import RegimeDetector

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Holdings extracted from Robinhood screenshots (2026-03-19)
# ---------------------------------------------------------------------------
HOLDINGS = {
    # Ticker: (shares, last_price)
    "VOO":   (7.77, 603.66),
    "GLD":   (10.06, 419.69),
    "GOOGL": (7.67, 304.18),
    "QQQ":   (3.73, 589.02),
    "HAL":   (53.31, 37.00),
    "VGT":   (2.48, 716.00),
    "AMAT":  (5.00, 353.13),
    "AMZN":  (8.42, 207.02),
    "IREN":  (42.56, 39.97),
    "IBKR":  (23.16, 67.29),
    "AAPL":  (5.69, 249.10),
    "GE":    (4.74, 286.66),
    "META":  (2.08, 605.33),
    "UBER":  (15.96, 75.51),
    "AVGO":  (3.80, 316.70),
    "MSFT":  (3.05, 388.90),
    "RKLB":  (None, 71.16),   # Shares partially visible
    "VGK":   (13.06, 81.14),
    "KTOS":  (11.90, 88.38),
    "AVAV":  (4.99, 206.56),
    "LHX":   (2.78, 357.30),
    "LMT":   (1.53, 627.10),
    "OXY":   (14.47, 60.07),
    "NEM":   (8.19, 97.13),
    "TEM":   (15.74, 48.34),
    "PANW":  (None, 168.04),  # Shares partially visible
    "MU":    (1.41, 438.65),
    "AMD":   (2.82, 198.73),
    "UNH":   (1.98, 282.50),
    "TSM":   (1.66, 332.82),
    "RTX":   (2.67, 197.89),
    "CVCO":  (1.10, 468.24),
    "JOBY":  (52.44, 9.53),
    "INTC":  (9.79, 45.32),
    "PLTR":  (None, None),    # Partially visible
    "LLY":   (0.462534, 918.24),
    "CRM":   (2.11, 194.55),
    "AXP":   (1.32, 293.42),
    "BAC":   (7.19, 46.57),
    "EL":    (2.29, 85.31),
    "IOVA":  (34.97, 3.78),
    "BA":    (0.578690, 198.02),
    "JPM":   (0.198080, 284.98),
}


def compute_position_values(holdings: dict) -> dict:
    """Compute dollar value of each position."""
    values = {}
    for ticker, (shares, price) in holdings.items():
        if shares is not None and price is not None:
            values[ticker] = shares * price
    return values


def analyze_fundamentals(tickers: list) -> dict:
    """Fetch fundamental data for all tickers."""
    provider = FundamentalsProvider()
    results = {}
    for ticker in tickers:
        try:
            data = provider.fetch(ticker)
            results[ticker] = data
        except Exception as e:
            logger.warning(f"  Fundamentals failed for {ticker}: {e}")
    return results


def analyze_momentum(tickers: list, settings) -> dict:
    """Compute momentum metrics for all tickers."""
    pp = PriceProvider()
    mc = MomentumCalculator(pp)
    ti = TechnicalIndicators(pp)

    results = {}
    for ticker in tickers:
        try:
            mom_6m = mc.get_momentum(ticker, lookback_days=settings.momentum_lookback_days)
            mom_1m = mc.get_momentum(ticker, lookback_days=settings.short_momentum_days)
            alignment = ti.check_trend_alignment(ticker, short_ma=50, long_ma=200)
            results[ticker] = {
                "momentum_6m": mom_6m,
                "momentum_1m": mom_1m,
                "above_50ma": alignment.get("above_short_ma", False),
                "above_200ma": alignment.get("above_long_ma", False),
                "trend_aligned": alignment.get("aligned", False),
            }
        except Exception as e:
            logger.warning(f"  Momentum failed for {ticker}: {e}")
    return results


def analyze_sentiment(tickers: list) -> dict:
    """Fetch sentiment for all tickers."""
    provider = SentimentProvider()
    results = {}
    for ticker in tickers:
        try:
            data = provider.get_sentiment(ticker)
            results[ticker] = data
        except Exception as e:
            logger.warning(f"  Sentiment failed for {ticker}: {e}")
    return results


def analyze_options(tickers: list) -> dict:
    """Fetch options data for all tickers."""
    provider = OptionsProvider()
    results = {}
    for ticker in tickers:
        try:
            data = provider.fetch(ticker)
            results[ticker] = data
        except Exception as e:
            logger.warning(f"  Options failed for {ticker}: {e}")
    return results


def build_sector_exposure(tickers: list, values: dict) -> dict:
    """Build sector exposure breakdown."""
    sector_values = defaultdict(float)
    total = sum(values.values())
    for ticker in tickers:
        sector = get_ticker_sector(ticker)
        if sector == "unknown":
            # Fallback: try yfinance
            try:
                import yfinance as yf
                info = yf.Ticker(ticker).info
                sector = info.get("sector", "Unknown")
            except Exception:
                sector = "Unknown"
        sector_values[sector] += values.get(ticker, 0)

    exposure = {}
    for sector, val in sorted(sector_values.items(), key=lambda x: -x[1]):
        exposure[sector] = {"value": round(val, 2), "pct": round(val / total * 100, 1) if total else 0}
    return exposure


def detect_concentration_risks(values: dict, total: float) -> list:
    """Flag positions that are too concentrated."""
    risks = []
    for ticker, val in sorted(values.items(), key=lambda x: -x[1]):
        pct = val / total * 100 if total else 0
        if pct > 10:
            risks.append(f"  {ticker}: {pct:.1f}% of portfolio (>10% concentration)")
        elif pct > 7:
            risks.append(f"  {ticker}: {pct:.1f}% of portfolio (approaching 10% limit)")
    return risks


def detect_correlation_clusters(corr_matrix: pd.DataFrame, threshold: float = 0.70) -> list:
    """Find highly correlated pairs."""
    clusters = []
    cols = corr_matrix.columns.tolist()
    for i, t1 in enumerate(cols):
        for t2 in cols[i + 1:]:
            corr_val = corr_matrix.loc[t1, t2]
            if abs(corr_val) >= threshold:
                clusters.append((t1, t2, round(corr_val, 3)))
    return sorted(clusters, key=lambda x: -abs(x[2]))


def print_separator(char="=", width=70):
    print(char * width)


def format_pct(val, na="N/A"):
    if val is None:
        return na
    return f"{val*100:+.1f}%"


def format_score(val, na="N/A"):
    if val is None:
        return na
    return f"{val:.0f}"


def main():
    parser = argparse.ArgumentParser(description="Analyze current holdings")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    settings = get_settings()

    # --- Position values ---
    values = compute_position_values(HOLDINGS)
    total_value = sum(values.values())
    tickers_with_values = [t for t in HOLDINGS if t in values]
    # Only analyze individual stocks (exclude ETFs for screening)
    etfs = {"VOO", "QQQ", "VGT", "GLD", "VGK"}
    stock_tickers = [t for t in tickers_with_values if t not in etfs]

    print()
    print_separator()
    print("  STEEX HOLDINGS ANALYSIS")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Holdings: {len(HOLDINGS)} positions")
    print(f"  Portfolio Value (visible): ${total_value:,.2f}")
    print_separator()

    # === 1. MARKET REGIME ===
    print("\n[1] MARKET REGIME")
    print("-" * 50)
    try:
        regime = RegimeDetector(settings=settings).detect_regime()
        print(f"  Regime:          {regime.name.upper()} (confidence: {regime.confidence:.0%})")
        print(f"  VIX:             {regime.vix_level:.1f}")
        print(f"  Yield Curve:     {regime.yield_curve_status} (spread: {regime.yield_spread:.2f}%)")
        print(f"  Market Breadth:  {regime.breadth_score:.0f}/100")
        print(f"  Dollar Trend:    {regime.dollar_trend}")
        print(f"  Sector Rotation: {regime.sector_rotation}")
        print(f"  Sizing Mult:     {regime.sizing_multiplier:.2f}x")
        print(f"  Entries Allowed: {'Yes' if regime.entries_allowed else 'NO'}")
    except Exception as e:
        print(f"  Failed to detect regime: {e}")
        regime = None

    # === 2. POSITION BREAKDOWN ===
    print("\n[2] POSITION BREAKDOWN")
    print("-" * 50)
    print(f"  {'Ticker':<8} {'Shares':>8} {'Price':>10} {'Value':>12} {'Weight':>8}")
    print(f"  {'------':<8} {'------':>8} {'-----':>10} {'-----':>12} {'------':>8}")
    for ticker, (shares, price) in sorted(HOLDINGS.items(), key=lambda x: -(values.get(x[0], 0))):
        val = values.get(ticker, 0)
        pct = val / total_value * 100 if total_value else 0
        sh_str = f"{shares:.2f}" if shares is not None else "???"
        pr_str = f"${price:.2f}" if price is not None else "???"
        print(f"  {ticker:<8} {sh_str:>8} {pr_str:>10} ${val:>10,.2f} {pct:>6.1f}%")

    # === 3. SECTOR EXPOSURE ===
    print("\n[3] SECTOR EXPOSURE")
    print("-" * 50)
    sector_exposure = build_sector_exposure(tickers_with_values, values)
    for sector, data in sector_exposure.items():
        bar = "#" * int(data["pct"] / 2)
        print(f"  {sector:<25} ${data['value']:>10,.2f}  {data['pct']:>5.1f}%  {bar}")

    # === 4. CONCENTRATION RISKS ===
    print("\n[4] CONCENTRATION RISKS")
    print("-" * 50)
    risks = detect_concentration_risks(values, total_value)
    if risks:
        for r in risks:
            print(r)
    else:
        print("  No single position exceeds 10% of portfolio.")

    # === 5. CORRELATION ANALYSIS ===
    print("\n[5] CORRELATION ANALYSIS")
    print("-" * 50)
    try:
        constructor = PortfolioConstructor(settings=settings)
        corr_matrix = constructor.compute_correlation_matrix(tickers_with_values)
        if not corr_matrix.empty:
            clusters = detect_correlation_clusters(corr_matrix, threshold=0.70)
            if clusters:
                print("  Highly correlated pairs (>0.70):")
                for t1, t2, corr_val in clusters[:15]:
                    print(f"    {t1:<6} <-> {t2:<6}  r={corr_val:+.3f}")
            else:
                print("  No pairs exceed 0.70 correlation. Good diversification.")

            # Compute average pairwise correlation
            n = len(corr_matrix)
            if n > 1:
                upper = corr_matrix.values[np.triu_indices(n, k=1)]
                avg_corr = np.mean(np.abs(upper))
                print(f"\n  Average pairwise |correlation|: {avg_corr:.3f}")
                print(f"  Diversification ratio: {1 - avg_corr:.3f} (higher = better)")
        else:
            print("  Could not compute correlation matrix.")
    except Exception as e:
        print(f"  Correlation analysis failed: {e}")

    # === 6. VOLATILITY & RISK-PARITY WEIGHTS ===
    print("\n[6] VOLATILITY & RISK-PARITY WEIGHTS")
    print("-" * 50)
    try:
        vols = constructor.compute_volatilities(tickers_with_values)
        rp_weights = constructor.risk_parity_weights(tickers_with_values, vols)

        print(f"  {'Ticker':<8} {'Ann Vol':>8} {'Actual Wt':>10} {'RP Wt':>8} {'Delta':>8}")
        print(f"  {'------':<8} {'------':>8} {'---------':>10} {'-----':>8} {'-----':>8}")
        for ticker in sorted(tickers_with_values, key=lambda t: -rp_weights.get(t, 0)):
            vol = vols.get(ticker)
            rp_w = rp_weights.get(ticker, 0)
            actual_w = values.get(ticker, 0) / total_value if total_value else 0
            delta = actual_w - rp_w
            vol_str = f"{vol*100:.1f}%" if vol else "N/A"
            print(f"  {ticker:<8} {vol_str:>8} {actual_w*100:>9.1f}% {rp_w*100:>6.1f}% {delta*100:>+7.1f}%")
    except Exception as e:
        print(f"  Volatility analysis failed: {e}")

    # === 7. FUNDAMENTALS (stocks only) ===
    print("\n[7] FUNDAMENTALS")
    print("-" * 50)
    print("  Fetching fundamentals for individual stocks...")
    fundamentals = analyze_fundamentals(stock_tickers)
    print(f"  {'Ticker':<8} {'P/E':>8} {'Fwd P/E':>8} {'ROE':>8} {'D/E':>8} {'Rev Grw':>8} {'Score':>6}")
    print(f"  {'------':<8} {'---':>8} {'------':>8} {'---':>8} {'---':>8} {'------':>8} {'-----':>6}")
    for ticker in stock_tickers:
        fd = fundamentals.get(ticker)
        if fd:
            pe_str = f"{fd.pe_ratio:.1f}" if fd.pe_ratio else "N/A"
            fpe_str = f"{fd.forward_pe:.1f}" if fd.forward_pe else "N/A"
            roe_str = f"{fd.return_on_equity*100:.1f}%" if fd.return_on_equity else "N/A"
            de_str = f"{fd.debt_to_equity:.2f}" if fd.debt_to_equity else "N/A"
            rg_str = format_pct(fd.revenue_growth)
            sc_str = format_score(fd.fundamental_score)
            print(f"  {ticker:<8} {pe_str:>8} {fpe_str:>8} {roe_str:>8} {de_str:>8} {rg_str:>8} {sc_str:>6}")
        else:
            print(f"  {ticker:<8} {'---':>8} {'---':>8} {'---':>8} {'---':>8} {'---':>8} {'---':>6}")

    # Flag fundamentals concerns
    print("\n  Fundamental flags:")
    flags = []
    for ticker in stock_tickers:
        fd = fundamentals.get(ticker)
        if not fd:
            continue
        if fd.pe_ratio and fd.pe_ratio > settings.fundamental_max_pe:
            flags.append(f"    {ticker}: P/E {fd.pe_ratio:.1f} > {settings.fundamental_max_pe} (high valuation)")
        if fd.debt_to_equity and fd.debt_to_equity > settings.fundamental_max_debt_equity:
            flags.append(f"    {ticker}: D/E {fd.debt_to_equity:.2f} > {settings.fundamental_max_debt_equity} (high leverage)")
        if fd.return_on_equity and fd.return_on_equity < settings.fundamental_min_roe:
            flags.append(f"    {ticker}: ROE {fd.return_on_equity*100:.1f}% < {settings.fundamental_min_roe*100:.0f}% (low profitability)")
    if flags:
        for f in flags:
            print(f)
    else:
        print("    None - all holdings pass fundamental screens.")

    # === 8. MOMENTUM ===
    print("\n[8] MOMENTUM ANALYSIS")
    print("-" * 50)
    print("  Fetching momentum for all holdings...")
    momentum = analyze_momentum(tickers_with_values, settings)
    print(f"  {'Ticker':<8} {'6M Mom':>8} {'1M Mom':>8} {'>50MA':>6} {'>200MA':>7} {'Aligned':>8}")
    print(f"  {'------':<8} {'-----':>8} {'-----':>8} {'----':>6} {'-----':>7} {'-------':>8}")
    bullish = 0
    bearish = 0
    for ticker in tickers_with_values:
        md = momentum.get(ticker)
        if md:
            m6_str = format_pct(md["momentum_6m"])
            m1_str = format_pct(md["momentum_1m"])
            ma50 = "Yes" if md["above_50ma"] else "No"
            ma200 = "Yes" if md["above_200ma"] else "No"
            aligned = "Yes" if md["trend_aligned"] else "No"
            print(f"  {ticker:<8} {m6_str:>8} {m1_str:>8} {ma50:>6} {ma200:>7} {aligned:>8}")
            if md["trend_aligned"]:
                bullish += 1
            elif not md["above_200ma"]:
                bearish += 1
        else:
            print(f"  {ticker:<8} {'N/A':>8} {'N/A':>8} {'N/A':>6} {'N/A':>7} {'N/A':>8}")

    print(f"\n  Summary: {bullish} aligned (bullish), {bearish} below 200MA (bearish)")

    # === 9. OPTIONS INTELLIGENCE ===
    print("\n[9] OPTIONS INTELLIGENCE")
    print("-" * 50)
    print("  Fetching options data for individual stocks...")
    options = analyze_options(stock_tickers)
    print(f"  {'Ticker':<8} {'P/C Ratio':>10} {'IV Rank':>8} {'Signal':>10} {'Score':>6}")
    print(f"  {'------':<8} {'---------':>10} {'------':>8} {'------':>10} {'-----':>6}")
    for ticker in stock_tickers:
        od = options.get(ticker)
        if od:
            pc_val = od.put_call_oi_ratio
            pc = f"{pc_val:.2f}" if pc_val else "N/A"
            iv = f"{od.iv_skew:.2f}" if od.iv_skew is not None else "N/A"
            sc = format_score(od.options_score)
            if pc_val:
                if pc_val < settings.options_bullish_pc_threshold:
                    signal = "Bullish"
                elif pc_val > settings.options_bearish_pc_threshold:
                    signal = "Bearish"
                else:
                    signal = "Neutral"
            else:
                signal = "N/A"
            print(f"  {ticker:<8} {pc:>10} {iv:>8} {signal:>10} {sc:>6}")

    # === 10. SUMMARY & RECOMMENDATIONS ===
    print("\n" + "=" * 70)
    print("  SUMMARY & RECOMMENDATIONS")
    print("=" * 70)

    # Count ETFs vs stocks
    etf_value = sum(values.get(t, 0) for t in etfs if t in values)
    stock_value = total_value - etf_value
    print(f"\n  Asset Mix:")
    print(f"    ETFs:   ${etf_value:>10,.2f} ({etf_value/total_value*100:.1f}%)")
    print(f"    Stocks: ${stock_value:>10,.2f} ({stock_value/total_value*100:.1f}%)")
    print(f"    Total:  ${total_value:>10,.2f}")

    # Identify biggest winners and losers by momentum
    print(f"\n  Top momentum (6M):")
    sorted_by_mom = sorted(
        [(t, momentum[t]["momentum_6m"]) for t in tickers_with_values if t in momentum and momentum[t].get("momentum_6m") is not None],
        key=lambda x: -x[1]
    )
    for t, m in sorted_by_mom[:5]:
        print(f"    {t:<8} {m*100:+.1f}%")

    print(f"\n  Worst momentum (6M):")
    for t, m in sorted_by_mom[-5:]:
        print(f"    {t:<8} {m*100:+.1f}%")

    # Holdings that would pass STEEX screening
    print(f"\n  Would pass STEEX entry screen (6M>{settings.momentum_min_return*100:.0f}%, 1M>5%, trend aligned):")
    passers = []
    for t in stock_tickers:
        md = momentum.get(t)
        if md and md.get("momentum_6m") is not None:
            if (md["momentum_6m"] >= settings.momentum_min_return
                    and md.get("momentum_1m", 0) >= 0.05
                    and md["trend_aligned"]):
                passers.append(t)
    if passers:
        print(f"    {', '.join(passers)}")
    else:
        print(f"    None currently pass all momentum criteria.")

    # Holdings that STEEX would flag for exit
    print(f"\n  STEEX exit flags:")
    exit_flags = []
    for t in tickers_with_values:
        md = momentum.get(t)
        if md:
            if not md.get("above_200ma", True):
                exit_flags.append(f"    {t}: Below 200-day MA")
            elif not md.get("above_50ma", True) and md.get("momentum_1m", 0) < -0.05:
                exit_flags.append(f"    {t}: Below 50MA with negative 1M momentum")
    if exit_flags:
        for ef in exit_flags:
            print(ef)
    else:
        print("    No exit signals triggered.")

    print()


if __name__ == "__main__":
    main()
