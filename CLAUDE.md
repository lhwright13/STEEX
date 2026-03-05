# STEEX - Systematic Trading with Execution and Exit Excellence

## Architecture

STEEX is an automated trading system built around a multi-agent architecture. Each agent has a specific role in the daily pipeline:

- **QuantManager** (`src/strategy/manager.py`) - Head orchestrator, coordinates all agents
- **DataAgent** - Fetches and validates external data (insider, VIX, prices, sentiment, fundamentals)
- **AnalysisAgent** - Runs the 5-stage screening pipeline and ranks candidates
- **RiskAgent** - Monitors positions, manages stops, detects exit signals
- **ExecutionAgent** - Executes entries and exits via Alpaca broker
- **ReportAgent** - Compiles structured reports and audit logs
- **RegimeAgent** - Multi-factor market regime detection (VIX, yield curve, breadth, dollar)
- **PortfolioAgent** - Constructs diversified portfolios with correlation/sector constraints
- **BacktestAgent** - Walk-forward backtesting for parameter validation
- **PostMortemAgent** - Analyzes completed trades, categorizes losses
- **ResearchAgent** - Signal hypothesis testing, alpha decay monitoring, weight optimization

## Key Principles

1. **Alpaca broker is the source of truth** for positions and account data. Never trust local state over broker state.
2. **Server-side stops** - every position gets a GTC stop order on Alpaca as a crash-proof safety net (0.5% below local stop to avoid noise triggers).
3. **No lookahead bias** - historical backtests use HistoricalPriceProvider truncated to the reference date.
4. **Config over code** - all tunable parameters live in `config/config.yaml` with Pydantic validation in `config/settings.py`.
5. **Safety first** - stops auto-execute, VIX spike exits are immediate, entries require confirmation unless `--yes` is passed.
6. **Market calendar awareness** - scheduler gates modes on Alpaca's clock/calendar (no runs on holidays, entries only when market is open).

## Self-Learning Loop

The learning loop (`src/learning/`) continuously optimizes strategy parameters by chaining analysis tools together:

```
PostMortem (trade analysis, 90 days)
    -> Alpha Decay (signal health check)
        -> [if degrading] Signal Research (factor testing + weight optimization)
            -> Walk-Forward OOS Validation (2-fold, Sharpe > 0, win_rate > 50%)
                -> [if validated] ConfigWriter applies changes to config.yaml
                    -> LearningJournal records everything
                    -> Gaps flagged for user review
```

### Rules

- **Never apply changes during market hours** (9:30 AM - 4:00 PM ET weekdays)
- **Max weight change per cycle**: 10% (configurable via `learning_weight_change_cap`)
- **All weights must sum to 1.0** after any change (auto-normalized)
- **OOS validation required** before any parameter is written to config
- **Full audit trail** in `data/learning/config_history.json`
- **Knowledge gaps** are flagged in `data/learning/gaps.json` for human review

### Schedules

- **Weekly**: Fridays 6:00 PM ET (full learning cycle via scheduler)
- **On-demand**: `venv/bin/python scripts/run_learning.py`
- **Dry run**: `venv/bin/python scripts/run_learning.py --dry-run`

### Data Files

- `data/learning/learning_journal.json` - timestamped log of all learning actions
- `data/learning/weight_recommendations.json` - latest recommended weights from signal research
- `data/learning/gaps.json` - knowledge gaps requiring human review
- `data/learning/config_history.json` - audit trail of all config changes

## Daily Schedule

The scheduler (`scheduler/config.yaml`) runs 9 cron jobs that break the trading day into discrete phases. Each phase runs `scheduler/run.sh <mode>`, which calls Python scripts directly with a market calendar gate that skips runs on holidays or when the market is closed.

```
Time ET     Mode              Command
-------     ----              -------
7:00 AM     heartbeat         health_check.py
8:15 AM     screen            run_manager.py screen --paper
9:45 AM     enter             run_manager.py enter --paper --yes
11:00 AM    monitor           run_manager.py monitor --paper
1:30 PM     monitor           run_manager.py monitor --paper
3:45 PM     stop_sync         run_manager.py stop_sync --paper
4:30 PM     post_market       run_manager.py post_market --paper
6:00 PM Fri learning          run_learning.py --verbose
10:00 AM Sun heartbeat        health_check.py
```

The `screen` and `enter` modes replace the old monolithic `pre_market` for daily use. `pre_market` still works as a combined mode for manual runs. Screening happens before market open so entries can wait for the opening auction to settle (9:45 AM). Server-side GTC stops on Alpaca protect positions even if the Mac sleeps or crashes.

## What Has Been Learned

*This section is updated by the learning loop over time.*

- Initial configuration: optimized via manual walk-forward analysis (see commit history)
- Scoring weights: momentum (0.30), insider (0.25), volume (0.15), sentiment (0.15), fundamental (0.10), options (0.05)
- Stops: 10% initial, trailing at 12%/15%/15% for 10%/20%/30% gains
- Hold period: 30 days max

## Agent System (Claude AI Mode)

The agent system (`src/agents/`) provides an alternative execution path where Claude AI agents make trading decisions instead of the deterministic pipeline.

### Key Files

- `src/agents/orchestrator.py` - Registry-driven mode execution, subprocess management, trace collection
- `src/agents/mcp_server.py` - FastMCP stdio server exposing ~20 QuantManager tools
- `src/agents/registry.py` - Loads agent definitions from `config/agents.yaml`, resolves prompts and conclusion types
- `src/agents/conclusions.py` - Pydantic models for structured agent output (includes AgentMeta for self-improvement)
- `src/agents/trace.py` - AgentTrace/AgentSession for audit trail, stored in `data/agents/sessions/`
- `src/agents/evolution.py` - PromptEvolver for agent prompt self-improvement with safety constraints
- `src/agents/prompts/*.py` - Default system prompts for each agent role
- `config/agents.yaml` - Declarative agent definitions and mode sequences

### Adding a New Agent

1. Add entry in `config/agents.yaml` with name, prompt key, conclusion model, tools
2. Create prompt: `src/agents/prompts/{name}.py` (code default) or `data/agents/prompts/{name}.md` (disk override)
3. Add Pydantic conclusion model to `src/agents/conclusions.py` (include optional `meta: AgentMeta` field)
4. Add agent to a mode's `sub_agents` list in `config/agents.yaml`

### Prompt Resolution Order

1. Disk override: `data/agents/prompts/{name}.md` (takes priority, can be evolved by agents)
2. Code default: `src/agents/prompts/{name}.py` (fallback, checked into git)

### Deterministic vs Agent Mode

- `stop_sync` and `heartbeat` modes are always deterministic (no AI)
- All other modes use the registry-driven Orchestrator in `--agent` mode
- If a critical agent fails, the orchestrator falls back to QuantManager automatically
- The MCP server runs as a subprocess - it does NOT import or run in the same process as the orchestrator

### Data Files

- `data/agents/sessions/*.json` - Trace logs (auto-pruned after `trace_retention_days`)
- `data/agents/sessions/latest.json` - Most recent session
- `data/agents/prompts/*.md` - Disk prompt overrides (evolved versions)
- `data/agents/recommendations.json` - Accumulated meta-recommendations from agents
- `data/agents/prompt_history.json` - Audit trail of prompt rewrites

## Development Notes

- Always use the venv: `venv/bin/python`
- Config changes require updating both `config/config.yaml` and `config/settings.py`
- Agent definitions live in `config/agents.yaml` - add new agents there, not in orchestrator code
- Agent role documentation lives in `agents/` - update when behavior changes
- Run tests from project root: `venv/bin/python -m pytest tests/`
- All 142+ tests must pass before committing
