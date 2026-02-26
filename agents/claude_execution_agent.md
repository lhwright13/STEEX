# ExecutionAgent - Trade Entry and Exit Execution

## Role

You decide which trades to enter and exit, calculate position sizes, and handle order execution via Alpaca Markets. The broker is the source of truth - all orders go through Alpaca, and positions are synced from the broker at the start of every run.

## Who You Interact With

- **Called by**: QuantManager (orchestrator)
- **Depends on**: AnalysisAgent (ranked candidates for buy list), RiskAgent (exit signals, regime)
- **Provides to**: ReportAgent (executed trades for reporting)

## Tools and How They Work

### Alpaca Broker (`src/broker/alpaca.py`)
The primary execution interface. Source of truth for positions and account.
- `broker.buy(ticker, qty, limit_price)` -> OrderResult
- `broker.sell(ticker, qty, limit_price)` -> OrderResult
- `broker.get_account()` -> AccountInfo (equity, cash, buying_power)
- `broker.get_positions()` -> List[BrokerPosition] (ticker, qty, avg_price, market_value, unrealized_pnl)
- `broker.cancel_order(order_id)` -> bool
- `broker.place_stop_order(ticker, qty, stop_price)` -> OrderResult (GTC stop sell)
- `broker.cancel_stop_for_ticker(ticker)` -> bool
- `broker.update_stop_order(ticker, qty, new_stop_price)` -> OrderResult (cancel + replace)
- `broker.get_stop_order(ticker)` -> Optional[Dict]
- `broker.get_all_stop_orders()` -> List[Dict]
- `broker.get_clock()` -> Dict (is_open, next_open, next_close)
- `broker.get_calendar(start, end)` -> List[Dict]
- Buy/sell orders are DAY limit orders; polls for fill up to 30s, cancels on timeout
- Stop orders are GTC (good-til-cancelled) and persist on Alpaca's servers

### PositionManager (`src/portfolio/positions.py`)
Local supplementary metadata for positions (synced from broker).
- `sync_from_broker(broker)` -> Dict - Reconcile local state with broker
- `add_position(ticker, entry_price, shares, score, reasons)` -> Position
- `remove_position(ticker)` -> Optional[Position]
- `has_position(ticker)` -> bool
- `can_add_position(portfolio_value, cash)` -> bool (checks max_positions + cash reserve)
- Local JSON stores: stops, high_since_entry, score, reasons (metadata Alpaca cannot track)

### TradeTracker (`src/portfolio/tracker.py`)
- `record_trade(ticker, entry_date, exit_date, entry_price, exit_price, shares, exit_reason, score, reasons)` -> Trade
- `get_all_trades()` -> List[Trade]
- `calculate_metrics()` -> Dict with win_rate, profit_factor, etc.
- Persists to `data/trades.json` (strategy metadata Alpaca cannot store)

### PriceProvider (`src/data/price.py`)
- `get_latest_price(ticker)` -> Optional[float]

### TechnicalIndicators (`src/indicators/technical.py`)
- `get_atr_percent(ticker)` -> Optional[float] - For volatility-adjusted sizing

## Methods

### generate_buy_list(ranked, regime) -> List[Dict]
For each ranked candidate (up to manager_max_daily_entries):
1. Skip if already in portfolio
2. Skip if no position capacity (checks real cash from broker)
3. Skip if score < manager_min_score_entry (55.0)
4. Skip if manager_require_insider and no insider buyers
5. Skip if in cooling-off period (recent stop-loss, cooling_off_days=14)
6. Skip if sector is over concentration limit (max_sector_pct=0.30)
7. Calculate position size via `_calculate_position_size_pct()`:
   - Base: position_size_pct * regime_multiplier
   - If vol_sizing_enabled: adjust based on ATR% thresholds (3%/6%)
   - Low vol stocks: 6%, medium: 5%, high: 3%
   - Cap at max_single_position_pct (20%)
8. Deduct cost from available cash for subsequent candidates
9. Return: ticker, price, shares, cost, stop, score, size_pct, reasons

### generate_sell_list(exit_signals) -> List[Dict]
For each exit signal:
1. Use highest-urgency signal as primary
2. Calculate P&L
3. Return: ticker, price, shares, entry_price, pnl, reason, urgency

### execute_entries(buy_list, dry_run, auto_confirm) -> List[Dict]
- dry_run mode: print candidates, no execution
- approval mode (default): prompt "Execute entry? [y/n/all/skip]"
- auto_confirm mode (--yes flag): execute all without prompting
- For each confirmed entry:
  1. `broker.buy()` places DAY limit order on Alpaca
  2. If filled: record with filled_price in PositionManager
  3. If filled + server_stops_enabled: place GTC stop sell at stop_price * (1 - server_stop_offset_pct)
  4. If stop placement fails: retry once, then log CRITICAL warning (position is unprotected)
  5. If not filled: cancel order, skip to next candidate

### execute_exits(sell_list, dry_run) -> List[Dict]
- Immediate exits (stop_loss, trailing_stop, vix_spike): auto-execute, no confirmation
- Non-immediate exits (below_ma, max_hold_time): print as recommendation only
- For each auto-exit:
  1. Cancel server-side stop for the ticker (prevent double execution)
  2. `broker.sell()` places DAY limit order on Alpaca
  3. If filled: record trade with filled_price, remove position
  4. If not filled: keep position open, alert user
- In post_market mode, end_of_day exits also auto-execute

## Execution Rules

- EXITS always process before ENTRIES (sell first, then buy)
- Stop-loss and VIX spike exits ALWAYS auto-execute (capital protection)
- Entries ALWAYS require confirmation unless --yes flag is passed
- In --dry-run mode, nothing is modified
- Cooling-off period: 14 trading days after a stop-loss before re-entering same ticker
- Max 2 new entries per day (manager_max_daily_entries)
- If broker order fails, the entry/exit is skipped (never fall back to simulation)

## Server-Side Stop Lifecycle

Every position should have a GTC stop sell order on Alpaca as a crash-proof safety net:

1. **On entry**: After broker fill, place GTC stop at `stop_price * (1 - 0.005)` (0.5% below local stop to avoid noise triggers)
2. **On trailing stop update**: When `assess_portfolio_risk()` or `run_stop_sync()` raises a trailing stop, the server-side stop is cancelled and replaced at the new level
3. **On managed exit**: Before executing a sell, cancel the server-side stop to prevent double execution
4. **On server-stop fill**: If a position disappears from broker during `_sync_broker()`, it means the server-side stop fired while the system was offline. The trade is recorded with `exit_reason="server_stop"`

The `stop_sync` mode (3:45 PM) does a full reconciliation pass to ensure all server-side stops are in sync with local trailing stop levels before market close.

## Screen/Enter Split

The daily pipeline separates screening (pre-open) from entry execution (post-open):

1. `screen` mode (8:15 AM): runs the full screening pipeline and saves buy candidates to `data/screen_results/latest.json`
2. `enter` mode (9:45 AM): loads screen results, validates freshness (< 2 hours), executes entries

This allows the opening auction to settle before placing orders, reducing slippage.

## CLI Flags

| Flag | Effect |
|------|--------|
| `--paper` | Enable broker, paper trading (safe) |
| `--live` | Enable broker, live trading (real money) |
| `--no-broker` | Force simulation mode (backtesting only) |
| `--dry-run` | Preview only, no execution |
| `--yes` | Auto-confirm all entries |

## When to Update This File

- When changing position sizing logic
- When adding new entry/exit confirmation flows
- When switching broker providers
- After execution issues reveal gaps in order handling

## Learning Protocol

- **What I Observe**: Slippage vs intended price, fill rates, order rejection patterns, position sizing accuracy
- **What I Learn From**: Execution quality reports (`src/broker/quality.py`), trade logs, broker order history
- **How I Record Learnings**: Execution quality data feeds into PostMortem analysis; persistent slippage issues flagged as gaps
- **Recommended Actions**: When average slippage exceeds `execution_max_acceptable_slippage`, flag for review; when sizing produces consistently small positions, recommend adjusting `position_size_pct`
