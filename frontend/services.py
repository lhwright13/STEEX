"""
Live data service layer for dashboard.

Connects dashboard to trading system:
- QuantManager for current market state
- AgentRegistry for agent configs
- Orchestrator for pipeline state
- Recent run data from data/
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

from config.settings import get_settings
from src.agents.registry import AgentRegistry, ModeConfig
from src.regime.detector import RegimeDetector

logger = logging.getLogger("steex.dashboard")


class DashboardService:
    """Provide live data from trading system to dashboard."""

    # The graph SVG and agent grid use legacy CamelCase node names; the agent
    # registry keys are snake_case. Map between them so detail/last-output
    # lookups resolve regardless of which name the UI sends.
    _AGENT_ALIASES = {
        "DataAgent": "data",
        "RiskAgent": "risk",
        "MetaAnalysisAgent": "meta_analysis",
        "ManagerAgent": "manager",
        "ExecutionAgent": "execution",
        "ReportAgent": "report",
        "ResearchAgent": "research",
    }
    _DISPLAY_NAMES = {v: k for k, v in _AGENT_ALIASES.items()}

    def _resolve_agent(self, name: str) -> str:
        """Map a UI agent name (possibly legacy CamelCase) to a registry key."""
        if name in self.registry.agents:
            return name
        return self._AGENT_ALIASES.get(name, name)

    def __init__(self):
        self.settings = get_settings()
        config_dir = Path(__file__).parent.parent / "config"
        self.registry = AgentRegistry(config_dir / "agents.yaml")
        self.regime_detector = RegimeDetector(self.settings)
        self.data_dir = Path(__file__).parent.parent / "data"

    # ====================================================================
    # Pipeline State
    # ====================================================================

    def get_pipeline_current(self) -> Dict[str, Any]:
        """Get current pipeline state and stage."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_pipeline_state()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_pipeline_state()

        # Extract stage and progress from run data
        mode = run_data.get("mode", "screen")
        status = run_data.get("status", "idle")  # running, complete, failed
        stage = run_data.get("stage", "idle")
        elapsed = self._elapsed_seconds(run_data.get("started_at"))
        current_agent = run_data.get("current_agent")

        # Estimate stage progress (0.0 - 1.0)
        stage_progress = self._calculate_stage_progress(run_data)

        return {
            "status": status,
            "mode": mode,
            "stage": stage,
            "elapsed": elapsed,
            "stage_progress": stage_progress,
            "current_agent": current_agent or "idle",
            "run_id": run_data.get("run_id"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_variants_results(self) -> Dict[str, Dict[str, Any]]:
        """Get results from all three analysis variants."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_variants_results()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_variants_results()

        conclusions = run_data.get("conclusions", {})
        variant_conclusions = run_data.get("variant_conclusions", [])

        results = {}
        for variant_item in variant_conclusions:
            variant_name = variant_item.get("variant")
            conclusion = variant_item.get("conclusion", {})
            if variant_name and conclusion:
                candidates = conclusion.get("candidates", [])
                scores = [c.get("score", 0) for c in candidates]
                avg_score = sum(scores) / len(scores) if scores else 0.0

                results[variant_name] = {
                    "variant": variant_name,
                    "status": "complete",
                    "candidate_count": len(candidates),
                    "avg_score": round(avg_score, 1),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }

        # Fill in missing variants with pending status
        for variant in ["conservative", "aggressive", "momentum"]:
            if variant not in results:
                results[variant] = {
                    "variant": variant,
                    "status": "pending",
                    "candidate_count": 0,
                    "avg_score": 0.0,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }

        return results

    def get_consensus(self) -> Dict[str, Any]:
        """Get consensus picks from meta-analysis."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_consensus()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_consensus()

        analysis_conclusion = run_data.get("conclusions", {}).get("analysis", {})
        if not analysis_conclusion:
            return self._default_consensus()

        # Extract consensus picks
        candidates = analysis_conclusion.get("candidates", [])

        high_conviction = [c for c in candidates if c.get("high_conviction", False)]
        consensus = [c for c in candidates if not c.get("high_conviction", False)]

        return {
            "high_conviction": high_conviction[:5],  # Top 5
            "consensus": consensus[:5],  # Top 5
            "speculative_excluded": analysis_conclusion.get("speculative_excluded", []),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_screening_stats(self) -> Dict[str, int]:
        """Get screening funnel statistics."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_screening_stats()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_screening_stats()

        screening = run_data.get("screening", {})
        return {
            "universe": screening.get("universe_size", 0),
            "passed_volume": screening.get("volume_filtered", 0),
            "passed_sentiment": screening.get("sentiment_filtered", 0),
            "passed_technical": screening.get("technical_filtered", 0),
            "passed_insider": screening.get("insider_filtered", 0),
            "final_screened": screening.get("final_count", 0),
            "final_picked": len(run_data.get("conclusions", {}).get("analysis", {}).get("candidates", [])),
        }

    def get_regime(self) -> Dict[str, Any]:
        """Get current market regime and VIX."""
        try:
            regime = self.regime_detector.detect_regime()
            regime_name = regime.name
            vix = regime.vix_level
            confidence = regime.confidence
        except Exception as e:
            logger.warning(f"Regime detection failed: {e}")
            regime_name = "unknown"
            vix = None
            confidence = 0.0

        return {
            "current": regime_name or "unknown",
            "vix": vix or 15.0,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "regimes": {
                "risk_on": {"probability": 0.15, "signal": "VIX < 12, inflows"},
                "cautious": {"probability": 0.60, "signal": "VIX 14-18, mixed flows"},
                "risk_off": {"probability": 0.20, "signal": "Rising volatility, outflows"},
                "crisis": {"probability": 0.05, "signal": "VIX > 30, panic selling"},
            },
        }

    def get_manager_decision(self) -> Dict[str, Any]:
        """Get manager's decision on trades."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return self._default_manager_decision()

        run_data = self._load_json(run_file)
        if not run_data:
            return self._default_manager_decision()

        manager_decision = run_data.get("manager_decision", {})
        status = "approved" if not run_data.get("abort") else "rejected"

        return {
            "status": status,
            "reasoning": manager_decision.get("reasoning", "Review pending"),
            "adjustments": manager_decision.get("position_adjustments", {}),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # UI period -> (Alpaca history period, SPY lookback days)
    _PERF_PERIODS = {
        "1W": ("1W", 10),
        "1M": ("1M", 35),
        "3M": ("3M", 100),
        "1Y": ("1A", 370),  # Alpaca uses "1A" for one year
    }

    def get_portfolio_performance(self, period: str = "1M") -> Dict[str, Any]:
        """Portfolio equity curve vs S&P 500, with alpha, rebased to % return.

        Pulls the broker's daily portfolio history (the authoritative equity
        curve), fetches SPY closes over the same window, rebases both to 0% at
        the first common date, and computes alpha = portfolio% - SPY% at each
        point. Returns {available: False} when the broker or price data can't
        be reached so the UI can show an empty state.
        """
        period = period if period in self._PERF_PERIODS else "1M"
        alpaca_period, spy_days = self._PERF_PERIODS[period]

        # --- Portfolio equity curve from the broker ---------------------
        equity_by_date = {}
        try:
            import os
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetPortfolioHistoryRequest
            paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
            tc = TradingClient(
                os.environ["ALPACA_API_KEY"],
                os.environ["ALPACA_SECRET_KEY"],
                paper=paper,
            )
            hist = tc.get_portfolio_history(
                GetPortfolioHistoryRequest(period=alpaca_period, timeframe="1D")
            )
            for ts, eq in zip(hist.timestamp or [], hist.equity or []):
                if eq:
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                    equity_by_date[d] = float(eq)
        except Exception as e:
            logger.debug("Portfolio history unavailable: %s", e)
            return {"available": False, "period": period, "reason": "broker unavailable"}

        if len(equity_by_date) < 2:
            return {"available": False, "period": period, "reason": "insufficient history"}

        # --- SPY closes over the same window ----------------------------
        spy_by_date = {}
        try:
            from src.data.price import PriceProvider
            df = PriceProvider().get_ohlcv("SPY", days=spy_days)
            if df is not None and "Close" in df.columns:
                for idx, row in df.iterrows():
                    d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                    spy_by_date[d] = float(row["Close"])
        except Exception as e:
            logger.debug("SPY history unavailable: %s", e)

        dates = sorted(equity_by_date.keys())

        # Align SPY to portfolio dates, carrying the last known close forward
        # for any portfolio date without an exact SPY bar (weekend boundary).
        spy_sorted = sorted(spy_by_date.keys())
        spy_aligned = {}
        last = None
        si = 0
        for d in dates:
            while si < len(spy_sorted) and spy_sorted[si] <= d:
                last = spy_by_date[spy_sorted[si]]
                si += 1
            if last is not None:
                spy_aligned[d] = last

        base_eq = equity_by_date[dates[0]]
        base_spy = spy_aligned.get(dates[0])

        series = []
        for d in dates:
            port_pct = round(100 * (equity_by_date[d] / base_eq - 1), 2)
            spy_pct = None
            alpha = None
            if base_spy and d in spy_aligned:
                spy_pct = round(100 * (spy_aligned[d] / base_spy - 1), 2)
                alpha = round(port_pct - spy_pct, 2)
            series.append({
                "date": d,
                "equity": round(equity_by_date[d], 2),
                "portfolio_pct": port_pct,
                "spy_pct": spy_pct,
                "alpha_pct": alpha,
            })

        last_pt = series[-1]
        return {
            "available": True,
            "period": period,
            "series": series,
            "summary": {
                "portfolio_return_pct": last_pt["portfolio_pct"],
                "spy_return_pct": last_pt["spy_pct"],
                "alpha_pct": last_pt["alpha_pct"],
                "start_equity": round(base_eq, 2),
                "end_equity": round(equity_by_date[dates[-1]], 2),
                "spy_available": base_spy is not None,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_portfolio_holdings(self) -> Dict[str, Any]:
        """Current open positions ("buys") plus a portfolio summary.

        Always returns the locally tracked positions (data/positions.json) so
        the panel works offline. Best-effort enriches each row with live
        market value / unrealized P&L from the broker; if the broker is
        unreachable, falls back to the cached heartbeat account snapshot and
        cost-basis-only figures.
        """
        positions = self._load_json_file(self.data_dir / "positions.json") or {}
        rows = []
        for tkr, p in positions.items():
            rows.append({
                "ticker": p.get("ticker", tkr),
                "shares": p.get("shares"),
                "entry_price": p.get("entry_price"),
                "cost_basis": p.get("cost_basis"),
                "current_stop": p.get("current_stop"),
                "score": p.get("score"),
                "entry_date": p.get("entry_date"),
                "reasons": p.get("reasons", []),
                "current_price": None,
                "market_value": None,
                "unrealized_pnl": None,
                "unrealized_pct": None,
            })
        rows.sort(key=lambda r: r["ticker"])

        summary = {"equity": None, "cash": None, "buying_power": None}
        live = False
        try:
            import os
            from src.broker.alpaca import AlpacaBroker
            paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
            broker = AlpacaBroker(paper=paper)
            bpos = {bp.ticker: bp for bp in broker.get_positions()}
            for r in rows:
                bp = bpos.get(r["ticker"])
                if not bp:
                    continue
                r["market_value"] = round(bp.market_value, 2)
                r["unrealized_pnl"] = round(bp.unrealized_pnl, 2)
                if bp.qty:
                    r["current_price"] = round(bp.market_value / bp.qty, 2)
                if r["cost_basis"]:
                    r["unrealized_pct"] = round(100 * bp.unrealized_pnl / r["cost_basis"], 2)
            acct = broker.get_account()
            summary = {
                "equity": acct.equity,
                "cash": acct.cash,
                "buying_power": acct.buying_power,
            }
            live = True
        except Exception as e:
            logger.debug("Live broker enrich failed, falling back to cache: %s", e)
            hb = self._load_json_file(self.data_dir / "heartbeat.json") or {}
            api = (hb.get("checks") or {}).get("api") or {}
            summary = {
                "equity": api.get("equity"),
                "cash": api.get("cash"),
                "buying_power": api.get("buying_power"),
            }

        total_cost = round(sum(r["cost_basis"] or 0 for r in rows), 2)
        total_mv = sum(r["market_value"] for r in rows if r["market_value"] is not None)
        total_upnl = sum(r["unrealized_pnl"] for r in rows if r["unrealized_pnl"] is not None)
        summary["total_cost_basis"] = total_cost
        summary["total_market_value"] = round(total_mv, 2) if live else None
        summary["total_unrealized_pnl"] = round(total_upnl, 2) if live else None
        if summary.get("equity"):
            invested = (total_mv if live else total_cost)
            summary["exposure_pct"] = round(100 * invested / summary["equity"], 1)
        else:
            summary["exposure_pct"] = None

        return {
            "positions": rows,
            "count": len(rows),
            "summary": summary,
            "live": live,
            "source": "broker" if live else "cache",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ====================================================================
    # System Configuration
    # ====================================================================

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
                "status": self._get_agent_status(agent_name),
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

    def get_system_schedules(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get schedule configuration from the cron scheduler config.

        Reads scheduler/config.yaml — the source of truth for what cron
        actually runs — rather than the run_manager mode registry, so the
        dashboard shows real cron expressions, enabled flags, and next-run
        times. Per-mode run history (last run, success rate, avg duration)
        is layered on from data/runs/.
        """
        schedules = []
        cfg = self._load_scheduler_config()
        modes = (cfg or {}).get("modes", {})

        for mode_name, mode_cfg in modes.items():
            mode_cfg = mode_cfg or {}
            cron = mode_cfg.get("schedule", "—")
            enabled = bool(mode_cfg.get("enabled", True))
            # The mode the manager actually runs (may differ from the cron key)
            manager_mode = mode_cfg.get("mode_name", mode_name)

            history = self._get_runs_for_mode(manager_mode, limit=10)
            last_run = history[0].get("started_at") if history else None
            durations = [r.get("elapsed", 0) for r in history if r.get("elapsed")]
            avg_dur = round(sum(durations) / len(durations)) if durations else None
            successes = [r for r in history if r.get("status") in ("complete", "success")]
            success_rate = (
                round(100 * len(successes) / len(history)) if history else None
            )

            schedules.append({
                "name": mode_name,
                "mode": manager_mode,
                "cron": cron,
                "frequency": self._humanize_cron(cron),
                "description": f"Run {manager_mode} mode",
                "next_run": self._next_cron_run(cron),
                "last_run": last_run,
                "avg_duration": avg_dur,
                "success_rate": success_rate,
                "enabled": enabled,
                "recent_runs": len(history),
            })

        return {
            "schedules": sorted(schedules, key=lambda s: s["name"]),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _load_scheduler_config(self) -> Optional[Dict]:
        """Load scheduler/config.yaml from the project root."""
        try:
            import yaml
            path = self.data_dir.parent / "scheduler" / "config.yaml"
            if not path.exists():
                return None
            with open(path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.debug("Could not load scheduler config: %s", e)
            return None

    @staticmethod
    def _humanize_cron(cron: str) -> str:
        """Turn a 5-field cron expr into a short human label (best-effort)."""
        if not cron or cron == "—":
            return "—"
        parts = cron.split()
        if len(parts) != 5:
            return cron
        minute, hour, _dom, _mon, dow = parts
        dow_names = {
            "1-5": "weekdays", "0": "Sundays", "6": "Saturdays",
            "5": "Fridays", "0,6": "weekends", "*": "daily",
        }
        when = dow_names.get(dow, f"dow {dow}")
        if hour.isdigit() and minute.isdigit():
            return f"{int(hour):02d}:{int(minute):02d} {when}"
        return f"{minute} {hour} · {when}"

    @staticmethod
    def _next_cron_run(cron: str) -> Optional[str]:
        """Compute the next fire time for a standard 5-field cron expression.

        Scans forward minute-by-minute up to 8 days. Avoids a croniter
        dependency; cron here is local time (matching the host crontab).
        """
        if not cron or cron == "—":
            return None
        parts = cron.split()
        if len(parts) != 5:
            return None
        minute_f, hour_f, dom_f, mon_f, dow_f = parts

        def field_match(value: int, field: str, lo: int, hi: int) -> bool:
            for token in field.split(","):
                if token == "*":
                    return True
                step = 1
                if "/" in token:
                    base, step_s = token.split("/", 1)
                    step = int(step_s)
                    token = base
                if token == "*":
                    rng = range(lo, hi + 1)
                elif "-" in token:
                    a, b = token.split("-", 1)
                    rng = range(int(a), int(b) + 1)
                else:
                    rng = range(int(token), int(token) + 1)
                if value in rng and (value - rng.start) % step == 0:
                    return True
            return False

        now = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
        for i in range(8 * 24 * 60):
            t = now + timedelta(minutes=i)
            if (
                field_match(t.minute, minute_f, 0, 59)
                and field_match(t.hour, hour_f, 0, 23)
                and field_match(t.day, dom_f, 1, 31)
                and field_match(t.month, mon_f, 1, 12)
                and field_match(t.weekday() + 1 if t.weekday() < 6 else 0, dow_f, 0, 7)
            ):
                # `t` is naive local time (cron runs in host local time).
                # Convert to a real UTC instant so the API stays consistent
                # with the rest of the Z-suffixed timestamps.
                utc = t.astimezone(timezone.utc)
                return utc.isoformat().replace("+00:00", "Z")
        return None

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
                # Extract the first `*_PROMPT = "..."` assignment (simple heuristic)
                if "_PROMPT = " in content:
                    body = content.split("_PROMPT = ", 1)[1].lstrip()
                    body = body.lstrip('"').lstrip("'").lstrip()  # drop opening quotes
                    prompt_text = body[:1500] + ("..." if len(body) > 1500 else "")
        except Exception as e:
            logger.debug(f"Could not load prompt for {agent_name}: {e}")

        last_run_info = self._get_agent_last_run(agent_name)

        return {
            "name": agent_name,
            "role": agent_config.prompt_key or agent_name,
            "status": self._get_agent_status(agent_name),
            "preprompt": prompt_text,
            "tools": self._get_agent_tools(agent_name),
            "external_servers": agent_config.external_servers or [],
            "last_run": last_run_info.get("timestamp"),
            "success_rate": last_run_info.get("success_rate"),
        }

    def get_agent_last_output(self, agent_name: str) -> Dict[str, Any]:
        """Get last execution output for a specific agent."""
        agent_name = self._resolve_agent(agent_name)
        run_file = self._get_latest_run_file()
        if not run_file:
            return {
                "agent": agent_name,
                "output": None,
                "message": "No run data available",
            }

        run_data = self._load_json(run_file)
        if not run_data:
            return {
                "agent": agent_name,
                "output": None,
                "message": "No run data available",
            }

        # Check conclusions for this agent
        conclusions = run_data.get("conclusions", {})
        agent_conclusion = conclusions.get(agent_name)
        if not agent_conclusion:
            # Check variant conclusions
            variant_conclusions = run_data.get("variant_conclusions", [])
            if isinstance(variant_conclusions, list):
                for vc in variant_conclusions:
                    if vc.get("variant") == agent_name:
                        agent_conclusion = vc.get("conclusion")
                        break

        traces = run_data.get("traces", [])
        agent_trace = None
        if isinstance(traces, list):
            agent_trace = next((t for t in traces if t.get("agent") == agent_name), None)

        return {
            "agent": agent_name,
            "conclusion": agent_conclusion,
            "trace_summary": agent_trace.get("summary") if agent_trace else None,
            "timestamp": run_data.get("started_at"),
        }

    # ====================================================================
    # Helper Methods
    # ====================================================================

    def _get_latest_run_file(self, exclude_modes=("event_scan",)) -> Optional[Path]:
        """Get the most recent run data file, skipping certain modes.

        event_scan runs every minute and would otherwise become the "latest"
        run for every pipeline panel, blanking the screen/trade view. The
        trading dashboard wants the latest screen/enter/monitor run, so
        event_scan is excluded by default. Pass exclude_modes=() for any mode.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return None

        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)
        if not exclude_modes:
            return run_files[0] if run_files else None
        for f in run_files:
            data = self._load_json(f)
            if data and data.get("mode") not in exclude_modes:
                return f
        return None

    def _load_json(self, path: Path) -> Optional[Dict]:
        """Load last line of JSONL file as JSON."""
        try:
            with open(path) as f:
                lines = f.readlines()
                if lines:
                    return json.loads(lines[-1])
        except Exception as e:
            logger.debug(f"Failed to load {path}: {e}")
        return None

    def _load_json_file(self, path: Path) -> Optional[Dict]:
        """Load a whole-file JSON document (not JSONL)."""
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Failed to load {path}: {e}")
        return None

    def _elapsed_seconds(self, started_at: Optional[str]) -> int:
        """Calculate seconds since started_at timestamp."""
        if not started_at:
            return 0
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed = (datetime.utcnow() - start).total_seconds()
            return max(0, int(elapsed))
        except Exception:
            return 0

    def _calculate_stage_progress(self, run_data: Dict) -> float:
        """Estimate pipeline stage progress (0.0 - 1.0)."""
        status = run_data.get("status", "idle")
        if status == "idle":
            return 0.0
        if status == "complete":
            return 1.0
        if status == "failed":
            return 0.0

        # Estimate progress based on stage
        stage = run_data.get("stage", "")
        stage_progress_map = {
            "data": 0.15,
            "risk": 0.25,
            "fan_out": 0.35,
            "analysis": 0.65,
            "merge_variants": 0.80,
            "manager": 0.90,
            "execution": 1.0,
        }
        return stage_progress_map.get(stage, 0.5)

    def _get_agent_status(self, agent_name: str) -> str:
        """Get agent status (ready, running, complete, failed)."""
        # TODO: Check actual agent state from orchestrator
        return "ready"

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

    # ====================================================================
    # Default Values (for when no live data available)
    # ====================================================================

    def _default_pipeline_state(self) -> Dict[str, Any]:
        return {
            "status": "idle",
            "mode": "screen",
            "stage": "idle",
            "elapsed": 0,
            "stage_progress": 0.0,
            "current_agent": "idle",
            "run_id": None,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _default_variants_results(self) -> Dict[str, Dict[str, Any]]:
        return {
            variant: {
                "variant": variant,
                "status": "idle",
                "candidate_count": 0,
                "avg_score": 0.0,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            for variant in ["conservative", "aggressive", "momentum"]
        }

    def _default_consensus(self) -> Dict[str, Any]:
        return {
            "high_conviction": [],
            "consensus": [],
            "speculative_excluded": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _default_screening_stats(self) -> Dict[str, int]:
        return {
            "universe": 0,
            "passed_volume": 0,
            "passed_sentiment": 0,
            "passed_technical": 0,
            "passed_insider": 0,
            "final_screened": 0,
            "final_picked": 0,
        }

    def _default_manager_decision(self) -> Dict[str, Any]:
        return {
            "status": "pending",
            "reasoning": "No recent run data",
            "adjustments": {},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ====================================================================
    # Agents & Schedules (for system transparency UI)
    # ====================================================================

    def get_agents_summary(self) -> Dict[str, Any]:
        """Get all agents with their current config and recent stats."""
        agents_list = []

        for agent_name, agent_config in self.registry.agents.items():
            # Get recent run data for this agent
            run_file = self._get_latest_run_file()
            last_run_info = None
            recent_output = None

            if run_file:
                run_data = self._load_json(run_file)
                if run_data:
                    conclusions = run_data.get("conclusions", {})
                    if agent_name in conclusions:
                        last_run_info = {
                            "timestamp": run_data.get("started_at"),
                            "status": "passed",
                        }
                        recent_output = conclusions[agent_name]

            agent_info = {
                "name": agent_name,
                "role": agent_config.prompt_key,
                "type": getattr(agent_config, 'agent_type', 'agent'),
                "max_turns": agent_config.max_turns,
                "tools": agent_config.allowed_tools or [],
                "external_servers": agent_config.external_servers or [],
                "last_run": last_run_info,
                "recent_output": recent_output,
                "prompt_file": f"src/agents/prompts/{agent_config.prompt_key}.py",
            }
            agents_list.append(agent_info)

        return {
            "agents": agents_list,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_schedules_status(self) -> Dict[str, Any]:
        """Get current schedule status and recent runs."""
        schedules = []

        # Get modes and their schedule info
        for mode_name, mode_config in self.registry.modes.items():
            # Find recent runs for this mode
            recent_runs = self._get_runs_for_mode(mode_name, limit=3)

            schedule_info = {
                "mode": mode_name,
                "status": "configured",
                "recent_runs": recent_runs,
            }
            schedules.append(schedule_info)

        return {
            "schedules": schedules,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_agent_trace(self, agent_name: str) -> Dict[str, Any]:
        """Get recent execution trace for a specific agent."""
        run_file = self._get_latest_run_file()
        if not run_file or not run_file.exists():
            return {
                "agent": agent_name,
                "trace": None,
                "message": "No recent run data",
            }

        run_data = self._load_json(run_file)
        if not run_data:
            return {
                "agent": agent_name,
                "trace": None,
                "message": "No recent run data",
            }

        # Extract agent conclusion and trace
        conclusions = run_data.get("conclusions", {})
        agent_conclusion = conclusions.get(agent_name)

        traces = run_data.get("traces", [])
        agent_trace = next((t for t in traces if t.get("agent") == agent_name), None)

        return {
            "agent": agent_name,
            "conclusion": agent_conclusion,
            "trace_summary": agent_trace.get("summary") if agent_trace else None,
            "timestamp": run_data.get("started_at"),
            "run_id": run_data.get("run_id"),
        }

    def _get_runs_for_mode(self, mode: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent runs for a specific mode."""
        runs = []
        data_dir = self.data_dir / "runs"

        if not data_dir.exists():
            return runs

        # Find run files for this mode
        run_files = sorted(
            data_dir.glob(f"run_*.jsonl"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        # Filter by mode BEFORE applying the limit — otherwise a burst of
        # other-mode runs at the top of the (mtime-sorted) list can crowd out
        # this mode entirely and return nothing.
        for run_file in run_files:
            if len(runs) >= limit:
                break
            run_data = self._load_json(run_file)
            if run_data and run_data.get("mode") == mode:
                runs.append({
                    "run_id": run_data.get("run_id"),
                    "started_at": run_data.get("started_at"),
                    "status": run_data.get("status"),
                    "stage": run_data.get("stage"),
                    "elapsed": self._elapsed_seconds(run_data.get("started_at")),
                })

        return runs

    # ====================================================================
    # Graph Structure (for transparency UI)
    # ====================================================================

    def get_graph_structure(self, mode: str) -> Dict[str, Any]:
        """Get the LangGraph structure for a given mode.

        Returns nodes and edges in a format suitable for visualization.
        """
        try:
            from src.agents.graph import build_graph
            from src.agents.state import RunnerContext
            from src.agents.evolution import PromptEvolver

            mode_config = self.registry.modes.get(mode)
            if not mode_config:
                return {"error": f"Mode '{mode}' not found", "modes": list(self.registry.modes.keys())}

            # Build execution context (minimal for graph structure extraction)
            ctx = RunnerContext(
                settings=self.settings,
                paper=True,
                dry_run=True,
                auto_confirm=False,
                verbose=False,
                registry=self.registry,
                evolver=PromptEvolver(str(self.data_dir)),
                project_root=Path(__file__).parent.parent,
            )

            # Build the graph
            graph = build_graph(
                mode=mode,
                mode_config=mode_config,
                ctx=ctx,
                format_conclusions_fn=lambda x: x,
                fallback_fn=lambda x: x,
            )

            # Extract structure from compiled graph
            return self._extract_graph_structure(mode, mode_config, graph)
        except Exception as e:
            logger.warning(f"Graph structure extraction failed: {e}")
            return {"error": str(e), "modes": list(self.registry.modes.keys())}

    def _extract_graph_structure(self, mode: str, mode_config, compiled_graph) -> Dict[str, Any]:
        """Extract nodes and edges from a compiled LangGraph."""
        nodes = []
        edges = []
        node_map = {}

        # Get the underlying Graph object
        graph = compiled_graph.get_graph()

        # Extract nodes (excluding start/end markers)
        node_counter = 0
        for node_name in graph.nodes:
            if node_name in ("__start__", "__end__"):
                continue
            node_counter += 1
            node_type = self._classify_node_type(node_name, mode_config)
            is_critical = node_name in (mode_config.critical_agents or [])

            node_info = {
                "id": node_name,
                "label": self._node_label(node_name),
                "type": node_type,
                "critical": is_critical,
                "index": node_counter,
            }
            nodes.append(node_info)
            node_map[node_name] = node_counter

        # Build edges based on mode configuration structure instead of trying to extract from graph
        # This is more reliable than trying to parse LangGraph's internal representation
        critical_agents = set(mode_config.critical_agents or [])
        parallel_agents = mode_config.parallel_agents or []
        post_actions = mode_config.post_actions or []

        # Chain sub-agents sequentially
        if mode_config.sub_agents:
            for i, agent in enumerate(mode_config.sub_agents[:-1]):
                edges.append({"from": agent, "to": mode_config.sub_agents[i + 1], "type": "direct"})
            # Last sub-agent connects to fan_out or manager
            last_agent = mode_config.sub_agents[-1]
            if parallel_agents:
                edges.append({"from": last_agent, "to": "fan_out", "type": "direct"})
            else:
                edges.append({"from": last_agent, "to": "manager", "type": "direct"})

        # Fan-out and parallel variants
        if parallel_agents:
            edges.append({"from": "fan_out", "to": parallel_agents[0], "type": "direct"})
            for i, agent in enumerate(parallel_agents[:-1]):
                edges.append({"from": agent, "to": parallel_agents[i + 1], "type": "direct"})
            # All parallel agents connect to merge
            for agent in parallel_agents:
                edges.append({"from": agent, "to": "merge_variants", "type": "direct"})
            # Merge connects to manager
            edges.append({"from": "merge_variants", "to": "manager", "type": "direct"})

        # Manager connects to executor or post-actions
        if mode_config.executor:
            edges.append({"from": "manager", "to": "execution", "type": "direct"})
            if post_actions:
                edges.append({"from": "execution", "to": post_actions[0], "type": "direct"})
        else:
            if post_actions:
                edges.append({"from": "manager", "to": post_actions[0], "type": "direct"})

        # Chain post-actions
        if post_actions:
            for i, action in enumerate(post_actions[:-1]):
                edges.append({"from": action, "to": post_actions[i + 1], "type": "direct"})

        # Critical agents have conditional edge to fallback
        for agent in critical_agents:
            if agent in node_map:
                edges.append({"from": agent, "to": "fallback", "type": "conditional"})

        return {
            "mode": mode,
            "nodes": nodes,
            "edges": edges,
            "layout": "auto",  # Will be computed on frontend
            "summary": {
                "total_nodes": len(nodes),
                "critical_nodes": sum(1 for n in nodes if n["critical"]),
                "parallel_nodes": len(mode_config.parallel_agents or []),
            }
        }

    def _classify_node_type(self, node_name: str, mode_config) -> str:
        """Classify a node by its type."""
        if node_name == "load_screen":
            return "pre-action"
        elif node_name == "fan_out":
            return "fan-out"
        elif node_name == "merge_variants":
            return "merge"
        elif node_name in (mode_config.parallel_agents or []):
            return "variant"
        elif node_name == "manager":
            return "manager"
        elif node_name == "execution":
            return "executor"
        elif node_name in ("save_screen", "evolve_prompts", "report"):
            return "post-action"
        elif node_name == "fallback":
            return "fallback"
        elif node_name in (mode_config.sub_agents or []):
            return "agent"
        return "unknown"

    def _node_label(self, node_name: str) -> str:
        """Get display label for a node."""
        labels = {
            "load_screen": "Load Screen",
            "fan_out": "Fan Out",
            "merge_variants": "Merge Variants",
            "manager": "Manager",
            "execution": "Executor",
            "save_screen": "Save Screen",
            "evolve_prompts": "Evolve Prompts",
            "report": "Report",
            "fallback": "Fallback",
            "data": "Data Agent",
            "risk": "Risk Agent",
            "analysis_conservative": "Conservative",
            "analysis_aggressive": "Aggressive",
            "analysis_momentum": "Momentum",
        }
        return labels.get(node_name, node_name.replace("_", " ").title())

    # ====================================================================
    # Recent Runs & Traces
    # ====================================================================

    def get_recent_runs(self, limit: int = 10) -> Dict[str, Any]:
        """Get recent pipeline runs across the trading modes.

        Excludes event_scan (it runs every minute and would crowd out the
        screen/enter/monitor runs); event activity has its own feed.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"runs": [], "timestamp": datetime.utcnow().isoformat() + "Z"}

        run_files = sorted(runs_dir.glob("run_*.jsonl"), reverse=True)
        runs = []

        for run_file in run_files:
            if len(runs) >= limit:
                break
            run_data = self._load_json(run_file)
            if not run_data or run_data.get("mode") == "event_scan":
                continue

            runs.append({
                "run_id": run_data.get("run_id"),
                "mode": run_data.get("mode", "unknown"),
                "status": run_data.get("status", "unknown"),
                "started_at": run_data.get("started_at"),
                "completed_at": run_data.get("completed_at"),
                "elapsed": self._elapsed_seconds(run_data.get("started_at")),
                "current_agent": run_data.get("current_agent"),
                "stage": run_data.get("stage"),
            })

        return {
            "runs": runs,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_event_activity(self, limit: int = 10) -> Dict[str, Any]:
        """Recent event-trigger scans: executed trades and review verdicts.

        Reads event_scan run logs (newest first) and surfaces any trades that
        actually fired plus the post-trade review verdicts, so the dashboard
        can show what the news fast-path has been doing.
        """
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"events": [], "last_scan": None, "timestamp": datetime.utcnow().isoformat() + "Z"}

        events = []
        last_scan = None
        scanned_files = 0
        for run_file in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
            if len(events) >= limit or scanned_files >= 500:
                break
            data = self._load_json(run_file)
            if not data or data.get("mode") != "event_scan":
                continue
            scanned_files += 1
            scan = (data.get("conclusions") or {}).get("event_scan") or {}
            if last_scan is None:
                last_scan = {
                    "completed_at": data.get("completed_at"),
                    "regime": scan.get("regime"),
                    "scanned": scan.get("scanned", 0),
                }
            reviews = {r.get("ticker"): r for r in (data.get("event_reviews") or [])}
            for trade in scan.get("executed", []) or []:
                ev = trade.get("event", {})
                rv = reviews.get(trade.get("ticker"), {})
                events.append({
                    "ticker": trade.get("ticker"),
                    "price": trade.get("price"),
                    "shares": trade.get("shares"),
                    "headline": ev.get("headline"),
                    "source": ev.get("source"),
                    "url": ev.get("url"),
                    "confidence": ev.get("confidence"),
                    "published_at": ev.get("published_at"),
                    "verdict": rv.get("verdict"),
                    "verdict_reason": rv.get("reasoning"),
                    "run_id": data.get("run_id"),
                })

        return {
            "events": events,
            "last_scan": last_scan,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_run_trace(self, run_id: str) -> Dict[str, Any]:
        """Get execution trace for a specific run."""
        runs_dir = self.data_dir / "runs"
        if not runs_dir.exists():
            return {"run_id": run_id, "traces": [], "message": "No run data available"}

        # Find run file with matching run_id
        for run_file in sorted(runs_dir.glob("run_*.jsonl"), reverse=True):
            run_data = self._load_json(run_file)
            if not run_data or run_data.get("run_id") != run_id:
                continue

            traces = run_data.get("traces", [])
            return {
                "run_id": run_id,
                "mode": run_data.get("mode"),
                "started_at": run_data.get("started_at"),
                "traces": traces,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

        return {"run_id": run_id, "traces": [], "message": f"Run {run_id} not found"}

    # ====================================================================
    # Kill switch / runtime controls
    # ====================================================================

    def get_controls(self) -> Dict[str, Any]:
        """Current kill-switch state (trading_armed, event_armed)."""
        from src.strategy.control import get_controls
        return get_controls(self.data_dir)

    def set_controls(self, trading_armed=None, event_armed=None) -> Dict[str, Any]:
        """Update kill-switch flags and persist."""
        from src.strategy.control import set_controls
        return set_controls(
            self.data_dir,
            trading_armed=trading_armed,
            event_armed=event_armed,
            updated_at=datetime.utcnow().isoformat() + "Z",
        )

    # ====================================================================
    # Closed-trade ledger / realized P&L
    # ====================================================================

    def get_trade_history(self, limit: int = 50) -> Dict[str, Any]:
        """Closed trades from data/trades.json plus realized-P&L summary stats.

        Surfaces realized performance (win rate, avg win/loss, hold time,
        exit-reason breakdown) that the unrealized-only holdings view misses.
        """
        trades = self._load_json_file(self.data_dir / "trades.json") or []
        if not isinstance(trades, list):
            trades = []

        # Newest first by exit date.
        trades = sorted(trades, key=lambda t: t.get("exit_date") or "", reverse=True)

        wins = [t for t in trades if (t.get("pnl_dollars") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl_dollars") or 0) < 0]
        total_pnl = round(sum(t.get("pnl_dollars") or 0 for t in trades), 2)
        avg_win = round(sum(t["pnl_dollars"] for t in wins) / len(wins), 2) if wins else 0.0
        avg_loss = round(sum(t["pnl_dollars"] for t in losses) / len(losses), 2) if losses else 0.0
        avg_hold = round(sum(t.get("hold_days") or 0 for t in trades) / len(trades), 1) if trades else 0.0

        # Exit-reason breakdown (e.g. surfaces a high server_stop rate).
        reasons: Dict[str, int] = {}
        for t in trades:
            r = t.get("exit_reason") or "unknown"
            reasons[r] = reasons.get(r, 0) + 1

        rows = [{
            "ticker": t.get("ticker"),
            "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"),
            "entry_price": round(t["entry_price"], 2) if t.get("entry_price") is not None else None,
            "exit_price": round(t["exit_price"], 2) if t.get("exit_price") is not None else None,
            "shares": t.get("shares"),
            "pnl_dollars": round(t.get("pnl_dollars") or 0, 2),
            "pnl_pct": round((t.get("pnl_pct") or 0) * 100, 2),
            "hold_days": t.get("hold_days"),
            "exit_reason": t.get("exit_reason"),
        } for t in trades[:limit]]

        return {
            "trades": rows,
            "summary": {
                "count": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(100 * len(wins) / len(trades), 1) if trades else None,
                "total_realized_pnl": total_pnl,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": round(abs(sum(t["pnl_dollars"] for t in wins) /
                                            sum(t["pnl_dollars"] for t in losses)), 2)
                                 if losses and sum(t["pnl_dollars"] for t in losses) else None,
                "avg_hold_days": avg_hold,
                "exit_reasons": reasons,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ====================================================================
    # Multi-agent observability
    # ====================================================================

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


# Singleton instance
_service_instance: Optional[DashboardService] = None


def get_dashboard_service() -> DashboardService:
    """Get or create dashboard service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = DashboardService()
    return _service_instance
