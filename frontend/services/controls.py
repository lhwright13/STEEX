"""ControlsMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class ControlsMixin:
    def get_controls(self) -> Dict[str, Any]:
        """Current kill-switch state (trading_armed, event_armed)."""
        from src.strategy.control import get_controls
        return get_controls(self.data_dir)

    def set_controls(self, trading_armed=None, event_armed=None) -> Dict[str, Any]:
        """Update kill-switch flags and persist."""
        from src.strategy.control import set_controls
        return set_controls(
            self.data_dir,
            trading_armed=trading_armed,
            event_armed=event_armed,
            updated_at=datetime.utcnow().isoformat() + "Z",
        )
