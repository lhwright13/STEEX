"""UserUpdatesMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class UserUpdatesMixin:
    def get_user_updates(
        self,
        limit: int = 50,
        types: Optional[List[str]] = None,
        day: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Recent user_updates (newest-first), optionally filtered by type/day."""
        from src.notify import user_updates as uu
        records = uu.read_updates(self.data_dir, limit=limit, types=types, day=day)
        return {
            "updates": [r.model_dump() for r in records],
            "count": len(records),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_user_update(self, update_id: str) -> Optional[Dict[str, Any]]:
        """A single update by id (for the clickable detail view)."""
        from src.notify import user_updates as uu
        rec = uu.get_update(self.data_dir, update_id)
        return rec.model_dump() if rec else None
