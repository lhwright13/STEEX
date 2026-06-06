"""The `user_updates` stream — the single canonical feed of user-facing events.

One record is written by a producer (a buy/sell fill, an event-trade, a big move,
a system note) and read by every surface: the iMessage notifier (P1-2) and the
dashboard "Today's Events" + event-trigger panels (P3-3/P3-4/P3-6). Defining it
once here is what keeps those surfaces from diverging.

Storage mirrors the run-log pattern (`src/agents/run_log.py`): an append-only,
date-partitioned JSONL store under `data/user_updates/<YYYY-MM-DD>.jsonl`. Reads
are tolerant (a malformed line is skipped, never fatal) and forward-compatible
(unknown fields are ignored), so producers and the schema can evolve
independently of readers.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("steex.user_updates")

UpdateType = str   # one of: buy | sell | event_trade | big_move | system
Severity = str     # one of: info | success | warning | critical

_TYPES = {"buy", "sell", "event_trade", "big_move", "system"}
_SEVERITIES = {"info", "success", "warning", "critical"}


class UpdateLink(BaseModel):
    """A reference attached to an update (e.g. the triggering post, a run log)."""
    model_config = ConfigDict(extra="ignore")
    label: str
    href: str


class UserUpdate(BaseModel):
    """One user-facing event. `extra="ignore"` keeps old readers forward-compatible."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Stable id for deep-linking to the detail view")
    ts: str = Field(description="UTC ISO-8601 timestamp")
    type: UpdateType = Field(description="buy | sell | event_trade | big_move | system")
    title: str
    summary: str = ""
    severity: Severity = "info"
    payload: Dict[str, Any] = Field(default_factory=dict)
    links: List[UpdateLink] = Field(default_factory=list)


def _dir(data_dir) -> Path:
    d = Path(data_dir) / "user_updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _day_files(data_dir) -> List[Path]:
    """All day files, newest day first."""
    d = Path(data_dir) / "user_updates"
    if not d.exists():
        return []
    return sorted(d.glob("*.jsonl"), reverse=True)


def write_update(
    data_dir,
    type: UpdateType,
    title: str,
    summary: str = "",
    severity: Severity = "info",
    payload: Optional[Dict[str, Any]] = None,
    links: Optional[List[Dict[str, str]]] = None,
    update_id: Optional[str] = None,
    ts: Optional[str] = None,
) -> UserUpdate:
    """Append one update to today's file and return the stored record.

    Assigns a stable `id` and a UTC `ts` when not supplied. Validates type and
    severity (a producer passing a bad value is a bug worth surfacing). The file
    append is wrapped so a notification write can never take down a trading path
    — on IO failure it logs and still returns the record.
    """
    if type not in _TYPES:
        raise ValueError(f"unknown update type {type!r}; expected one of {sorted(_TYPES)}")
    if severity not in _SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}; expected one of {sorted(_SEVERITIES)}")

    now = datetime.now(timezone.utc)
    rec = UserUpdate(
        id=update_id or uuid.uuid4().hex[:12],
        ts=ts or now.isoformat().replace("+00:00", "Z"),
        type=type,
        title=title,
        summary=summary,
        severity=severity,
        payload=payload or {},
        links=[UpdateLink(**l) for l in (links or [])],
    )
    try:
        day = (rec.ts[:10]) or now.strftime("%Y-%m-%d")
        path = _dir(data_dir) / f"{day}.jsonl"
        with open(path, "a") as f:
            f.write(rec.model_dump_json() + "\n")
    except Exception as e:  # a notification must never break a trading run
        logger.error("write_update failed (%s); record not persisted: %s", e, rec.id)
    return rec


def _iter_records(data_dir, day: Optional[str] = None):
    """Yield stored updates newest-first, tolerant of malformed/forward lines."""
    files = _day_files(data_dir)
    if day is not None:
        files = [f for f in files if f.stem == day]
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except Exception as e:
            logger.debug("could not read %s: %s", path, e)
            continue
        for line in reversed(lines):  # within a file, later lines are newer
            line = line.strip()
            if not line:
                continue
            try:
                yield UserUpdate.model_validate_json(line)
            except Exception as e:
                logger.debug("skipping malformed user_update line in %s: %s", path, e)
                continue


def read_updates(
    data_dir,
    limit: int = 50,
    types: Optional[List[str]] = None,
    day: Optional[str] = None,
    since: Optional[str] = None,
) -> List[UserUpdate]:
    """Return updates newest-first, optionally filtered by type / day / since-ts.

    `types` filters to those update types; `day` restricts to one YYYY-MM-DD
    partition; `since` drops records at or before that ISO timestamp.
    """
    want = set(types) if types else None
    out: List[UserUpdate] = []
    for rec in _iter_records(data_dir, day=day):
        if want is not None and rec.type not in want:
            continue
        if since is not None and rec.ts <= since:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def get_update(data_dir, update_id: str) -> Optional[UserUpdate]:
    """Find a single update by id (for the clickable detail view)."""
    for rec in _iter_records(data_dir):
        if rec.id == update_id:
            return rec
    return None
