# Post-Market Wrap-Up

You are running the STEEX end-of-day wrap-up. This finalizes the trading day: updates all stops with closing prices, generates the daily report, and logs performance.

## Instructions

1. Read the orchestrator doc for the post_market sequence:
   ```
   agents/claude_manager.md
   ```

2. Run the post-market pipeline (source profile first for Alpaca API keys):
   ```bash
   source ~/.bash_profile 2>/dev/null && venv/bin/python scripts/run_manager.py post_market {{FLAGS}}
   ```
   If broker init fails with missing credentials, do NOT fall back to simulation. Stop and report the error.

3. After the pipeline completes, read the latest report:
   ```bash
   cat data/reports/latest.json
   ```

4. Provide a daily summary:
   - Total trades today (entries + exits)
   - Realized P&L from exits
   - Unrealized P&L across open positions
   - Portfolio value and daily change (% and $)
   - Updated stop levels for all positions
   - Sector allocation breakdown

5. Compare today's actions against the morning plan (if pre_market ran):
   - Were planned entries executed?
   - Any unplanned exits?
   - Did regime change during the day?

6. Flag anything that needs review before tomorrow's pre-market:
   - Positions near stop levels
   - Upcoming earnings for held stocks
   - Unusual volume or price action

7. End with a health assessment: OK / WARNING / CRITICAL, and list action items for the next session.
