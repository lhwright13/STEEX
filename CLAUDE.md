# STEEX — operator guide for Claude Code

STEEX is a multi-agent algorithmic **paper-trading** system: a LangGraph agent
pipeline + an Alpaca paper broker + a Flask dashboard, run as macOS **crontab
one-shots** (no daemon). This file orients whoever opens `claude` in this repo —
it *is* the user interface. Run `claude` from the repo root so the STEEX tools
load.

## Operating STEEX from this terminal

The STEEX MCP server is wired up in [`.mcp.json`](.mcp.json), so its tools are
available to Claude Code as `mcp__steex__<tool>` (run `/mcp` to confirm it's
connected). You drive the system by asking in plain language — "what are my
positions?", "run a screen", "what did the event trigger do today?" — and Claude
calls the tools. **40 tools** are exposed; the ones you'll use most:

| You want to… | Tool(s) |
|---|---|
| See holdings / cash / equity | `get_positions`, `get_account`, `sync_broker` |
| Realized P&L / trade history | `get_trade_history` |
| Market regime & risk | `get_regime`, `assess_portfolio_risk`, `get_exit_signals` |
| Run a screen (find candidates) | `run_screening` (or `run_screening_variant`) |
| Place a buy | `generate_buy_list` → `size_buy_list` → `execute_entries` (or `place_paper_order` for a direct order) |
| Place a sell / exit | `generate_sell_list` → `execute_exits` |
| Research a name / postmortem | `run_postmortem`, `get_unusual_options_activity` |
| Read / change config | `get_current_weights`, `propose_config_changes`, `apply_config_changes`, `get_config_change_history` |
| Notify your phone | `send_user_message` (Telegram) |
| Learning loop | `run_learning_loop`, `check_alpha_decay` |

The dashboard (read-only view of all this) runs separately:
`./start_dash.sh` → http://localhost:5055.

## Safety model — read before mutating

- **Paper only.** `.mcp.json` launches the server with `--paper`, so every order
  hits the Alpaca **paper** endpoint. There is no live trading wired here.
- **Kill switch** lives in `data/control.json` (`trading_armed`, `event_armed`).
  When disarmed, the pipeline still runs but places **no** orders. Toggle it from
  the dashboard kill-switch, or directly:
  `python -c "from src.strategy.control import set_controls; set_controls('data', trading_armed=False)"`.
  It **fails closed** — a corrupt/unreadable control file disarms.
- **Cron one-shots:** code/config changes take effect on the **next scheduled
  run**, not instantly. Nothing to restart.
- **Confirm mutating actions.** `execute_entries`/`execute_exits`/`place_paper_order`/
  `apply_config_changes` change real (paper) state — confirm intent before firing.
- Secrets (`ALPACA_*`, `STEEX_TELEGRAM_*`) come from the shell environment / `.env`
  (gitignored). Never commit them or paste them into chat.

## How the system runs

- **Scheduled modes** (crontab, market-gated): `screen` → `enter` → `monitor` →
  `post_market` → `learning`, plus `event_scan` every minute (the news fast-path
  watching configured figures, e.g. @realDonaldTrump on Truth Social).
- **Event trigger:** an actionable bullish post → LLM resolves the ticker →
  deterministic guardrails → auto-buys a small paper position; you get a Telegram
  summary and it shows in the dashboard's Today's Events + Event Trigger panel.
- **Agents** run as one-shot `claude -p` subprocesses wired to this same MCP
  server (`src/agents/nodes.py::run_agent`); the orchestrator (LangGraph) is the
  control plane.

## Where things are

- `config/agents.yaml` — agent/mode definitions · `config/settings.py` — tunables (`STEEX_` env prefix)
- `src/agents/` — orchestrator, graph, nodes, `mcp_tools/` (the 40 tools)
- `src/strategy/` — manager (execution), event_trigger, control (kill switch)
- `src/notify/` — messaging (Telegram), event_summary, user_updates stream
- `frontend/` — Flask dashboard (`app.py`, `services/`, ES-module `static/js/`)
- `data/` — runs/, reports/, user_updates/, control.json, logs/ (gitignored)
- `docs/implementation-plan/` — the roadmap (untracked working docs)

## Remote access (headless Mac mini)

Reach the box and keep a session alive across disconnects:

```bash
ssh <you>@<mini>          # enable Remote Login on the mini first
tmux new -s steex         # or: tmux attach -t steex   (persists across disconnects)
cd ~/Projects/STEEX && claude
```

For phone/anywhere access without exposing the mini, put it on a Tailscale
network and SSH to its tailnet IP.

## Conventions

- Commit messages: no `Co-Authored-By: Claude` trailer.
- Tests: `venv/bin/python -m pytest -q` (full suite). Dashboard check:
  `bash scripts/verify_dashboard.sh`.
