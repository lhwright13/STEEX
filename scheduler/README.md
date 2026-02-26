# STEEX Scheduler

Automated cron-based scheduler that invokes `claude -p` (non-interactive mode) to run the trading pipeline on a timer. Claude reads the agent docs, reasons about market conditions, executes the pipeline, and reports results.

## How It Works

1. **cron** fires at scheduled times
2. **run.sh** reads `config.yaml`, checks the market gate, loads the prompt template, and builds a `claude -p` command
3. **market_gate.py** queries Alpaca's clock/calendar to decide if the mode should run (skips holidays, checks market hours)
4. **lockfile** prevents overlapping runs of the same mode
5. **Claude** reads the relevant agent doc, runs the pipeline, reads the report, and summarizes results
6. Output is logged to `scheduler/logs/`

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | Schedules, model, budget, safety flags, tool permissions |
| `run.sh` | Main entry point - market gate, lockfile, parses config, runs `claude -p` |
| `install.sh` | Reads config and writes cron entries (dynamic mode discovery, idempotent) |
| `uninstall.sh` | Removes cron entries, preserves other crontab entries |
| `prompts/heartbeat.md` | Health check - API connectivity, stop reconciliation |
| `prompts/screen.md` | Pre-open - data refresh, screening, ranking (no entries) |
| `prompts/enter.md` | Post-open - load screen results, execute entries, place stops |
| `prompts/monitor.md` | Midday - risk-only check, no new entries |
| `prompts/stop_sync.md` | Pre-close - update trailing stops, sync server-side stops |
| `prompts/post_market.md` | EOD - update stops, daily summary, post-mortem |
| `prompts/learning.md` | Weekly - signal research, parameter optimization |
| `prompts/pre_market.md` | Legacy combined mode (screen + enter in one pass) |
| `logs/` | Auto-created, gitignored - timestamped output from each run |
| `locks/` | Auto-created - PID lockfiles to prevent overlapping runs |

## Default Schedule (ET)

| Mode | Time | Days | What it does |
|------|------|------|-------------|
| heartbeat_morning | 7:00 AM | Mon-Fri | API health, account check, stop reconciliation |
| screen | 8:15 AM | Mon-Fri | Data refresh, screening, ranking (no entries) |
| enter | 9:45 AM | Mon-Fri | Load screen results, execute entries, place server-side stops |
| monitor_midday | 11:00 AM | Mon-Fri | Risk check, stops, exits |
| monitor_afternoon | 1:30 PM | Mon-Fri | Risk check, stops, exits |
| stop_sync | 3:45 PM | Mon-Fri | Update trailing stops, sync server-side stops before close |
| post_market | 4:30 PM | Mon-Fri | EOD wrap-up, post-mortem, report |
| learning | 6:00 PM | Fridays | Weekly parameter optimization |
| heartbeat_weekend | 10:00 AM | Sundays | Weekend health check |

The screen/enter split ensures screening happens before market open while entries wait for the opening auction to settle. Two monitor passes reduce the maximum monitoring gap to 2.5 hours. The stop_sync pass ensures server-side stops are up to date before market close.

## Market Calendar Gate

`scripts/market_gate.py` checks Alpaca's `get_clock()` and `get_calendar()` before each run:

| Mode | Requires open? | Requires market day? |
|------|---------------|---------------------|
| heartbeat | No | No |
| screen | No | Yes |
| enter | Yes | Yes |
| monitor | Yes | Yes |
| stop_sync | Yes | Yes |
| post_market | No | Yes |
| learning | No | No |

If the gate says "don't run", the scheduler logs the reason and exits cleanly. If the Alpaca API is unreachable, the gate defaults to "run anyway" to avoid silent failures.

## Usage

```bash
# Manual run (any mode)
scheduler/run.sh screen
scheduler/run.sh enter
scheduler/run.sh monitor_midday
scheduler/run.sh stop_sync
scheduler/run.sh post_market

# Preview cron entries without installing
scheduler/install.sh --show

# Install cron schedule
scheduler/install.sh

# Verify cron is installed
crontab -l

# Remove cron schedule
scheduler/uninstall.sh
```

## Safety Defaults

The config ships with safe defaults:

- `paper: true` - paper trading only
- `dry_run: false` - executes trades (set to true for preview-only mode)
- `allowed_tools: "Bash,Read,Glob,Grep"` - Claude cannot edit or write source files
- `broker_enabled: true` in main config - Alpaca is the source of truth
- `server_stops_enabled: true` - GTC stops on Alpaca protect positions even if the system goes offline
- `run.sh` sources `~/.bash_profile` and `~/.zprofile` for API keys
- Lockfiles prevent overlapping runs of the same mode

If the broker fails to initialize (missing keys), the pipeline halts rather than falling back to simulation.

## Requirements

- macOS with cron enabled (grant Full Disk Access to `/usr/sbin/cron` in System Settings)
- Machine must be awake during scheduled times (cron does not fire while asleep; server-side stops provide a safety net)
- Valid Claude CLI auth (`claude` command must work)
- Alpaca API keys in `~/.bash_profile` (ALPACA_API_KEY, ALPACA_SECRET_KEY)
- Project venv with pyyaml installed

## Troubleshooting

**Cron didn't fire:**
- Mac was probably asleep. Server-side GTC stops on Alpaca protect positions even if the scheduler misses a run.
- Check Full Disk Access for `/usr/sbin/cron` in System Settings -> Privacy & Security.

**Broker orders timeout:**
- Market is closed. The market gate should prevent this, but DAY limit orders need the market open to fill.

**"Broker init failed" error:**
- Alpaca keys not set. Ensure `~/.bash_profile` exports ALPACA_API_KEY and ALPACA_SECRET_KEY.

**Re-running install.sh:**
- Safe to run multiple times. It replaces the old cron block, never duplicates.

**"previous run still active" in logs:**
- A lockfile is preventing overlapping runs. If the previous run crashed, the stale lock is auto-cleaned on the next attempt.
