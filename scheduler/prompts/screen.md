# Pre-Open Screening

You are running the STEEX pre-open screening pipeline. This runs BEFORE market open: data refresh, regime check, risk assessment, exit signals, and full screening + ranking. It does NOT execute entries.

## Instructions

1. Read the orchestrator doc for context:
   ```
   agents/claude_manager.md
   ```

2. Run the screening pipeline (source profile first for Alpaca API keys):
   ```bash
   source ~/.bash_profile 2>/dev/null && venv/bin/python scripts/run_manager.py screen {{FLAGS}}
   ```
   If broker init fails with missing credentials, do NOT fall back to simulation. Stop and report the error.

3. After the pipeline completes, read the latest report:
   ```bash
   cat data/reports/latest.json
   ```

4. Also check screen results saved for the entry phase:
   ```bash
   cat data/screen_results/latest.json
   ```

5. Summarize results:
   - Market regime and VIX level
   - Any exit signals detected (will be executed)
   - Screening funnel (universe -> final candidates)
   - Top ranked candidates with scores
   - Portfolio construction selections

6. End with a health assessment: OK / WARNING / CRITICAL, and note that entries will execute in the next phase (9:45 AM).
