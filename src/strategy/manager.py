"""Automated trading orchestrator.

Coordinates data refresh, screening, risk management, execution,
and reporting into a single end-to-end pipeline.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config.settings import Settings, get_settings
from ..broker.base import Broker
from ..data.geopolitical import get_ticker_sector
from ..data.price import PriceProvider
from ..data.vix import VixProvider
from ..indicators.technical import TechnicalIndicators
from ..portfolio.positions import Position, PositionManager
from ..portfolio.tracker import TradeTracker
from ..portfolio.risk import RiskManager
from ..strategy.signals import ExitReason, ExitSignal, SignalGenerator
from ..strategy.screener import (
    ScreeningPipelineResult,
    ScreeningResult,
    StockScreener,
)
from ..strategy.ranking import RankedStock, StockRanker

console = Console()


class QuantManager:
    """Automated trading orchestrator.

    Modes:
        pre_market  - Full pipeline: data refresh, screening, recommendations
        monitor     - Position monitoring: stop checks, VIX, exit signals
        post_market - End of day: update stops, record P&L, daily report
        full_cycle  - Run all three in sequence
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        position_manager: Optional[PositionManager] = None,
        trade_tracker: Optional[TradeTracker] = None,
        risk_manager: Optional[RiskManager] = None,
        signal_generator: Optional[SignalGenerator] = None,
        screener: Optional[StockScreener] = None,
        ranker: Optional[StockRanker] = None,
        price_provider: Optional[PriceProvider] = None,
        vix_provider: Optional[VixProvider] = None,
        broker: Optional[Broker] = None,
    ):
        self.settings = settings or get_settings()
        self.price_provider = price_provider or PriceProvider()
        self.vix_provider = vix_provider or VixProvider()
        self.position_manager = position_manager or PositionManager(self.settings)
        self.trade_tracker = trade_tracker or TradeTracker(self.settings)
        self.signal_generator = signal_generator or SignalGenerator(
            self.settings, self.price_provider, self.vix_provider
        )
        self.risk_manager = risk_manager or RiskManager(
            self.settings,
            self.position_manager,
            self.signal_generator,
            self.price_provider,
            self.vix_provider,
        )
        self.technical = TechnicalIndicators(self.price_provider)
        self.screener = screener or StockScreener(settings=self.settings)
        self.ranker = ranker or StockRanker(self.settings)

        # Broker setup
        self.broker = broker
        if self.broker is None and self.settings.broker_enabled:
            try:
                from ..broker.alpaca import AlpacaBroker

                self.broker = AlpacaBroker(paper=self.settings.broker_paper)
                mode = "paper" if self.settings.broker_paper else "LIVE"
                console.print(f"[bold]Broker: Alpaca ({mode})[/bold]")
            except Exception as e:
                console.print(f"[bold red]Broker init failed: {e}[/bold red]")
                raise RuntimeError(
                    f"Broker is required but failed to initialize: {e}"
                ) from e

        self.log: List[Dict] = []
        self.report: Dict = {}

    def _log(self, action: str, detail: str, data: Optional[Dict] = None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "detail": detail,
        }
        if data:
            entry["data"] = data
        self.log.append(entry)

    # -------------------------------------------------------------------------
    # DataAgent
    # -------------------------------------------------------------------------

    def refresh_data(self) -> Dict:
        """Fetch fresh insider filings and VIX data."""
        self._log("data", "Starting data refresh")
        status = {"insider": None, "vix": None, "price_api": None}

        # Insider data
        try:
            from ..sec.scanners.insider import InsiderScanner

            scanner = InsiderScanner()
            with console.status("Fetching insider filings..."):
                transactions = scanner.scan(days_back=7, max_filings=200, verbose=False)
            purchases = [t for t in transactions if t.is_purchase]
            status["insider"] = {
                "total": len(transactions),
                "purchases": len(purchases),
                "healthy": True,
            }
            self._log("data", f"Fetched {len(transactions)} insider txns ({len(purchases)} purchases)")
        except Exception as e:
            status["insider"] = {"error": str(e), "healthy": False}
            self._log("data", f"Insider fetch failed: {e}")

        # VIX
        try:
            vix_level = self.vix_provider.get_current()
            vix_pct = self.vix_provider.get_percentile()
            status["vix"] = {
                "level": vix_level,
                "percentile": vix_pct,
                "healthy": vix_level is not None,
            }
            self._log("data", f"VIX: {vix_level}, percentile: {vix_pct}")
        except Exception as e:
            status["vix"] = {"error": str(e), "healthy": False}
            self._log("data", f"VIX fetch failed: {e}")

        # Price API health check
        try:
            spy_price = self.price_provider.get_latest_price("SPY")
            status["price_api"] = {"spy": spy_price, "healthy": spy_price is not None}
        except Exception as e:
            status["price_api"] = {"error": str(e), "healthy": False}

        self.report["data_health"] = status
        return status

    def check_data_health(self) -> Dict:
        """Quick health check without fetching new data."""
        issues = []

        # Check insider cache
        cache_dir = Path(self.settings.data_dir) / "sec"
        if cache_dir.exists():
            cache_files = list(cache_dir.glob("*.json"))
            if cache_files:
                newest = max(cache_files, key=lambda f: f.stat().st_mtime)
                age_hours = (datetime.now().timestamp() - newest.stat().st_mtime) / 3600
                if age_hours > 24:
                    issues.append(f"Insider cache is {age_hours:.0f}h old")
            else:
                issues.append("No insider cache files found")
        else:
            issues.append("No insider cache directory")

        # Check VIX
        try:
            vix = self.vix_provider.get_current()
            if vix is None:
                issues.append("VIX data unavailable")
        except Exception:
            issues.append("VIX provider error")

        # Check price API
        try:
            spy = self.price_provider.get_latest_price("SPY")
            if spy is None:
                issues.append("Price API not responding")
        except Exception:
            issues.append("Price API error")

        result = {"healthy": len(issues) == 0, "issues": issues}
        self.report["data_health"] = result
        return result

    # -------------------------------------------------------------------------
    # AnalysisAgent
    # -------------------------------------------------------------------------

    def run_screening(self) -> ScreeningPipelineResult:
        """Run the full screening pipeline."""
        self._log("analysis", "Starting screening pipeline")

        with console.status("Running screening pipeline..."):
            result = self.screener.run_pipeline()

        self._log("analysis", "Pipeline complete", {
            "universe": result.universe_size,
            "stage_1": result.stage_1_passed,
            "stage_2": result.stage_2_passed,
            "stage_3": result.stage_3_passed,
            "stage_4": result.stage_4_passed,
            "stage_5": result.stage_5_passed,
            "final": len(result.final_candidates),
        })

        self.report["screening"] = {
            "universe": result.universe_size,
            "stage_1": result.stage_1_passed,
            "stage_2": result.stage_2_passed,
            "stage_3": result.stage_3_passed,
            "stage_4": result.stage_4_passed,
            "stage_5": result.stage_5_passed,
            "final": len(result.final_candidates),
        }

        return result

    def rank_candidates(self, pipeline_result: ScreeningPipelineResult) -> List[RankedStock]:
        """Rank screening candidates by composite score."""
        candidates = pipeline_result.final_candidates
        if not candidates:
            self._log("analysis", "No candidates to rank")
            return []

        ranked = self.ranker.get_top_picks(candidates, self.settings.daily_picks)

        self._log("analysis", f"Ranked {len(ranked)} candidates", {
            "picks": [
                {"ticker": r.ticker, "score": round(r.composite_score, 1)}
                for r in ranked
            ]
        })

        self.report["candidates"] = [
            self.ranker.format_pick_summary(r) for r in ranked
        ]

        return ranked

    # -------------------------------------------------------------------------
    # RiskAgent
    # -------------------------------------------------------------------------

    def assess_portfolio_risk(self) -> Dict:
        """Full portfolio risk assessment."""
        self._log("risk", "Assessing portfolio risk")

        positions = self.position_manager.get_all_positions()
        if not positions:
            summary = {
                "position_count": 0,
                "total_value": 0,
                "daily_pnl": 0,
                "exits_needed": 0,
                "vix": self.risk_manager.check_vix_risk(),
            }
            self.report["portfolio"] = summary
            return summary

        # Fetch current prices and update highs/stops
        current_prices = {}
        for pos in positions:
            price = self.price_provider.get_latest_price(pos.ticker)
            if price is not None:
                current_prices[pos.ticker] = price
                self.position_manager.update_high(pos.ticker, price)

        # Update trailing stops
        stop_updates = self.risk_manager.update_stops()
        if stop_updates:
            self._log("risk", f"Updated stops for {len(stop_updates)} positions", stop_updates)

        # Portfolio summary
        port_summary = self.position_manager.get_portfolio_summary(current_prices)

        # VIX risk
        vix_risk = self.risk_manager.check_vix_risk()

        # Portfolio drawdown
        portfolio_value = self.settings.manager_portfolio_value
        cash = portfolio_value - port_summary["total_cost"]
        drawdown = self.risk_manager.calculate_portfolio_drawdown(
            portfolio_value, current_prices, max(0, cash)
        )

        # Exit signals
        all_exits = self.risk_manager.check_all_exits()
        immediate_count = sum(
            1 for _, signals in all_exits if any(s.urgency == "immediate" for s in signals)
        )

        # Broker position sync (detect drift)
        broker_drift = []
        if self.broker:
            try:
                broker_positions = {
                    p.ticker: p for p in self.broker.get_positions()
                }
                local_tickers = {pos.ticker for pos in positions}
                broker_tickers = set(broker_positions.keys())

                for t in local_tickers - broker_tickers:
                    broker_drift.append(f"{t}: local only (not in broker)")
                for t in broker_tickers - local_tickers:
                    broker_drift.append(f"{t}: broker only (not tracked locally)")

                if broker_drift:
                    self._log("risk", "Position drift detected", {"drift": broker_drift})
                    for msg in broker_drift:
                        console.print(f"  [yellow]DRIFT: {msg}[/yellow]")
            except Exception as e:
                self._log("risk", f"Broker position sync failed: {e}")

        summary = {
            "position_count": port_summary["position_count"],
            "total_cost": port_summary["total_cost"],
            "total_value": port_summary["total_value"],
            "total_pnl_dollars": port_summary["total_pnl_dollars"],
            "total_pnl_pct": port_summary["total_pnl_pct"],
            "drawdown": drawdown,
            "vix": vix_risk,
            "immediate_exits": immediate_count,
            "positions": port_summary["positions"],
            "broker_drift": broker_drift,
        }

        self.report["portfolio"] = summary
        self._log("risk", f"Portfolio: {port_summary['position_count']} positions, "
                  f"P&L: ${port_summary['total_pnl_dollars']:+,.0f}")
        return summary

    def get_exit_signals(self) -> List[Tuple[Position, List[ExitSignal]]]:
        """Get all exit signals grouped by urgency."""
        all_exits = self.risk_manager.check_all_exits()

        if all_exits:
            self._log("risk", f"Exit signals for {len(all_exits)} positions", {
                "tickers": [pos.ticker for pos, _ in all_exits]
            })

        return all_exits

    def get_regime(self) -> Dict:
        """Determine market regime from VIX level."""
        vix_level = self.vix_provider.get_current()

        if vix_level is None:
            regime = {
                "name": "unknown",
                "vix": None,
                "sizing_multiplier": 0.5,
                "entries_allowed": True,
            }
        elif vix_level < 15:
            regime = {
                "name": "low_vol",
                "vix": vix_level,
                "sizing_multiplier": 1.0,
                "entries_allowed": True,
            }
        elif vix_level <= 25:
            regime = {
                "name": "normal",
                "vix": vix_level,
                "sizing_multiplier": 1.0,
                "entries_allowed": True,
            }
        elif vix_level <= 35:
            regime = {
                "name": "elevated",
                "vix": vix_level,
                "sizing_multiplier": 0.5,
                "entries_allowed": True,
            }
        else:
            regime = {
                "name": "crisis",
                "vix": vix_level,
                "sizing_multiplier": 0.0,
                "entries_allowed": False,
            }

        self.report["regime"] = regime
        self._log("risk", f"Regime: {regime['name']} (VIX: {vix_level})")
        return regime

    # -------------------------------------------------------------------------
    # ExecutionAgent
    # -------------------------------------------------------------------------

    def _get_sector_map(self) -> Dict[str, str]:
        """Build ticker-to-sector map for current positions."""
        sector_map = {}
        for pos in self.position_manager.get_all_positions():
            sector_map[pos.ticker] = get_ticker_sector(pos.ticker)
        return sector_map

    def _calculate_position_size_pct(
        self, ticker: str, regime: Dict
    ) -> float:
        """Calculate position size as a fraction of portfolio value.

        Applies regime multiplier, volatility adjustment, and
        max_single_position_pct cap.
        """
        base_pct = self.settings.position_size_pct * regime["sizing_multiplier"]

        # Volatility-adjusted sizing
        if self.settings.vol_sizing_enabled:
            atr_pct = self.technical.get_atr_percent(ticker)
            if atr_pct is not None:
                if atr_pct < self.settings.vol_low_threshold:
                    base_pct = self.settings.vol_low_position_pct
                elif atr_pct < self.settings.vol_med_threshold:
                    base_pct = self.settings.vol_med_position_pct
                else:
                    base_pct = self.settings.vol_high_position_pct
                # Still apply regime multiplier on top
                base_pct *= regime["sizing_multiplier"]

        # Cap at max single position size
        return min(base_pct, self.settings.max_single_position_pct)

    def generate_buy_list(
        self, ranked: List[RankedStock], regime: Dict
    ) -> List[Dict]:
        """Generate list of potential buys from ranked candidates."""
        if not regime["entries_allowed"]:
            self._log("execution", "Entries blocked by regime")
            return []

        portfolio_value = self.settings.manager_portfolio_value
        buy_list = []
        entries_today = 0

        # Check cooling-off periods (recent stop-loss exits)
        recent_stops = set()
        cutoff = datetime.now() - timedelta(days=self.settings.cooling_off_days)
        for trade in self.trade_tracker.get_all_trades():
            if trade.exit_reason in ("stop_loss", "trailing_stop"):
                exit_dt = datetime.fromisoformat(trade.exit_date)
                if exit_dt > cutoff:
                    recent_stops.add(trade.ticker)

        # Build sector map for concentration checks
        sector_map = self._get_sector_map()
        sectors_over_limit = set(
            self.risk_manager.check_sector_limits(sector_map)
        )

        for pick in ranked:
            if entries_today >= self.settings.manager_max_daily_entries:
                break

            ticker = pick.ticker

            # Already in portfolio
            if self.position_manager.has_position(ticker):
                self._log("execution", f"Skip {ticker}: already in portfolio")
                continue

            # Position capacity
            if not self.position_manager.can_add_position(portfolio_value):
                self._log("execution", f"Skip {ticker}: no position capacity")
                break

            # Minimum score
            if pick.composite_score < self.settings.manager_min_score_entry:
                self._log("execution", f"Skip {ticker}: score {pick.composite_score:.1f} < {self.settings.manager_min_score_entry}")
                continue

            # Insider requirement
            if self.settings.manager_require_insider and pick.screening_result.insider_buyers < 1:
                self._log("execution", f"Skip {ticker}: no insider activity")
                continue

            # Cooling-off
            if ticker in recent_stops:
                self._log("execution", f"Skip {ticker}: cooling-off period after stop-loss")
                continue

            # Sector concentration check
            ticker_sector = get_ticker_sector(ticker)
            if ticker_sector in sectors_over_limit:
                self._log("execution", f"Skip {ticker}: sector {ticker_sector} over limit")
                continue

            # Get price and calculate sizing
            price = self.price_provider.get_latest_price(ticker)
            if price is None:
                continue

            size_pct = self._calculate_position_size_pct(ticker, regime)
            target_value = portfolio_value * size_pct
            shares = int(target_value / price)
            if shares < 1:
                continue

            stop_price = price * (1 - self.settings.initial_stop_pct)

            # Build reasons list
            summary = self.ranker.format_pick_summary(pick)
            reasons = summary.get("reasons", [])

            buy_list.append({
                "ticker": ticker,
                "price": price,
                "shares": shares,
                "cost": round(price * shares, 2),
                "stop": round(stop_price, 2),
                "score": round(pick.composite_score, 1),
                "size_pct": round(size_pct * 100, 1),
                "reasons": reasons,
            })
            entries_today += 1

            # Track the new sector for subsequent iterations
            sector_map[ticker] = ticker_sector

        self.report["entries"] = buy_list
        self._log("execution", f"Generated {len(buy_list)} buy candidates")
        return buy_list

    def generate_sell_list(
        self, exit_signals: List[Tuple[Position, List[ExitSignal]]]
    ) -> List[Dict]:
        """Generate list of positions to sell."""
        sell_list = []

        for position, signals in exit_signals:
            # Use highest-urgency signal
            signals_sorted = sorted(
                signals,
                key=lambda s: {"immediate": 0, "end_of_day": 1, "next_session": 2}.get(s.urgency, 3),
            )
            primary_signal = signals_sorted[0]

            price = primary_signal.current_price
            pnl = position.calculate_pnl(price)

            sell_list.append({
                "ticker": position.ticker,
                "price": price,
                "shares": position.shares,
                "entry_price": position.entry_price,
                "pnl_dollars": round(pnl["pnl_dollars"], 2),
                "pnl_pct": round(pnl["pnl_pct"] * 100, 1),
                "reason": primary_signal.reason.value,
                "urgency": primary_signal.urgency,
                "all_reasons": [s.reason.value for s in signals],
            })

        self.report["exits"] = sell_list
        self._log("execution", f"Generated {len(sell_list)} sell signals")
        return sell_list

    def execute_entries(
        self, buy_list: List[Dict], dry_run: bool = False, auto_confirm: bool = False
    ) -> List[Dict]:
        """Execute or prompt for entry confirmation."""
        executed = []

        if not buy_list:
            return executed

        if dry_run:
            console.print("\n[bold yellow]DRY RUN - No entries will be executed[/bold yellow]")
            for entry in buy_list:
                console.print(
                    f"  [cyan]{entry['ticker']}[/cyan] @ ${entry['price']:.2f} "
                    f"x {entry['shares']} shares (${entry['cost']:,.0f}) "
                    f"| Stop: ${entry['stop']:.2f} | Score: {entry['score']}"
                )
                for reason in entry["reasons"]:
                    console.print(f"    - {reason}")
            return executed

        confirm_all = auto_confirm

        for entry in buy_list:
            console.print(
                f"\n  [bold cyan]{entry['ticker']}[/bold cyan] @ ${entry['price']:.2f} "
                f"x {entry['shares']} shares (${entry['cost']:,.0f})"
            )
            console.print(f"  Stop: ${entry['stop']:.2f} | Score: {entry['score']} | Size: {entry['size_pct']}%")
            for reason in entry["reasons"]:
                console.print(f"    - {reason}")

            if not confirm_all:
                response = console.input("  Execute entry? [y/n/all/skip]: ").strip().lower()
                if response == "skip":
                    self._log("execution", f"Skipped all remaining entries")
                    break
                elif response == "all":
                    confirm_all = True
                elif response != "y":
                    self._log("execution", f"Skipped {entry['ticker']}")
                    continue

            # Execute the entry
            entry_price = entry["price"]
            entry_shares = entry["shares"]

            if self.broker:
                result = self.broker.buy(entry["ticker"], entry_shares, entry_price)
                if result.status == "filled":
                    entry_price = result.filled_price
                    entry_shares = int(result.filled_qty)
                    console.print(
                        f"  [green]Broker filled: {entry_shares} shares "
                        f"@ ${entry_price:.2f}[/green]"
                    )
                else:
                    console.print(
                        f"  [red]Broker order failed: {result.error}[/red]"
                    )
                    self._log(
                        "execution",
                        f"Broker buy failed for {entry['ticker']}: {result.error}",
                    )
                    continue

            pos = self.position_manager.add_position(
                ticker=entry["ticker"],
                entry_price=entry_price,
                shares=entry_shares,
                score=entry["score"],
                reasons=entry["reasons"],
            )
            executed.append(entry)
            self._log("execution", f"Entered {entry['ticker']} @ ${entry_price:.2f} x {entry_shares}")
            console.print(f"  [green]Entered {entry['ticker']}[/green]")

        return executed

    def execute_exits(
        self, sell_list: List[Dict], dry_run: bool = False
    ) -> List[Dict]:
        """Execute exits. Immediate exits auto-fire, others are recommendations."""
        executed = []

        if not sell_list:
            return executed

        for item in sell_list:
            is_immediate = item["urgency"] == "immediate"
            pnl_color = "green" if item["pnl_dollars"] >= 0 else "red"

            if dry_run:
                label = "AUTO-EXIT" if is_immediate else "RECOMMEND"
                console.print(
                    f"  [{pnl_color}][DRY RUN {label}] {item['ticker']}[/{pnl_color}] "
                    f"@ ${item['price']:.2f} | P&L: ${item['pnl_dollars']:+,.0f} "
                    f"({item['pnl_pct']:+.1f}%) | {item['reason']}"
                )
                continue

            if is_immediate:
                # Auto-execute stop-loss and VIX spike exits
                position = self.position_manager.get_position(item["ticker"])
                if position is None:
                    continue

                exit_price = item["price"]

                if self.broker:
                    result = self.broker.sell(
                        item["ticker"], position.shares, exit_price
                    )
                    if result.status == "filled":
                        exit_price = result.filled_price
                        console.print(
                            f"  [green]Broker sell filled @ ${exit_price:.2f}[/green]"
                        )
                    else:
                        console.print(
                            f"  [red]Broker sell failed for {item['ticker']}: "
                            f"{result.error} - keeping position open[/red]"
                        )
                        self._log(
                            "execution",
                            f"Broker sell failed for {item['ticker']}: {result.error}",
                        )
                        continue

                self.trade_tracker.record_trade(
                    ticker=item["ticker"],
                    entry_date=position.entry_datetime,
                    exit_date=datetime.now(),
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    shares=position.shares,
                    exit_reason=item["reason"],
                    score=position.score,
                    reasons=position.reasons,
                )
                self.position_manager.remove_position(item["ticker"])
                executed.append(item)

                console.print(
                    f"  [{pnl_color}]EXIT {item['ticker']}[/{pnl_color}] "
                    f"@ ${exit_price:.2f} | P&L: ${item['pnl_dollars']:+,.0f} "
                    f"({item['pnl_pct']:+.1f}%) | {item['reason']}"
                )
                self._log("execution", f"Auto-exited {item['ticker']}: {item['reason']}")
            else:
                # Non-immediate: print recommendation only
                console.print(
                    f"  [{pnl_color}]RECOMMEND EXIT {item['ticker']}[/{pnl_color}] "
                    f"@ ${item['price']:.2f} | P&L: ${item['pnl_dollars']:+,.0f} "
                    f"({item['pnl_pct']:+.1f}%) | {item['reason']} ({item['urgency']})"
                )

        return executed

    # -------------------------------------------------------------------------
    # ReportAgent
    # -------------------------------------------------------------------------

    def generate_daily_report(self, mode: str) -> Dict:
        """Compile everything from the current run into a structured report."""
        # Performance metrics from trade history
        metrics = self.trade_tracker.calculate_metrics()

        report = {
            "timestamp": datetime.now().isoformat(),
            "mode": mode,
            "data_health": self.report.get("data_health", {}),
            "regime": self.report.get("regime", {}),
            "portfolio": self.report.get("portfolio", {}),
            "exits": self.report.get("exits", []),
            "entries": self.report.get("entries", []),
            "screening": self.report.get("screening", {}),
            "candidates": self.report.get("candidates", []),
            "risk_alerts": self._collect_risk_alerts(),
            "performance": {
                "total_trades": metrics["total_trades"],
                "win_rate": round(metrics["win_rate"] * 100, 1),
                "profit_factor": round(metrics["profit_factor"], 2),
                "avg_pnl_pct": round(metrics["avg_pnl_pct"] * 100, 1),
            },
            "log": self.log,
        }

        return report

    def _collect_risk_alerts(self) -> List[str]:
        """Collect risk alerts from the current state."""
        alerts = []

        regime = self.report.get("regime", {})
        if regime.get("name") == "crisis":
            alerts.append("CRISIS: VIX above 35 - no new entries, exit weak positions")
        elif regime.get("name") == "elevated":
            alerts.append("ELEVATED: VIX 25-35 - reduced position sizes, tighter stops")

        portfolio = self.report.get("portfolio", {})
        drawdown = portfolio.get("drawdown", {})
        if isinstance(drawdown, dict):
            action = drawdown.get("action", "none")
            if action == "exit_all":
                alerts.append("DRAWDOWN: Portfolio drawdown exceeds exit threshold")
            elif action == "pause_entries":
                alerts.append("DRAWDOWN: Portfolio drawdown exceeds pause threshold")
            elif action == "reduce_size":
                alerts.append("DRAWDOWN: Consider reducing position sizes")

        if portfolio.get("immediate_exits", 0) > 0:
            alerts.append(f"EXITS: {portfolio['immediate_exits']} positions need immediate exit")

        self.report["risk_alerts"] = alerts
        return alerts

    def save_report(self, report: Dict) -> Path:
        """Save report to JSON file."""
        report_dir = Path(self.settings.manager_report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = report_dir / f"report_{timestamp}.json"

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, default=str)

        # Also save as latest
        latest = report_dir / "latest.json"
        with open(latest, "w") as f:
            json.dump(report, f, indent=2, default=str)

        self._log("report", f"Saved report to {filepath}")
        return filepath

    def print_summary(self, report: Dict):
        """Print formatted console summary."""
        console.print()
        console.print(Panel.fit(
            f"[bold]STEEX QuantManager Report[/bold]\n"
            f"Mode: {report['mode']} | {datetime.now().strftime('%A, %B %d, %Y %H:%M')}",
            border_style="blue",
        ))

        # Regime
        regime = report.get("regime", {})
        regime_name = regime.get("name", "unknown")
        vix_level = regime.get("vix")
        regime_colors = {
            "low_vol": "green",
            "normal": "white",
            "elevated": "yellow",
            "crisis": "bold red",
            "unknown": "dim",
        }
        color = regime_colors.get(regime_name, "white")
        vix_str = f"{vix_level:.1f}" if vix_level else "N/A"
        console.print(f"\nRegime: [{color}]{regime_name.upper()}[/{color}] (VIX: {vix_str})")

        # Data health
        health = report.get("data_health", {})
        if isinstance(health, dict) and "issues" in health:
            if health["healthy"]:
                console.print("Data: [green]All healthy[/green]")
            else:
                console.print("Data: [yellow]Issues detected[/yellow]")
                for issue in health["issues"]:
                    console.print(f"  - {issue}")

        # Portfolio
        portfolio = report.get("portfolio", {})
        pos_count = portfolio.get("position_count", 0)
        if pos_count > 0:
            console.print(
                f"\nPortfolio: {pos_count} positions | "
                f"Value: ${portfolio.get('total_value', 0):,.0f} | "
                f"P&L: ${portfolio.get('total_pnl_dollars', 0):+,.0f} "
                f"({portfolio.get('total_pnl_pct', 0) * 100:+.1f}%)"
            )

            # Position table
            positions = portfolio.get("positions", [])
            if positions:
                table = Table(box=box.SIMPLE)
                table.add_column("Ticker")
                table.add_column("P&L %", justify="right")
                table.add_column("P&L $", justify="right")
                table.add_column("Days", justify="right")

                for p in sorted(positions, key=lambda x: x.get("pnl_pct", 0), reverse=True):
                    pnl_pct = p.get("pnl_pct", 0)
                    pnl_color = "green" if pnl_pct > 0 else "red"
                    table.add_row(
                        p["ticker"],
                        f"[{pnl_color}]{pnl_pct * 100:+.1f}%[/{pnl_color}]",
                        f"[{pnl_color}]${p.get('pnl_dollars', 0):+,.0f}[/{pnl_color}]",
                        str(p.get("days_held", 0)),
                    )
                console.print(table)
        else:
            console.print("\nPortfolio: No open positions")

        # Exits
        exits = report.get("exits", [])
        if exits:
            console.print(f"\n[bold]Exits ({len(exits)}):[/bold]")
            for ex in exits:
                pnl_color = "green" if ex["pnl_dollars"] >= 0 else "red"
                label = "AUTO" if ex["urgency"] == "immediate" else "REC"
                console.print(
                    f"  [{pnl_color}][{label}] {ex['ticker']}[/{pnl_color}] "
                    f"${ex['pnl_dollars']:+,.0f} ({ex['pnl_pct']:+.1f}%) - {ex['reason']}"
                )

        # Buy candidates
        entries = report.get("entries", [])
        if entries:
            console.print(f"\n[bold]Buy Candidates ({len(entries)}):[/bold]")
            table = Table(box=box.SIMPLE)
            table.add_column("Ticker", style="bold cyan")
            table.add_column("Price", justify="right")
            table.add_column("Shares", justify="right")
            table.add_column("Cost", justify="right")
            table.add_column("Stop", justify="right")
            table.add_column("Score", justify="right")
            table.add_column("Reasons")

            for e in entries:
                table.add_row(
                    e["ticker"],
                    f"${e['price']:.2f}",
                    str(e["shares"]),
                    f"${e['cost']:,.0f}",
                    f"${e['stop']:.2f}",
                    f"{e['score']:.0f}",
                    ", ".join(e["reasons"][:3]),
                )
            console.print(table)

        # Screening funnel
        screening = report.get("screening", {})
        if screening:
            console.print(
                f"\nScreening: {screening.get('universe', 0)} -> "
                f"{screening.get('stage_1', 0)} -> "
                f"{screening.get('stage_2', 0)} -> "
                f"{screening.get('stage_3', 0)} -> "
                f"{screening.get('stage_4', 0)} -> "
                f"{screening.get('stage_5', 0)} -> "
                f"{screening.get('final', 0)} final"
            )

        # Risk alerts
        alerts = report.get("risk_alerts", [])
        if alerts:
            console.print(f"\n[bold red]Risk Alerts:[/bold red]")
            for alert in alerts:
                console.print(f"  - {alert}")

        # Performance
        perf = report.get("performance", {})
        if perf.get("total_trades", 0) > 0:
            console.print(
                f"\nTrack Record: {perf['total_trades']} trades | "
                f"Win Rate: {perf['win_rate']:.0f}% | "
                f"Profit Factor: {perf['profit_factor']:.2f}"
            )

        console.print()

    # -------------------------------------------------------------------------
    # Orchestration
    # -------------------------------------------------------------------------

    def run_pre_market(
        self, dry_run: bool = False, auto_confirm: bool = False, verbose: bool = False
    ) -> Dict:
        """Full pre-market pipeline."""
        self.log = []
        self.report = {}

        console.print(Panel.fit(
            "[bold]Pre-Market Pipeline[/bold]",
            border_style="blue",
        ))

        # 1. Data refresh
        console.print("\n[bold]1. Data Refresh[/bold]")
        data_status = self.refresh_data()
        healthy_count = sum(1 for v in data_status.values() if isinstance(v, dict) and v.get("healthy"))
        console.print(f"   Sources: {healthy_count}/{len(data_status)} healthy")

        # 2. Health check
        console.print("\n[bold]2. Data Health[/bold]")
        health = self.check_data_health()
        if not health["healthy"]:
            for issue in health["issues"]:
                console.print(f"   [yellow]Warning: {issue}[/yellow]")
        else:
            console.print("   [green]All data sources healthy[/green]")

        # 3. Regime
        console.print("\n[bold]3. Market Regime[/bold]")
        regime = self.get_regime()
        regime_colors = {"low_vol": "green", "normal": "white", "elevated": "yellow", "crisis": "bold red"}
        color = regime_colors.get(regime["name"], "white")
        console.print(f"   [{color}]{regime['name'].upper()}[/{color}] (VIX: {regime.get('vix', 'N/A')})")

        # 4. Portfolio risk
        console.print("\n[bold]4. Portfolio Risk[/bold]")
        risk = self.assess_portfolio_risk()
        console.print(f"   Positions: {risk.get('position_count', 0)}/{self.settings.max_positions}")
        if risk.get("total_pnl_dollars"):
            pnl_color = "green" if risk["total_pnl_dollars"] >= 0 else "red"
            console.print(f"   P&L: [{pnl_color}]${risk['total_pnl_dollars']:+,.0f}[/{pnl_color}]")

        # 5. Exit signals
        console.print("\n[bold]5. Exit Signals[/bold]")
        exit_signals = self.get_exit_signals()
        sell_list = self.generate_sell_list(exit_signals)

        if sell_list:
            console.print(f"   {len(sell_list)} exit signals")
            self.execute_exits(sell_list, dry_run=dry_run)
        else:
            console.print("   No exit signals")

        # 6. Screening
        console.print("\n[bold]6. Screening Pipeline[/bold]")
        pipeline = self.run_screening()
        console.print(f"   {pipeline.universe_size} -> {len(pipeline.final_candidates)} candidates")

        # 7. Ranking and buy list
        console.print("\n[bold]7. Buy Candidates[/bold]")
        ranked = self.rank_candidates(pipeline)
        buy_list = self.generate_buy_list(ranked, regime)

        if buy_list:
            self.execute_entries(buy_list, dry_run=dry_run, auto_confirm=auto_confirm)
        else:
            console.print("   No buy candidates")

        # 8. Report
        report = self.generate_daily_report("pre_market")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")

        self.print_summary(report)
        return report

    def run_monitor(self, dry_run: bool = False, verbose: bool = False) -> Dict:
        """Midday position monitoring."""
        self.log = []
        self.report = {}

        console.print(Panel.fit(
            "[bold]Position Monitor[/bold]",
            border_style="yellow",
        ))

        # 1. Health check
        console.print("\n[bold]1. Data Health[/bold]")
        health = self.check_data_health()
        if not health["healthy"]:
            for issue in health["issues"]:
                console.print(f"   [yellow]{issue}[/yellow]")
        else:
            console.print("   [green]All healthy[/green]")

        # 2. Regime
        console.print("\n[bold]2. Market Regime[/bold]")
        regime = self.get_regime()
        console.print(f"   {regime['name'].upper()} (VIX: {regime.get('vix', 'N/A')})")

        # 3. Portfolio risk
        console.print("\n[bold]3. Portfolio Risk[/bold]")
        risk = self.assess_portfolio_risk()
        console.print(f"   Positions: {risk.get('position_count', 0)}")

        # 4. Exit signals
        console.print("\n[bold]4. Exit Signals[/bold]")
        exit_signals = self.get_exit_signals()
        sell_list = self.generate_sell_list(exit_signals)

        if sell_list:
            self.execute_exits(sell_list, dry_run=dry_run)
        else:
            console.print("   No exit signals")

        # 5. Report
        report = self.generate_daily_report("monitor")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")

        self.print_summary(report)
        return report

    def run_post_market(self, dry_run: bool = False, verbose: bool = False) -> Dict:
        """End-of-day wrap-up."""
        self.log = []
        self.report = {}

        console.print(Panel.fit(
            "[bold]Post-Market Wrap-up[/bold]",
            border_style="green",
        ))

        # 1. Portfolio risk (final prices)
        console.print("\n[bold]1. Portfolio Assessment[/bold]")
        risk = self.assess_portfolio_risk()
        console.print(f"   Positions: {risk.get('position_count', 0)}")

        # 2. Regime
        console.print("\n[bold]2. Market Regime[/bold]")
        regime = self.get_regime()
        console.print(f"   {regime['name'].upper()} (VIX: {regime.get('vix', 'N/A')})")

        # 3. Exit signals (end-of-day exits)
        console.print("\n[bold]3. Exit Signals[/bold]")
        exit_signals = self.get_exit_signals()
        sell_list = self.generate_sell_list(exit_signals)

        if sell_list:
            # For post-market, also execute end_of_day urgency exits
            for item in sell_list:
                if item["urgency"] == "end_of_day":
                    item["urgency"] = "immediate"
            self.execute_exits(sell_list, dry_run=dry_run)
        else:
            console.print("   No exit signals")

        # 4. Report
        report = self.generate_daily_report("post_market")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")

        self.print_summary(report)
        return report

    def run_full_cycle(
        self, dry_run: bool = False, auto_confirm: bool = False, verbose: bool = False
    ) -> Dict:
        """Run all three modes in sequence."""
        console.print(Panel.fit(
            "[bold]Full Cycle[/bold]",
            border_style="magenta",
        ))

        self.run_pre_market(dry_run=dry_run, auto_confirm=auto_confirm, verbose=verbose)
        self.run_monitor(dry_run=dry_run, verbose=verbose)
        report = self.run_post_market(dry_run=dry_run, verbose=verbose)

        return report
