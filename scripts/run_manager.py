#!/usr/bin/env python3
"""CLI entry point for the QuantManager trading orchestrator.

Usage:
    python scripts/run_manager.py                    # Default: pre_market
    python scripts/run_manager.py pre_market         # Morning routine
    python scripts/run_manager.py monitor            # Midday check
    python scripts/run_manager.py post_market        # End of day
    python scripts/run_manager.py full               # All three
    python scripts/run_manager.py --portfolio 50000  # Set portfolio value
    python scripts/run_manager.py --dry-run          # Show plan without executing
    python scripts/run_manager.py --yes              # Auto-confirm entries
    python scripts/run_manager.py --paper            # Enable broker (paper trading)
    python scripts/run_manager.py --live             # Enable broker (LIVE trading)
    python scripts/run_manager.py --no-broker        # Force simulation mode
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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
        choices=["pre_market", "monitor", "post_market", "full"],
        help="Operating mode (default: pre_market)",
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

    manager = QuantManager(settings=settings)

    if args.mode == "pre_market":
        manager.run_pre_market(
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )
    elif args.mode == "monitor":
        manager.run_monitor(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "post_market":
        manager.run_post_market(
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    elif args.mode == "full":
        manager.run_full_cycle(
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
