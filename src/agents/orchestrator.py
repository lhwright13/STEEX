"""Registry-driven orchestrator for the Claude AI multi-agent trading system.

Each mode launches sub-agents via the claude CLI, backed by the STEEX MCP
server for tool access. A ManagerAgent synthesizes sub-agent conclusions
into a final decision. Agent definitions and mode sequences are loaded
from config/agents.yaml - adding a new agent requires no code changes.

Usage:
    orchestrator = Orchestrator(settings, paper=True, dry_run=False)
    report = orchestrator.run_mode("screen")
"""

import inspect
import json
import logging
import os
import re
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

from .conclusions import LearningConclusion, LearningManagerDecision, ManagerDecision
from .evolution import PromptEvolver
from .registry import AgentRegistry, ModeConfig
from .trace import AgentSession, AgentTrace, extract_tool_calls_from_envelope

logger = logging.getLogger("steex.orchestrator")
console = Console()

DETERMINISTIC_MODES = {"stop_sync", "heartbeat"}

MODE_DISPLAY = {
    "screen": ("Pre-Open Screening", "cyan"),
    "enter": ("Entry Execution", "green"),
    "monitor": ("Position Monitor", "yellow"),
    "post_market": ("Post-Market Wrap-up", "green"),
    "learning": ("Learning Loop", "magenta"),
    "pre_market": ("Pre-Market (Screen)", "cyan"),
}

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
    """Runs trading modes as Claude agent ensembles."""

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

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _find_claude(self) -> str:
        """Locate the claude CLI binary."""
        claude = shutil.which("claude")
        if claude:
            return claude
        for path in ["/usr/local/bin/claude", os.path.expanduser("~/.claude/local/claude")]:
            if os.path.isfile(path):
                return path
        raise FileNotFoundError(
            "claude CLI not found. Install Claude Code: https://claude.ai/claude-code"
        )

    def _get_mcp_config(self) -> str:
        """Write a temp MCP config file for the claude CLI.

        Includes the core STEEX server plus any enabled external MCP servers
        (Alpaca, Polygon/Massive, Alpha Vantage).
        """
        if self._mcp_config_path and os.path.exists(self._mcp_config_path):
            return self._mcp_config_path

        venv_python = str(self._project_root / "venv" / "bin" / "python")
        venv_bin = str(self._project_root / "venv" / "bin")
        server_script = str(self._project_root / "src" / "agents" / "mcp_server.py")

        server_args = []
        if self.paper:
            server_args.append("--paper")
        else:
            server_args.append("--live")
        if self.dry_run:
            server_args.append("--dry-run")

        servers = {
            "steex": {
                "command": venv_python,
                "args": [server_script] + server_args,
            }
        }

        # Alpaca Official MCP - real-time quotes, order management, market data
        if self.settings.mcp_alpaca_enabled:
            alpaca_env = {}
            for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
                val = os.environ.get(var)
                if val:
                    alpaca_env[var] = val
            if self.paper:
                alpaca_env["ALPACA_PAPER"] = "true"
            if alpaca_env.get("ALPACA_API_KEY"):
                servers["alpaca"] = {
                    "command": venv_python,
                    "args": ["-m", "alpaca_mcp_server"],
                    "env": alpaca_env,
                }
            else:
                logger.warning("Alpaca MCP enabled but ALPACA_API_KEY not set")

        # Polygon/Massive MCP - aggregates, snapshots, news, options flow
        if self.settings.mcp_polygon_enabled:
            polygon_key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
            if polygon_key:
                servers["polygon"] = {
                    "command": os.path.join(venv_bin, "mcp_massive"),
                    "env": {"MASSIVE_API_KEY": polygon_key},
                }
            else:
                logger.warning("Polygon MCP enabled but POLYGON_API_KEY not set")

        # Alpha Vantage MCP - technical indicators, economic data, fundamentals
        if self.settings.mcp_alphavantage_enabled:
            av_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
            if av_key:
                servers["alphavantage"] = {
                    "command": os.path.join(venv_bin, "av-mcp"),
                    "args": [av_key],
                }
            else:
                logger.warning("Alpha Vantage MCP enabled but ALPHA_VANTAGE_API_KEY not set")

        config = {"mcpServers": servers}

        fd, path = tempfile.mkstemp(suffix=".json", prefix="steex_mcp_")
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        self._mcp_config_path = path
        return path

    def _cleanup(self):
        """Remove temp files."""
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
        external_servers: Optional[List[str]] = None,
        mode: str = "",
        run_id: str = "",
    ) -> Tuple[Optional[BaseModel], AgentTrace]:
        """Run a single agent via the claude CLI.

        Returns (parsed conclusion or None, trace).
        """
        trace = AgentTrace(run_id=run_id or str(uuid.uuid4())[:8], role=role, mode=mode)
        trace.start()

        # Re-resolve binary path before each invocation to handle
        # mid-session updates (e.g. Homebrew auto-update).
        try:
            claude_bin = self._find_claude()
        except FileNotFoundError:
            claude_bin = self._claude_bin

        cmd = [
            claude_bin,
            "-p", f"{system_prompt}\n\n---\n\n{task_message}",
            "--output-format", "json",
            "--max-turns", str(max_turns),
        ]

        if needs_tools:
            cmd.extend(["--mcp-config", self._get_mcp_config()])
            if allowed_tools:
                tool_perms = [f"mcp__steex__{t}" for t in allowed_tools]
            else:
                tool_perms = ["mcp__steex__*"]
            # Grant wildcard access to external MCP servers assigned to this agent
            for server_name in (external_servers or []):
                tool_perms.append(f"mcp__{server_name}__*")
            cmd.extend(["--allowedTools", ",".join(tool_perms)])

        console.print(f"  Running {role}...", style="dim")
        logger.info("Running %s (max_turns=%d)", role, max_turns)

        try:
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)

            result = subprocess.run(
                cmd,
                text=True,
                timeout=self.settings.agent_timeout_seconds,
                cwd=str(self._project_root),
                env=env,
                **({"stdout": subprocess.PIPE, "stderr": None} if self.verbose
                   else {"capture_output": True}),
            )

            if result.returncode != 0:
                stderr_text = result.stderr or ""
                logger.error("%s failed (rc=%d): %s", role, result.returncode, stderr_text[:500])
                console.print(f"  [red]{role} failed[/red]")
                trace.finish(success=False, error=f"exit code {result.returncode}")
                return None, trace

            # Parse CLI JSON envelope
            try:
                envelope = json.loads(result.stdout)
            except json.JSONDecodeError:
                logger.error("%s: invalid CLI JSON output", role)
                console.print(f"  [red]{role}: invalid CLI output[/red]")
                trace.finish(success=False, error="invalid JSON output")
                return None, trace

            if envelope.get("is_error"):
                error_msg = envelope.get("result", "unknown error")
                logger.error("%s returned error: %s", role, error_msg)
                console.print(f"  [red]{role} error: {error_msg}[/red]")
                trace.finish(success=False, error=str(error_msg))
                return None, trace

            for tc in extract_tool_calls_from_envelope(envelope):
                trace.add_tool_call(tc)

            agent_text = envelope.get("result", "")
            trace.raw_output = agent_text

            conclusion = self._parse_conclusion(agent_text, conclusion_type, role)
            if conclusion:
                trace.set_conclusion(conclusion.model_dump())
                trace.finish(success=True)
                self._print_trace_summary(role, trace, conclusion)
            else:
                console.print(f"  [yellow]{role}: could not parse conclusion[/yellow]")
                trace.finish(success=False, error="conclusion parse failed")

            return conclusion, trace

        except subprocess.TimeoutExpired:
            logger.error("%s timed out after %ds", role, self.settings.agent_timeout_seconds)
            console.print(f"  [red]{role} timed out[/red]")
            trace.finish(success=False, error="timeout")
            return None, trace
        except Exception as e:
            logger.error("%s error: %s", role, e)
            console.print(f"  [red]{role} error: {e}[/red]")
            trace.finish(success=False, error=str(e))
            return None, trace

    def _print_trace_summary(self, role: str, trace: AgentTrace, conclusion: BaseModel):
        """Print a one-line status after each agent completes."""
        tools_str = ""
        if trace.tools_called:
            tool_names = [tc.get("tool", "?") for tc in trace.tools_called]
            tools_str = f" | Tools: {', '.join(tool_names)}"

        console.print(f"  [green]{role} complete[/green] ({trace.duration_seconds:.1f}s{tools_str})")

        meta = getattr(conclusion, "meta", None)
        if meta:
            for s in meta.prompt_suggestions or []:
                console.print(f"    [dim]Prompt: {s}[/dim]")
            for s in meta.tool_suggestions or []:
                console.print(f"    [dim]Tool: {s}[/dim]")
            for s in meta.process_suggestions or []:
                console.print(f"    [dim]Process: {s}[/dim]")

    def _parse_conclusion(
        self, text: str, model_class: Type[BaseModel], role: str,
    ) -> Optional[BaseModel]:
        """Extract and validate a JSON conclusion from agent text output.

        Tries three strategies: direct parse, fenced code block, and
        brace-matching to find the last JSON object in the text.
        """
        text = text.strip()

        # Direct parse
        try:
            return model_class.model_validate(json.loads(text))
        except (json.JSONDecodeError, Exception):
            pass

        # Fenced code block
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if fenced:
            try:
                return model_class.model_validate(json.loads(fenced.group(1).strip()))
            except (json.JSONDecodeError, Exception):
                pass

        # Brace-match the last JSON object
        last_brace = text.rfind("}")
        if last_brace == -1:
            logger.warning("%s: no JSON object found in output", role)
            return None

        depth, start = 0, last_brace
        for i in range(last_brace, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start = i
                    break

        try:
            return model_class.model_validate(json.loads(text[start:last_brace + 1]))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("%s: failed to parse extracted JSON: %s", role, e)
            return None

    def _fallback(self, mode: str, **kwargs) -> Dict:
        """Fall back to deterministic QuantManager."""
        console.print("[yellow]Agent mode failed, falling back to deterministic pipeline[/yellow]")
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

        sig = inspect.signature(method)
        accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return method(**accepted)

    # ------------------------------------------------------------------
    # Mode execution
    # ------------------------------------------------------------------

    def run_mode(self, mode: str) -> Optional[Dict]:
        """Entry point: route to registry-driven agent pipeline or deterministic fallback."""
        effective_mode = "screen" if mode == "pre_market" else mode

        if effective_mode in DETERMINISTIC_MODES:
            return self._run_deterministic(effective_mode)

        if effective_mode == "test_roundtrip":
            # test_roundtrip is parameterized; use defaults if invoked through
            # the generic run_mode path. Callers with custom ticker/amount
            # should call run_test_roundtrip directly.
            return self.run_test_roundtrip("AAPL", 1000.0)

        mode_config = self.registry.get_mode(effective_mode)
        if mode_config is None:
            logger.error("Unknown mode: %s", effective_mode)
            return self._fallback(effective_mode, dry_run=self.dry_run, verbose=self.verbose)

        try:
            return self._run_pipeline(effective_mode, mode_config)
        except FileNotFoundError as e:
            console.print(f"[red]Agent mode unavailable: {e}[/red]")
            return self._fallback(
                mode_config.fallback or effective_mode,
                dry_run=self.dry_run, verbose=self.verbose,
            )

    def run_test_roundtrip(self, ticker: str, amount_usd: float) -> Optional[Dict]:
        """Run the test_trader agent against a single ticker/amount and return its conclusion.

        Bypasses the manager-synthesis pipeline used by trading modes - this
        is a self-contained verification of the agent -> MCP -> broker path.
        Falls back to the deterministic QuantManager.run_test_roundtrip if
        the agent fails.
        """
        mode = "test_roundtrip"
        mode_config = self.registry.get_mode(mode)
        if mode_config is None or not mode_config.sub_agents:
            logger.error("test_roundtrip mode missing or has no sub_agents in agents.yaml")
            return self._fallback_test_roundtrip(ticker, amount_usd)

        agent_name = mode_config.sub_agents[0]
        agent_config = self.registry.get_agent(agent_name)
        if agent_config is None:
            logger.error("test_trader agent missing from registry")
            return self._fallback_test_roundtrip(ticker, amount_usd)

        run_id = str(uuid.uuid4())[:8]
        session = AgentSession(run_id=run_id, mode=mode)

        console.print(Panel.fit(
            f"[bold]Test Roundtrip (Agent Mode)[/bold]\n"
            f"Ticker: {ticker}  Amount: ${amount_usd:.2f}  Paper: {self.paper}",
            border_style="cyan",
        ))

        task_message = (
            f"Run a paper-mode buy/sell roundtrip on {ticker} for "
            f"${amount_usd:.2f}. Use place_paper_order for both legs. "
            f"Do not change the ticker or amount. Dry run: {self.dry_run}."
        )

        try:
            prompt = self.registry.resolve_prompt(agent_name, data_dir=self.settings.data_dir)
            conclusion_type = self.registry.resolve_conclusion_type(agent_name)
            result, trace = self._run_agent(
                role="TestTraderAgent",
                system_prompt=prompt,
                task_message=task_message,
                conclusion_type=conclusion_type,
                max_turns=agent_config.max_turns,
                needs_tools=agent_config.needs_tools,
                allowed_tools=agent_config.allowed_tools,
                external_servers=agent_config.external_servers,
                mode=mode,
                run_id=run_id,
            )
            session.add_trace(trace)
        except FileNotFoundError as e:
            console.print(f"[red]Agent mode unavailable: {e}[/red]")
            session.fallback_used = True
            self._finalize_session(session)
            return self._fallback_test_roundtrip(ticker, amount_usd)

        if result is None:
            session.fallback_used = True
            self._finalize_session(session)
            self._cleanup()
            return self._fallback_test_roundtrip(ticker, amount_usd)

        report = {"mode": mode, "ticker": ticker, "amount_usd": amount_usd,
                  "conclusion": result.model_dump()}
        session.set_manager_decision(report)
        self._save_report(report)
        self._finalize_session(session)
        self._cleanup()
        return report

    def _fallback_test_roundtrip(self, ticker: str, amount_usd: float) -> Optional[Dict]:
        """Deterministic fallback for test_roundtrip - bypasses agents."""
        from src.strategy.manager import QuantManager
        try:
            manager = QuantManager(settings=self.settings)
        except RuntimeError:
            self.settings.broker_enabled = False
            manager = QuantManager(settings=self.settings)
        return manager.run_test_roundtrip(
            ticker=ticker, amount_usd=amount_usd, dry_run=self.dry_run,
        )

    def _run_deterministic(self, mode: str) -> Optional[Dict]:
        """Run a mode through the deterministic QuantManager (no AI)."""
        from src.strategy.manager import QuantManager

        try:
            manager = QuantManager(settings=self.settings)
        except RuntimeError:
            self.settings.broker_enabled = False
            manager = QuantManager(settings=self.settings)

        method = getattr(manager, f"run_{mode}", None)
        if method:
            return method(dry_run=self.dry_run, verbose=self.verbose)
        return None

    def _run_pipeline(self, mode: str, mode_config: ModeConfig) -> Dict:
        """Execute the full agent pipeline for a mode."""
        run_id = str(uuid.uuid4())[:8]
        session = AgentSession(run_id=run_id, mode=mode)

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

        # 1. Sub-agents
        console.print("\n[bold]1. Sub-Agent Analysis[/bold]")
        conclusions = self._run_sub_agents(mode_config, task_context, session, mode, run_id)

        # Bail to fallback if a critical agent failed
        for critical in mode_config.critical_agents:
            if conclusions.get(critical) is None:
                session.fallback_used = True
                self._finalize_session(session)
                return self._fallback(
                    mode_config.fallback or mode,
                    dry_run=self.dry_run, auto_confirm=self.auto_confirm, verbose=self.verbose,
                )

        # 2. Manager synthesis
        console.print("\n[bold]2. Manager Synthesis[/bold]")
        decision = self._run_manager(mode_config, mode, conclusions, screen_data, today, session, run_id)

        if decision is None:
            session.fallback_used = True
            self._finalize_session(session)
            return self._fallback(
                mode_config.fallback or mode,
                dry_run=self.dry_run, auto_confirm=self.auto_confirm, verbose=self.verbose,
            )

        session.set_manager_decision(decision.model_dump())

        # Post-actions
        if "save_screen" in mode_config.post_actions:
            self._save_screen_results(decision, conclusions.get("risk"))

        # 3. Execution
        if mode_config.executor and isinstance(decision, ManagerDecision):
            self._run_execution(mode, mode_config, decision, today, session, run_id)

        # Prompt evolution post-action (learning mode)
        if "evolve_prompts" in mode_config.post_actions:
            self._run_prompt_evolution(conclusions, decision, session)

        # 4. Report agent (post_market only)
        if "report" in mode_config.post_actions and mode == "post_market":
            self._run_report_agent(mode, today, session, run_id)

        # 5. Build and save structured report
        step_num = len(mode_config.sub_agents) + 2 + (1 if mode_config.executor else 0)
        console.print(f"\n[bold]{step_num}. Report[/bold]")

        report = self._build_report(
            mode, decision,
            data=conclusions.get("data"),
            risk=conclusions.get("risk"),
            analysis=conclusions.get("analysis"),
            research=conclusions.get("research"),
            learning=conclusions.get("learning_agent"),
        )
        self._save_report(report)
        self._print_summary(decision, conclusions.get("risk"))

        self._finalize_session(session)
        self._cleanup()
        return report

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _run_sub_agents(
        self, mode_config: ModeConfig, task_context: str,
        session: AgentSession, mode: str, run_id: str,
    ) -> Dict[str, Optional[BaseModel]]:
        """Run all sub-agents in sequence, collecting conclusions."""
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
                external_servers=agent_config.external_servers,
                mode=mode,
                run_id=run_id,
            )
            conclusions[agent_name] = result
            session.add_trace(trace)

        return conclusions

    def _run_manager(
        self, mode_config: ModeConfig, mode: str,
        conclusions: Dict[str, Optional[BaseModel]],
        screen_data: Optional[Dict], today: str,
        session: AgentSession, run_id: str,
    ) -> Optional[BaseModel]:
        """Synthesize sub-agent conclusions via the ManagerAgent."""
        conclusions_text = self._format_conclusions(**conclusions)

        if screen_data:
            conclusions_text += f"\n\nScreen Results (from earlier):\n{json.dumps(screen_data, indent=2)}"

        template = MANAGER_TASK_TEMPLATES.get(mode, f"Mode: {mode}.\nSynthesize conclusions.")
        manager_task = (
            f"Today is {today}. {template}\n"
            f"Dry run: {self.dry_run}.\n\n"
            f"Sub-agent conclusions:\n{conclusions_text}\n\n"
        )

        manager_config = self.registry.get_agent(mode_config.manager)
        if manager_config is None:
            logger.error("Manager agent not in registry: %s", mode_config.manager)
            return None

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
        return decision

    def _run_execution(
        self, mode: str, mode_config: ModeConfig,
        decision: ManagerDecision, today: str,
        session: AgentSession, run_id: str,
    ):
        """Run the ExecutionAgent if there are trades to execute."""
        should_execute = False
        if mode == "enter":
            # Exits always fire; entries only when approved
            if decision.sells or (decision.entries_approved and decision.buys):
                should_execute = True
        elif mode in ("monitor", "post_market") and decision.sells:
            should_execute = True

        if not should_execute:
            if mode == "enter":
                console.print("  No trades to execute")
            return

        step_num = len(mode_config.sub_agents) + 2
        console.print(f"\n[bold]{step_num}. Execution[/bold]")

        exec_config = self.registry.get_agent(mode_config.executor)
        if not exec_config:
            return

        exec_prompt = self.registry.resolve_prompt(mode_config.executor, data_dir=self.settings.data_dir)
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
            external_servers=exec_config.external_servers,
            mode=mode,
            run_id=run_id,
        )
        session.add_trace(exec_trace)

    def _run_report_agent(self, mode: str, today: str, session: AgentSession, run_id: str):
        """Run the ReportAgent for post-market summaries."""
        report_config = self.registry.get_agent("report")
        if not report_config:
            return

        report_prompt = self.registry.resolve_prompt("report", data_dir=self.settings.data_dir)
        _, report_trace = self._run_agent(
            role="ReportAgent",
            system_prompt=report_prompt,
            task_message=f"Generate the {mode} report for {today}.",
            conclusion_type=self.registry.resolve_conclusion_type("report"),
            max_turns=report_config.max_turns,
            needs_tools=report_config.needs_tools,
            allowed_tools=report_config.allowed_tools,
            external_servers=report_config.external_servers,
            mode=mode,
            run_id=run_id,
        )
        session.add_trace(report_trace)

    def _run_prompt_evolution(
        self,
        conclusions: Dict[str, Optional[BaseModel]],
        manager_decision: Optional[BaseModel],
        session: AgentSession,
    ):
        """Execute prompt evolution post-action for learning mode.

        Uses the LearningManagerDecision to determine which prompt
        evolutions to apply, then calls PromptEvolver with data-backed
        rationale from the LearningConclusion.
        """
        if not isinstance(manager_decision, LearningManagerDecision):
            return

        approved = manager_decision.prompt_evolutions_approved
        if not approved:
            return

        # Find the LearningConclusion with prompt evolution recommendations
        learning_conclusion = None
        for name, conclusion in conclusions.items():
            if isinstance(conclusion, LearningConclusion):
                learning_conclusion = conclusion
                break

        if not learning_conclusion:
            return

        # Build lookup of recommendations by agent name
        rec_lookup: Dict[str, Any] = {}
        for rec in learning_conclusion.prompt_evolution_recommendations:
            rec_lookup[rec.agent_name] = rec

        console.print("\n[bold]Prompt Evolution[/bold]")
        evolved_count = 0

        for agent_name in approved:
            rec = rec_lookup.get(agent_name)
            if not rec:
                console.print(f"  [yellow]{agent_name}: no recommendation found, skipping[/yellow]")
                continue

            try:
                current_prompt = self.registry.resolve_prompt(
                    agent_name, data_dir=self.settings.data_dir
                )
            except ValueError:
                console.print(f"  [yellow]{agent_name}: agent not in registry, skipping[/yellow]")
                continue

            success = self.evolver.evolve_agent_with_rationale(
                agent_name=agent_name,
                current_prompt=current_prompt,
                suggestion=rec.suggestion,
                rationale=rec.rationale,
                claude_bin=self._claude_bin,
            )

            if success:
                evolved_count += 1
                console.print(f"  [green]{agent_name}: prompt evolved[/green]")
                logger.info(
                    "Evolved prompt for %s: %s (rationale: %s)",
                    agent_name, rec.suggestion, rec.rationale,
                )
            else:
                console.print(f"  [yellow]{agent_name}: evolution skipped (rate limit or safety)[/yellow]")

        if evolved_count:
            console.print(f"  Evolved {evolved_count} prompt(s)")
        else:
            console.print("  No prompts evolved this cycle")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _finalize_session(self, session: AgentSession):
        """Save trace, collect evolution meta, prune old sessions."""
        session.save(data_dir=self.settings.data_dir)
        self._collect_evolution_meta(session)
        AgentSession.prune_old_sessions(
            data_dir=self.settings.data_dir,
            max_days=getattr(self.settings, "trace_retention_days", 30),
        )

    def _collect_evolution_meta(self, session: AgentSession):
        """Forward meta-recommendations from traces to the prompt evolver."""
        try:
            from dataclasses import asdict
            session_data = {
                "run_id": session.run_id,
                "mode": session.mode,
                "traces": [asdict(t) for t in session.traces],
            }
            for trace_dict in session_data["traces"]:
                trace_dict.pop("_start_time", None)
            self.evolver.collect_meta(session_data)
        except Exception as e:
            logger.debug("Failed to collect evolution meta: %s", e)

    # ------------------------------------------------------------------
    # Legacy mode methods
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
    # Data helpers
    # ------------------------------------------------------------------

    def _format_conclusions(self, **kwargs) -> str:
        """Format sub-agent conclusions as markdown for the manager prompt."""
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
        """Persist screen results for the enter phase."""
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

        console.print(f"  Screen results saved ({len(buy_list)} candidates)")

    def _load_screen_results(self) -> Optional[Dict]:
        """Load screen results saved by a previous screen run."""
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
        learning: Optional[BaseModel] = None,
    ) -> Dict:
        """Build a structured report dict from agent conclusions."""
        report: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "mode": mode,
            "agent_mode": True,
        }

        if data and hasattr(data, "all_healthy"):
            report["data_health"] = {"healthy": data.all_healthy, "issues": data.issues}

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

        if learning and isinstance(learning, LearningConclusion):
            report["learning"] = {
                "trades_analyzed": learning.trades_analyzed,
                "win_rate": learning.win_rate,
                "signals_degrading": learning.signals_degrading,
                "weight_changes_proposed": learning.weight_changes_proposed,
                "oos_validated": learning.oos_validated,
                "config_changes_applied": learning.config_changes_applied,
                "prompt_evolutions": [
                    r.model_dump() for r in learning.prompt_evolution_recommendations
                ],
                "agent_insights_used": learning.agent_insights_used,
                "gaps_flagged": learning.gaps_flagged,
                "gaps_resolved": learning.gaps_resolved,
            }

        if isinstance(decision, LearningManagerDecision):
            report["manager_decision"] = {
                "config_changes_approved": decision.config_changes_approved,
                "prompt_evolutions_approved": decision.prompt_evolutions_approved,
                "prompt_evolutions_rejected": decision.prompt_evolutions_rejected,
                "escalations": decision.escalations,
                "reasoning": decision.reasoning,
            }

        return report

    def _save_report(self, report: Dict) -> Path:
        """Write report to timestamped JSON file and update latest pointer."""
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

    def _print_summary(self, decision: Optional[BaseModel], risk: Optional[BaseModel] = None):
        """Print the manager's final decision to the console."""
        if not decision or not hasattr(decision, "entries_approved"):
            return

        console.print()
        console.print(Panel.fit(
            f"[bold]Manager Decision[/bold]\n"
            f"Regime: {decision.regime_name} | "
            f"Entries: {'APPROVED' if decision.entries_approved else 'BLOCKED'}",
            border_style="blue",
        ))

        if decision.buys:
            console.print(f"\n[bold]Buys ({len(decision.buys)}):[/bold]")
            for b in decision.buys:
                parts = [f"[cyan]{b.ticker}[/cyan]"]
                if b.price:
                    parts.append(f"@ ${b.price:.2f}")
                if b.shares:
                    parts.append(f"x {b.shares}")
                if b.cost:
                    parts.append(f"(${b.cost:,.0f})")
                if b.score:
                    parts.append(f"| Score: {b.score:.0f}")
                console.print(f"  {' '.join(parts)}")

        if decision.sells:
            console.print(f"\n[bold]Exits ({len(decision.sells)}):[/bold]")
            for s in decision.sells:
                color = "green" if (s.pnl_pct or 0) >= 0 else "red"
                parts = [f"[{color}]{s.ticker}[/{color}]"]
                if s.price:
                    parts.append(f"@ ${s.price:.2f}")
                if s.pnl_pct is not None:
                    parts.append(f"| P&L: {s.pnl_pct:+.1f}%")
                parts.append(f"| {s.reason} ({s.urgency})")
                console.print(f"  {' '.join(parts)}")

        if decision.alerts:
            console.print("\n[bold red]Alerts:[/bold red]")
            for alert in decision.alerts:
                console.print(f"  - {alert}")

        if decision.reasoning:
            text = decision.reasoning
            if len(text) > 200:
                text = text[:200] + "..."
            console.print(f"\n[dim]Reasoning: {text}[/dim]")

        console.print()
