# Health Check / Heartbeat

You are running the STEEX health check. This verifies API connectivity, account status, position reconciliation, and server-side stop order coverage.

## Instructions

1. Run the health check (source profile first for Alpaca API keys):
   ```bash
   source ~/.bash_profile 2>/dev/null && venv/bin/python scripts/health_check.py
   ```

2. Read the heartbeat results:
   ```bash
   cat data/heartbeat.json
   ```

3. Summarize:
   - API connectivity status
   - Market status (open/closed, next open)
   - Position reconciliation (broker vs local)
   - Stop order coverage (every position should have a server-side stop)
   - Last successful run timestamp and mode

4. If there are missing stops, recommend running stop_sync.
   If positions are out of sync, flag for broker sync on next run.

5. End with overall health: OK / WARNING / CRITICAL.
