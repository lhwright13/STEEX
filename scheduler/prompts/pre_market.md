# Pre-Market Pipeline

You are running the STEEX pre-market pipeline. This is the full daily routine: data refresh, regime check, risk assessment, exit execution, screening, entry execution, and reporting.

## Instructions

1. Read the orchestrator doc to understand the full pre_market sequence:
   ```
   agents/claude_manager.md
   ```

2. Run the pipeline:
   ```bash
   python scripts/run_manager.py pre_market {{FLAGS}}
   ```

3. After the pipeline completes, read the latest report:
   ```bash
   cat data/reports/latest.json
   ```

4. Summarize results in this order:
   - Market regime (bull / bear / neutral) and VIX level
   - Exits triggered (ticker, reason, P&L)
   - New entries (ticker, score, position size)
   - Portfolio snapshot (positions held, cash %, total value)
   - Risk alerts (drawdown level, sector concentration, stop clusters)

5. If anything looks abnormal, reference the relevant agent doc for context:
   - Data issues -> `agents/claude_data_agent.md`
   - Scoring anomalies -> `agents/claude_analysis_agent.md`
   - Risk threshold breaches -> `agents/claude_risk_agent.md`
   - Execution failures -> `agents/claude_execution_agent.md`

6. End with a health assessment: OK / WARNING / CRITICAL, and list any action items.
