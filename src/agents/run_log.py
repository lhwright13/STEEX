"""Per-run JSONL logs consumed by the frontend dashboard.

`frontend/services.py` reads `data/runs/run_*.jsonl`, takes the most recent file
(`sorted(..., reverse=True)[0]`) and parses its LAST line as the full run state.
Its field names mirror the orchestrator's PipelineState (`conclusions`,
`variant_conclusions`, `traces`, `manager_decision`, `abort`), so we serialize
that state almost verbatim, adding a little run metadata and a few derived fields
the reader expects.

Lifecycle: `start_run_log` writes one "running" line when the pipeline begins;
`finish_run_log` appends the consolidated final line. The reader uses the last
line, so the final line wins. Filenames carry a sortable UTC stamp so the newest
run sorts first.

"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("steex.run_log")

# Map the variant agent role (e.g. "Analysis_momentumAgent") to the bare variant
# name the dashboard groups by.
_VARIANT_NAMES = ("conservative", "aggressive", "momentum")


def _runs_dir(data_dir) -> Path:
    d = Path(data_dir) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_run_log(data_dir, run_id: str, mode: str) -> Optional[Path]:
    """Write the initial "running" line and return the run file path (or None)."""
    try:
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S")
        path = _runs_dir(data_dir) / f"run_{stamp}_{run_id}.jsonl"
        record = {
            "run_id": run_id,
            "mode": mode,
            "status": "running",
            "stage": "data",
            "current_agent": "data",
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "completed_at": None,
        }
        with open(path, "w") as f:
            f.write(json.dumps(record) + "\n")
        return path
    except Exception as e:  # logging must never break a trading run
        logger.debug("start_run_log failed: %s", e)
        return None


def _derive_screening(conclusions: dict) -> dict:
    """Map the analysis conclusion's funnel into the keys the dashboard reads."""
    analysis = (conclusions or {}).get("analysis") or {}
    funnel = analysis.get("screening_funnel") or {}
    return {
        "universe_size": analysis.get("universe_size", 0),
        "volume_filtered": funnel.get("stage_1", 0),
        "sentiment_filtered": funnel.get("stage_2", 0),
        "technical_filtered": funnel.get("stage_3", 0),
        "insider_filtered": funnel.get("stage_4", 0),
        "final_count": funnel.get("final", 0),
    }


def _normalize_traces(traces: list) -> list:
    """The dashboard looks up traces by `agent` and reads a `summary` string.

    PipelineState traces key on `role` and carry no summary, so add both while
    preserving the original fields.
    """
    out = []
    for t in traces or []:
        role = t.get("role", "")
        agent = role[:-5].lower() if role.endswith("Agent") else role.lower()
        summary = t.get("error") if not t.get("success") else "ok"
        out.append({**t, "agent": agent, "summary": summary})
    return out


def finish_run_log(
    path: Optional[Path],
    data_dir,
    run_id: str,
    mode: str,
    final_state: dict,
    status: str,
) -> None:
    """Append the consolidated final line for a finished run."""
    try:
        if path is None:
            # start_run_log failed or wasn't called; create a file now so the
            # dashboard still sees this run.
            now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            path = _runs_dir(data_dir) / f"run_{now}_{run_id}.jsonl"

        conclusions = final_state.get("conclusions", {}) or {}
        record = {
            "run_id": run_id,
            "mode": mode,
            "status": status,  # "complete" | "failed"
            "stage": "execution",
            "current_agent": None,
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "abort": bool(final_state.get("abort")),
            "abort_reason": final_state.get("abort_reason"),
            "conclusions": conclusions,
            "variant_conclusions": final_state.get("variant_conclusions", []) or [],
            "manager_decision": final_state.get("manager_decision") or {},
            "traces": _normalize_traces(final_state.get("traces", [])),
            "screening": _derive_screening(conclusions),
        }
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.debug("finish_run_log failed: %s", e)
