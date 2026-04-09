"""Adapters for notebook-based platforms (Colab, Kaggle, SageMaker, Lightning).

These platforms cannot be auto-dispatched — they require a human to click "Run".
The adapters generate ready-to-use URLs and send macOS notifications.
"""

import logging
import subprocess
from typing import Dict

from .base import PlatformAdapter, PlatformConfig

logger = logging.getLogger(__name__)


def _notify_macos(title: str, message: str):
    """Send a macOS notification."""
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{message}" with title "{title}" sound name "Glass"',
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception as e:
        logger.debug(f"Notification failed: {e}")


class ColabAdapter(PlatformAdapter):
    """Google Colab adapter."""

    NOTEBOOK_URL = (
        "https://colab.research.google.com/github/{github_repo}"
        "/blob/main/notebooks/llm/colab_train.ipynb"
    )

    def can_auto_dispatch(self) -> bool:
        return False

    @property
    def gpu_tier(self) -> int:
        return 1  # T4

    def quota_remaining(self, usage: Dict) -> float:
        weekly_limit = self.config.weekly_hours or 25
        used = usage.get("colab_hours_this_week", 0)
        return max(0, weekly_limit - used)

    def dispatch(self, training_config: Dict) -> str:
        github_repo = training_config.get("github_repo", "")
        url = self.config.notebook_url or self.NOTEBOOK_URL.format(github_repo=github_repo)

        est_hours = min(
            self.config.max_session_hours,
            self.quota_remaining(training_config.get("usage", {})),
        )

        msg = f"~{est_hours:.0f}h session on T4. Config auto-loaded from Hub."
        _notify_macos("STEEX Training: Open Colab", msg)
        logger.info(f"Colab dispatch: {url}")
        logger.info(f"  {msg}")

        return url


class KaggleAdapter(PlatformAdapter):
    """Kaggle Notebooks adapter."""

    def can_auto_dispatch(self) -> bool:
        return False

    @property
    def gpu_tier(self) -> int:
        return 1  # P100/T4

    def quota_remaining(self, usage: Dict) -> float:
        weekly_limit = self.config.weekly_hours or 30
        used = usage.get("kaggle_hours_this_week", 0)
        return max(0, weekly_limit - used)

    def dispatch(self, training_config: Dict) -> str:
        url = self.config.notebook_url or "https://www.kaggle.com/code"

        est_hours = min(
            self.config.max_session_hours,
            self.quota_remaining(training_config.get("usage", {})),
        )

        msg = f"~{est_hours:.0f}h session on T4. Upload notebook and click Run."
        _notify_macos("STEEX Training: Open Kaggle", msg)
        logger.info(f"Kaggle dispatch: {url}")
        logger.info(f"  {msg}")

        return url


class SageMakerAdapter(PlatformAdapter):
    """Amazon SageMaker Studio Lab adapter."""

    def can_auto_dispatch(self) -> bool:
        return False

    @property
    def gpu_tier(self) -> int:
        return 1  # T4

    def quota_remaining(self, usage: Dict) -> float:
        daily_limit = self.config.daily_hours or 4
        used = usage.get("sagemaker_hours_today", 0)
        return max(0, daily_limit - used)

    def dispatch(self, training_config: Dict) -> str:
        url = self.config.notebook_url or "https://studiolab.sagemaker.aws"

        msg = "4h GPU session. Persistent storage — packages survive restarts."
        _notify_macos("STEEX Training: Open SageMaker", msg)
        logger.info(f"SageMaker dispatch: {url}")
        logger.info(f"  {msg}")

        return url


class LightningAdapter(PlatformAdapter):
    """Lightning AI Studios adapter."""

    def can_auto_dispatch(self) -> bool:
        return False

    @property
    def gpu_tier(self) -> int:
        return 2  # A10G

    def quota_remaining(self, usage: Dict) -> float:
        monthly_limit = self.config.monthly_hours or 22
        used = usage.get("lightning_hours_this_month", 0)
        return max(0, monthly_limit - used)

    def dispatch(self, training_config: Dict) -> str:
        url = self.config.notebook_url or "https://lightning.ai"

        remaining = self.quota_remaining(training_config.get("usage", {}))
        msg = f"A10G 24GB. {remaining:.0f}h remaining this month."
        _notify_macos("STEEX Training: Open Lightning AI", msg)
        logger.info(f"Lightning dispatch: {url}")
        logger.info(f"  {msg}")

        return url
