"""Modal serverless GPU adapter — the only fully automated platform."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict

from .base import PlatformAdapter, PlatformConfig

logger = logging.getLogger(__name__)

MODAL_WORKER = str(Path(__file__).parent / "modal_worker.py")


class ModalAdapter(PlatformAdapter):
    """Modal adapter — dispatches training via `modal run`."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config)

    def can_auto_dispatch(self) -> bool:
        return True

    @property
    def gpu_tier(self) -> int:
        return 3  # A100

    def quota_remaining(self, usage: Dict) -> float:
        budget = self.config.monthly_budget_dollars or 30.0
        used = usage.get("modal_dollars_this_month", 0)
        remaining_dollars = max(0, budget - used)
        # A100 ~$3.50/hr
        return remaining_dollars / 3.50

    def dispatch(self, training_config: Dict) -> str:
        """Launch training on Modal via CLI."""
        hub_repo = training_config["hub_repo"]
        dataset_repo = training_config["dataset_repo"]
        max_steps = training_config.get("max_steps_per_session", 500)

        logger.info(f"Dispatching to Modal (A100, max {max_steps} steps)...")

        try:
            result = subprocess.run(
                [
                    "modal", "run", MODAL_WORKER,
                    "--hub-repo", hub_repo,
                    "--dataset-repo", dataset_repo,
                    "--max-steps", str(max_steps),
                ],
                capture_output=True,
                text=True,
                timeout=int(self.config.max_session_hours * 3600),
            )

            if result.returncode == 0:
                logger.info(f"Modal session completed:\n{result.stdout[-500:]}")
                return "modal:completed"
            else:
                logger.error(f"Modal failed: {result.stderr[-500:]}")
                return f"modal:error:{result.stderr[-200:]}"

        except FileNotFoundError:
            logger.error("Modal CLI not installed. Run: pip install modal")
            return "modal:error:cli_not_installed"
        except subprocess.TimeoutExpired:
            logger.warning("Modal session timed out (may still be running)")
            return "modal:timeout"
