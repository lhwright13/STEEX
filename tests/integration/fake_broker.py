"""In-memory fake Broker that *simulates the distributed races* behind every
July incident.

Mocked unit tests kept passing because they returned deterministic, internally
consistent values. The real bugs lived in the seam between a cron one-shot and
Alpaca's async server state: a position that vanishes between two calls (a
server-side stop filled), a stop-cancel that stays open for a few polls, a
transient empty ``get_positions()`` read, a late fill arriving after we already
cancelled, an order history that lags reality.

``FakeBroker`` is a real, stateful :class:`~src.broker.base.Broker`
implementation with scriptable knobs for exactly those behaviours, so
integration tests can drive :class:`~src.strategy.manager.QuantManager`
end-to-end against races instead of against a happy-path mock.

Design notes
------------
* State is a dict of ``BrokerPosition`` keyed by ticker plus a list of order
  history dicts shaped like :meth:`AlpacaBroker.get_order_history` returns
  (``side``/``status``/``ticker``/``filled_avg_price``/``order_id``).
* "Scriptable" knobs are per-ticker queues/counters consumed as the manager
  polls, so a test can say "the stop stays open for the first 3
  ``get_stop_order`` calls, then clears" without patching time.
* Nothing sleeps; ``execute_exits`` uses ``time.sleep(0.25)`` in its
  cancel-wait loop, so tests that exercise the cancel race should keep the poll
  budget tiny (a couple of iterations) to stay fast.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.broker.base import (
    AccountConfig,
    AccountInfo,
    AssetInfo,
    Broker,
    BrokerPosition,
    OrderResult,
)


class FakeBroker(Broker):
    """A stateful, race-scriptable in-memory broker for integration tests."""

    def __init__(
        self,
        positions: Optional[List[BrokerPosition]] = None,
        *,
        equity: float = 100_000.0,
        cash: float = 50_000.0,
    ):
        self._positions: Dict[str, BrokerPosition] = {
            p.ticker: p for p in (positions or [])
        }
        self._account = AccountInfo(
            buying_power=cash * 2, equity=equity, cash=cash
        )
        self._orders: List[Dict] = []
        self._stops: Dict[str, Dict] = {}
        self._order_seq = 0

        # ---- scriptable race knobs -------------------------------------
        # get_positions() returns [] for the first N calls (transient read).
        self.empty_reads_remaining = 0
        # Per-ticker: get_stop_order keeps returning the stop for N polls
        # AFTER cancel_stop_for_ticker before it "clears" (async cancel).
        self._stop_clear_lag: Dict[str, int] = {}
        # Per-ticker: sell_market returns this canned result instead of filling.
        self._sell_overrides: Dict[str, OrderResult] = {}
        # Per-ticker: a sell that "fills late" — recorded in order history but
        # the position is removed WITHOUT a synchronous OrderResult fill.
        self._late_fill_prices: Dict[str, float] = {}
        # Call counters, for assertions.
        self.calls: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # test-facing scripting helpers
    # ------------------------------------------------------------------

    def script_empty_reads(self, n: int) -> "FakeBroker":
        """Next ``n`` ``get_positions()`` calls return ``[]`` (transient)."""
        self.empty_reads_remaining = n
        return self

    def script_stop_clear_lag(self, ticker: str, polls: int) -> "FakeBroker":
        """After a cancel, ``get_stop_order(ticker)`` still shows the stop for
        ``polls`` more calls before clearing (async Alpaca cancel)."""
        self._stop_clear_lag[ticker] = polls
        return self

    def script_sell_result(self, ticker: str, result: OrderResult) -> "FakeBroker":
        """Force ``sell_market(ticker)`` to return ``result`` (e.g. a
        timeout/cancel) instead of filling."""
        self._sell_overrides[ticker] = result
        return self

    def vanish_position(
        self,
        ticker: str,
        *,
        filled_sell_price: Optional[float] = None,
        cancel_stop: bool = True,
    ) -> None:
        """Simulate a position disappearing from the broker between polls.

        With ``filled_sell_price`` a matching FILLED sell order is appended to
        order history (a real server-side stop fill). Without it, the position
        just vanishes with NO sell order — the rename-style ghost the sync
        integrity guard must refuse to record as a trade.
        """
        self._positions.pop(ticker, None)
        if cancel_stop:
            self._stops.pop(ticker, None)
        if filled_sell_price is not None:
            self._append_order(
                ticker=ticker,
                side="sell",
                status="filled",
                filled_avg_price=filled_sell_price,
            )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _bump(self, name: str) -> int:
        self.calls[name] = self.calls.get(name, 0) + 1
        return self.calls[name]

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"ord-{self._order_seq:04d}"

    def _append_order(
        self,
        *,
        ticker: str,
        side: str,
        status: str,
        filled_avg_price: Optional[float] = None,
        qty: float = 0.0,
    ) -> str:
        order_id = self._next_order_id()
        self._orders.append(
            {
                "order_id": order_id,
                "ticker": ticker,
                "side": side,
                "status": status,
                "filled_avg_price": filled_avg_price,
                "qty": qty,
            }
        )
        return order_id

    # ------------------------------------------------------------------
    # Limit orders
    # ------------------------------------------------------------------

    def buy(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        return self._do_buy(ticker, qty, limit_price)

    def sell(self, ticker: str, qty: int, limit_price: float) -> OrderResult:
        return self._do_sell(ticker, qty, limit_price)

    # ------------------------------------------------------------------
    # Market orders
    # ------------------------------------------------------------------

    def buy_market(self, ticker: str, qty: int) -> OrderResult:
        pos = self._positions.get(ticker)
        price = pos.avg_price if pos else 100.0
        return self._do_buy(ticker, qty, price)

    def buy_notional(self, ticker: str, notional: float) -> OrderResult:
        price = 100.0
        pos = self._positions.get(ticker)
        if pos:
            price = pos.avg_price
        qty = notional / price
        return self._do_buy(ticker, qty, price)

    def sell_market(self, ticker: str, qty: int) -> OrderResult:
        self._bump("sell_market")
        override = self._sell_overrides.get(ticker)
        if override is not None:
            return override
        return self._do_sell(ticker, qty, None)

    def _do_buy(self, ticker: str, qty: float, price: float) -> OrderResult:
        existing = self._positions.get(ticker)
        if existing:
            total_shares = existing.qty + qty
            existing.avg_price = (
                existing.avg_price * existing.qty + price * qty
            ) / total_shares
            existing.qty = total_shares
        else:
            self._positions[ticker] = BrokerPosition(
                ticker=ticker,
                qty=qty,
                avg_price=price,
                market_value=price * qty,
            )
        order_id = self._append_order(
            ticker=ticker, side="buy", status="filled",
            filled_avg_price=price, qty=qty,
        )
        return OrderResult(
            order_id=order_id, filled_qty=qty, filled_price=price, status="filled"
        )

    def _do_sell(
        self, ticker: str, qty: float, price: Optional[float]
    ) -> OrderResult:
        pos = self._positions.get(ticker)
        if pos is None:
            return OrderResult(status="failed", error="no position")
        fill_price = price if price is not None else pos.avg_price
        sell_qty = min(qty, pos.qty)
        pos.qty -= sell_qty
        if pos.qty <= 0:
            self._positions.pop(ticker, None)
        order_id = self._append_order(
            ticker=ticker, side="sell", status="filled",
            filled_avg_price=fill_price, qty=sell_qty,
        )
        return OrderResult(
            order_id=order_id, filled_qty=sell_qty,
            filled_price=fill_price, status="filled",
        )

    # ------------------------------------------------------------------
    # Account & positions
    # ------------------------------------------------------------------

    def get_account(self) -> AccountInfo:
        return self._account

    def get_positions(self) -> List[BrokerPosition]:
        self._bump("get_positions")
        if self.empty_reads_remaining > 0:
            self.empty_reads_remaining -= 1
            return []
        return list(self._positions.values())

    def get_position(self, ticker: str) -> Optional[BrokerPosition]:
        self._bump("get_position")
        return self._positions.get(ticker)

    def get_account_config(self) -> AccountConfig:
        return AccountConfig()

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_history(
        self,
        status: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        self._bump("get_order_history")
        orders = self._orders
        if status in ("closed", "filled"):
            orders = [o for o in orders if o.get("status") == "filled"]
        # Alpaca returns newest-first; mirror that so the sync's "first match"
        # picks the most recent fill.
        return list(reversed(orders))[:limit]

    # ------------------------------------------------------------------
    # Stop orders
    # ------------------------------------------------------------------

    def place_stop_order(
        self, ticker: str, qty: int, stop_price: float
    ) -> OrderResult:
        self._bump("place_stop_order")
        order_id = self._next_order_id()
        self._stops[ticker] = {
            "order_id": order_id, "ticker": ticker,
            "qty": qty, "stop_price": stop_price,
        }
        return OrderResult(order_id=order_id, status="accepted")

    def place_trailing_stop_order(
        self, ticker: str, qty: int, trail_percent: float
    ) -> OrderResult:
        return self.place_stop_order(ticker, qty, 0.0)

    def cancel_stop_for_ticker(self, ticker: str) -> bool:
        self._bump("cancel_stop_for_ticker")
        # Async cancel: if a clear-lag is scripted, the stop stays visible for
        # that many subsequent get_stop_order polls before actually clearing.
        lag = self._stop_clear_lag.get(ticker, 0)
        if lag > 0:
            return True  # accepted, but not yet reflected in get_stop_order
        self._stops.pop(ticker, None)
        return True

    def update_stop_order(
        self, ticker: str, qty: int, new_stop_price: float
    ) -> OrderResult:
        return self.place_stop_order(ticker, qty, new_stop_price)

    def get_stop_order(self, ticker: str) -> Optional[Dict]:
        self._bump("get_stop_order")
        lag = self._stop_clear_lag.get(ticker, 0)
        if lag > 0:
            self._stop_clear_lag[ticker] = lag - 1
            if lag - 1 == 0:
                # Last lagged poll: the cancel now takes effect.
                self._stops.pop(ticker, None)
            return self._stops.get(ticker)
        return self._stops.get(ticker)

    def get_all_stop_orders(self) -> List[Dict]:
        return list(self._stops.values())

    # ------------------------------------------------------------------
    # Bracket / close
    # ------------------------------------------------------------------

    def place_bracket_order(
        self,
        ticker: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        take_profit_price: float,
    ) -> OrderResult:
        res = self._do_buy(ticker, qty, limit_price)
        self.place_stop_order(ticker, qty, stop_price)
        return res

    def close_position(self, ticker: str) -> OrderResult:
        pos = self._positions.get(ticker)
        if pos is None:
            return OrderResult(status="failed", error="no position")
        return self._do_sell(ticker, pos.qty, None)

    def close_all_positions(self) -> List[OrderResult]:
        return [self.close_position(t) for t in list(self._positions)]

    # ------------------------------------------------------------------
    # Asset info / clock
    # ------------------------------------------------------------------

    def get_asset(self, ticker: str) -> Optional[AssetInfo]:
        return AssetInfo(
            ticker=ticker, tradable=True, fractionable=True,
            shortable=True, asset_class="us_equity", status="active",
        )

    def get_clock(self) -> Dict:
        return {"is_open": True, "next_open": None, "next_close": None}

    def get_calendar(self, start: str, end: str) -> List[Dict]:
        return []
