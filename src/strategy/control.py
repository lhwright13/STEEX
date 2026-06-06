"""Runtime trading controls (kill switch).

A tiny JSON file at data/control.json holds two flags read at the order
choke-points:

  trading_armed : master switch. When false, NO real entries execute
                  (screen/enter AND event-trigger), regardless of mode.
  event_armed   : when false, the news event-trigger fast-path is halted but
                  the regular screen/enter pipeline still trades.

Defaults are armed (true) so absence of the file preserves existing behaviour.
The dashboard toggles these; cron-driven runs read them every execution, so a
disarm takes effect on the next order attempt with no process restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger("steex.control")

_DEFAULTS = {"trading_armed": True, "event_armed": True}


def _control_path(data_dir) -> Path:
    return Path(data_dir) / "control.json"


def get_controls(data_dir) -> Dict:
    """Return the current control flags.

    File ABSENT -> armed defaults: a fresh install was never configured, so we
    preserve existing behavior. File EXISTS but unreadable/corrupt -> FAIL CLOSED
    (disarmed): we cannot confirm the intended state, and for a kill switch the
    only safe default is to NOT trade. Re-arming via set_controls overwrites the
    bad file, so this is recoverable.
    """
    path = _control_path(data_dir)
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        with open(path) as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("control.json is not a JSON object")
        state = dict(_DEFAULTS)
        for k in _DEFAULTS:
            if k in loaded:
                state[k] = bool(loaded[k])
        state["updated_at"] = loaded.get("updated_at")
        return state
    except Exception as e:  # file exists but is corrupt/unreadable -> fail closed
        logger.error(
            "control file %s unreadable (%s); FAILING CLOSED (disarmed)", path, e
        )
        return {"trading_armed": False, "event_armed": False, "updated_at": None}


def set_controls(data_dir, *, trading_armed=None, event_armed=None, updated_at=None) -> Dict:
    """Update one or both flags and persist. Returns the new state."""
    state = get_controls(data_dir)
    if trading_armed is not None:
        state["trading_armed"] = bool(trading_armed)
    if event_armed is not None:
        state["event_armed"] = bool(event_armed)
    if updated_at is not None:
        state["updated_at"] = updated_at
    path = _control_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({k: state[k] for k in ("trading_armed", "event_armed", "updated_at")
                       if k in state}, f)
    except Exception as e:
        logger.error("control write failed: %s", e)
    return state


def trading_armed(data_dir) -> bool:
    return get_controls(data_dir).get("trading_armed", True)


def event_armed(data_dir) -> bool:
    c = get_controls(data_dir)
    # event trading also requires the master switch
    return c.get("trading_armed", True) and c.get("event_armed", True)
