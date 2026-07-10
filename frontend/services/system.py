"""SystemMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class SystemMixin:
    def get_system_agents(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all agent configurations with recent stats."""
        agents = []

        # Get latest run for stats context
        latest_run_file = self._get_latest_run_file()
        latest_run = self._load_json(latest_run_file) if latest_run_file else None

        for agent_name, agent_config in self.registry.agents.items():
            last_run_info = self._get_agent_last_run(agent_name)

            tools = self._get_agent_tools(agent_name)
            agent_data = {
                "name": agent_name,
                "display_name": self._DISPLAY_NAMES.get(agent_name, agent_name),
                "role": agent_config.prompt_key or agent_name,
                "max_turns": agent_config.max_turns,
                "needs_tools": agent_config.needs_tools,
                "tool_count": len(tools),
                "external_servers": agent_config.external_servers or [],
                "mcp_count": len(agent_config.external_servers or []),
                "prompt_id": agent_config.prompt_key or agent_name,
                "critical": agent_name in self.registry.modes.get("screen", ModeConfig("screen")).critical_agents,
                "last_run_timestamp": last_run_info.get("timestamp"),
                "success_rate": last_run_info.get("success_rate"),
                "avg_duration": last_run_info.get("avg_duration", "—"),
            }
            agents.append(agent_data)

        return {"agents": sorted(agents, key=lambda a: a["name"])}

    def get_agent_detail(self, agent_name: str) -> Dict[str, Any]:
        """Get detailed configuration for a specific agent."""
        agent_name = self._resolve_agent(agent_name)
        agent_config = self.registry.agents.get(agent_name)
        if not agent_config:
            return {"error": f"Agent {agent_name} not found"}

        # Load prompt if available
        prompt_text = "Prompt not found"
        try:
            prompt_file = self.data_dir.parent / f"src/agents/prompts/{agent_config.prompt_key}.py"
            if prompt_file.exists():
                with open(prompt_file) as f:
                    content = f.read()
                # Extract the first `*_PROMPT = "..."` assignment (simple heuristic).
                # Return the FULL prompt (P3-8) — the popup is now scrollable, so
                # truncating to 1500 chars + "…" only hid the real prompt.
                if "_PROMPT = " in content:
                    body = content.split("_PROMPT = ", 1)[1].lstrip()
                    body = body.lstrip('"').lstrip("'").lstrip()  # drop opening quotes
                    prompt_text = body
        except Exception as e:
            logger.debug(f"Could not load prompt for {agent_name}: {e}")

        last_run_info = self._get_agent_last_run(agent_name)

        return {
            "name": agent_name,
            "role": agent_config.prompt_key or agent_name,
            "preprompt": prompt_text,
            "tools": self._get_agent_tools(agent_name),
            "external_servers": agent_config.external_servers or [],
            "last_run": last_run_info.get("timestamp"),
            "success_rate": last_run_info.get("success_rate"),
        }

    def get_agent_last_output(self, agent_name: str) -> Dict[str, Any]:
        """Most recent output for an agent.

        Scans back to the latest non-event_scan run that actually INCLUDED this
        agent — so e.g. 'execution' isn't blank just because the very latest run
        was a 'screen' (which has no execution node). Falls back to the top-level
        manager_decision for the manager (stored there, not under conclusions).
        """
        agent_name = self._resolve_agent(agent_name)
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"agent": agent_name, "conclusion": None, "message": "No run data available"}

        scanned = 0
        for run_file in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
            if scanned >= 80:
                break
            data = self._load_json(run_file)
            if not data or data.get("mode") == "event_scan":
                continue
            scanned += 1

            conclusion = (data.get("conclusions") or {}).get(agent_name)
            if not conclusion:
                for vc in data.get("variant_conclusions") or []:
                    if vc.get("variant") == agent_name:
                        conclusion = vc.get("conclusion")
                        break
            if not conclusion and agent_name == "manager":
                conclusion = data.get("manager_decision")

            traces = data.get("traces") or []
            agent_trace = next(
                (t for t in traces if t.get("agent") == agent_name
                 or str(t.get("role", "")).lower().startswith(agent_name)),
                None,
            )

            # This run included the agent if it has its conclusion or a trace.
            if conclusion or agent_trace:
                return {
                    "agent": agent_name,
                    "conclusion": conclusion,
                    "trace_summary": agent_trace.get("summary") if agent_trace else None,
                    "run_id": data.get("run_id"),
                    "mode": data.get("mode"),
                    "timestamp": data.get("completed_at") or data.get("started_at"),
                    "message": (None if conclusion
                                else "Agent ran but recorded no conclusion in this run."),
                }

        return {"agent": agent_name, "conclusion": None,
                "message": "No recent output for this agent."}

    def get_agent_timeline(self, run_id: str = None) -> Dict[str, Any]:
        """Per-run agent execution timeline: the sequence of agents with
        status, duration, tools called, and a short summary/conclusion.

        Defaults to the latest non-event_scan run. Normalizes the run-log
        traces (role/success/error/duration_seconds/tools_called/summary) into
        an ordered timeline the dashboard renders as a step list.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"run_id": None, "steps": [], "message": "No run data available"}

        run_data = None
        if run_id:
            for f in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
                d = self._load_json(f)
                if d and d.get("run_id") == run_id:
                    run_data = d
                    break
        else:
            f = self._get_latest_run_file()
            run_data = self._load_json(f) if f else None

        if not run_data:
            return {"run_id": run_id, "steps": [], "message": "Run not found"}

        steps = []
        for i, t in enumerate(run_data.get("traces", []) or []):
            steps.append({
                "order": i + 1,
                "agent": t.get("agent") or t.get("role"),
                "role": t.get("role"),
                "success": t.get("success"),
                "duration_seconds": t.get("duration_seconds"),
                "tools_called": t.get("tools_called") or [],
                "summary": t.get("summary") or (t.get("error") if not t.get("success") else "ok"),
                "has_conclusion": t.get("conclusion") is not None,
            })

        return {
            "run_id": run_data.get("run_id"),
            "mode": run_data.get("mode"),
            "status": run_data.get("status"),
            "started_at": run_data.get("started_at"),
            "completed_at": run_data.get("completed_at"),
            "abort": run_data.get("abort"),
            "abort_reason": run_data.get("abort_reason"),
            "steps": steps,
            "agent_count": len(steps),
            "failed_count": sum(1 for s in steps if s["success"] is False),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_system_health(self) -> Dict[str, Any]:
        """Heartbeat integrity status + a read-only quarantined-trades listing.

        Surfaces the health_check.py `integrity` check (07-07/07-08 phantom-
        trades incident) and the count/contents of data/trades_quarantine.json
        so the operator can see, on the System page, whether the book is clean.
        Read-only: no buttons, no mutation.
        """
        hb = self._load_json_file(self.data_dir / "heartbeat.json") or {}
        integrity = (hb.get("checks") or {}).get("integrity") or {}

        integrity_status = integrity.get("status") or "UNKNOWN"
        health = {
            "integrity": {
                "status": integrity_status,
                "violations": integrity.get("violations") or [],
                "error": integrity.get("error"),
            },
            "overall": hb.get("overall"),
            "heartbeat_at": hb.get("timestamp"),
        }

        quarantine = self._load_json_file(self.data_dir / "trades_quarantine.json")
        rows = []
        if isinstance(quarantine, list):
            for q in quarantine:
                if not isinstance(q, dict):
                    continue
                rows.append({
                    "ticker": q.get("ticker"),
                    "exit_date": q.get("exit_date"),
                    "reason": q.get("_quarantine_reason") or q.get("exit_reason"),
                })

        health["quarantine"] = {
            "count": len(rows),
            "rows": rows,
        }
        return health

    def _get_agent_tools(self, agent_name: str) -> List[Dict[str, str]]:
        """Get tools available to agent."""
        agent_config = self.registry.agents.get(agent_name)
        if not agent_config or not agent_config.allowed_tools:
            return []

        return [
            {"name": tool, "description": f"Tool: {tool}"}
            for tool in agent_config.allowed_tools
        ]

    def _get_agent_last_run(self, agent_name: str) -> Dict[str, Any]:
        """Get info about agent's last run from recent execution history."""
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {
                "timestamp": None,
                "success_rate": None,
                "avg_duration": "—",
            }

        # Get most recent 10 runs
        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)[:10]

        success_count = 0
        total_count = 0
        durations = []
        last_timestamp = None

        for run_file in run_files:
            run_data = self._load_json(run_file)
            if not run_data:
                continue

            # Check if this agent was in this run
            conclusions = run_data.get("conclusions", {})
            variant_conclusions = run_data.get("variant_conclusions", [])

            agent_in_run = (agent_name in conclusions or
                          any(vc.get("variant") == agent_name for vc in variant_conclusions))

            if not agent_in_run:
                continue

            total_count += 1

            # Track success (assuming successful runs have agent conclusions)
            if agent_name in conclusions or any(vc.get("variant") == agent_name for vc in variant_conclusions):
                success_count += 1

            # Calculate duration if available
            started = run_data.get("started_at")
            completed = run_data.get("completed_at")
            if started and completed and not last_timestamp:
                last_timestamp = started
                try:
                    start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                    duration_secs = int((end_dt - start_dt).total_seconds())
                    durations.append(duration_secs)
                except Exception:
                    pass

        success_rate = (success_count / total_count) if total_count > 0 else None
        avg_duration = "—"
        if durations:
            avg_secs = int(sum(durations) / len(durations))
            if avg_secs < 60:
                avg_duration = f"{avg_secs}s"
            else:
                avg_duration = f"{avg_secs // 60}:{avg_secs % 60:02d}"

        return {
            "timestamp": last_timestamp,
            "success_rate": success_rate,
            "avg_duration": avg_duration,
        }
