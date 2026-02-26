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
- Orders are DAY limit orders; polls for fill up to 30s, cancels on timeout

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
  3. If not filled: cancel order, skip to next candidate

### execute_exits(sell_list, dry_run) -> List[Dict]
- Immediate exits (stop_loss, trailing_stop, vix_spike): auto-execute, no confirmation
- Non-immediate exits (below_ma, max_hold_time): print as recommendation only
- For each auto-exit:
  1. `broker.sell()` places DAY limit order on Alpaca
  2. If filled: record trade with filled_price, remove position
  3. If not filled: keep position open, alert user
- In post_market mode, end_of_day exits also auto-execute

## Execution Rules

- EXITS always process before ENTRIES (sell first, then buy)
- Stop-loss and VIX spike exits ALWAYS auto-execute (capital protection)
- Entries ALWAYS require confirmation unless --yes flag is passed
- In --dry-run mode, nothing is modified
- Cooling-off period: 14 trading days after a stop-loss before re-entering same ticker
- Max 2 new entries per day (manager_max_daily_entries)
- If broker order fails, the entry/exit is skipped (never fall back to simulation)

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
