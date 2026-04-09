"""Base class for GPU platform adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PlatformConfig:
    """Configuration for a GPU platform."""

    name: str
    enabled: bool = True
    gpu: str = "T4"
    priority: int = 1
    phase: str = "any"  # "early", "late", or "any"

    # Quotas (set the relevant one per platform)
    weekly_hours: Optional[float] = None
    monthly_hours: Optional[float] = None
    daily_hours: Optional[float] = None
    monthly_budget_dollars: Optional[float] = None

    max_session_hours: float = 6.0
    notebook_url: Optional[str] = None


@dataclass
class SessionResult:
    """Result from a completed training session."""

    platform: str
    steps_completed: int
    final_loss: float
    duration_minutes: float
    checkpoint_id: str
    gpu_name: str = ""
    error: Optional[str] = None
    timestamp: str = ""


class PlatformAdapter(ABC):
    """Base class for GPU platform adapters."""

    def __init__(self, config: PlatformConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    def quota_remaining(self, usage: Dict) -> float:
        """Return estimated remaining GPU hours this period."""

    @abstractmethod
    def can_auto_dispatch(self) -> bool:
        """Whether this platform can be started programmatically."""

    @abstractmethod
    def dispatch(self, training_config: Dict) -> str:
        """Start a training session.

        Returns:
            Session ID (auto) or instruction URL (manual)
        """

    @property
    @abstractmethod
    def gpu_tier(self) -> int:
        """GPU quality tier (higher=better). Used for scheduling."""

    def estimated_steps_per_hour(self) -> float:
        """Estimated training throughput for planning."""
        # Rough estimates for LFM2.5-1.2B with LoRA, batch=2, grad_accum=8
        tiers = {1: 180, 2: 250, 3: 500}  # T4, A10G, A100
        return tiers.get(self.gpu_tier, 180)
