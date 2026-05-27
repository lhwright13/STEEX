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
from .trace import AgentTrace, extract_tool_calls_from_envelope

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
        if ctx.settings.broker_paper:
            alpaca_env["ALPACA_PAPER"] = "true"
        if alpaca_env.get("ALPACA_API_KEY"):
            servers["alpaca"] = {
                "command": venv_python,
                "args": ["-m", "alpaca_mcp_server"],
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
) -> Tuple[Optional[BaseModel], AgentTrace]:
    """Run a single agent via the claude CLI subprocess.

    Returns (parsed conclusion or None, trace).
    """
    trace = AgentTrace(run_id=run_id or str(uuid.uuid4())[:8], role=role, mode=mode)
    trace.start()

    claude_bin = shutil.which("claude")
    if not claude_bin:
        for path in ["/usr/local/bin/claude", os.path.expanduser("~/.claude/local/claude")]:
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
        "--output-format", "json",
        "--max-turns", str(max_turns),
    ]

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

        result = subprocess.run(
            cmd,
            text=True,
            timeout=ctx.settings.agent_timeout_seconds,
            cwd=str(ctx.project_root),
            env=env,
            **({"stdout": subprocess.PIPE, "stderr": None} if ctx.verbose
               else {"capture_output": True}),
        )

        if result.returncode != 0:
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
            trace.finish(
                success=False,
                error=f"exit code {result.returncode}: {fail_path}",
            )
            return None, trace

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

        _, trace = run_agent(
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

        return {"traces": [_trace_to_dict(trace)]}
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

        screen_data = {
            "timestamp": datetime.now().isoformat(),
            "entries": decision_dict.get("buys", []),
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

        _, trace = run_agent(
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

        return {"traces": [_trace_to_dict(trace)]}
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
