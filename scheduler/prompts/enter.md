# Post-Open Entry Execution

You are running the STEEX entry execution phase. This runs AFTER market open (9:45 AM) to let the opening auction settle. It loads screen results from the morning screening pass and executes entries with server-side stops.

## Instructions

1. Read the orchestrator doc for context:
   ```
   agents/claude_manager.md
   ```

2. Run the entry pipeline (source profile first for Alpaca API keys):
   ```bash
   source ~/.bash_profile 2>/dev/null && venv/bin/python scripts/run_manager.py enter {{FLAGS}}
   ```
   If broker init fails with missing credentials, do NOT fall back to simulation. Stop and report the error.

3. After the pipeline completes, read the latest report:
   ```bash
   cat data/reports/latest.json
   ```

4. Summarize results:
   - Whether screen results were fresh and loaded successfully
   - Quick risk check results and any exits triggered
   - Entries executed (ticker, shares, price, stop level)
   - Server-side stops placed on Alpaca
   - Updated portfolio snapshot

5. Verify server-side stops are in place:
   ```bash
   venv/bin/python -c "
   from src.broker.alpaca import AlpacaBroker
   import os
   broker = AlpacaBroker(paper=True)
   stops = broker.get_all_stop_orders()
   for s in stops:
       print(f'{s[\"ticker\"]}: stop @ \${s[\"stop_price\"]:.2f} ({s[\"qty\"]} shares)')
   if not stops:
       print('No active stop orders')
   "
   ```

6. End with a health assessment: OK / WARNING / CRITICAL, and list any action items.
