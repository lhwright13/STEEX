# STEEX Scheduler

Automated cron-based scheduler that invokes `claude -p` (non-interactive mode) to run the trading pipeline on a timer. Claude reads the agent docs, reasons about market conditions, executes the pipeline, and reports results.

## How It Works

1. **cron** fires at scheduled times (weekdays only)
2. **run.sh** reads `config.yaml`, loads the prompt template, and builds a `claude -p` command
3. **Claude** reads the relevant agent doc, runs `scripts/run_manager.py`, reads the report, and summarizes results
4. Output is logged to `scheduler/logs/`

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | Schedules, model, budget, safety flags, tool permissions |
| `run.sh` | Main entry point - parses config, builds and runs `claude -p` |
| `install.sh` | Reads config and writes cron entries (idempotent, safe to re-run) |
| `uninstall.sh` | Removes cron entries, preserves other crontab entries |
| `prompts/pre_market.md` | Morning prompt - full pipeline (screen, enter, exit, report) |
| `prompts/monitor.md` | Midday prompt - risk-only check, no new entries |
| `prompts/post_market.md` | EOD prompt - update stops, daily summary, next-day prep |
| `logs/` | Auto-created, gitignored - timestamped output from each run |

## Default Schedule (ET, weekdays)

| Mode | Time | What it does |
|------|------|-------------|
| pre_market | 8:30 AM | Full pipeline: data refresh, screening, entries, exits |
| monitor | 12:00 PM | Risk check: stops, regime shifts, drawdown |
| post_market | 4:30 PM | Wrap-up: update stops, daily report, flag issues |

## Usage

```bash
# Manual run (any mode)
scheduler/run.sh pre_market
scheduler/run.sh monitor
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
- `run.sh` sources `~/.bash_profile` and `~/.zprofile` for API keys

If the broker fails to initialize (missing keys), the pipeline halts rather than falling back to simulation.

## Requirements

- macOS with cron enabled (grant Full Disk Access to `/usr/sbin/cron` in System Settings)
- Machine must be awake during scheduled times (cron does not fire while asleep)
- Valid Claude CLI auth (`claude` command must work)
- Alpaca API keys in `~/.bash_profile` (ALPACA_API_KEY, ALPACA_SECRET_KEY)
- Project venv with pyyaml installed

## Troubleshooting

**Cron didn't fire:**
- Mac was probably asleep. Keep it awake during market hours.
- Check Full Disk Access for `/usr/sbin/cron` in System Settings -> Privacy & Security.

**Broker orders timeout:**
- Market is closed. Orders are DAY limit orders and need the market open to fill.

**"Broker init failed" error:**
- Alpaca keys not set. Ensure `~/.bash_profile` exports ALPACA_API_KEY and ALPACA_SECRET_KEY.

**Re-running install.sh:**
- Safe to run multiple times. It replaces the old cron block, never duplicates.
