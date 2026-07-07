"""LangGraph node functions for the multi-agent orchestration pipeline.

Nodes are stateless callables that read from PipelineState and return partial
state updates. They wrap the subprocess-based agent invocations and MCP
configuration logic extracted from orchestrator.py.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from langgraph.types import Send
from pydantic import BaseModel
from rich.console import Console

from .conclusions import LearningConclusion, LearningManagerDecision, ManagerDecision
from .registry import ModeConfig
from .state import PipelineState, RunnerContext
from .trace import AgentTrace, parse_stream_json_output

logger = logging.getLogger("steex.nodes")
console = Console()


def get_mcp_config(ctx: RunnerContext) -> str:
    """Write a temp MCP config file for the claude CLI.

    Includes the core STEEX server plus any enabled external MCP servers.
    Caches the path in ctx.mcp_config_path.
    """
    if ctx.mcp_config_path and os.path.exists(ctx.mcp_config_path):
        return ctx.mcp_config_path

    venv_python = str(ctx.project_root / "venv" / "bin" / "python")
    venv_bin = str(ctx.project_root / "venv" / "bin")
    server_script = str(ctx.project_root / "src" / "agents" / "mcp_server.py")

    # Broker mode follows the configured settings, not the bare ctx.paper flag.
    # broker_paper defaults to True (safety net), so a run without an explicit
    # --live must stay on the paper endpoint — launching --live with paper keys
    # produces Alpaca auth error 40110000 on every real call (e.g. sync_broker).
    server_args = []
    if not ctx.settings.broker_enabled:
        server_args.append("--no-broker")
    elif ctx.settings.broker_paper:
        server_args.append("--paper")
    else:
        server_args.append("--live")
    if ctx.dry_run:
        server_args.append("--dry-run")

    servers = {
        "steex": {
            "command": venv_python,
            "args": [server_script] + server_args,
        }
    }

    if ctx.settings.mcp_alpaca_enabled:
        alpaca_env = {}
        for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            val = os.environ.get(var)
            if val:
                alpaca_env[var] = val
        # Official alpaca-mcp-server reads ALPACA_PAPER_TRADE (default "true");
        # set it explicitly for both modes so live runs aren't silently paper.
        alpaca_env["ALPACA_PAPER_TRADE"] = "true" if ctx.settings.broker_paper else "false"
        if alpaca_env.get("ALPACA_API_KEY"):
            # Official server (PyPI: alpaca-mcp-server) installs a console
            # script entry point; it has no `-m` runnable module.
            servers["alpaca"] = {
                "command": os.path.join(venv_bin, "alpaca-mcp-server"),
                "args": [],
                "env": alpaca_env,
            }
        else:
            logger.warning("Alpaca MCP enabled but ALPACA_API_KEY not set")

    if ctx.settings.mcp_polygon_enabled:
        polygon_key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
        if polygon_key:
            servers["polygon"] = {
                "command": os.path.join(venv_bin, "mcp_massive"),
                "env": {"MASSIVE_API_KEY": polygon_key},
            }
        else:
            logger.warning("Polygon MCP enabled but POLYGON_API_KEY not set")

    if ctx.settings.mcp_alphavantage_enabled:
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
    ctx.mcp_config_path = path
    return path


def cleanup_mcp(ctx: RunnerContext):
    """Remove temp MCP config file."""
    if ctx.mcp_config_path and os.path.exists(ctx.mcp_config_path):
        os.unlink(ctx.mcp_config_path)
        ctx.mcp_config_path = None


def parse_conclusion(
    text: str, model_class: Type[BaseModel], role: str,
) -> Optional[BaseModel]:
    """Extract and validate a JSON conclusion from agent text output.

    Tries three strategies: direct parse, fenced code block, and
    brace-matching to find the last JSON object in the text.
    """
    text = text.strip()

    try:
        return model_class.model_validate(json.loads(text))
    except (json.JSONDecodeError, Exception):
        pass

    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fenced:
        try:
            return model_class.model_validate(json.loads(fenced.group(1).strip()))
        except (json.JSONDecodeError, Exception):
            pass

    # Try every balanced {...} object in the text, largest first. The old code
    # walked back from the LAST "}" only, so a trailing stray brace (a "meta"
    # example, a brace in prose) shadowed the real conclusion. The real object is
    # almost always the biggest one that validates against the model.
    spans = _balanced_json_spans(text)
    if not spans:
        logger.warning("%s: no JSON object found in output", role)
        return None

    last_err = None
    for start, end in sorted(spans, key=lambda s: s[1] - s[0], reverse=True):
        try:
            return model_class.model_validate(json.loads(text[start:end]))
        except (json.JSONDecodeError, Exception) as e:
            last_err = e
            continue

    logger.warning(
        "%s: found %d JSON object(s) but none validated as %s: %s",
        role, len(spans), model_class.__name__, last_err,
    )
    return None


def _balanced_json_spans(text: str) -> List[Tuple[int, int]]:
    """Return (start, end) slices of every top-level balanced {...} object.

    Tracks brace depth so nested objects are kept whole and only complete
    top-level objects are returned. (Braces inside string literals are not
    special-cased — same limitation as the prior rfind approach, but this no
    longer lets a trailing stray object hide the real one.)
    """
    spans: List[Tuple[int, int]] = []
    depth = 0
    start: Optional[int] = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i + 1))
                    start = None
    return spans


def _classify_transient_cli_error(
    stdout: Optional[str], stderr: Optional[str]
) -> Optional[str]:
    """Return a short label if a non-zero CLI exit looks transient (retryable), else None.

    The claude CLI surfaces upstream API failures in its JSON stdout
    (e.g. an error envelope carrying "401") and/or stderr. Auth blips (401),
    rate limits (429) and 5xx/overloaded are worth a bounded retry; everything
    else (bad request, config, parse) is terminal. A single un-retried 401 took
    out all five 06-01 agents and silently laundered the day onto the fallback.
    """
    blob = f"{stdout or ''}\n{stderr or ''}".lower()
    if not blob.strip():
        return None
    # Hard logout is TERMINAL, not transient: retrying can never succeed until a
    # human runs /login. Check first — on 07-02 raw substring matching sometimes
    # classified a logged-out CLI as "server 502" because a session UUID in the
    # init JSON happened to contain a 5xx digit run, burning the retry budget.
    if "authentication_failed" in blob or "not logged in" in blob:
        return None
    # Max-turns exhaustion is a workflow-length problem, not API weather —
    # retrying replays the same too-long workflow (07-06: 3 x ~13min wasted
    # in the enter run when a stray digit-run matched "500").
    if "error_max_turns" in blob:
        return None
    # Word-boundary match so status codes aren't found inside UUIDs/hex ids.
    for code, label in (
        ("401", "auth 401"), ("403", "auth 403"), ("429", "rate-limit 429"),
        ("500", "server 500"), ("502", "server 502"), ("503", "server 503"),
        ("529", "overloaded 529"),
    ):
        if re.search(rf"\b{code}\b", blob):
            return label
    if any(k in blob for k in ("overloaded", "rate limit", "econnreset", "etimedout")):
        return "transient"
    return None


def _is_auth_logout(stdout: Optional[str], stderr: Optional[str]) -> bool:
    """Hard CLI logout — the whole agent layer is down until /login is run."""
    blob = f"{stdout or ''}\n{stderr or ''}".lower()
    return "authentication_failed" in blob or "not logged in" in blob


def _alert_auth_logout(ctx: RunnerContext, role: str) -> None:
    """Telegram the operator ONCE per UTC day that the CLI is logged out.

    Both prior auth outages (06-01, 07-02) ran all day with zero operator
    notification — every agent silently fell back to the deterministic
    pipeline. Idempotent via the user_updates id; deterministic summarizer
    because the LLM path is exactly what's broken.
    """
    try:
        from datetime import datetime, timezone
        from src.notify.event_summary import summarize_and_notify
        day = datetime.now(timezone.utc).date().isoformat()
        summarize_and_notify(
            {
                "id": f"auth_down_{day}",
                "type": "system",
                "title": "🚨 Claude CLI logged out — agent layer down",
                "context": {"first_failed_agent": role,
                            "fix": "ssh to the mini and run: claude /login"},
            },
            settings=ctx.settings,
            summarizer=lambda _e: (
                f"The claude CLI on the trading box is logged out ({role} failed with "
                f"authentication_failed). All agents are falling back to the deterministic "
                f"pipeline and the event trigger is blind until you run /login on the mini."
            ),
        )
    except Exception:
        logger.exception("auth-logout alert failed")


def run_agent(
    ctx: RunnerContext,
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
    model: Optional[str] = None,
) -> Tuple[Optional[BaseModel], AgentTrace]:
    """Run a single agent via the claude CLI subprocess.

    `model` optionally pins the Claude model (e.g. "haiku") — used for cheap,
    high-frequency calls like the event ticker resolver. Defaults to the CLI's
    configured model.

    Returns (parsed conclusion or None, trace).
    """
    trace = AgentTrace(run_id=run_id or str(uuid.uuid4())[:8], role=role, mode=mode)
    trace.start()

    claude_bin = shutil.which("claude")
    if not claude_bin:
        # Include the Homebrew path: cron's minimal PATH can omit it.
        for path in ["/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                     os.path.expanduser("~/.claude/local/claude")]:
            if os.path.isfile(path):
                claude_bin = path
                break

    if not claude_bin:
        logger.error("claude CLI not found")
        trace.finish(success=False, error="claude binary not found")
        return None, trace

    cmd = [
        claude_bin,
        "-p", f"{system_prompt}\n\n---\n\n{task_message}",
        # stream-json (+ --verbose, required) emits one NDJSON line per turn,
        # including the tool_use blocks needed for tool-call telemetry. The
        # terminal {"type":"result"} line is the same envelope --output-format
        # json produced, so downstream handling is unchanged.
        "--output-format", "stream-json", "--verbose",
        "--max-turns", str(max_turns),
    ]
    if model:
        cmd.extend(["--model", model])

    if needs_tools:
        cmd.extend(["--mcp-config", get_mcp_config(ctx)])
        if allowed_tools:
            tool_perms = [f"mcp__steex__{t}" for t in allowed_tools]
        else:
            tool_perms = ["mcp__steex__*"]
        for server_name in (external_servers or []):
            tool_perms.append(f"mcp__{server_name}__*")
        cmd.extend(["--allowedTools", ",".join(tool_perms)])

    console.print(f"  Running {role}...", style="dim")
    logger.info("Running %s (max_turns=%d)", role, max_turns)

    try:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        # Bounded retry on transient upstream errors (401/429/5xx/overloaded).
        # Without this a single auth blip aborts a critical agent and launders the
        # whole run onto the deterministic fallback, mislabeled as a logic failure.
        max_attempts = 3
        result = None
        for attempt in range(1, max_attempts + 1):
            result = subprocess.run(
                cmd,
                text=True,
                timeout=ctx.settings.agent_timeout_seconds,
                cwd=str(ctx.project_root),
                env=env,
                stdin=subprocess.DEVNULL,  # -p needs no stdin; avoids a 3s wait/warning
                **({"stdout": subprocess.PIPE, "stderr": None} if ctx.verbose
                   else {"capture_output": True}),
            )

            if result.returncode == 0:
                break

            transient = _classify_transient_cli_error(result.stdout, result.stderr)
            if transient and attempt < max_attempts:
                wait = 2 ** attempt
                logger.warning(
                    "%s transient CLI error (%s) — retry %d/%d after %ds",
                    role, transient, attempt, max_attempts - 1, wait,
                )
                console.print(f"  [yellow]{role} transient {transient}, retrying...[/yellow]")
                time.sleep(wait)
                continue

            stderr_text = result.stderr or ""
            stdout_text = result.stdout or ""
            fail_dir = Path(ctx.settings.data_dir) / "agents" / "failures"
            fail_dir.mkdir(parents=True, exist_ok=True)
            fail_path = fail_dir / (
                f"{role}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                f"_rc{result.returncode}.log"
            )
            fail_path.write_text(
                f"=== STDERR ===\n{stderr_text}\n\n=== STDOUT ===\n{stdout_text}\n"
            )
            logger.error(
                "%s failed (rc=%d). stderr[:500]=%r stdout[:500]=%r (full: %s)",
                role, result.returncode, stderr_text[:500], stdout_text[:500], fail_path,
            )
            console.print(f"  [red]{role} failed[/red] (see {fail_path})")
            # Hard logout: alert the operator (once/day) — both prior auth
            # outages (06-01, 07-02) ran silently all day on the fallback.
            if _is_auth_logout(stdout_text, stderr_text):
                _alert_auth_logout(ctx, role)
            # Distinct label so an outage isn't mislabeled as a risk-logic failure.
            label = (
                f"{transient} outage (exhausted retries)" if transient
                else f"exit code {result.returncode}"
            )
            trace.finish(success=False, error=f"{label}: {fail_path}")
            return None, trace

        # stream-json: collect tool_use calls from the assistant turns and the
        # terminal result line (same envelope shape the json format produced).
        envelope, tool_calls = parse_stream_json_output(result.stdout)
        if envelope is None:
            logger.error("%s: no result envelope in CLI output", role)
            console.print(f"  [red]{role}: invalid CLI output[/red]")
            trace.finish(success=False, error="invalid CLI output (no result line)")
            return None, trace

        if envelope.get("is_error"):
            error_msg = envelope.get("result", "unknown error")
            logger.error("%s returned error: %s", role, error_msg)
            console.print(f"  [red]{role} error: {error_msg}[/red]")
            if _is_auth_logout(str(error_msg), None):
                _alert_auth_logout(ctx, role)
            trace.finish(success=False, error=str(error_msg))
            return None, trace

        for tc in tool_calls:
            trace.add_tool_call(tc)

        # Observability: surface two failure modes even when tool_calls is empty —
        #   1. a tool-using agent that finished in <=1 turn never actually called a tool
        #   2. permission denials silently blocked tools the agent tried to use
        if needs_tools and not trace.tools_called:
            num_turns = envelope.get("num_turns", 0)
            if num_turns is not None and num_turns <= 1:
                logger.warning(
                    "%s needs_tools but finished in %s turn(s) with no tool calls — "
                    "likely emitted JSON without using MCP tools",
                    role, num_turns,
                )
        denials = envelope.get("permission_denials") or []
        if denials:
            logger.warning(
                "%s had %d permission denial(s): %s",
                role, len(denials),
                [d.get("tool_name", d) if isinstance(d, dict) else d for d in denials][:5],
            )

        agent_text = envelope.get("result", "")
        trace.raw_output = agent_text

        conclusion = parse_conclusion(agent_text, conclusion_type, role)
        if conclusion:
            trace.set_conclusion(conclusion.model_dump())
            trace.finish(success=True)
            _print_trace_summary(role, trace, conclusion)
        else:
            fail_dir = Path(ctx.settings.data_dir) / "agents" / "failures"
            fail_dir.mkdir(parents=True, exist_ok=True)
            fail_path = fail_dir / (
                f"{role}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_parse.log"
            )
            fail_path.write_text(
                f"=== conclusion_type: {conclusion_type.__name__} ===\n"
                f"=== AGENT OUTPUT ===\n{agent_text}\n"
            )
            logger.error(
                "%s: could not parse %s from output (saved: %s)",
                role, conclusion_type.__name__, fail_path,
            )
            console.print(
                f"  [yellow]{role}: could not parse conclusion[/yellow] (see {fail_path})"
            )
            trace.finish(success=False, error=f"conclusion parse failed: {fail_path}")

        return conclusion, trace

    except subprocess.TimeoutExpired:
        logger.error("%s timed out after %ds", role, ctx.settings.agent_timeout_seconds)
        console.print(f"  [red]{role} timed out[/red]")
        trace.finish(success=False, error="timeout")
        return None, trace
    except Exception as e:
        logger.error("%s error: %s", role, e)
        console.print(f"  [red]{role} error: {e}[/red]")
        trace.finish(success=False, error=str(e))
        return None, trace


def _print_trace_summary(role: str, trace: AgentTrace, conclusion: BaseModel):
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


def _trace_to_dict(trace: AgentTrace) -> dict:
    """Convert AgentTrace to a serializable dict."""
    return {
        "run_id": trace.run_id,
        "role": trace.role,
        "mode": trace.mode,
        "success": trace.success,
        "error": trace.error,
        "duration_seconds": trace.duration_seconds,
        "tools_called": trace.tools_called or [],
        "raw_output": trace.raw_output,
        "conclusion": trace.conclusion,
    }


def make_agent_node(agent_name: str, is_critical: bool, ctx: RunnerContext) -> Callable:
    """Factory: create a node that runs a sub-agent and merges its conclusion into state."""
    def node(state: PipelineState) -> dict:
        agent_cfg = ctx.registry.get_agent(agent_name)
        if agent_cfg is None:
            logger.error("Agent not in registry: %s", agent_name)
            return {"abort": True, "abort_reason": f"Agent {agent_name} not in registry"}

        try:
            prompt = ctx.registry.resolve_prompt(agent_name, data_dir=ctx.settings.data_dir)
            conclusion_type = ctx.registry.resolve_conclusion_type(agent_name)
        except ValueError as e:
            logger.error("Failed to resolve agent config for %s: %s", agent_name, e)
            return {"abort": True, "abort_reason": str(e)}

        result, trace = run_agent(
            ctx,
            role=f"{agent_name.title()}Agent",
            system_prompt=prompt,
            task_message=state["task_context"],
            conclusion_type=conclusion_type,
            max_turns=agent_cfg.max_turns,
            needs_tools=agent_cfg.needs_tools,
            allowed_tools=agent_cfg.allowed_tools,
            external_servers=agent_cfg.external_servers,
            mode=state["mode"],
            run_id=state["run_id"],
        )

        new_conclusions = {**state["conclusions"]}
        if result is not None:
            new_conclusions[agent_name] = result.model_dump()

        abort = is_critical and result is None
        return {
            "conclusions": new_conclusions,
            "traces": [_trace_to_dict(trace)],
            "abort": abort or state["abort"],
            "abort_reason": (
                f"critical agent {agent_name} failed" if abort
                else state.get("abort_reason")
            ),
        }
    return node


def make_manager_node(
    manager_name: str, mode: str, ctx: RunnerContext, format_conclusions_fn: Callable
) -> Callable:
    """Factory: create a node that runs the manager agent."""
    def node(state: PipelineState) -> dict:
        # Format conclusions from state (convert dicts to kwargs for formatter)
        conclusions_text = format_conclusions_fn(**state["conclusions"])

        if state.get("screen_data"):
            conclusions_text += f"\n\nScreen Results (from earlier):\n{json.dumps(state['screen_data'], indent=2)}"

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

        template = MANAGER_TASK_TEMPLATES.get(mode, f"Mode: {mode}.\nSynthesize conclusions.")
        manager_task = (
            f"Today is {state['today']}. {template}\n"
            f"Dry run: {ctx.dry_run}.\n\n"
            f"Sub-agent conclusions:\n{conclusions_text}\n\n"
        )

        manager_config = ctx.registry.get_agent(manager_name)
        if manager_config is None:
            logger.error("Manager agent not in registry: %s", manager_name)
            return {"abort": True, "abort_reason": f"Manager {manager_name} not in registry"}

        try:
            manager_prompt = ctx.registry.resolve_prompt(manager_name, data_dir=ctx.settings.data_dir)
            conclusion_type = ctx.registry.resolve_conclusion_type(manager_name)
        except ValueError as e:
            logger.error("Failed to resolve manager config: %s", e)
            return {"abort": True, "abort_reason": str(e)}

        decision, trace = run_agent(
            ctx,
            role="ManagerAgent",
            system_prompt=manager_prompt,
            task_message=manager_task,
            conclusion_type=conclusion_type,
            max_turns=manager_config.max_turns,
            needs_tools=manager_config.needs_tools,
            mode=mode,
            run_id=state["run_id"],
        )

        if decision is None:
            return {
                "traces": [_trace_to_dict(trace)],
                "abort": True,
                "abort_reason": "Manager agent failed",
            }

        return {
            "manager_decision": decision.model_dump(),
            "traces": [_trace_to_dict(trace)],
        }
    return node


def make_execution_node(executor_name: str, ctx: RunnerContext) -> Callable:
    """Factory: create a node that runs the execution agent."""
    def node(state: PipelineState) -> dict:
        exec_config = ctx.registry.get_agent(executor_name)
        if not exec_config:
            logger.error("Executor agent not in registry: %s", executor_name)
            return {}

        try:
            exec_prompt = ctx.registry.resolve_prompt(executor_name, data_dir=ctx.settings.data_dir)
            exec_type = ctx.registry.resolve_conclusion_type(executor_name)
        except ValueError as e:
            logger.error("Failed to resolve executor config: %s", e)
            return {}

        decision_dict = state.get("manager_decision") or {}
        exec_task = (
            f"Today is {state['today']}. Execute the manager's approved trades.\n"
            f"Dry run: {ctx.dry_run}.\n\n"
            f"Manager decision:\n{json.dumps(decision_dict, indent=2)}\n\n"
            "Execute exits first, then entries."
        )

        conclusion, trace = run_agent(
            ctx,
            role="ExecutionAgent",
            system_prompt=exec_prompt,
            task_message=exec_task,
            conclusion_type=exec_type,
            max_turns=exec_config.max_turns,
            needs_tools=exec_config.needs_tools,
            allowed_tools=exec_config.allowed_tools,
            external_servers=exec_config.external_servers,
            mode=state["mode"],
            run_id=state["run_id"],
        )

        # Persist the execution conclusion (entries/exits executed) into the run
        # log like every other agent node, so the dashboard + "View Last Output"
        # can show what execution actually did instead of an empty result.
        new_conclusions = {**(state.get("conclusions") or {})}
        if conclusion is not None:
            new_conclusions[executor_name] = conclusion.model_dump()
        return {"conclusions": new_conclusions, "traces": [_trace_to_dict(trace)]}
    return node


# Cached deterministic manager for the reconcile node, so monitor/post_market
# runs don't reconnect to the broker on every invocation.
_reconcile_manager = None


def _get_reconcile_manager(ctx: RunnerContext):
    """Build (once) the deterministic QuantManager used for exit/stop reconciliation.

    Mirrors Orchestrator._fallback: the runner already constructs an in-process
    QuantManager for the deterministic/fallback paths, so this introduces no new
    broker coupling.
    """
    global _reconcile_manager
    if _reconcile_manager is None:
        from src.strategy.manager import QuantManager
        try:
            _reconcile_manager = QuantManager(settings=ctx.settings)
        except RuntimeError:
            ctx.settings.broker_enabled = False
            _reconcile_manager = QuantManager(settings=ctx.settings)
    return _reconcile_manager


def make_reconcile_exits_node(ctx: RunnerContext) -> Callable:
    """Factory: deterministic exit + stop-sync floor for monitor/post_market.

    The LLM ManagerAgent's `sells` are advisory and have repeatedly come back
    empty while hard-stop / max-hold / dead-money / VIX exit signals were live —
    a fail-open exit gate. This node runs the deterministic exit engine and
    force-merges its signals into manager_decision['sells'] so route_execution_gate
    cannot skip a real exit, then re-syncs server-side stops (the agent monitor
    path otherwise never reconciles them — the cause of the rotating
    `missing_stops` in the heartbeat).

    Defensive: any failure returns {} (decision unchanged), preserving prior
    behavior rather than breaking a monitor run.
    """
    def node(state: PipelineState) -> dict:
        decision = dict(state.get("manager_decision") or {})
        try:
            mgr = _get_reconcile_manager(ctx)
            mgr._sync_broker()
            det_sells = mgr.generate_sell_list(mgr.get_exit_signals())
        except Exception as e:
            logger.error("Exit reconciliation failed (%s); leaving decision unchanged", e)
            return {}

        if det_sells:
            # Union by ticker; the deterministic signal wins (real reason/urgency/shares).
            existing = {s.get("ticker"): s for s in (decision.get("sells") or [])}
            for s in det_sells:
                existing[s["ticker"]] = {**existing.get(s["ticker"], {}), **s}
            decision["sells"] = list(existing.values())
            logger.warning(
                "Deterministic exits force-merged into %s decision: %s",
                state["mode"], [s["ticker"] for s in det_sells],
            )

        # Re-sync server-side stops in the agent path. update_stop_order is now
        # idempotent (skips when the live stop already matches), so this is cheap.
        if mgr.broker and ctx.settings.server_stops_enabled and not ctx.dry_run:
            for pos in mgr.position_manager.get_all_positions():
                try:
                    server_stop = round(
                        pos.current_stop * (1 - ctx.settings.server_stop_offset_pct), 2
                    )
                    res = mgr.broker.update_stop_order(pos.ticker, pos.shares, server_stop)
                    if res.status == "failed":
                        logger.warning("Stop sync failed for %s: %s", pos.ticker, res.error)
                except Exception as e:
                    logger.warning("Stop sync error for %s: %s", pos.ticker, e)

        return {"manager_decision": decision}
    return node


def make_load_screen_node(ctx: RunnerContext) -> Callable:
    """Factory: create a node that loads screen results from disk."""
    def node(state: PipelineState) -> dict:
        screen_path = Path(ctx.settings.data_dir) / "screen_results" / "latest.json"
        if not screen_path.exists():
            logger.warning("Screen results file not found: %s", screen_path)
            return {}

        try:
            screen_data = json.loads(screen_path.read_text())
            return {"screen_data": screen_data}
        except Exception as e:
            logger.warning("Failed to load screen results: %s", e)
            return {}
    return node


def make_save_screen_node(ctx: RunnerContext) -> Callable:
    """Factory: create a node that saves screen results to disk."""
    def node(state: PipelineState) -> dict:
        decision_dict = state.get("manager_decision")
        if not decision_dict:
            return {}

        screen_path = Path(ctx.settings.data_dir) / "screen_results"
        screen_path.mkdir(parents=True, exist_ok=True)

        buys = decision_dict.get("buys", [])
        # Persist the FULL regime, not just its name: the enter phase sizes
        # positions with sizing_multiplier and gates entries on entries_allowed.
        # A name-only regime would drop those keys and crash size_buy_list
        # (KeyError) / silently bypass the entries_allowed freeze. Source from
        # the RiskAgent conclusion; fall back to safe, trade-permitting defaults.
        risk = state.get("conclusions", {}).get("risk") or {}
        regime = {
            "name": risk.get("regime_name") or decision_dict.get("regime_name"),
            "sizing_multiplier": risk.get("sizing_multiplier", 1.0),
            "entries_allowed": risk.get("entries_allowed", True),
            "vix": risk.get("vix_level"),
        }
        # Write both keys so every loader agrees: make_load_screen_node reads
        # "entries", while the MCP load_screen_results tool (used by the
        # enter-phase ExecutionAgent) reads "buy_list". Writing only one
        # silently dropped every enter-phase buy.
        screen_data = {
            "timestamp": datetime.now().isoformat(),
            "entries": buys,
            "buy_list": buys,
            "regime": regime,
        }

        (screen_path / "latest.json").write_text(json.dumps(screen_data, indent=2))
        logger.info("Saved screen results to %s", screen_path / "latest.json")
        return {}
    return node


def make_evolve_prompts_node(ctx: RunnerContext, conclusions_lookup_fn: Callable) -> Callable:
    """Factory: create a node that handles prompt evolution post-action."""
    def node(state: PipelineState) -> dict:
        decision_dict = state.get("manager_decision")
        if not decision_dict:
            return {}

        if not isinstance(decision_dict.get("prompt_evolutions_approved"), list):
            return {}

        approved = decision_dict.get("prompt_evolutions_approved", [])
        if not approved:
            return {}

        # Get the learning conclusion from state
        learning_conclusion_dict = state["conclusions"].get("learning_agent")
        if not learning_conclusion_dict:
            return {}

        rec_lookup = {}
        for rec in learning_conclusion_dict.get("prompt_evolution_recommendations", []):
            rec_lookup[rec["agent_name"]] = rec

        console.print("\n[bold]Prompt Evolution[/bold]")
        evolved_count = 0

        for agent_name in approved:
            rec = rec_lookup.get(agent_name)
            if not rec:
                console.print(f"  [yellow]{agent_name}: no recommendation found[/yellow]")
                continue

            try:
                current_prompt = ctx.registry.resolve_prompt(
                    agent_name, data_dir=ctx.settings.data_dir
                )
            except ValueError:
                console.print(f"  [yellow]{agent_name}: agent not in registry[/yellow]")
                continue

            success = ctx.evolver.evolve_agent_with_rationale(
                agent_name=agent_name,
                current_prompt=current_prompt,
                suggestion=rec["suggestion"],
                rationale=rec["rationale"],
                claude_bin=shutil.which("claude") or "/usr/local/bin/claude",
            )

            if success:
                evolved_count += 1
                console.print(f"  [green]{agent_name}: evolved[/green]")
            else:
                console.print(f"  [yellow]{agent_name}: skipped[/yellow]")

        if evolved_count:
            console.print(f"  Evolved {evolved_count} prompt(s)")
        return {}
    return node


def make_report_node(mode: str, ctx: RunnerContext) -> Callable:
    """Factory: create a node that runs the report agent."""
    def node(state: PipelineState) -> dict:
        report_config = ctx.registry.get_agent("report")
        if not report_config:
            return {}

        try:
            report_prompt = ctx.registry.resolve_prompt("report", data_dir=ctx.settings.data_dir)
            report_type = ctx.registry.resolve_conclusion_type("report")
        except ValueError as e:
            logger.error("Failed to resolve report agent: %s", e)
            return {}

        conclusion, trace = run_agent(
            ctx,
            role="ReportAgent",
            system_prompt=report_prompt,
            task_message=f"Generate the {mode} report for {state['today']}.",
            conclusion_type=report_type,
            max_turns=report_config.max_turns,
            needs_tools=report_config.needs_tools,
            allowed_tools=report_config.allowed_tools,
            external_servers=report_config.external_servers,
            mode=mode,
            run_id=state["run_id"],
        )

        new_conclusions = {**(state.get("conclusions") or {})}
        if conclusion is not None:
            new_conclusions["report"] = conclusion.model_dump()
        return {"conclusions": new_conclusions, "traces": [_trace_to_dict(trace)]}
    return node


def make_fallback_node(mode: str, ctx: RunnerContext, fallback_fn: Callable) -> Callable:
    """Factory: create a fallback node that invokes deterministic QuantManager."""
    def node(state: PipelineState) -> dict:
        logger.warning("Falling back to deterministic mode for %s", mode)
        console.print("[yellow]Falling back to deterministic pipeline[/yellow]")

        report = fallback_fn(
            mode, dry_run=ctx.dry_run, auto_confirm=ctx.auto_confirm, verbose=ctx.verbose
        )

        return {
            "manager_decision": report if report else None,
            "abort_reason": f"Fallback: {state.get('abort_reason', 'unknown error')}",
        }
    return node


def route_after_agent(state: PipelineState) -> str:
    """Conditional edge: continue or abort after a sub-agent."""
    return "fallback" if state["abort"] else "continue"


def route_execution_gate(state: PipelineState) -> str:
    """Conditional edge: decide whether to run execution."""
    decision = state.get("manager_decision") or {}
    mode = state["mode"]

    if mode == "enter":
        if decision.get("sells") or (decision.get("entries_approved") and decision.get("buys")):
            return "execute"
    elif mode in ("monitor", "post_market"):
        if decision.get("sells"):
            return "execute"

    return "skip_execution"


def route_evolve_gate(state: PipelineState) -> str:
    """Conditional edge: decide whether to run prompt evolution."""
    decision = state.get("manager_decision") or {}
    return "evolve" if decision.get("prompt_evolutions_approved") else "skip_evolve"


# ---- Parallel Variant Analysis Nodes ----------------------------------------


def make_fan_out_node(parallel_agents: List[str]) -> Callable:
    """Factory: create a fan-out node that dispatches to parallel analysis agents.

    Returns a list of Send objects for LangGraph to dispatch in parallel.
    """
    def node(state: PipelineState) -> List[Send]:
        return [Send(agent_name, state) for agent_name in parallel_agents]
    return node


def make_variant_agent_node(agent_name: str, ctx: RunnerContext) -> Callable:
    """Factory: create a node for a parallel analysis variant agent.

    Similar to make_agent_node but returns results to variant_conclusions
    instead of conclusions (for parallel fan-in via reducer).
    """
    def node(state: PipelineState) -> dict:
        agent_cfg = ctx.registry.get_agent(agent_name)
        if agent_cfg is None:
            logger.error("Agent not in registry: %s", agent_name)
            return {
                "variant_conclusions": [{"variant": agent_name, "conclusion": None}],
                "abort": True,
                "abort_reason": f"Agent {agent_name} not in registry",
            }

        try:
            prompt = ctx.registry.resolve_prompt(agent_name, data_dir=ctx.settings.data_dir)
            conclusion_type = ctx.registry.resolve_conclusion_type(agent_name)
        except ValueError as e:
            logger.error("Failed to resolve agent config for %s: %s", agent_name, e)
            return {
                "variant_conclusions": [{"variant": agent_name, "conclusion": None}],
                "abort": True,
                "abort_reason": str(e),
            }

        result, trace = run_agent(
            ctx,
            role=f"{agent_name.title()}Agent",
            system_prompt=prompt,
            task_message=state["task_context"],
            conclusion_type=conclusion_type,
            max_turns=agent_cfg.max_turns,
            needs_tools=agent_cfg.needs_tools,
            allowed_tools=agent_cfg.allowed_tools,
            external_servers=agent_cfg.external_servers,
            mode=state["mode"],
            run_id=state["run_id"],
        )

        variant_item = {
            "variant": agent_name,
            "conclusion": result.model_dump() if result else None,
        }

        return {
            "variant_conclusions": [variant_item],
            "traces": [_trace_to_dict(trace)],
        }
    return node


def make_merge_variants_node(
    meta_agent_name: str, ctx: RunnerContext, format_conclusions_fn: Callable
) -> Callable:
    """Factory: create a node that merges parallel variant results via meta-analysis.

    Reads variant_conclusions list from state, formats as text for meta agent,
    and synthesizes into a single analysis conclusion.
    """
    def node(state: PipelineState) -> dict:
        variant_conclusions = state.get("variant_conclusions") or []

        if not variant_conclusions:
            return {
                "abort": True,
                "abort_reason": "No variants produced conclusions",
            }

        conclusions_text = "\n\n".join([
            f"## Variant: {v.get('variant', 'unknown')}\n"
            f"{json.dumps(v.get('conclusion', {}), indent=2)}"
            for v in variant_conclusions if v.get("conclusion")
        ])

        if not conclusions_text:
            return {
                "abort": True,
                "abort_reason": "All analysis variants failed",
            }

        meta_cfg = ctx.registry.get_agent(meta_agent_name)
        if meta_cfg is None:
            logger.error("Meta agent not in registry: %s", meta_agent_name)
            return {
                "abort": True,
                "abort_reason": f"Meta agent {meta_agent_name} not in registry",
            }

        try:
            meta_prompt = ctx.registry.resolve_prompt(
                meta_agent_name, data_dir=ctx.settings.data_dir
            )
            conclusion_type = ctx.registry.resolve_conclusion_type(meta_agent_name)
        except ValueError as e:
            logger.error("Failed to resolve meta agent config: %s", e)
            return {
                "abort": True,
                "abort_reason": str(e),
            }

        task = (
            f"{state['task_context']}\n\n"
            f"Synthesize consensus from these variant results:\n\n"
            f"{conclusions_text}"
        )

        result, trace = run_agent(
            ctx,
            role="MetaAnalysisAgent",
            system_prompt=meta_prompt,
            task_message=task,
            conclusion_type=conclusion_type,
            max_turns=meta_cfg.max_turns,
            needs_tools=meta_cfg.needs_tools,
            allowed_tools=meta_cfg.allowed_tools,
            external_servers=meta_cfg.external_servers,
            mode=state["mode"],
            run_id=state["run_id"],
        )

        new_conclusions = {**state["conclusions"]}
        if result:
            new_conclusions["analysis"] = result.model_dump()

        return {
            "conclusions": new_conclusions,
            "traces": [_trace_to_dict(trace)],
            "abort": result is None,
            "abort_reason": (
                "Meta-analysis failed" if result is None
                else state.get("abort_reason")
            ),
        }
    return node
