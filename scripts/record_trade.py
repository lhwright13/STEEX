#!/usr/bin/env python3
"""
Record trades - Add positions and record exits.

Usage:
    # Add a new position
    python scripts/record_trade.py add AAPL --price 185.50 --shares 27 --score 65

    # Record an exit
    python scripts/record_trade.py exit AAPL --price 195.00 --reason trailing_stop

    # List current positions
    python scripts/record_trade.py list

    # Show position details
    python scripts/record_trade.py show AAPL
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from config.settings import get_settings
from src.data.price import PriceProvider
from src.portfolio.positions import PositionManager
from src.portfolio.tracker import TradeTracker

console = Console()
settings = get_settings()


def add_position(args):
    """Add a new position."""
    pm = PositionManager(settings)

    # Check if already have position
    if pm.has_position(args.ticker):
        console.print(f"[red]Already have a position in {args.ticker}[/red]")
        return

    # Get price if not provided
    if args.price:
        price = args.price
    else:
        pp = PriceProvider()
        price = pp.get_latest_price(args.ticker)
        if price is None:
            console.print(f"[red]Could not fetch price for {args.ticker}. Use --price to specify.[/red]")
            return
        console.print(f"Using current price: ${price:.2f}")

    # Calculate shares if not provided
    if args.shares:
        shares = args.shares
    elif args.dollars:
        shares = int(args.dollars / price)
        console.print(f"Calculated shares: {shares} (${args.dollars:.0f} / ${price:.2f})")
    else:
        console.print("[red]Must provide --shares or --dollars[/red]")
        return

    # Parse reasons
    reasons = args.reasons.split(",") if args.reasons else []

    # Add the position
    position = pm.add_position(
        ticker=args.ticker.upper(),
        entry_price=price,
        shares=shares,
        score=args.score or 0,
        reasons=reasons,
    )

    console.print(f"\n[green]Position added:[/green]")
    console.print(f"  Ticker: {position.ticker}")
    console.print(f"  Entry: ${position.entry_price:.2f}")
    console.print(f"  Shares: {position.shares}")
    console.print(f"  Cost: ${position.cost_basis:.2f}")
    console.print(f"  Stop: ${position.current_stop:.2f} ({settings.initial_stop_pct*100:.0f}%)")


def exit_position(args):
    """Record an exit."""
    pm = PositionManager(settings)
    tt = TradeTracker(settings)

    # Get position
    position = pm.get_position(args.ticker.upper())
    if position is None:
        console.print(f"[red]No position found for {args.ticker}[/red]")
        return

    # Get exit price
    if args.price:
        exit_price = args.price
    else:
        pp = PriceProvider()
        exit_price = pp.get_latest_price(args.ticker)
        if exit_price is None:
            console.print(f"[red]Could not fetch price. Use --price to specify.[/red]")
            return
        console.print(f"Using current price: ${exit_price:.2f}")

    # Record the trade
    trade = tt.record_trade(
        ticker=position.ticker,
        entry_date=position.entry_datetime,
        exit_date=datetime.now(),
        entry_price=position.entry_price,
        exit_price=exit_price,
        shares=position.shares,
        exit_reason=args.reason,
        score=position.score,
        reasons=position.reasons,
    )

    # Remove position
    pm.remove_position(args.ticker.upper())

    # Display result
    pnl_color = "green" if trade.pnl_dollars > 0 else "red"
    console.print(f"\n[{pnl_color}]Trade recorded:[/{pnl_color}]")
    console.print(f"  Ticker: {trade.ticker}")
    console.print(f"  Entry: ${trade.entry_price:.2f} -> Exit: ${trade.exit_price:.2f}")
    console.print(f"  P&L: [{pnl_color}]${trade.pnl_dollars:+,.2f} ({trade.pnl_pct*100:+.1f}%)[/{pnl_color}]")
    console.print(f"  Days held: {trade.hold_days}")
    console.print(f"  Exit reason: {trade.exit_reason}")


def list_positions(args):
    """List all positions."""
    pm = PositionManager(settings)
    positions = pm.get_all_positions()

    if not positions:
        console.print("[yellow]No open positions[/yellow]")
        return

    pp = PriceProvider()

    table = Table(title=f"Open Positions ({len(positions)})")
    table.add_column("Ticker", style="bold")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Shares", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("P&L %", justify="right")
    table.add_column("Days", justify="right")
    table.add_column("Stop", justify="right")

    total_cost = 0
    total_value = 0

    for pos in positions:
        price = pp.get_latest_price(pos.ticker) or pos.entry_price
        pnl = pos.calculate_pnl(price)
        days = (datetime.now() - pos.entry_datetime).days

        pnl_color = "green" if pnl["pnl_dollars"] > 0 else "red"

        total_cost += pos.cost_basis
        total_value += pnl["current_value"]

        table.add_row(
            pos.ticker,
            f"${pos.entry_price:.2f}",
            f"${price:.2f}",
            str(int(pos.shares)),
            f"[{pnl_color}]${pnl['pnl_dollars']:+,.0f}[/{pnl_color}]",
            f"[{pnl_color}]{pnl['pnl_pct']*100:+.1f}%[/{pnl_color}]",
            str(days),
            f"${pos.current_stop:.2f}",
        )

    console.print(table)

    total_pnl = total_value - total_cost
    pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    pnl_color = "green" if total_pnl > 0 else "red"

    console.print(f"\nTotal Cost: ${total_cost:,.2f}")
    console.print(f"Total Value: ${total_value:,.2f}")
    console.print(f"Total P&L: [{pnl_color}]${total_pnl:+,.2f} ({pnl_pct:+.1f}%)[/{pnl_color}]")


def show_position(args):
    """Show details for a single position."""
    pm = PositionManager(settings)
    position = pm.get_position(args.ticker.upper())

    if position is None:
        console.print(f"[red]No position found for {args.ticker}[/red]")
        return

    pp = PriceProvider()
    price = pp.get_latest_price(position.ticker) or position.entry_price
    pnl = position.calculate_pnl(price)
    days = (datetime.now() - position.entry_datetime).days

    console.print(f"\n[bold]{position.ticker}[/bold]")
    console.print(f"  Entry Date: {position.entry_date[:10]}")
    console.print(f"  Entry Price: ${position.entry_price:.2f}")
    console.print(f"  Current Price: ${price:.2f}")
    console.print(f"  Shares: {int(position.shares)}")
    console.print(f"  Cost Basis: ${position.cost_basis:,.2f}")
    console.print(f"  Current Value: ${pnl['current_value']:,.2f}")

    pnl_color = "green" if pnl["pnl_dollars"] > 0 else "red"
    console.print(f"  P&L: [{pnl_color}]${pnl['pnl_dollars']:+,.2f} ({pnl['pnl_pct']*100:+.1f}%)[/{pnl_color}]")

    console.print(f"  Days Held: {days}")
    console.print(f"  High Since Entry: ${position.high_since_entry:.2f}")
    console.print(f"  Current Stop: ${position.current_stop:.2f}")
    console.print(f"  Score: {position.score}")

    if position.reasons:
        console.print(f"  Reasons: {', '.join(position.reasons)}")


def main():
    parser = argparse.ArgumentParser(description="Record trades")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Add position
    add_parser = subparsers.add_parser("add", help="Add a new position")
    add_parser.add_argument("ticker", help="Stock ticker")
    add_parser.add_argument("--price", type=float, help="Entry price (default: current)")
    add_parser.add_argument("--shares", type=int, help="Number of shares")
    add_parser.add_argument("--dollars", type=float, help="Dollar amount (calculates shares)")
    add_parser.add_argument("--score", type=float, help="Strategy score")
    add_parser.add_argument("--reasons", help="Entry reasons (comma-separated)")

    # Exit position
    exit_parser = subparsers.add_parser("exit", help="Record an exit")
    exit_parser.add_argument("ticker", help="Stock ticker")
    exit_parser.add_argument("--price", type=float, help="Exit price (default: current)")
    exit_parser.add_argument("--reason", required=True,
                             choices=["stop_loss", "trailing_stop", "dead_money", "max_hold",
                                      "vix_spike", "below_ma", "manual"],
                             help="Exit reason")

    # List positions
    subparsers.add_parser("list", help="List all positions")

    # Show position
    show_parser = subparsers.add_parser("show", help="Show position details")
    show_parser.add_argument("ticker", help="Stock ticker")

    args = parser.parse_args()

    if args.command == "add":
        add_position(args)
    elif args.command == "exit":
        exit_position(args)
    elif args.command == "list":
        list_positions(args)
    elif args.command == "show":
        show_position(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
