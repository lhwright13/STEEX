# ReportAgent - Reporting and Audit Logging

## Role

You compile all data from a pipeline run into structured reports, save them to disk, and produce formatted console output. You are the last agent to run in every pipeline mode. You also maintain the in-memory audit log.

## Who You Interact With

- **Called by**: QuantManager (orchestrator) - always runs last
- **Depends on**: All other agents (you compile their output)
- **Provides to**: Dashboard (reads report JSON), User (console output), Scheduler logs

## Tools and How They Work

### TradeTracker (`src/portfolio/tracker.py`)
- `calculate_metrics()` -> Dict with total_trades, win_rate, profit_factor, avg_pnl_pct
- Used to populate the performance section of the report

### Report Storage
- Reports saved to `data/reports/report_YYYYMMDD_HHMMSS.json`
- Latest always saved as `data/reports/latest.json`
- Dashboard reads `latest.json` for current state

### Rich Console (`rich` library)
- Panel for headers, Table for data grids
- Color coding: green for profit, red for loss, yellow for warnings

## Methods

### generate_daily_report(mode) -> Dict
Compiles the full report structure:
```
{
    timestamp, mode,
    data_health: {healthy, issues},
    regime: {name, vix, sizing_multiplier, entries_allowed},
    portfolio: {
        position_count, total_cost, total_value,
        total_pnl_dollars, total_pnl_pct,
        portfolio_equity,    // from broker account
        cash,                // from broker account
        drawdown, vix, immediate_exits,
        positions: [...]
    },
    exits: [{ticker, price, pnl, reason, urgency}, ...],
    entries: [{ticker, price, shares, score, reasons}, ...],
    screening: {universe, stage_1, ..., final},
    candidates: [{ticker, score, reasons}, ...],
    risk_alerts: ["CRISIS: ...", "DRAWDOWN: ...", ...],
    performance: {total_trades, win_rate, profit_factor},
    log: [{timestamp, action, detail, data}, ...]
}
```

### save_report(report) -> Path
- Creates data/reports/ directory if needed
- Saves timestamped file + latest.json
- Returns the file path

### print_summary(report)
Console output sections:
1. Header (mode, date)
2. Regime + VIX
3. Data health (if issues)
4. Account: Equity + Cash (from broker)
5. Portfolio positions table (sorted by P&L)
6. Exit signals (with AUTO/REC labels)
7. Buy candidates table
8. Screening funnel
9. Risk alerts
10. Track record (trades, win rate, profit factor)

## Audit Log

The `self.log` list on QuantManager accumulates entries throughout the run:
- `{"timestamp": ..., "action": "data|analysis|risk|execution", "detail": "...", "data": {...}}`
- Every significant decision gets logged
- Included in the final report JSON for full auditability

## When to Update This File

- When adding new sections to the report
- When the dashboard expects new fields in latest.json
- When changing console output formatting
- When adding notification support (email, Slack, etc.)
