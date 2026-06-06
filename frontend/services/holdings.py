"""HoldingsMixin (split from services.py, P0-5)."""
import json  # noqa: F401
import logging
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Dict, List, Optional, Any  # noqa: F401

from config.settings import get_settings  # noqa: F401
from src.agents.registry import AgentRegistry, ModeConfig  # noqa: F401
from src.regime.detector import RegimeDetector  # noqa: F401

logger = logging.getLogger("steex.dashboard")

class HoldingsMixin:
    def get_portfolio_performance(self, period: str = "1M") -> Dict[str, Any]:
        """Portfolio equity curve vs S&P 500, with alpha, rebased to % return.

        Pulls the broker's daily portfolio history (the authoritative equity
        curve), fetches SPY closes over the same window, rebases both to 0% at
        the first common date, and computes alpha = portfolio% - SPY% at each
        point. Returns {available: False} when the broker or price data can't
        be reached so the UI can show an empty state.
        """
        period = period if period in self._PERF_PERIODS else "1M"
        alpaca_period, spy_days = self._PERF_PERIODS[period]

        # --- Portfolio equity curve from the broker ---------------------
        equity_by_date = {}
        try:
            import os
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetPortfolioHistoryRequest
            paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
            tc = TradingClient(
                os.environ["ALPACA_API_KEY"],
                os.environ["ALPACA_SECRET_KEY"],
                paper=paper,
            )
            hist = tc.get_portfolio_history(
                GetPortfolioHistoryRequest(period=alpaca_period, timeframe="1D")
            )
            for ts, eq in zip(hist.timestamp or [], hist.equity or []):
                if eq:
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                    equity_by_date[d] = float(eq)
        except Exception as e:
            logger.debug("Portfolio history unavailable: %s", e)
            return {"available": False, "period": period, "reason": "broker unavailable"}

        if len(equity_by_date) < 2:
            return {"available": False, "period": period, "reason": "insufficient history"}

        # --- SPY closes over the same window ----------------------------
        spy_by_date = {}
        try:
            from src.data.price import PriceProvider
            df = PriceProvider().get_ohlcv("SPY", days=spy_days)
            if df is not None and "Close" in df.columns:
                for idx, row in df.iterrows():
                    d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                    spy_by_date[d] = float(row["Close"])
        except Exception as e:
            logger.debug("SPY history unavailable: %s", e)

        dates = sorted(equity_by_date.keys())

        # Align SPY to portfolio dates, carrying the last known close forward
        # for any portfolio date without an exact SPY bar (weekend boundary).
        spy_sorted = sorted(spy_by_date.keys())
        spy_aligned = {}
        last = None
        si = 0
        for d in dates:
            while si < len(spy_sorted) and spy_sorted[si] <= d:
                last = spy_by_date[spy_sorted[si]]
                si += 1
            if last is not None:
                spy_aligned[d] = last

        base_eq = equity_by_date[dates[0]]
        base_spy = spy_aligned.get(dates[0])

        series = []
        for d in dates:
            port_pct = round(100 * (equity_by_date[d] / base_eq - 1), 2)
            spy_pct = None
            alpha = None
            if base_spy and d in spy_aligned:
                spy_pct = round(100 * (spy_aligned[d] / base_spy - 1), 2)
                alpha = round(port_pct - spy_pct, 2)
            series.append({
                "date": d,
                "equity": round(equity_by_date[d], 2),
                "portfolio_pct": port_pct,
                "spy_pct": spy_pct,
                "alpha_pct": alpha,
            })

        last_pt = series[-1]
        return {
            "available": True,
            "period": period,
            "series": series,
            "summary": {
                "portfolio_return_pct": last_pt["portfolio_pct"],
                "spy_return_pct": last_pt["spy_pct"],
                "alpha_pct": last_pt["alpha_pct"],
                "start_equity": round(base_eq, 2),
                "end_equity": round(equity_by_date[dates[-1]], 2),
                "spy_available": base_spy is not None,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_portfolio_holdings(self) -> Dict[str, Any]:
        """Current open positions ("buys") plus a portfolio summary.

        Always returns the locally tracked positions (data/positions.json) so
        the panel works offline. Best-effort enriches each row with live
        market value / unrealized P&L from the broker; if the broker is
        unreachable, falls back to the cached heartbeat account snapshot and
        cost-basis-only figures.
        """
        positions = self._load_json_file(self.data_dir / "positions.json") or {}
        rows = []
        for tkr, p in positions.items():
            rows.append({
                "ticker": p.get("ticker", tkr),
                "shares": p.get("shares"),
                "entry_price": p.get("entry_price"),
                "cost_basis": p.get("cost_basis"),
                "current_stop": p.get("current_stop"),
                "score": p.get("score"),
                "entry_date": p.get("entry_date"),
                "reasons": p.get("reasons", []),
                "current_price": None,
                "market_value": None,
                "unrealized_pnl": None,
                "unrealized_pct": None,
            })
        rows.sort(key=lambda r: r["ticker"])

        summary = {"equity": None, "cash": None, "buying_power": None}
        live = False
        try:
            import os
            from src.broker.alpaca import AlpacaBroker
            paper = os.environ.get("STEEX_BROKER_PAPER", "true").lower() == "true"
            broker = AlpacaBroker(paper=paper)
            bpos = {bp.ticker: bp for bp in broker.get_positions()}
            # The Alpaca paper feed sometimes caches a stale/garbage per-share
            # price (e.g. BNY showing $10 vs a real $142). The trading logic
            # always uses PriceProvider, so cross-check here: when the broker
            # price diverges >25% from the live quote, trust the quote and
            # recompute market value / P&L from it.
            from src.data.price import PriceProvider
            price_provider = PriceProvider()
            for r in rows:
                bp = bpos.get(r["ticker"])
                if not bp:
                    continue
                broker_price = (bp.market_value / bp.qty) if bp.qty else None
                quote = None
                try:
                    quote = price_provider.get_latest_price(r["ticker"])
                except Exception:
                    pass
                use_quote = (
                    quote and broker_price
                    and abs(broker_price - quote) / quote > 0.25
                )
                if use_quote:
                    qty = bp.qty or r.get("shares") or 0
                    r["current_price"] = round(quote, 2)
                    r["market_value"] = round(quote * qty, 2)
                    r["unrealized_pnl"] = round((quote * qty) - (r.get("cost_basis") or 0), 2)
                    r["price_source"] = "quote (broker stale)"
                else:
                    r["market_value"] = round(bp.market_value, 2)
                    r["unrealized_pnl"] = round(bp.unrealized_pnl, 2)
                    if broker_price is not None:
                        r["current_price"] = round(broker_price, 2)
                if r["cost_basis"]:
                    r["unrealized_pct"] = round(100 * r["unrealized_pnl"] / r["cost_basis"], 2)
            acct = broker.get_account()
            summary = {
                "equity": acct.equity,
                "cash": acct.cash,
                "buying_power": acct.buying_power,
            }
            live = True
        except Exception as e:
            logger.debug("Live broker enrich failed, falling back to cache: %s", e)
            hb = self._load_json_file(self.data_dir / "heartbeat.json") or {}
            api = (hb.get("checks") or {}).get("api") or {}
            summary = {
                "equity": api.get("equity"),
                "cash": api.get("cash"),
                "buying_power": api.get("buying_power"),
            }

        total_cost = round(sum(r["cost_basis"] or 0 for r in rows), 2)
        total_mv = sum(r["market_value"] for r in rows if r["market_value"] is not None)
        total_upnl = sum(r["unrealized_pnl"] for r in rows if r["unrealized_pnl"] is not None)
        summary["total_cost_basis"] = total_cost
        summary["total_market_value"] = round(total_mv, 2) if live else None
        summary["total_unrealized_pnl"] = round(total_upnl, 2) if live else None
        if summary.get("equity"):
            invested = (total_mv if live else total_cost)
            summary["exposure_pct"] = round(100 * invested / summary["equity"], 1)
        else:
            summary["exposure_pct"] = None

        return {
            "positions": rows,
            "count": len(rows),
            "summary": summary,
            "live": live,
            "source": "broker" if live else "cache",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def get_trade_history(self, limit: int = 50) -> Dict[str, Any]:
        """Closed trades from data/trades.json plus realized-P&L summary stats.

        Surfaces realized performance (win rate, avg win/loss, hold time,
        exit-reason breakdown) that the unrealized-only holdings view misses.
        """
        trades = self._load_json_file(self.data_dir / "trades.json") or []
        if not isinstance(trades, list):
            trades = []

        # Newest first by exit date.
        trades = sorted(trades, key=lambda t: t.get("exit_date") or "", reverse=True)

        wins = [t for t in trades if (t.get("pnl_dollars") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl_dollars") or 0) < 0]
        total_pnl = round(sum(t.get("pnl_dollars") or 0 for t in trades), 2)
        avg_win = round(sum(t["pnl_dollars"] for t in wins) / len(wins), 2) if wins else 0.0
        avg_loss = round(sum(t["pnl_dollars"] for t in losses) / len(losses), 2) if losses else 0.0
        avg_hold = round(sum(t.get("hold_days") or 0 for t in trades) / len(trades), 1) if trades else 0.0

        # Exit-reason breakdown (e.g. surfaces a high server_stop rate).
        reasons: Dict[str, int] = {}
        for t in trades:
            r = t.get("exit_reason") or "unknown"
            reasons[r] = reasons.get(r, 0) + 1

        rows = [{
            "ticker": t.get("ticker"),
            "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"),
            "entry_price": round(t["entry_price"], 2) if t.get("entry_price") is not None else None,
            "exit_price": round(t["exit_price"], 2) if t.get("exit_price") is not None else None,
            "shares": t.get("shares"),
            "pnl_dollars": round(t.get("pnl_dollars") or 0, 2),
            "pnl_pct": round((t.get("pnl_pct") or 0) * 100, 2),
            "hold_days": t.get("hold_days"),
            "exit_reason": t.get("exit_reason"),
        } for t in trades[:limit]]

        return {
            "trades": rows,
            "summary": {
                "count": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(100 * len(wins) / len(trades), 1) if trades else None,
                "total_realized_pnl": total_pnl,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": round(abs(sum(t["pnl_dollars"] for t in wins) /
                                            sum(t["pnl_dollars"] for t in losses)), 2)
                                 if losses and sum(t["pnl_dollars"] for t in losses) else None,
                "avg_hold_days": avg_hold,
                "exit_reasons": reasons,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
