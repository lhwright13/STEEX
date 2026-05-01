#!/usr/bin/env python3
"""CLI entry point for the QuantManager trading orchestrator.

Usage:
    python scripts/run_manager.py screen             # Pre-open screening (no entries)
    python scripts/run_manager.py enter              # Post-open entry execution
    python scripts/run_manager.py monitor            # Midday risk check
    python scripts/run_manager.py stop_sync          # Pre-close stop sync
    python scripts/run_manager.py post_market        # End of day wrap-up
    python scripts/run_manager.py learning           # Self-learning loop
    python scripts/run_manager.py pre_market         # Legacy combined (screen + enter)
    python scripts/run_manager.py full               # All three legacy modes
    python scripts/run_manager.py test_roundtrip --paper --agent --ticker AAPL --amount 100
                                                      # Verify agent->MCP->broker buy/sell roundtrip
    python scripts/run_manager.py --dry-run          # Show plan without executing
    python scripts/run_manager.py --yes              # Auto-confirm entries
    python scripts/run_manager.py --paper            # Enable broker (paper trading)
    python scripts/run_manager.py --live             # Enable broker (LIVE trading)
    python scripts/run_manager.py --no-broker        # Force simulation mode
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path so `python scripts/run_manager.py`
# can resolve `src.*` and `config.*` packages without relying on cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

# Disable yfinance's internal SQLite tz cache to avoid "unable to open database
# file" errors when multiple threads hit ~/Library/Caches/py-yfinance/tkr-tz.db.
# STEEX has its own L2 cache (data/cache.db) so this is redundant.
import tempfile, yfinance as yf
yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_tz_"))

from config.settings import get_settings
from src.strategy.manager import QuantManager


def main():
    parser = argparse.ArgumentParser(
        description="STEEX QuantManager - Automated Trading Orchestrator"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="pre_market",
        choices=[
            "pre_market", "screen", "enter", "monitor",
            "stop_sync", "post_market", "learning", "full",
            "test_roundtrip",
        ],
        help="Operating mode (default: pre_market)",
    )
    parser.add_argument(
        "--ticker",
        default="AAPL",
        help="Ticker for test_roundtrip mode (default: AAPL)",
    )
    parser.add_argument(
        "--amount",
        type=float,
        default=100.0,
        help="Dollar amount for test_roundtrip mode (default: 100.0; capped at $1000)",
    )
    parser.add_argument(
        "--portfolio",
        type=float,
        default=None,
        help="Portfolio total value for position sizing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without executing",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Auto-confirm all entry prompts",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Detailed output",
    )

    parser.add_argument(
        "--agent",
        action="store_true",
        help="Use Claude AI agent mode instead of deterministic pipeline",
    )

    broker_group = parser.add_mutually_exclusive_group()
    broker_group.add_argument(
        "--paper",
        action="store_true",
        help="Enable broker with paper trading",
    )
    broker_group.add_argument(
        "--live",
        action="store_true",
        help="Enable broker with LIVE trading",
    )
    broker_group.add_argument(
        "--no-broker",
        action="store_true",
        help="Force simulation mode (no broker)",
    )

    args = parser.parse_args()

    settings = get_settings()

    if args.portfolio is not None:
        settings.manager_portfolio_value = args.portfolio

    if args.paper:
        settings.broker_enabled = True
        settings.broker_paper = True
    elif args.live:
        settings.broker_enabled = True
        settings.broker_paper = False
    elif args.no_broker:
        settings.broker_enabled = False

    # Agent mode: route to Claude AI orchestrator
    if args.agent:
        from src.agents.orchestrator import Orchestrator

        orchestrator = Orchestrator(
            settings=settings,
            paper=args.paper,
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )
        if args.mode == "test_roundtrip":
            orchestrator.run_test_roundtrip(args.ticker, args.amount)
        else:
            orchestrator.run_mode(args.mode)
        return

    # Deterministic mode: use QuantManager directly
    manager = QuantManager(settings=settings)

    if args.mode == "pre_market":
        manager.run_pre_market(
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )
    elif args.mode == "screen":
        manager.run_screen(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "enter":
        manager.run_enter(
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )
    elif args.mode == "monitor":
        manager.run_monitor(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "stop_sync":
        manager.run_stop_sync(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "post_market":
        manager.run_post_market(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "learning":
        manager.run_learning(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "full":
        manager.run_full_cycle(
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )
    elif args.mode == "test_roundtrip":
        manager.run_test_roundtrip(
            ticker=args.ticker,
            amount_usd=args.amount,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
