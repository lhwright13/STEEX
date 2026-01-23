#!/usr/bin/env python3
"""
Morning Trading Routine - Pre-market preparation script.

Run this before market open to:
1. Fetch fresh insider data
2. Check VIX levels
3. Review current positions
4. Run screening pipeline
5. Generate actionable trade list

Usage:
    python scripts/morning_routine.py
    python scripts/morning_routine.py --live  # For actual trading (confirms actions)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config.settings import get_settings
from src.data.price import PriceProvider
from src.data.vix import VixProvider
from src.portfolio.positions import PositionManager
from src.portfolio.tracker import TradeTracker
from src.strategy.screener import StockScreener
from src.strategy.ranking import StockRanker

console = Console()
settings = get_settings()

# File paths
PROJECT_ROOT = Path(__file__).parent.parent
WATCHLIST_FILE = PROJECT_ROOT / "data" / "watchlist.json"
MORNING_REPORT_FILE = PROJECT_ROOT / "data" / "morning_report.json"


def check_market_status():
    """Check if market is open or in pre-market."""
    from dashboard.services.cache import get_market_status
    return get_market_status()


def fetch_fresh_insider_data():
    """Fetch latest insider filings."""
    console.print("\n[bold]1. FETCHING INSIDER DATA[/bold]")

    try:
        from src.sec.scanners.insider import InsiderScanner
        scanner = InsiderScanner()

        with console.status("Fetching recent Form 4 filings..."):
            transactions = scanner.scan(days_back=7, max_filings=200, verbose=False)

        console.print(f"   Found [green]{len(transactions)}[/green] insider transactions in last 7 days")

        # Summarize by type
        purchases = [t for t in transactions if t.is_purchase]
        console.print(f"   Purchases: [cyan]{len(purchases)}[/cyan]")

        return transactions

    except Exception as e:
        console.print(f"   [red]Error fetching insider data: {e}[/red]")
        return []


def check_vix():
    """Check current VIX level and implications."""
    console.print("\n[bold]2. VIX CHECK[/bold]")

    vix = VixProvider()
    vix_level = vix.get_current()

    if vix_level is None:
        console.print("   [red]Could not fetch VIX data[/red]")
        return None

    # Determine status
    if vix_level > settings.vix_exit_level:
        status = "SPIKE"
        color = "bold red"
        action = "EXIT 50% of positions immediately"
    elif vix_level > settings.vix_caution_level:
        status = "ELEVATED"
        color = "bold yellow"
        action = "Tighten stops, reduce position sizes"
    elif vix_level > 20:
        status = "NORMAL"
        color = "white"
        action = "Normal trading"
    else:
        status = "LOW"
        color = "green"
        action = "Favorable conditions"

    console.print(f"   VIX Level: [{color}]{vix_level:.1f} - {status}[/{color}]")
    console.print(f"   Action: {action}")

    return {"level": vix_level, "status": status, "action": action}


def review_positions():
    """Review current positions and check for exits."""
    console.print("\n[bold]3. POSITION REVIEW[/bold]")

    pm = PositionManager(settings)
    positions = pm.get_all_positions()

    if not positions:
        console.print("   No open positions")
        return {"count": 0, "positions": [], "alerts": []}

    console.print(f"   Open positions: [cyan]{len(positions)}[/cyan]")

    # Get current prices
    price_provider = PriceProvider()
    alerts = []
    position_data = []

    for pos in positions:
        try:
            current_price = price_provider.get_latest_price(pos.ticker)
            if current_price is None:
                current_price = pos.entry_price

            pnl = pos.calculate_pnl(current_price)
            days_held = (datetime.now() - pos.entry_datetime).days
            stop_distance = (current_price - pos.current_stop) / current_price

            position_data.append({
                "ticker": pos.ticker,
                "entry": pos.entry_price,
                "current": current_price,
                "pnl_pct": pnl["pnl_pct"],
                "pnl_dollars": pnl["pnl_dollars"],
                "days_held": days_held,
                "stop": pos.current_stop,
                "stop_distance": stop_distance,
            })

            # Check for alerts
            if current_price < pos.current_stop:
                alerts.append({
                    "type": "STOP_HIT",
                    "ticker": pos.ticker,
                    "message": f"{pos.ticker} below stop! Current: ${current_price:.2f}, Stop: ${pos.current_stop:.2f}",
                })
            elif stop_distance < 0.02:
                alerts.append({
                    "type": "NEAR_STOP",
                    "ticker": pos.ticker,
                    "message": f"{pos.ticker} within 2% of stop",
                })
            elif days_held >= settings.dead_money_days and current_price < pos.entry_price:
                alerts.append({
                    "type": "DEAD_MONEY",
                    "ticker": pos.ticker,
                    "message": f"{pos.ticker} below entry for {days_held} days",
                })

        except Exception as e:
            console.print(f"   [red]Error checking {pos.ticker}: {e}[/red]")

    # Display position table
    if position_data:
        table = Table(title="Current Positions", box=box.SIMPLE)
        table.add_column("Ticker")
        table.add_column("P&L %", justify="right")
        table.add_column("P&L $", justify="right")
        table.add_column("Days", justify="right")
        table.add_column("Stop Dist", justify="right")

        for p in sorted(position_data, key=lambda x: x["pnl_pct"], reverse=True):
            pnl_color = "green" if p["pnl_pct"] > 0 else "red"
            table.add_row(
                p["ticker"],
                f"[{pnl_color}]{p['pnl_pct']*100:+.1f}%[/{pnl_color}]",
                f"[{pnl_color}]${p['pnl_dollars']:+,.0f}[/{pnl_color}]",
                str(p["days_held"]),
                f"{p['stop_distance']*100:.1f}%",
            )

        console.print(table)

    # Display alerts
    if alerts:
        console.print("\n   [bold red]ALERTS:[/bold red]")
        for alert in alerts:
            console.print(f"   - {alert['message']}")

    return {"count": len(positions), "positions": position_data, "alerts": alerts}


def run_screening():
    """Run the full screening pipeline."""
    console.print("\n[bold]4. SCREENING PIPELINE[/bold]")

    screener = StockScreener()

    with console.status("Running screening pipeline..."):
        result = screener.run_pipeline()

    console.print(f"   Universe:    {result.universe_size}")
    console.print(f"   Stage 1:     {result.stage_1_passed} passed")
    console.print(f"   Stage 2:     {result.stage_2_passed} passed")
    console.print(f"   Stage 3:     {result.stage_3_passed} passed")
    console.print(f"   Stage 4:     {result.stage_4_passed} passed")

    return result


def generate_trade_list(screening_result, vix_status, position_count):
    """Generate actionable trade recommendations."""
    console.print("\n[bold]5. TRADE RECOMMENDATIONS[/bold]")

    candidates = screening_result.final_candidates

    if not candidates:
        console.print("   [yellow]No candidates passed all screening criteria[/yellow]")
        return []

    # Rank candidates
    ranker = StockRanker()
    ranked = ranker.get_top_picks(candidates, settings.daily_picks)

    # Adjust for VIX
    if vix_status and vix_status["status"] == "ELEVATED":
        console.print("   [yellow]VIX elevated - reducing position sizes by 50%[/yellow]")
        position_size = settings.position_size_pct * 0.5
    elif vix_status and vix_status["status"] == "SPIKE":
        console.print("   [red]VIX spike - NO NEW ENTRIES recommended[/red]")
        return []
    else:
        position_size = settings.position_size_pct

    # Check position capacity
    available_slots = settings.max_positions - position_count
    if available_slots <= 0:
        console.print(f"   [yellow]At max positions ({settings.max_positions}). No new entries.[/yellow]")
        return []

    console.print(f"   Available position slots: {available_slots}")

    # Get prices and build recommendations
    price_provider = PriceProvider()
    recommendations = []

    for pick in ranked[:available_slots]:
        try:
            price = price_provider.get_latest_price(pick.ticker)
            if price is None:
                continue

            stop_price = price * (1 - settings.initial_stop_pct)

            recommendations.append({
                "ticker": pick.ticker,
                "score": pick.composite_score,
                "current_price": price,
                "stop_price": stop_price,
                "position_size_pct": position_size,
                "momentum_6m": pick.screening_result.momentum_6m,
                "insider_buyers": pick.screening_result.insider_buyers,
                "insider_value": pick.screening_result.total_insider_value,
                "reasons": pick.reasons[:3],
            })

        except Exception as e:
            console.print(f"   [red]Error processing {pick.ticker}: {e}[/red]")

    # Display recommendations
    if recommendations:
        table = Table(title="Buy Candidates", box=box.SIMPLE)
        table.add_column("Ticker", style="bold cyan")
        table.add_column("Price", justify="right")
        table.add_column("Stop", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("6M Mom", justify="right")
        table.add_column("Insiders")
        table.add_column("Reasons")

        for r in recommendations:
            table.add_row(
                r["ticker"],
                f"${r['current_price']:.2f}",
                f"${r['stop_price']:.2f}",
                f"{r['score']:.0f}",
                f"{(r['momentum_6m'] or 0)*100:.0f}%",
                f"{r['insider_buyers']} (${r['insider_value']:,.0f})",
                ", ".join(r["reasons"]),
            )

        console.print(table)

    return recommendations


def save_morning_report(vix_status, positions, recommendations, screening_result):
    """Save morning report to file."""
    report = {
        "date": datetime.now().isoformat(),
        "vix": vix_status,
        "positions": positions,
        "recommendations": recommendations,
        "screening": {
            "universe_size": screening_result.universe_size,
            "stage_1": screening_result.stage_1_passed,
            "stage_2": screening_result.stage_2_passed,
            "stage_3": screening_result.stage_3_passed,
            "stage_4": screening_result.stage_4_passed,
        },
    }

    MORNING_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MORNING_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)

    console.print(f"\n   Report saved to: {MORNING_REPORT_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Morning trading routine")
    parser.add_argument("--live", action="store_true", help="Live trading mode (confirms actions)")
    parser.add_argument("--skip-insider-fetch", action="store_true", help="Skip fetching new insider data")
    args = parser.parse_args()

    console.print(Panel.fit(
        f"[bold]STEEX Morning Routine[/bold]\n{datetime.now().strftime('%A, %B %d, %Y %H:%M')}",
        border_style="blue",
    ))

    # Check market status
    market_status = check_market_status()
    console.print(f"\nMarket Status: [bold]{market_status['status'].upper()}[/bold] ({market_status['reason']})")

    # 1. Fetch insider data (optional)
    if not args.skip_insider_fetch:
        fetch_fresh_insider_data()
    else:
        console.print("\n[bold]1. INSIDER DATA[/bold] (skipped)")

    # 2. Check VIX
    vix_status = check_vix()

    # 3. Review positions
    positions = review_positions()

    # 4. Run screening
    screening_result = run_screening()

    # 5. Generate trade list
    recommendations = generate_trade_list(
        screening_result,
        vix_status,
        positions["count"],
    )

    # Save report
    save_morning_report(vix_status, positions, recommendations, screening_result)

    # Summary
    console.print("\n" + "=" * 50)
    console.print("[bold]MORNING SUMMARY[/bold]")
    console.print("=" * 50)

    if positions["alerts"]:
        console.print(f"\n[bold red]ACTION REQUIRED: {len(positions['alerts'])} position alerts[/bold red]")

    if recommendations:
        console.print(f"\n[bold green]BUY CANDIDATES: {len(recommendations)} stocks[/bold green]")
        for r in recommendations:
            console.print(f"  - {r['ticker']} @ ${r['current_price']:.2f} (stop: ${r['stop_price']:.2f})")
    else:
        console.print("\n[yellow]No buy candidates today[/yellow]")

    if args.live:
        console.print("\n[bold]LIVE MODE[/bold] - Confirm actions in dashboard")
        console.print("Run: streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
