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


def extract_tool_calls_from_envelope(envelope: Dict) -> List[ToolCall]:
    """Extract tool call info from the claude CLI JSON envelope.

    The envelope may contain tool use info in various formats depending
    on the CLI version. We extract what we can.
    """
    calls = []

    # The claude CLI --output-format json returns a "result" field with
    # the agent's text output. Tool calls aren't always in the envelope,
    # but some versions include them in a "tool_uses" or "messages" field.
    messages = envelope.get("messages", [])
    for msg in messages:
        if isinstance(msg, dict) and msg.get("type") == "tool_use":
            calls.append(ToolCall(
                tool=msg.get("name", "unknown"),
                input_summary=_summarize(msg.get("input", {})),
            ))

    # Also check for tool_uses at top level
    tool_uses = envelope.get("tool_uses", [])
    for tu in tool_uses:
        if isinstance(tu, dict):
            calls.append(ToolCall(
                tool=tu.get("name", "unknown"),
                input_summary=_summarize(tu.get("input", {})),
            ))

    return calls


def _summarize(obj: Any, max_len: int = 200) -> str:
    """Summarize an object to a short string."""
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
