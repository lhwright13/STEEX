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
from .graph import build_graph
from .nodes import cleanup_mcp, run_agent, _trace_to_dict
from .registry import AgentRegistry, ModeConfig
from .run_log import start_run_log, finish_run_log
from .state import PipelineState, RunnerContext
from .trace import AgentSession, AgentTrace

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
    "event_scan": ("Event Scan", "red"),
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
        """Locate the claude CLI binary.

        Checks PATH first, then common install locations. The Homebrew path
        (/opt/homebrew/bin) is included because cron's minimal PATH can omit it,
        which made event_scan fail to find claude even though it was installed.
        """
        claude = shutil.which("claude")
        if claude:
            return claude
        for path in [
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
            os.path.expanduser("~/.claude/local/claude"),
        ]:
            if os.path.isfile(path):
                return path
        raise FileNotFoundError(
            "claude CLI not found. Install Claude Code: https://claude.ai/claude-code"
        )



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

        if effective_mode == "event_scan":
            return self.run_event_scan()

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

        ctx = RunnerContext(
            settings=self.settings, paper=self.paper, dry_run=self.dry_run,
            auto_confirm=self.auto_confirm, verbose=self.verbose,
            registry=self.registry, evolver=self.evolver,
            project_root=self._project_root,
        )
        try:
            prompt = self.registry.resolve_prompt(agent_name, data_dir=self.settings.data_dir)
            conclusion_type = self.registry.resolve_conclusion_type(agent_name)
            result, trace = run_agent(
                ctx,
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
            cleanup_mcp(ctx)
            return self._fallback_test_roundtrip(ticker, amount_usd)

        report = {"mode": mode, "ticker": ticker, "amount_usd": amount_usd,
                  "conclusion": result.model_dump()}
        session.set_manager_decision(report)
        self._save_report(report)
        self._finalize_session(session)
        cleanup_mcp(ctx)
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

    def run_event_scan(self) -> Optional[Dict]:
        """News event-trigger pass: deterministic ingest+guardrails+auto-buy,
        then dispatch the event_review agent on each fill.

        Gated by settings.event_trigger_enabled. The buy is deterministic (no
        LLM in the hot path); the review agent is the post-trade safety net and
        may exit the position. Writes a dashboard run log either way.
        """
        mode = "event_scan"
        run_id = str(uuid.uuid4())[:8]

        if not getattr(self.settings, "event_trigger_enabled", False):
            console.print("[yellow]event_trigger_enabled is false — skipping event scan.[/yellow]")
            return {"mode": mode, "skipped": "event_trigger_enabled=false"}

        from src.strategy.manager import QuantManager
        from src.data.event_source import (
            NewsEventSource, TruthSocialEventSource, CompositeEventSource,
        )
        from src.data.sentiment import SentimentProvider
        from src.strategy.event_trigger import EventTrigger
        from src.agents.conclusions import EventTickerResolution
        from src.agents.prompts.event_ticker import EVENT_TICKER_AGENT_PROMPT

        try:
            manager = QuantManager(settings=self.settings)
        except RuntimeError:
            self.settings.broker_enabled = False
            manager = QuantManager(settings=self.settings)

        sentiment = SentimentProvider()

        # Source: one or more watched figures on Truth Social (P1-4), else the
        # Finnhub watchlist poller. event_figures drives the multi-figure list;
        # when empty it falls back to the legacy single-account config.
        figures = list(getattr(self.settings, "event_figures", []) or [])
        if not figures and getattr(self.settings, "event_truth_social_enabled", False):
            figures = [{
                "name": "realDonaldTrump",
                "platform": "truth_social",
                "account_id": self.settings.event_truth_social_account_id,
                "enabled": True,
            }]

        if figures:
            # One-time cursor migration: preserve the legacy single-account dedup
            # state (data/events/truth_cursor.json) under its account-namespaced name.
            events_dir = Path(self.settings.data_dir) / "events"
            legacy_cursor = events_dir / "truth_cursor.json"
            legacy_acct = str(self.settings.event_truth_social_account_id)
            target = events_dir / f"truth_cursor_{legacy_acct}.json"
            if legacy_cursor.exists() and not target.exists():
                try:
                    legacy_cursor.rename(target)
                except Exception as e:
                    logger.debug("legacy truth cursor migration skipped: %s", e)

            enabled_figs = [
                f for f in figures
                if f.get("enabled", True)
                and f.get("platform", "truth_social") == "truth_social"
                and f.get("account_id")
            ]
            sources = [
                TruthSocialEventSource(
                    account_id=f["account_id"],
                    data_dir=self.settings.data_dir,
                    lookback_hours=self.settings.event_truth_lookback_hours,
                    figure=f.get("name") or str(f["account_id"]),
                )
                for f in enabled_figs
            ]
            source = CompositeEventSource(sources)
            source_desc = "truth_social:" + (
                ",".join(f.get("name") or str(f["account_id"]) for f in enabled_figs)
                or "none-enabled"
            )
        else:
            source = NewsEventSource(
                watchlist=self.settings.event_watchlist,
                data_dir=self.settings.data_dir,
                sentiment_provider=sentiment,
                lookback_days=self.settings.event_news_lookback_days,
            )
            source_desc = f"watchlist:{len(self.settings.event_watchlist)}"

        trigger = EventTrigger(manager, self.settings, source, sentiment)

        # LLM resolver: free-text post -> {ticker, is_bullish, confidence}.
        # Lazily build a RunnerContext so untickered events get an LLM call.
        resolver_ctx = RunnerContext(
            settings=self.settings, paper=self.paper, dry_run=self.dry_run,
            auto_confirm=self.auto_confirm, verbose=self.verbose,
            registry=self.registry, evolver=self.evolver,
            project_root=self._project_root,
        )

        # Collect every agent trace so the run log keeps an audit trail (the
        # resolver runs per post, the review agent per fill). Discarding these
        # blinded the dashboard timeline / tool trace.
        event_traces = []

        def _resolve(ev):
            try:
                res, trace = run_agent(
                    resolver_ctx,
                    role="EventTickerResolver",
                    system_prompt=EVENT_TICKER_AGENT_PROMPT,
                    task_message=f"Post by {ev.source}:\n\n{ev.headline}",
                    conclusion_type=EventTickerResolution,
                    max_turns=1,
                    needs_tools=False,
                    mode=mode,
                    run_id=run_id,
                    model=getattr(self.settings, "event_resolver_model", None) or None,
                )
                event_traces.append(_trace_to_dict(trace))
                return res
            except Exception as e:
                logger.error("ticker resolver failed: %s", e)
                return None

        run_file = start_run_log(self.settings.data_dir, run_id, mode)
        console.print(Panel.fit(
            f"[bold]Event Scan[/bold]  source={source_desc}  "
            f"dry_run={self.dry_run}  paper={self.paper}",
            border_style="cyan",
        ))

        scan = trigger.run(dry_run=self.dry_run, resolver=_resolve)
        console.print(
            f"  scanned={scan['scanned']} actionable={len(scan['actionable'])} "
            f"executed={len(scan['executed'])} regime={scan.get('regime')}"
        )

        # Post-trade review: one review agent per fill.
        reviews = []
        ctx = None
        if scan["executed"] and not self.dry_run:
            ctx = RunnerContext(
                settings=self.settings, paper=self.paper, dry_run=self.dry_run,
                auto_confirm=self.auto_confirm, verbose=self.verbose,
                registry=self.registry, evolver=self.evolver,
                project_root=self._project_root,
            )
            agent_cfg = self.registry.get_agent("event_review")
            for trade in scan["executed"]:
                ev = trade.get("event", {})
                task = (
                    f"An event trade was just auto-executed. Review it.\n"
                    f"Ticker: {trade['ticker']}\n"
                    f"Headline: {ev.get('headline')}\n"
                    f"Source: {ev.get('source')}  Published: {ev.get('published_at')}\n"
                    f"Sentiment score: {ev.get('sentiment')}\n"
                    f"Entry: {trade['shares']} shares @ ${trade['price']} "
                    f"(stop ${trade['stop']}).\n"
                    f"Decide keep / exit / tighten_stop. Paper={self.paper}."
                )
                try:
                    result, trace = run_agent(
                        ctx,
                        role="EventReviewAgent",
                        system_prompt=self.registry.resolve_prompt("event_review", data_dir=self.settings.data_dir),
                        task_message=task,
                        conclusion_type=self.registry.resolve_conclusion_type("event_review"),
                        max_turns=agent_cfg.max_turns,
                        needs_tools=agent_cfg.needs_tools,
                        allowed_tools=agent_cfg.allowed_tools,
                        external_servers=agent_cfg.external_servers,
                        mode=mode,
                        run_id=run_id,
                    )
                    event_traces.append(_trace_to_dict(trace))
                    reviews.append(result.model_dump() if result else {"ticker": trade["ticker"], "verdict": "unknown"})
                except Exception as e:
                    logger.error("event_review failed for %s: %s", trade["ticker"], e)
                    reviews.append({"ticker": trade["ticker"], "verdict": "error", "reasoning": str(e)})
        elif scan["executed"]:
            console.print("  [dim]dry_run: skipping post-trade review agent[/dim]")

        # P1-2: summarize + notify the user about real event fills and big moves.
        # The summary stage is idempotent per id and the message layer is itself
        # kill-switched, so this is safe to call every scan.
        try:
            from src.notify.event_summary import summarize_and_notify
            from src.strategy.move_watch import MoveWatcher

            review_by_ticker = {r.get("ticker"): r for r in reviews}
            if not self.dry_run:
                for trade in scan["executed"]:
                    ev = trade.get("event", {})
                    eid = str(ev.get("id") or f"{trade['ticker']}_{ev.get('published_at', '')}")
                    summarize_and_notify({
                        "id": f"evt_{eid}",
                        "type": "event_trade",
                        "ticker": trade["ticker"],
                        "context": {
                            "headline": ev.get("headline"),
                            "source": ev.get("source"),
                            "figure": ev.get("figure"),
                            "shares": trade.get("shares"),
                            "price": trade.get("price"),
                            "stop": trade.get("stop"),
                            "review": review_by_ticker.get(trade["ticker"]),
                        },
                        "links": ([{"label": "post", "href": ev["url"]}]
                                  if ev.get("url") else []),
                    }, settings=self.settings)

            for mv in MoveWatcher(manager, self.settings).scan():
                summarize_and_notify({
                    "id": f"move_{mv['ticker']}_{mv['ts']}",
                    "type": "big_move",
                    "ticker": mv["ticker"],
                    "context": mv,
                }, settings=self.settings)
        except Exception as e:  # a notification must never break the scan
            logger.error("event/move notification failed: %s", e)

        report = {"mode": mode, "scan": scan, "reviews": reviews}
        final_state = {
            "mode": mode,
            "conclusions": {"event_scan": scan},
            "event_reviews": reviews,
            "manager_decision": {"decision": "event_scan", "reasoning":
                f"{len(scan['executed'])} event trade(s) executed, {len(reviews)} reviewed"},
            "traces": event_traces,
            "abort": False,
        }
        finish_run_log(run_file, self.settings.data_dir, run_id, mode, final_state, status="complete")
        self._save_report(report)
        if ctx is not None:
            cleanup_mcp(ctx)
        return report

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
        """Execute the full agent pipeline for a mode via LangGraph StateGraph."""
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

        # Create RunnerContext for nodes
        ctx = RunnerContext(
            settings=self.settings,
            paper=self.paper,
            dry_run=self.dry_run,
            auto_confirm=self.auto_confirm,
            verbose=self.verbose,
            registry=self.registry,
            evolver=self.evolver,
            project_root=self._project_root,
        )

        # Initialize state
        initial_state: PipelineState = {
            "mode": mode,
            "task_context": task_context,
            "today": today,
            "run_id": run_id,
            "conclusions": {},
            "traces": [],
            "manager_decision": None,
            "screen_data": None,
            "abort": False,
            "abort_reason": None,
        }

        console.print("\n[bold]1. Sub-Agent Analysis & Manager Synthesis[/bold]")

        # Open the dashboard run log (frontend/services.py reads data/runs/*.jsonl)
        run_file = start_run_log(self.settings.data_dir, run_id, mode)

        # Build and invoke graph
        graph = build_graph(
            mode,
            mode_config,
            ctx,
            format_conclusions_fn=self._format_conclusions,
            fallback_fn=self._fallback,
        )

        final_state = graph.invoke(
            initial_state,
            config={"run_name": f"steex_{mode}_{run_id}"},
        )

        # Append the consolidated final line to the dashboard run log.
        finish_run_log(
            run_file, self.settings.data_dir, run_id, mode, final_state,
            status="failed" if final_state.get("abort") else "complete",
        )

        # Reconstruct session from traces
        for trace_dict in final_state.get("traces", []):
            trace = AgentTrace.from_dict(trace_dict)
            session.add_trace(trace)

        # Extract decision
        decision_dict = final_state.get("manager_decision")
        if decision_dict:
            session.set_manager_decision(decision_dict)

        # Determine if fallback was used
        if final_state.get("abort"):
            session.fallback_used = True
            self._finalize_session(session)
            cleanup_mcp(ctx)
            return final_state.get("manager_decision") or {}

        # Deserialize decision for report building
        decision = None
        if decision_dict:
            decision_cls = self.registry.resolve_conclusion_type(mode_config.manager)
            try:
                decision = decision_cls.model_validate(decision_dict)
            except Exception as e:
                logger.error("Failed to deserialize manager decision: %s", e)

        # 5. Build and save structured report
        step_num = len(mode_config.sub_agents) + 2 + (1 if mode_config.executor else 0)
        console.print(f"\n[bold]{step_num}. Report[/bold]")

        # Reconstruct conclusion objects from dicts for report building
        conclusions_objs = {}
        for name, conc_dict in final_state.get("conclusions", {}).items():
            try:
                agent_cfg = self.registry.get_agent(name)
                if agent_cfg:
                    conc_cls = self.registry.resolve_conclusion_type(name)
                    conclusions_objs[name] = conc_cls.model_validate(conc_dict)
            except Exception as e:
                logger.debug("Failed to reconstruct conclusion for %s: %s", name, e)

        report = self._build_report(
            mode, decision,
            data=conclusions_objs.get("data"),
            risk=conclusions_objs.get("risk"),
            analysis=conclusions_objs.get("analysis"),
            research=conclusions_objs.get("research"),
            learning=conclusions_objs.get("learning_agent"),
        )
        self._save_report(report)
        if decision:
            self._print_summary(decision, conclusions_objs.get("risk"))

        self._finalize_session(session)
        cleanup_mcp(ctx)

        # After the morning screen, send a once-per-day market brief to the user
        # (idempotent per date, so the every-2.5h screen only sends the first run).
        if mode == "screen" and not final_state.get("abort"):
            try:
                from src.notify.morning_digest import send_morning_digest
                send_morning_digest(final_state, self.settings)
            except Exception as e:  # a notification must never break a trading run
                logger.error("morning digest failed: %s", e)

        # After the post-market wrap-up, send a once-per-day end-of-day recap
        # (today's P&L, closed trades, alpha vs SPY). Idempotent per date.
        if mode == "post_market" and not final_state.get("abort"):
            try:
                from src.notify.daily_recap import send_daily_recap
                send_daily_recap(final_state, self.settings)
            except Exception as e:  # a notification must never break a trading run
                logger.error("daily recap failed: %s", e)

        return report


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
