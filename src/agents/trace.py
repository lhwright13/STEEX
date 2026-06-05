"""Audit trail for agent runs.

Captures what each agent did during an invocation: tools called,
conclusions produced, timing, and self-improvement suggestions.
Sessions group all traces for a single mode run.

Storage: data/agents/sessions/{YYYYMMDD}_{mode}_{HHMMSS}.json
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("steex.trace")


@dataclass
class ToolCall:
    """Record of a single tool invocation by an agent."""

    tool: str
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: float = 0.0


@dataclass
class AgentTrace:
    """Everything an agent did during one invocation."""

    run_id: str
    role: str
    mode: str
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    tools_called: List[Dict[str, Any]] = field(default_factory=list)
    raw_output: str = ""
    conclusion: Optional[Dict[str, Any]] = None
    success: bool = False
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def start(self):
        self.started_at = datetime.now().isoformat()
        self._start_time = time.monotonic()

    def finish(self, success: bool = True, error: Optional[str] = None):
        self.finished_at = datetime.now().isoformat()
        elapsed = time.monotonic() - getattr(self, "_start_time", time.monotonic())
        self.duration_seconds = round(elapsed, 2)
        self.success = success
        self.error = error

    def add_tool_call(self, tool_call: ToolCall):
        self.tools_called.append(asdict(tool_call))

    def set_conclusion(self, conclusion_dict: Optional[Dict[str, Any]]):
        self.conclusion = conclusion_dict
        if conclusion_dict and "meta" in conclusion_dict:
            self.meta = conclusion_dict["meta"]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentTrace":
        """Deserialize an AgentTrace from a dict (e.g., from LangGraph state)."""
        return cls(
            run_id=data.get("run_id", ""),
            role=data.get("role", ""),
            mode=data.get("mode", ""),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            tools_called=data.get("tools_called", []),
            raw_output=data.get("raw_output", ""),
            conclusion=data.get("conclusion"),
            success=data.get("success", False),
            error=data.get("error"),
            meta=data.get("meta"),
        )


@dataclass
class AgentSession:
    """Groups all traces for one mode run."""

    run_id: str
    mode: str
    timestamp: str = ""
    traces: List[AgentTrace] = field(default_factory=list)
    manager_decision: Optional[Dict[str, Any]] = None
    fallback_used: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def add_trace(self, trace: AgentTrace):
        self.traces.append(trace)

    def set_manager_decision(self, decision_dict: Optional[Dict[str, Any]]):
        self.manager_decision = decision_dict

    def save(self, data_dir: str = "data"):
        """Save session to disk and update latest pointer."""
        sessions_dir = Path(data_dir) / "agents" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{datetime.now().strftime('%Y%m%d')}_{self.mode}_{ts.split('_')[1]}.json"
        filepath = sessions_dir / filename

        session_data = {
            "run_id": self.run_id,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "fallback_used": self.fallback_used,
            "manager_decision": self.manager_decision,
            "traces": [asdict(t) for t in self.traces],
        }

        # Remove internal timing attributes from serialized traces
        for trace_dict in session_data["traces"]:
            trace_dict.pop("_start_time", None)

        with open(filepath, "w") as f:
            json.dump(session_data, f, indent=2, default=str)

        # Update latest pointer
        latest = sessions_dir / "latest.json"
        with open(latest, "w") as f:
            json.dump(session_data, f, indent=2, default=str)

        logger.info("Session saved: %s", filepath)
        return filepath

    @staticmethod
    def prune_old_sessions(data_dir: str = "data", max_days: int = 30):
        """Remove session files older than max_days."""
        sessions_dir = Path(data_dir) / "agents" / "sessions"
        if not sessions_dir.exists():
            return

        cutoff = datetime.now() - timedelta(days=max_days)
        removed = 0

        for f in sessions_dir.glob("*.json"):
            if f.name == "latest.json":
                continue
            try:
                # Parse date from filename: YYYYMMDD_mode_HHMMSS.json
                date_str = f.stem.split("_")[0]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    f.unlink()
                    removed += 1
            except (ValueError, IndexError):
                continue

        if removed:
            logger.info("Pruned %d old session files", removed)


def parse_stream_json_output(stdout: str) -> "tuple[Optional[Dict], List[ToolCall]]":
    """Parse `claude --output-format stream-json --verbose` NDJSON output.

    Returns (envelope, tool_calls):
      - envelope: the terminal {"type": "result"} line, which has the SAME shape
        as the old --output-format json envelope (is_error / result / num_turns /
        permission_denials / usage), or None if no result line was found.
      - tool_calls: ToolCall for every tool_use block across all assistant turns.

    The --output-format json envelope carried no per-message tool_use blocks, so
    tool telemetry was always empty; stream-json emits an "assistant" line per
    turn whose message.content[] holds the tool_use blocks.

    Fallback: if no result line is found (e.g. a CLI/format change, or output is
    actually single-json), try to parse the whole stdout as one JSON object so a
    regression degrades to the prior behavior instead of breaking.
    """
    envelope: Optional[Dict] = None
    tool_calls: List[ToolCall] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")
        if otype == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append(ToolCall(
                        tool=block.get("name", "unknown"),
                        input_summary=_summarize(block.get("input", {})),
                    ))
        elif otype == "result":
            envelope = obj  # last result line wins (there is exactly one)

    if envelope is None:
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                envelope = parsed
        except json.JSONDecodeError:
            envelope = None

    return envelope, tool_calls


def _summarize(obj: Any, max_len: int = 200) -> str:
    """Summarize an object to a short string."""
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
