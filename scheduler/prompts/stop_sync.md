# Pre-Close Stop Sync

You are running the STEEX pre-close stop sync. This updates trailing stops with the latest prices and syncs server-side GTC stops on Alpaca before market close.

## Instructions

1. Read the orchestrator doc for context:
   ```
   agents/claude_manager.md
   ```

2. Run the stop sync pipeline (source profile first for Alpaca API keys):
   ```bash
   source ~/.bash_profile 2>/dev/null && venv/bin/python scripts/run_manager.py stop_sync {{FLAGS}}
   ```
   If broker init fails with missing credentials, do NOT fall back to simulation. Stop and report the error.

3. After the pipeline completes, read the latest report:
   ```bash
   cat data/reports/latest.json
   ```

4. Summarize results:
   - Number of stops updated
   - Which positions had trailing stops raised
   - Current stop levels for each position
   - Any discrepancies between local and server-side stops

5. End with a brief status: OK / WARNING / CRITICAL.
