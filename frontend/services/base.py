"""DashboardService base: init + shared helpers (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class DashboardServiceBase:
    """Shared state + low-level helpers used across the domain mixins."""

    _AGENT_ALIASES = {
        "DataAgent": "data",
        "RiskAgent": "risk",
        "MetaAnalysisAgent": "meta_analysis",
        "ManagerAgent": "manager",
        "ExecutionAgent": "execution",
        "ReportAgent": "report",
        "ResearchAgent": "research",
    }
    _DISPLAY_NAMES = {v: k for k, v in _AGENT_ALIASES.items()}

    def __init__(self):
        self.settings = get_settings()
        config_dir = Path(__file__).resolve().parents[2] / "config"
        self.registry = AgentRegistry(config_dir / "agents.yaml")
        self.regime_detector = RegimeDetector(self.settings)
        self.data_dir = Path(__file__).resolve().parents[2] / "data"

    def _resolve_agent(self, name: str) -> str:
        """Map a UI agent name (possibly legacy CamelCase) to a registry key."""
        if name in self.registry.agents:
            return name
        return self._AGENT_ALIASES.get(name, name)

    def _load_json(self, path: Path) -> Optional[Dict]:
        """Load last line of JSONL file as JSON."""
        try:
            with open(path) as f:
                lines = f.readlines()
                if lines:
                    return json.loads(lines[-1])
        except Exception as e:
            logger.debug(f"Failed to load {path}: {e}")
        return None

    def _load_json_file(self, path: Path) -> Optional[Dict]:
        """Load a whole-file JSON document (not JSONL)."""
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Failed to load {path}: {e}")
        return None

    def _get_latest_run_file(self, exclude_modes=("event_scan",)) -> Optional[Path]:
        """Get the most recent run data file, skipping certain modes.

        event_scan runs every minute and would otherwise become the "latest"
        run for every pipeline panel, blanking the screen/trade view. The
        trading dashboard wants the latest screen/enter/monitor run, so
        event_scan is excluded by default. Pass exclude_modes=() for any mode.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return None

        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)
        if not exclude_modes:
            return run_files[0] if run_files else None
        for f in run_files:
            data = self._load_json(f)
            if data and data.get("mode") not in exclude_modes:
                return f
        return None

    def _elapsed_seconds(self, started_at: Optional[str]) -> int:
        """Calculate seconds since started_at timestamp."""
        if not started_at:
            return 0
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed = (datetime.utcnow() - start).total_seconds()
            return max(0, int(elapsed))
        except Exception:
            return 0

    def _get_runs_for_mode(self, mode: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent runs for a specific mode."""
        runs = []
        data_dir = self.data_dir / "runs"

        if not data_dir.exists():
            return runs

        # Find run files for this mode
        run_files = sorted(
            data_dir.glob(f"run_*.jsonl"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        # Filter by mode BEFORE applying the limit — otherwise a burst of
        # other-mode runs at the top of the (mtime-sorted) list can crowd out
        # this mode entirely and return nothing.
        for run_file in run_files:
            if len(runs) >= limit:
                break
            run_data = self._load_json(run_file)
            if run_data and run_data.get("mode") == mode:
                runs.append({
                    "run_id": run_data.get("run_id"),
                    "started_at": run_data.get("started_at"),
                    "status": run_data.get("status"),
                    "stage": run_data.get("stage"),
                    "elapsed": self._elapsed_seconds(run_data.get("started_at")),
                })

        return runs
