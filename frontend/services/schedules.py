"""SchedulesMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class SchedulesMixin:
    def get_system_schedules(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get schedule configuration from the cron scheduler config.

        Reads scheduler/config.yaml — the source of truth for what cron
        actually runs — rather than the run_manager mode registry, so the
        dashboard shows real cron expressions, enabled flags, and next-run
        times. Per-mode run history (last run, success rate, avg duration)
        is layered on from data/runs/.
        """
        schedules = []
        cfg = self._load_scheduler_config()
        modes = (cfg or {}).get("modes", {})

        for mode_name, mode_cfg in modes.items():
            mode_cfg = mode_cfg or {}
            cron = mode_cfg.get("schedule", "—")
            enabled = bool(mode_cfg.get("enabled", True))
            # The mode the manager actually runs (may differ from the cron key)
            manager_mode = mode_cfg.get("mode_name", mode_name)

            history = self._get_runs_for_mode(manager_mode, limit=10)
            last_run = history[0].get("started_at") if history else None
            durations = [r.get("elapsed", 0) for r in history if r.get("elapsed")]
            avg_dur = round(sum(durations) / len(durations)) if durations else None
            successes = [r for r in history if r.get("status") in ("complete", "success")]
            success_rate = (
                round(100 * len(successes) / len(history)) if history else None
            )

            schedules.append({
                "name": mode_name,
                "mode": manager_mode,
                "cron": cron,
                "frequency": self._humanize_cron(cron),
                "description": f"Run {manager_mode} mode",
                "next_run": self._next_cron_run(cron),
                "last_run": last_run,
                "avg_duration": avg_dur,
                "success_rate": success_rate,
                "enabled": enabled,
                "recent_runs": len(history),
            })

        return {
            "schedules": sorted(schedules, key=lambda s: s["name"]),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _load_scheduler_config(self) -> Optional[Dict]:
        """Load scheduler/config.yaml from the project root."""
        try:
            import yaml
            path = self.data_dir.parent / "scheduler" / "config.yaml"
            if not path.exists():
                return None
            with open(path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.debug("Could not load scheduler config: %s", e)
            return None

    @staticmethod
    def _humanize_cron(cron: str) -> str:
        """Turn a 5-field cron expr into a short human label (best-effort)."""
        if not cron or cron == "—":
            return "—"
        parts = cron.split()
        if len(parts) != 5:
            return cron
        minute, hour, _dom, _mon, dow = parts
        dow_names = {
            "1-5": "weekdays", "0": "Sundays", "6": "Saturdays",
            "5": "Fridays", "0,6": "weekends", "*": "daily",
        }
        when = dow_names.get(dow, f"dow {dow}")
        if hour.isdigit() and minute.isdigit():
            return f"{int(hour):02d}:{int(minute):02d} {when}"
        return f"{minute} {hour} · {when}"

    @staticmethod
    def _next_cron_run(cron: str) -> Optional[str]:
        """Compute the next fire time for a standard 5-field cron expression.

        Scans forward minute-by-minute up to 8 days. Avoids a croniter
        dependency; cron here is local time (matching the host crontab).
        """
        if not cron or cron == "—":
            return None
        parts = cron.split()
        if len(parts) != 5:
            return None
        minute_f, hour_f, dom_f, mon_f, dow_f = parts

        def field_match(value: int, field: str, lo: int, hi: int) -> bool:
            for token in field.split(","):
                if token == "*":
                    return True
                step = 1
                if "/" in token:
                    base, step_s = token.split("/", 1)
                    step = int(step_s)
                    token = base
                if token == "*":
                    rng = range(lo, hi + 1)
                elif "-" in token:
                    a, b = token.split("-", 1)
                    rng = range(int(a), int(b) + 1)
                else:
                    rng = range(int(token), int(token) + 1)
                if value in rng and (value - rng.start) % step == 0:
                    return True
            return False

        now = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
        for i in range(8 * 24 * 60):
            t = now + timedelta(minutes=i)
            if (
                field_match(t.minute, minute_f, 0, 59)
                and field_match(t.hour, hour_f, 0, 23)
                and field_match(t.day, dom_f, 1, 31)
                and field_match(t.month, mon_f, 1, 12)
                and field_match(t.weekday() + 1 if t.weekday() < 6 else 0, dow_f, 0, 7)
            ):
                # `t` is naive local time (cron runs in host local time).
                # Convert to a real UTC instant so the API stays consistent
                # with the rest of the Z-suffixed timestamps.
                utc = t.astimezone(timezone.utc)
                return utc.isoformat().replace("+00:00", "Z")
        return None
