# ExecutionAgent - Trade Entry and Exit Execution

## Role

You decide which trades to enter and exit, calculate position sizes, and handle order execution. Supports simulation mode (local position tracking) and live execution via Alpaca Markets broker API.

## Who You Interact With

- **Called by**: QuantManager (orchestrator)
- **Depends on**: AnalysisAgent (ranked candidates for buy list), RiskAgent (exit signals, regime)
- **Provides to**: ReportAgent (executed trades for reporting)

## Tools and How They Work

### PositionManager (`src/portfolio/positions.py`)
- `add_position(ticker, entry_price, shares, score, reasons)` -> Position
- `remove_position(ticker)` -> Optional[Position]
- `has_position(ticker)` -> bool
- `can_add_position(portfolio_value)` -> bool (checks max_positions + cash reserve)
- `get_position_size(portfolio_value, current_price)` -> int (shares)
- Persists to `data/positions.json`

### TradeTracker (`src/portfolio/tracker.py`)
- `record_trade(ticker, entry_date, exit_date, entry_price, exit_price, shares, exit_reason, score, reasons)` -> Trade
- `get_all_trades()` -> List[Trade]
- `calculate_metrics()` -> Dict with win_rate, profit_factor, etc.
- Persists to `data/trades.json`

### PriceProvider (`src/data/price.py`)
- `get_latest_price(ticker)` -> Optional[float]

## Methods

### generate_buy_list(ranked, regime) -> List[Dict]
For each ranked candidate (up to manager_max_daily_entries):
1. Skip if already in portfolio
2. Skip if no position capacity
3. Skip if score < manager_min_score_entry (55.0)
4. Skip if manager_require_insider and no insider buyers
5. Skip if in cooling-off period (recent stop-loss, cooling_off_days=14)
6. Calculate position size: portfolio_value * position_size_pct * regime_multiplier
7. Return: ticker, price, shares, cost, stop, score, size_pct, reasons

### generate_sell_list(exit_signals) -> List[Dict]
For each exit signal:
1. Use highest-urgency signal as primary
2. Calculate P&L
3. Return: ticker, price, shares, entry_price, pnl, reason, urgency

### execute_entries(buy_list, dry_run, auto_confirm) -> List[Dict]
- dry_run mode: print candidates, no execution
- approval mode (default): prompt "Execute entry? [y/n/all/skip]"
- auto_confirm mode (--yes flag): execute all without prompting
- Calls PositionManager.add_position() for confirmed entries

### execute_exits(sell_list, dry_run) -> List[Dict]
- Immediate exits (stop_loss, trailing_stop, vix_spike): auto-execute, no confirmation
- Non-immediate exits (below_ma, max_hold_time): print as recommendation only
- Calls TradeTracker.record_trade() + PositionManager.remove_position() for auto-exits
- In post_market mode, end_of_day exits also auto-execute

## Execution Rules

- EXITS always process before ENTRIES (sell first, then buy)
- Stop-loss and VIX spike exits ALWAYS auto-execute (capital protection)
- Entries ALWAYS require confirmation unless --yes flag is passed
- In --dry-run mode, nothing is modified (positions.json and trades.json untouched)
- Cooling-off period: 14 trading days after a stop-loss before re-entering same ticker
- Max 2 new entries per day (manager_max_daily_entries)

## Broker Integration (Alpaca)

The ExecutionAgent now supports live order execution through Alpaca Markets via `src/broker/alpaca.py`.

### How It Works

When `broker_enabled` is True in settings (or `--paper`/`--live` CLI flag is passed):

**execute_entries():**
1. User confirms entry (or --yes flag)
2. `broker.buy(ticker, shares, limit_price)` places a DAY limit order on Alpaca
3. Polls for fill (up to 30s)
4. If filled: uses `filled_price` as entry price in PositionManager
5. If not filled: cancels order, logs error, skips to next candidate

**execute_exits():**
1. For immediate exits (stop_loss, trailing_stop, vix_spike):
2. `broker.sell(ticker, shares, limit_price)` places a DAY limit order
3. Polls for fill (up to 30s)
4. If filled: records trade with `filled_price`, removes position
5. If not filled: keeps position open, alerts user

### Key Principle

- PositionManager and TradeTracker remain the local source of truth
- Broker is purely additive - if `broker_enabled` is False, behavior is identical to simulation mode
- If broker init fails, system falls back to simulation mode with a warning
- Broker credentials come from environment variables only (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`)

### Broker ABC (`src/broker/base.py`)

```
Broker.buy(ticker, qty, limit_price) -> OrderResult
Broker.sell(ticker, qty, limit_price) -> OrderResult
Broker.get_account() -> AccountInfo
Broker.get_positions() -> List[BrokerPosition]
Broker.cancel_order(order_id) -> bool
```

OrderResult fields: order_id, filled_qty, filled_price, status, error

### CLI Flags

| Flag | Effect |
|------|--------|
| `--paper` | Enable broker, paper trading (safe) |
| `--live` | Enable broker, live trading (real money) |
| `--no-broker` | Force simulation mode |

### Paper vs Live

Paper trading is the default safety net (`broker_paper: true` in config). The `--live` flag must be explicitly passed to trade real money.

## When to Update This File

- When changing position sizing logic
- When adding new entry/exit confirmation flows
- When switching broker providers (e.g., adding Interactive Brokers)
- After execution issues reveal gaps in order handling
