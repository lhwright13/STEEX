"""Knowledge persistence layer for the learning loop.

Manages three JSON files in data/learning/:
- learning_journal.json: timestamped log of all learning actions
- weight_recommendations.json: latest weight recommendations from signal research
- gaps.json: knowledge gaps flagged for user review
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import get_settings

ACTION_TYPES = {
    "postmortem_analysis",
    "alpha_decay_check",
    "signal_research",
    "weight_recommendation",
    "oos_validation",
    "config_change",
    "gap_identified",
}

GAP_TYPES = {
    "missing_data",
    "degrading_signal",
    "parameter_drift",
    "new_regime",
    "implementation_needed",
}


class LearningJournal:
    """Manages the learning knowledge base in data/learning/."""

    def __init__(self, data_dir: Optional[str] = None):
        settings = get_settings()
        base = Path(data_dir or settings.data_dir)
        self.learning_dir = base / "learning"
        self.journal_path = self.learning_dir / "learning_journal.json"
        self.weights_path = self.learning_dir / "weight_recommendations.json"
        self.gaps_path = self.learning_dir / "gaps.json"

    def _ensure_dir(self) -> None:
        self.learning_dir.mkdir(parents=True, exist_ok=True)

    def log_action(
        self,
        action_type: str,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
        source: str = "learning_loop",
    ) -> Dict:
        """Record a learning action to the journal.

        Args:
            action_type: One of ACTION_TYPES
            summary: One-line summary of what happened
            details: Optional structured data
            source: Which system generated this action

        Returns:
            The journal entry that was written
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action_type": action_type,
            "source": source,
            "summary": summary,
        }
        if details:
            entry["details"] = details

        self._ensure_dir()
        journal = self._read_json(self.journal_path, default=[])
        journal.append(entry)
        self._write_json(self.journal_path, journal)

        return entry

    def save_weight_recommendations(
        self,
        recommended_weights: Dict[str, float],
        source: str,
        validation_result: Optional[Dict] = None,
    ) -> None:
        """Save the latest weight recommendations.

        Args:
            recommended_weights: Dict of weight_name -> recommended_value
            source: Which analysis produced these (e.g. "signal_research")
            validation_result: OOS validation metrics if available
        """
        self._ensure_dir()
        data = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "recommended_weights": recommended_weights,
            "validation": validation_result,
        }
        self._write_json(self.weights_path, data)

    def get_weight_recommendations(self) -> Optional[Dict]:
        """Read the latest weight recommendations."""
        if not self.weights_path.exists():
            return None
        return self._read_json(self.weights_path)

    def flag_gap(
        self,
        gap_type: str,
        description: str,
        context: Optional[Dict] = None,
        severity: str = "medium",
    ) -> Dict:
        """Flag a knowledge gap for user review.

        Args:
            gap_type: One of GAP_TYPES
            description: What the gap is
            context: Supporting data
            severity: "low", "medium", or "high"

        Returns:
            The gap entry
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "gap_type": gap_type,
            "description": description,
            "severity": severity,
            "resolved": False,
        }
        if context:
            entry["context"] = context

        self._ensure_dir()
        gaps = self._read_json(self.gaps_path, default=[])
        gaps.append(entry)
        self._write_json(self.gaps_path, gaps)

        return entry

    def get_gaps(self, include_resolved: bool = False) -> List[Dict]:
        """Get all knowledge gaps.

        Args:
            include_resolved: If True, include gaps marked as resolved

        Returns:
            List of gap entries
        """
        if not self.gaps_path.exists():
            return []

        gaps = self._read_json(self.gaps_path, default=[])
        if not include_resolved:
            gaps = [g for g in gaps if not g.get("resolved", False)]
        return gaps

    def resolve_gap(self, index: int) -> bool:
        """Mark a gap as resolved by index.

        Args:
            index: Zero-based index into the gaps list

        Returns:
            True if the gap was found and resolved
        """
        if not self.gaps_path.exists():
            return False

        gaps = self._read_json(self.gaps_path, default=[])
        unresolved = [g for g in gaps if not g.get("resolved", False)]

        if index < 0 or index >= len(unresolved):
            return False

        # Find the actual index in the full list
        count = 0
        for i, g in enumerate(gaps):
            if not g.get("resolved", False):
                if count == index:
                    gaps[i]["resolved"] = True
                    gaps[i]["resolved_at"] = datetime.now().isoformat()
                    self._write_json(self.gaps_path, gaps)
                    return True
                count += 1

        return False

    def get_journal(self, limit: int = 50) -> List[Dict]:
        """Get recent journal entries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of journal entries (newest first)
        """
        if not self.journal_path.exists():
            return []

        journal = self._read_json(self.journal_path, default=[])
        return journal[-limit:][::-1]

    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default if default is not None else {}
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
