# Midday Monitor

You are running the STEEX midday monitoring check. No new screening or entries - this is a risk-only pass to catch intraday stop triggers and regime shifts.

## Instructions

1. Read the orchestrator doc for the monitor sequence:
   ```
   agents/claude_manager.md
   ```

2. Run the monitor pipeline (source profile first for Alpaca API keys):
   ```bash
   source ~/.bash_profile 2>/dev/null && venv/bin/python scripts/run_manager.py monitor {{FLAGS}}
   ```
   If broker init fails with missing credentials, do NOT fall back to simulation. Stop and report the error.

3. After the pipeline completes, read the latest report:
   ```bash
   cat data/reports/latest.json
   ```

4. Summarize:
   - Current regime and any change since pre-market
   - Positions approaching stop levels (within 2% of trigger)
   - Any exits triggered since morning
   - VIX movement and whether caution/exit thresholds are in play
   - Portfolio drawdown status

5. If risk thresholds are breached, read `agents/claude_risk_agent.md` for the expected response protocol and confirm the system followed it.

6. End with a health assessment: OK / WARNING / CRITICAL, and flag anything that needs manual attention before market close.
