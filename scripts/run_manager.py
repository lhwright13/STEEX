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

    args = parser.parse_args()

    settings = get_settings()

    if args.portfolio is not None:
        settings.manager_portfolio_value = args.portfolio

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
