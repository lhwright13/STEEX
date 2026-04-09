#!/usr/bin/env python3
"""STEEX LLM Training Pipeline CLI.

Near-continuous fine-tuning of LFM2.5-1.2B across free GPU platforms.

Usage:
    venv/bin/python scripts/run_training_pipeline.py status
    venv/bin/python scripts/run_training_pipeline.py init
    venv/bin/python scripts/run_training_pipeline.py dispatch
    venv/bin/python scripts/run_training_pipeline.py monitor --single-check
    venv/bin/python scripts/run_training_pipeline.py sync-data
    venv/bin/python scripts/run_training_pipeline.py export
    venv/bin/python scripts/run_training_pipeline.py validate
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.pipeline import TrainingPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="STEEX LLM Training Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First time setup:
    %(prog)s init

  Check training status:
    %(prog)s status

  Trigger next training session:
    %(prog)s dispatch

  Run one poll cycle (for cron):
    %(prog)s monitor --single-check

  Upload dataset to Hub:
    %(prog)s sync-data

  Export best model to GGUF + Ollama:
    %(prog)s export
""",
    )

    parser.add_argument(
        "--config",
        default="config/llm_pipeline.yaml",
        help="Path to pipeline config (default: config/llm_pipeline.yaml)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show training status dashboard")
    sub.add_parser("init", help="First-time setup: create Hub repos, upload data")
    sub.add_parser("dispatch", help="Trigger next training session")

    monitor_p = sub.add_parser("monitor", help="Monitor training loop")
    monitor_p.add_argument(
        "--single-check",
        action="store_true",
        help="Run one poll cycle and exit (for cron)",
    )
    monitor_p.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Poll interval in minutes (default: 15)",
    )

    sub.add_parser("sync-data", help="Upload dataset to Hub")
    sub.add_parser("export", help="Export best checkpoint to GGUF + Ollama")
    sub.add_parser("validate", help="Validate latest checkpoint")

    args = parser.parse_args()
    config_path = Path(args.config)

    if not config_path.exists():
        print(f"Config not found: {config_path}")
        print("Create config/llm_pipeline.yaml first (see config/llm_pipeline.yaml.example)")
        sys.exit(1)

    pipeline = TrainingPipeline(config_path)

    if args.command == "status":
        print(pipeline.status())

    elif args.command == "init":
        pipeline.init()
        print("\nPipeline initialized. Next steps:")
        print("  1. Edit config/llm_pipeline.yaml with your HF username")
        print("  2. Run: venv/bin/python scripts/run_training_pipeline.py sync-data")
        print("  3. Run: venv/bin/python scripts/run_training_pipeline.py dispatch")

    elif args.command == "dispatch":
        result = pipeline.dispatch()
        print(result)

    elif args.command == "monitor":
        if args.single_check:
            result = pipeline.run_check()
            print(result)
        else:
            print(f"Monitoring every {args.interval} minutes (Ctrl+C to stop)")
            import time
            while True:
                try:
                    result = pipeline.run_check()
                    from datetime import datetime
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {result}")
                    time.sleep(args.interval * 60)
                except KeyboardInterrupt:
                    print("\nStopped.")
                    break

    elif args.command == "sync-data":
        success = pipeline.sync_data()
        if success:
            print("Dataset synced to Hub")
        else:
            print("Dataset sync failed")
            sys.exit(1)

    elif args.command == "export":
        path = pipeline.export_best()
        if path:
            model_name = pipeline.config["export"]["ollama_model_name"]
            print(f"\nExported to: {path}")
            print(f"Run locally: ollama run {model_name}")
        else:
            print("No checkpoint to export. Train first.")
            sys.exit(1)

    elif args.command == "validate":
        hub_state = pipeline.hub.get_training_state()
        if not hub_state:
            print("No training state on Hub")
            sys.exit(1)
        loss = hub_state.get("final_loss", 0)
        result = pipeline.validator.validate(
            current_loss=loss,
            best_loss=pipeline.state.get("best_loss"),
            loss_history=pipeline.state.get("loss_history", []),
        )
        print(f"Loss: {loss:.4f}")
        print(f"Valid: {result.valid}")
        print(f"Promoted: {result.promoted}")
        print(f"Converged: {result.converged}")
        print(f"Reason: {result.reason}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
