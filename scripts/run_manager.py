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
    python scripts/run_manager.py train              # PySR walk-forward training
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

from dotenv import load_dotenv
load_dotenv()

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
            "stop_sync", "post_market", "learning", "full", "train",
        ],
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
    elif args.mode == "train":
        from src.ml.trainer import PySRTrainer
        from src.ml.dataset import DatasetBuilder

        print("Building training dataset...")
        builder = DatasetBuilder(settings=settings)
        dataset = builder.build_dataset()
        if dataset is None or dataset.X.empty:
            print("No training data available. Run the pipeline first to populate data.")
            sys.exit(1)

        print(f"Dataset: {len(dataset.X)} samples, {len(dataset.feature_names)} features")
        trainer = PySRTrainer(settings=settings)
        print("Starting PySR walk-forward training...")
        result = trainer.walk_forward_train(dataset)
        print(f"Training complete: {len(result.folds)} folds")
        for fold in result.folds:
            print(f"  Fold {fold.fold_number}: {len(fold.equations)} equations discovered")
    elif args.mode == "full":
        manager.run_full_cycle(
            dry_run=args.dry_run,
            auto_confirm=args.yes,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
