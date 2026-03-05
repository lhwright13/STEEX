"""Orchestrator for the Claude AI multi-agent trading system.

Replaces QuantManager's mode methods with an ensemble of Claude agents.
Each sub-agent runs independently via the claude CLI with an MCP server
providing trading tools. A ManagerAgent synthesizes all conclusions into
a final decision.

Agent definitions and mode sequences are loaded from config/agents.yaml
via the AgentRegistry. Adding a new agent requires no code changes -
just a config entry, prompt file, and Pydantic conclusion model.

Usage:
    orchestrator = Orchestrator(settings, paper=True, dry_run=False)
    report = orchestrator.run_mode("screen")
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel

from config.settings import Settings, get_settings

from .conclusions import (
    AnalysisConclusion,
    DataConclusion,
    ExecutionConclusion,
    ManagerDecision,
    ReportConclusion,
    ResearchConclusion,
    RiskConclusion,
)
from .evolution import PromptEvolver
from .registry import AgentConfig, AgentRegistry, ModeConfig
from .trace import AgentSession, AgentTrace, ToolCall, extract_tool_calls_from_envelope

logger = logging.getLogger("steex.orchestrator")
console = Console()

# Modes that stay fully deterministic (no Claude agents)
DETERMINISTIC_MODES = {"stop_sync", "heartbeat"}

# Mode display names and colors
MODE_DISPLAY = {
    "screen": ("Pre-Open Screening", "cyan"),
    "enter": ("Entry Execution", "green"),
    "monitor": ("Position Monitor", "yellow"),
    "post_market": ("Post-Market Wrap-up", "green"),
    "learning": ("Learning Loop", "magenta"),
    "pre_market": ("Pre-Market (Screen)", "cyan"),
}

# Mode-specific manager task templates
MANAGER_TASK_TEMPLATES = {
    "screen": (
        "Mode: screen (pre-open screening).\n"
        "Synthesize these into a ManagerDecision. Approve entries if "
        "conditions are favorable. The buy list will be saved for the "
        "entry phase later - no orders are placed now."
    ),
    "enter": (
        "Mode: enter (execute entries).\n"
        "Decide whether to execute the queued entries based on current risk conditions. "
        "Include the buy list from screen results in your buys if approved."
    ),
    "monitor": (
        "Mode: monitor (midday check).\n"
        "Review risk conditions. Approve any immediate exits. "
        "No new entries in monitor mode."
    ),
    "post_market": (
        "Mode: post_market.\n"
        "Approve end-of-day exits. Note research findings."
    ),
    "learning": (
        "Mode: learning.\n"
        "Review the research findings. Note any concerning signals or gaps."
    ),
}


class Orchestrator:
    """Runs trading modes as Claude agent ensembles.

    Each mode launches sub-agents via the claude CLI, each backed by the
    STEEX MCP server for tool access. Sub-agent conclusions are synthesized
    by a ManagerAgent into a final decision.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        paper: bool = True,
        dry_run: bool = False,
        auto_confirm: bool = False,
        verbose: bool = False,
    ):
        self.settings = settings or get_settings()
        self.paper = paper
        self.dry_run = dry_run
        self.auto_confirm = auto_confirm
        self.verbose = verbose

        self._project_root = Path(__file__).parent.parent.parent
        self._claude_bin = self._find_claude()
        self._mcp_config_path: Optional[str] = None

        self.registry = AgentRegistry()
        self.evolver = PromptEvolver(data_dir=self.settings.data_dir)

    def _find_claude(self) -> str:
        """Find the claude CLI binary."""
        claude = shutil.which("claude")
        if claude:
            return claude
        for path in [
            "/usr/local/bin/claude",
            os.path.expanduser("~/.claude/local/claude"),
        ]:
            if os.path.isfile(path):
                return path
        raise FileNotFoundError(
            "claude CLI not found. Install Claude Code: https://claude.ai/claude-code"
        )

    def _get_mcp_config(self) -> str:
        """Generate MCP config file for the claude CLI."""
        if self._mcp_config_path and os.path.exists(self._mcp_config_path):
            return self._mcp_config_path

        venv_python = str(self._project_root / "venv" / "bin" / "python")
        server_script = str(self._project_root / "src" / "agents" / "mcp_server.py")

        args = [venv_python, server_script]
        if self.paper:
            args.append("--paper")
        elif not self.paper:
            args.append("--live")
        if self.dry_run:
            args.append("--dry-run")

        config = {
            "mcpServers": {
                "steex": {
                    "command": venv_python,
                    "args": [server_script] + args[2:],
                }
            }
        }

        fd, path = tempfile.mkstemp(suffix=".json", prefix="steex_mcp_")
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        self._mcp_config_path = path
        return path

    def _cleanup(self):
        """Clean up temp files."""
        if self._mcp_config_path and os.path.exists(self._mcp_config_path):
            os.unlink(self._mcp_config_path)
            self._mcp_config_path = None

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    def _run_agent(
        self,
        role: str,
        system_prompt: str,
        task_message: str,
        conclusion_type: Type[BaseModel],
        max_turns: int = 15,
        needs_tools: bool = True,
        allowed_tools: Optional[List[str]] = None,
        mode: str = "",
        run_id: str = "",
    ) -> Tuple[Optional[BaseModel], AgentTrace]:
        """Run a single agent via the claude CLI.

        Returns:
            Tuple of (parsed conclusion or None, AgentTrace)
        """
        trace = AgentTrace(run_id=run_id or str(uuid.uuid4())[:8], role=role, mode=mode)
        trace.start()

        full_prompt = f"{system_prompt}\n\n---\n\n{task_message}"

        cmd = [
            self._claude_bin,
            "-p", full_prompt,
            "--output-format", "json",
            "--max-turns", str(max_turns),
        ]

        if needs_tools:
            mcp_config = self._get_mcp_config()
            cmd.extend(["--mcp-config", mcp_config])
            # Auto-approve MCP tools so the agent doesn't block on permission prompts
            if allowed_tools:
                tool_perms = [f"mcp__steex__{t}" for t in allowed_tools]
            else:
                tool_perms = ["mcp__steex__*"]
            cmd.extend(["--allowedTools", ",".join(tool_perms)])

        console.print(f"  Running {role}...", style="dim")
        logger.info("Running %s agent (max_turns=%d)", role, max_turns)

        if self.verbose:
            # Show the command (redact the full prompt for readability)
            cmd_display = [c for c in cmd]
            prompt_idx = cmd_display.index("-p") + 1 if "-p" in cmd_display else -1
            if prompt_idx > 0:
                prompt_preview = cmd_display[prompt_idx][:100] + "..." if len(cmd_display[prompt_idx]) > 100 else cmd_display[prompt_idx]
                cmd_display[prompt_idx] = f'"{prompt_preview}"'
            console.print(f"  [dim]$ {' '.join(cmd_display[:8])} ...[/dim]")

        try:
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)

            # In verbose mode, let stderr flow to terminal so user can see
            # agent progress (tool calls, thinking). Only capture stdout (JSON).
            if self.verbose:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=None,  # inherits terminal - shows live progress
                    text=True,
                    timeout=self.settings.agent_timeout_seconds,
                    cwd=str(self._project_root),
                    env=env,
                )
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.agent_timeout_seconds,
                    cwd=str(self._project_root),
                    env=env,
                )

            if result.returncode != 0:
                stderr_text = getattr(result, "stderr", "") or ""
                logger.error("%s agent failed (rc=%d): %s", role, result.returncode, stderr_text[:500])
                console.print(f"  [red]{role} agent failed[/red]")
                if self.verbose and stderr_text:
                    console.print(f"  stderr: {stderr_text[:300]}", style="dim red")
                trace.finish(success=False, error=f"exit code {result.returncode}")
                return None, trace

            # Parse the claude CLI JSON envelope
            try:
                envelope = json.loads(result.stdout)
            except json.JSONDecodeError:
                logger.error("%s: Failed to parse CLI output as JSON", role)
                console.print(f"  [red]{role}: invalid CLI output[/red]")
                trace.finish(success=False, error="invalid JSON output")
                return None, trace

            if envelope.get("is_error"):
                error_msg = envelope.get("result", "unknown")
                logger.error("%s agent returned error: %s", role, error_msg)
                console.print(f"  [red]{role} error: {error_msg}[/red]")
                trace.finish(success=False, error=str(error_msg))
                return None, trace

            # Extract tool calls from envelope
            tool_calls = extract_tool_calls_from_envelope(envelope)
            for tc in tool_calls:
                trace.add_tool_call(tc)
            if not tool_calls:
                logger.debug("%s: no tool calls extracted (envelope keys: %s)", role, list(envelope.keys()))

            agent_text = envelope.get("result", "")
            trace.raw_output = agent_text
            logger.debug("%s raw output: %s", role, agent_text[:500])

            # Extract conclusion JSON from agent's text response
            conclusion = self._parse_conclusion(agent_text, conclusion_type, role)
            if conclusion:
                trace.set_conclusion(conclusion.model_dump())
                trace.finish(success=True)
                # Print trace summary
                self._print_trace_summary(role, trace, conclusion)
            else:
                console.print(f"  [yellow]{role}: could not parse conclusion[/yellow]")
                trace.finish(success=False, error="conclusion parse failed")

            return conclusion, trace

        except subprocess.TimeoutExpired:
            logger.error("%s agent timed out", role)
            console.print(f"  [red]{role} timed out[/red]")
            trace.finish(success=False, error="timeout")
            return None, trace
        except Exception as e:
            logger.error("%s agent error: %s", role, e)
            console.print(f"  [red]{role} error: {e}[/red]")
            trace.finish(success=False, error=str(e))
            return None, trace

    def _print_trace_summary(self, role: str, trace: AgentTrace, conclusion: BaseModel):
        """Print concise trace info after each agent completes."""
        tools_str = ""
        if trace.tools_called:
            tool_names = [tc.get("tool", "?") for tc in trace.tools_called]
            tools_str = f" | Tools: {', '.join(tool_names)}"

        duration_str = f"{trace.duration_seconds:.1f}s"
        console.print(f"  [green]{role} complete[/green] ({duration_str}{tools_str})")

        # Print meta-recommendations if present
        meta = getattr(conclusion, "meta", None)
        if meta:
            for suggestion in (meta.prompt_suggestions or []):
                console.print(f"    [dim]Prompt: {suggestion}[/dim]")
            for suggestion in (meta.tool_suggestions or []):
                console.print(f"    [dim]Tool: {suggestion}[/dim]")
            for suggestion in (meta.process_suggestions or []):
                console.print(f"    [dim]Process: {suggestion}[/dim]")

    def _parse_conclusion(
        self,
        text: str,
        model_class: Type[BaseModel],
        role: str,
    ) -> Optional[BaseModel]:
        """Extract and parse a JSON conclusion from agent text output."""
        import re

        text = text.strip()

        # Try direct parse first
        try:
            data = json.loads(text)
            return model_class.model_validate(data)
        except (json.JSONDecodeError, Exception):
            pass

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if fenced:
            try:
                data = json.loads(fenced.group(1).strip())
                return model_class.model_validate(data)
            except (json.JSONDecodeError, Exception):
                pass

        # Find the last JSON object in the text
        last_brace = text.rfind("}")
        if last_brace == -1:
            logger.warning("%s: No JSON object found in output", role)
            return None

        depth = 0
        start = last_brace
        for i in range(last_brace, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start = i
                    break

        try:
            json_str = text[start:last_brace + 1]
            data = json.loads(json_str)
            return model_class.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("%s: Failed to parse extracted JSON: %s", role, e)
            return None

    def _fallback(self, mode: str, **kwargs) -> Dict:
        """Fall back to deterministic QuantManager on agent failure."""
        import inspect

        console.print(
            "[yellow]Agent mode failed, falling back to deterministic QuantManager[/yellow]"
        )
        logger.warning("Falling back to deterministic mode for %s", mode)

        from src.strategy.manager import QuantManager

        try:
            manager = QuantManager(settings=self.settings)
        except RuntimeError:
            self.settings.broker_enabled = False
            manager = QuantManager(settings=self.settings)

        method = getattr(manager, f"run_{mode}", None)
        if method is None:
            raise ValueError(f"Unknown mode: {mode}")

        # Filter kwargs to only params the method accepts
        sig = inspect.signature(method)
        accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return method(**accepted)

    # ------------------------------------------------------------------
    # Generic registry-driven mode execution
    # ------------------------------------------------------------------

    def run_mode(self, mode: str) -> Optional[Dict]:
        """Route to the appropriate mode, using registry if available."""
        # Map pre_market to screen
        effective_mode = "screen" if mode == "pre_market" else mode

        if effective_mode in DETERMINISTIC_MODES:
            from src.strategy.manager import QuantManager

            try:
                manager = QuantManager(settings=self.settings)
            except RuntimeError:
                self.settings.broker_enabled = False
                manager = QuantManager(settings=self.settings)

            method = getattr(manager, f"run_{effective_mode}", None)
            if method:
                return method(dry_run=self.dry_run, verbose=self.verbose)
            return None

        mode_config = self.registry.get_mode(effective_mode)
        if mode_config is None:
            logger.error("Unknown mode for agent orchestrator: %s", effective_mode)
            return self._fallback(effective_mode, dry_run=self.dry_run, verbose=self.verbose)

        try:
            return self._run_mode_generic(effective_mode, mode_config)
        except FileNotFoundError as e:
            console.print(f"[red]Agent mode unavailable: {e}[/red]")
            return self._fallback(
                mode_config.fallback or effective_mode,
                dry_run=self.dry_run, verbose=self.verbose,
            )

    def _run_mode_generic(self, mode: str, mode_config: ModeConfig) -> Dict:
        """Execute a mode using registry-driven agent sequence."""
        run_id = str(uuid.uuid4())[:8]
        session = AgentSession(run_id=run_id, mode=mode)

        # Display header
        display_name, color = MODE_DISPLAY.get(mode, (mode.replace("_", " ").title(), "white"))
        console.print(Panel.fit(f"[bold]{display_name} (Agent Mode)[/bold]", border_style=color))

        today = datetime.now().strftime("%A, %B %d, %Y")
        if mode in ("enter", "monitor"):
            today = datetime.now().strftime("%A, %B %d, %Y %H:%M")

        task_context = (
            f"Today is {today}. Run the {mode} pipeline.\n"
            f"Dry run: {self.dry_run}. Paper trading: {self.paper}."
        )

        # Pre-actions
        screen_data = None
        if "load_screen" in mode_config.pre_actions:
            screen_data = self._load_screen_results()

        # Run sub-agents
        console.print("\n[bold]1. Sub-Agent Analysis[/bold]")
        conclusions: Dict[str, Optional[BaseModel]] = {}

        for agent_name in mode_config.sub_agents:
            agent_config = self.registry.get_agent(agent_name)
            if agent_config is None:
                logger.error("Agent not found in registry: %s", agent_name)
                continue

            prompt = self.registry.resolve_prompt(agent_name, data_dir=self.settings.data_dir)
            conclusion_type = self.registry.resolve_conclusion_type(agent_name)

            result, trace = self._run_agent(
                role=f"{agent_name.title()}Agent",
                system_prompt=prompt,
                task_message=task_context,
                conclusion_type=conclusion_type,
                max_turns=agent_config.max_turns,
                needs_tools=agent_config.needs_tools,
                allowed_tools=agent_config.allowed_tools,
                mode=mode,
                run_id=run_id,
            )
            conclusions[agent_name] = result
            session.add_trace(trace)

        # Check critical agents
        for critical in mode_config.critical_agents:
            if conclusions.get(critical) is None:
                session.fallback_used = True
                session.save(data_dir=self.settings.data_dir)
                self._collect_evolution_meta(session)
                return self._fallback(
                    mode_config.fallback or mode,
                    dry_run=self.dry_run,
                    auto_confirm=self.auto_confirm,
                    verbose=self.verbose,
                )

        # Manager synthesis
        console.print("\n[bold]2. Manager Synthesis[/bold]")
        conclusions_text = self._format_conclusions(
            **{k: v for k, v in conclusions.items()}
        )

        # Add screen data to conclusions for enter mode
        if screen_data:
            conclusions_text += f"\n\nScreen Results (from earlier):\n{json.dumps(screen_data, indent=2)}"

        manager_template = MANAGER_TASK_TEMPLATES.get(mode, f"Mode: {mode}.\nSynthesize conclusions.")
        manager_task = (
            f"Today is {today}. {manager_template}\n"
            f"Dry run: {self.dry_run}.\n\n"
            f"Sub-agent conclusions:\n{conclusions_text}\n\n"
        )

        manager_config = self.registry.get_agent(mode_config.manager)
        if manager_config is None:
            logger.error("Manager agent not in registry: %s", mode_config.manager)
            session.fallback_used = True
            session.save(data_dir=self.settings.data_dir)
            return self._fallback(mode_config.fallback or mode, dry_run=self.dry_run, verbose=self.verbose)

        manager_prompt = self.registry.resolve_prompt(mode_config.manager, data_dir=self.settings.data_dir)
        decision, trace = self._run_agent(
            role="ManagerAgent",
            system_prompt=manager_prompt,
            task_message=manager_task,
            conclusion_type=self.registry.resolve_conclusion_type(mode_config.manager),
            max_turns=manager_config.max_turns,
            needs_tools=manager_config.needs_tools,
            mode=mode,
            run_id=run_id,
        )
        session.add_trace(trace)

        if decision is None:
            session.fallback_used = True
            session.save(data_dir=self.settings.data_dir)
            self._collect_evolution_meta(session)
            return self._fallback(
                mode_config.fallback or mode,
                dry_run=self.dry_run,
                auto_confirm=self.auto_confirm,
                verbose=self.verbose,
            )

        session.set_manager_decision(decision.model_dump())

        # Post-actions: save screen results
        if "save_screen" in mode_config.post_actions:
            risk_conclusion = conclusions.get("risk")
            self._save_screen_results(decision, risk_conclusion)

        # Execution (if mode has an executor and there are trades)
        if mode_config.executor and isinstance(decision, ManagerDecision):
            should_execute = False
            if mode == "enter" and decision.entries_approved and (decision.buys or decision.sells):
                should_execute = True
            elif mode in ("monitor", "post_market") and decision.sells:
                should_execute = True

            if should_execute:
                step_num = len(mode_config.sub_agents) + 2
                console.print(f"\n[bold]{step_num}. Execution[/bold]")

                exec_config = self.registry.get_agent(mode_config.executor)
                if exec_config:
                    exec_prompt = self.registry.resolve_prompt(
                        mode_config.executor, data_dir=self.settings.data_dir
                    )
                    exec_task = (
                        f"Today is {today}. Execute the manager's approved trades.\n"
                        f"Dry run: {self.dry_run}.\n\n"
                        f"Manager decision:\n{decision.model_dump_json(indent=2)}\n\n"
                        "Execute exits first, then entries."
                    )
                    _, exec_trace = self._run_agent(
                        role="ExecutionAgent",
                        system_prompt=exec_prompt,
                        task_message=exec_task,
                        conclusion_type=self.registry.resolve_conclusion_type(mode_config.executor),
                        max_turns=exec_config.max_turns,
                        needs_tools=exec_config.needs_tools,
                        allowed_tools=exec_config.allowed_tools,
                        mode=mode,
                        run_id=run_id,
                    )
                    session.add_trace(exec_trace)
            elif mode == "enter":
                console.print("  No trades to execute")

        # Post-actions: report agent
        if "report" in mode_config.post_actions and mode == "post_market":
            report_config = self.registry.get_agent("report")
            if report_config:
                report_prompt = self.registry.resolve_prompt("report", data_dir=self.settings.data_dir)
                report_task = f"Generate the {mode} report for {today}."
                _, report_trace = self._run_agent(
                    role="ReportAgent",
                    system_prompt=report_prompt,
                    task_message=report_task,
                    conclusion_type=self.registry.resolve_conclusion_type("report"),
                    max_turns=report_config.max_turns,
                    needs_tools=report_config.needs_tools,
                    allowed_tools=report_config.allowed_tools,
                    mode=mode,
                    run_id=run_id,
                )
                session.add_trace(report_trace)

        # Build and save report
        risk_result = conclusions.get("risk")
        data_result = conclusions.get("data")
        analysis_result = conclusions.get("analysis")
        research_result = conclusions.get("research")

        report_step = len(mode_config.sub_agents) + 2
        if mode_config.executor:
            report_step += 1
        console.print(f"\n[bold]{report_step}. Report[/bold]")

        report = self._build_report(
            mode, decision,
            data=data_result,
            risk=risk_result,
            analysis=analysis_result,
            research=research_result,
        )
        self._save_report(report)
        self._print_summary(decision, risk_result)

        # Save session trace and collect evolution meta
        session.save(data_dir=self.settings.data_dir)
        self._collect_evolution_meta(session)

        # Prune old sessions
        AgentSession.prune_old_sessions(
            data_dir=self.settings.data_dir,
            max_days=getattr(self.settings, "trace_retention_days", 30),
        )

        self._cleanup()
        return report

    def _collect_evolution_meta(self, session: AgentSession):
        """Collect meta-recommendations from session traces for evolution."""
        try:
            from dataclasses import asdict
            session_data = {
                "run_id": session.run_id,
                "mode": session.mode,
                "traces": [asdict(t) for t in session.traces],
            }
            # Remove internal timing attributes
            for trace_dict in session_data["traces"]:
                trace_dict.pop("_start_time", None)
            self.evolver.collect_meta(session_data)
        except Exception as e:
            logger.debug("Failed to collect evolution meta: %s", e)

    # ------------------------------------------------------------------
    # Legacy mode methods (delegate to generic runner)
    # ------------------------------------------------------------------

    def run_screen(self) -> Dict:
        return self.run_mode("screen")

    def run_enter(self) -> Dict:
        return self.run_mode("enter")

    def run_monitor(self) -> Dict:
        return self.run_mode("monitor")

    def run_post_market(self) -> Dict:
        return self.run_mode("post_market")

    def run_learning(self) -> Optional[Dict]:
        return self.run_mode("learning")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_conclusions(self, **kwargs) -> str:
        """Format sub-agent conclusions as a readable text block."""
        parts = []
        for name, conclusion in kwargs.items():
            if conclusion is None:
                parts.append(f"## {name.title()}\nAgent did not produce a conclusion.\n")
            elif isinstance(conclusion, BaseModel):
                parts.append(f"## {name.title()}\n{conclusion.model_dump_json(indent=2)}\n")
            else:
                parts.append(f"## {name.title()}\n{json.dumps(conclusion, indent=2)}\n")
        return "\n".join(parts)

    def _save_screen_results(self, decision: ManagerDecision, risk: Optional[Any]):
        """Save screen results for the enter phase."""
        screen_dir = Path(self.settings.data_dir) / "screen_results"
        screen_dir.mkdir(parents=True, exist_ok=True)

        buy_list = [b.model_dump() for b in decision.buys] if decision.buys else []
        regime = {"name": decision.regime_name}
        if risk and hasattr(risk, "vix_level"):
            regime.update({
                "vix": risk.vix_level,
                "entries_allowed": risk.entries_allowed,
                "sizing_multiplier": risk.sizing_multiplier,
            })

        screen_data = {
            "timestamp": datetime.now().isoformat(),
            "regime": regime,
            "buy_list": buy_list,
            "ranked_count": len(buy_list),
            "agent_mode": True,
        }

        screen_path = screen_dir / "latest.json"
        with open(screen_path, "w") as f:
            json.dump(screen_data, f, indent=2, default=str)

        console.print(f"  Screen results saved: {screen_path}")
        console.print(f"  {len(buy_list)} buy candidates queued for entry phase")

    def _load_screen_results(self) -> Optional[Dict]:
        """Load saved screen results."""
        screen_path = Path(self.settings.data_dir) / "screen_results" / "latest.json"
        if not screen_path.exists():
            return None
        with open(screen_path) as f:
            return json.load(f)

    def _build_report(
        self,
        mode: str,
        decision: Optional[BaseModel],
        data: Optional[BaseModel] = None,
        risk: Optional[BaseModel] = None,
        analysis: Optional[BaseModel] = None,
        research: Optional[BaseModel] = None,
    ) -> Dict:
        """Build a structured report from agent conclusions."""
        report: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "mode": mode,
            "agent_mode": True,
        }

        if data and hasattr(data, "all_healthy"):
            report["data_health"] = {
                "healthy": data.all_healthy,
                "issues": data.issues,
            }

        if risk and hasattr(risk, "regime_name"):
            report["regime"] = {
                "name": risk.regime_name,
                "vix": risk.vix_level,
                "entries_allowed": risk.entries_allowed,
                "sizing_multiplier": risk.sizing_multiplier,
                "confidence": risk.regime_confidence,
            }
            report["portfolio"] = {
                "position_count": risk.position_count,
                "portfolio_equity": risk.portfolio_equity,
                "cash": risk.cash_available,
            }
            report["risk_alerts"] = risk.risk_alerts

        if analysis and hasattr(analysis, "screening_funnel"):
            report["screening"] = analysis.screening_funnel
            report["candidates"] = [c.model_dump() for c in analysis.candidates]

        if decision and hasattr(decision, "buys"):
            report["entries"] = [b.model_dump() for b in decision.buys]
            report["exits"] = [s.model_dump() for s in decision.sells]
            report["manager_reasoning"] = decision.reasoning
            report["alerts"] = decision.alerts

        if research and hasattr(research, "trades_analyzed"):
            report["research"] = {
                "trades_analyzed": research.trades_analyzed,
                "signals_degrading": research.signals_degrading,
                "gaps": research.gaps_flagged,
            }

        return report

    def _save_report(self, report: Dict) -> Path:
        """Save report to JSON file."""
        report_dir = Path(self.settings.manager_report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = report_dir / f"report_{timestamp}.json"

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, default=str)

        latest = report_dir / "latest.json"
        with open(latest, "w") as f:
            json.dump(report, f, indent=2, default=str)

        console.print(f"\nReport saved: {filepath}")
        return filepath

    def _print_summary(
        self,
        decision: Optional[BaseModel],
        risk: Optional[BaseModel] = None,
    ):
        """Print a concise summary to console."""
        if not decision or not hasattr(decision, "entries_approved"):
            return

        console.print()
        console.print(Panel.fit(
            f"[bold]Manager Decision[/bold]\n"
            f"Regime: {decision.regime_name} | Entries: {'APPROVED' if decision.entries_approved else 'BLOCKED'}",
            border_style="blue",
        ))

        if decision.buys:
            console.print(f"\n[bold]Buys ({len(decision.buys)}):[/bold]")
            for b in decision.buys:
                price_str = f"@ ${b.price:.2f} " if b.price else ""
                shares_str = f"x {b.shares} " if b.shares else ""
                cost_str = f"(${b.cost:,.0f}) " if b.cost else ""
                score_str = f"| Score: {b.score:.0f}" if b.score else ""
                console.print(f"  [cyan]{b.ticker}[/cyan] {price_str}{shares_str}{cost_str}{score_str}")

        if decision.sells:
            console.print(f"\n[bold]Exits ({len(decision.sells)}):[/bold]")
            for s in decision.sells:
                color = "green" if (s.pnl_pct or 0) >= 0 else "red"
                price_str = f"@ ${s.price:.2f} " if s.price else ""
                pnl_str = f"| P&L: {s.pnl_pct:+.1f}% " if s.pnl_pct is not None else ""
                console.print(
                    f"  [{color}]{s.ticker}[/{color}] {price_str}{pnl_str}| {s.reason} ({s.urgency})"
                )

        if decision.alerts:
            console.print(f"\n[bold red]Alerts:[/bold red]")
            for alert in decision.alerts:
                console.print(f"  - {alert}")

        if decision.reasoning:
            console.print(f"\n[dim]Reasoning: {decision.reasoning[:200]}...[/dim]"
                          if len(decision.reasoning) > 200 else
                          f"\n[dim]Reasoning: {decision.reasoning}[/dim]")
        console.print()
