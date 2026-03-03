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
from ..broker.base import AccountInfo, Broker
from ..data.geopolitical import get_ticker_sector
from ..data.prefetch import DataPrefetcher
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
from ..regime.detector import RegimeDetector, MacroRegime

console = Console()


class QuantManager:
    """Automated trading orchestrator.

    Modes:
        screen      - Pre-open: data refresh, screening, ranking (saves results, no entries)
        enter       - Post-open: load screen results, execute entries, place server-side stops
        monitor     - Position monitoring: stop checks, VIX, exit signals
        stop_sync   - Pre-close: update trailing stops, sync server-side stops on Alpaca
        post_market - End of day: final exits, post-mortem, daily report
        learning    - Self-learning loop: signal research, parameter optimization
        pre_market  - Legacy combined: screen + enter in one pass
        full_cycle  - Run pre_market -> monitor -> post_market in sequence
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
        regime_detector: Optional[RegimeDetector] = None,
        portfolio_constructor=None,
        postmortem_analyzer=None,
        execution_quality_tracker=None,
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

        # Multi-factor regime detector
        self.regime_detector = regime_detector
        if self.regime_detector is None and self.settings.regime_multi_factor_enabled:
            self.regime_detector = RegimeDetector(
                settings=self.settings,
                vix_provider=self.vix_provider,
            )

        # Portfolio construction (lazy - imported only when used)
        self.portfolio_constructor = portfolio_constructor
        if self.portfolio_constructor is None:
            try:
                from ..portfolio.construction import PortfolioConstructor
                self.portfolio_constructor = PortfolioConstructor(
                    settings=self.settings,
                    price_provider=self.price_provider,
                    position_manager=self.position_manager,
                )
            except ImportError:
                pass

        # Post-mortem analyzer (lazy)
        self.postmortem_analyzer = postmortem_analyzer
        if self.postmortem_analyzer is None and self.settings.postmortem_enabled:
            try:
                from ..portfolio.postmortem import PostMortemAnalyzer
                self.postmortem_analyzer = PostMortemAnalyzer(
                    settings=self.settings,
                    trade_tracker=self.trade_tracker,
                    price_provider=self.price_provider,
                    vix_provider=self.vix_provider,
                )
            except ImportError:
                pass

        # Execution quality tracker (lazy)
        self.execution_quality_tracker = execution_quality_tracker
        if self.execution_quality_tracker is None and self.settings.execution_quality_enabled:
            try:
                from ..broker.quality import ExecutionQualityTracker
                self.execution_quality_tracker = ExecutionQualityTracker(
                    settings=self.settings,
                    price_provider=self.price_provider,
                )
            except ImportError:
                pass

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

        # Cache for broker account info (refreshed each pipeline run)
        self._account: Optional[AccountInfo] = None

        self.log: List[Dict] = []
        self.report: Dict = {}

    def _sync_broker(self) -> None:
        """Sync positions and account data from broker.

        Detects positions that disappeared from broker (e.g. server-side
        stop filled while the system was offline) and records them as trades.
        """
        if not self.broker:
            return

        # Snapshot local positions before sync to detect removals
        local_before = {
            pos.ticker: pos
            for pos in self.position_manager.get_all_positions()
        }

        # Sync positions: broker is source of truth
        result = self.position_manager.sync_from_broker(self.broker)
        if result["added"] or result["removed"]:
            console.print(f"  Broker sync: +{len(result['added'])} -{len(result['removed'])} positions")
            for t in result["added"]:
                console.print(f"    [green]+{t}[/green] (from broker)")
            for t in result["removed"]:
                console.print(f"    [red]-{t}[/red] (stale local)")

        # Record trades for positions removed by sync (server-side stop fills)
        for ticker in result.get("removed", []):
            pos = local_before.get(ticker)
            if pos is None:
                continue
            # Use the stop price as approximate exit price
            exit_price = pos.current_stop if pos.current_stop else pos.entry_price
            self.trade_tracker.record_trade(
                ticker=ticker,
                entry_date=pos.entry_datetime,
                exit_date=datetime.now(),
                entry_price=pos.entry_price,
                exit_price=exit_price,
                shares=pos.shares,
                exit_reason="server_stop",
                score=pos.score,
                reasons=pos.reasons,
            )
            console.print(
                f"    [yellow]Recorded server-stop exit for {ticker} "
                f"@ ~${exit_price:.2f}[/yellow]"
            )
            self._log("sync", f"Server-stop exit detected for {ticker}")

        # Cache account info
        self._account = self.broker.get_account()

    def _get_portfolio_value(self) -> float:
        """Get current portfolio value from broker, falling back to config."""
        if self._account is not None:
            return self._account.equity
        return self.settings.manager_portfolio_value

    def _get_cash(self) -> float:
        """Get available cash from broker, falling back to estimate."""
        if self._account is not None:
            return self._account.cash
        total_invested = self.position_manager.get_total_cost_basis()
        return self._get_portfolio_value() - total_invested

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
        """Full portfolio risk assessment.

        Uses broker account data for portfolio value and cash.
        Positions are already synced from broker at pipeline start.
        """
        self._log("risk", "Assessing portfolio risk")

        positions = self.position_manager.get_all_positions()
        if not positions:
            summary = {
                "position_count": 0,
                "total_value": 0,
                "daily_pnl": 0,
                "exits_needed": 0,
                "portfolio_equity": self._get_portfolio_value(),
                "cash": self._get_cash(),
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

        # Sync server-side stops with updated trailing levels
        if self.broker and self.settings.server_stops_enabled and stop_updates:
            for ticker, update in stop_updates.items():
                pos = self.position_manager.get_position(ticker)
                if pos is None:
                    continue
                new_server_stop = round(
                    pos.current_stop * (1 - self.settings.server_stop_offset_pct), 2
                )
                self.broker.update_stop_order(ticker, pos.shares, new_server_stop)
                self._log("risk", f"Server stop updated for {ticker} @ ${new_server_stop:.2f}")

        # Portfolio summary
        port_summary = self.position_manager.get_portfolio_summary(current_prices)

        # VIX risk
        vix_risk = self.risk_manager.check_vix_risk()

        # Portfolio drawdown - use broker account data
        portfolio_value = self._get_portfolio_value()
        cash = self._get_cash()
        drawdown = self.risk_manager.calculate_portfolio_drawdown(
            portfolio_value, current_prices, max(0, cash)
        )

        # Exit signals
        all_exits = self.risk_manager.check_all_exits()
        immediate_count = sum(
            1 for _, signals in all_exits if any(s.urgency == "immediate" for s in signals)
        )

        summary = {
            "position_count": port_summary["position_count"],
            "total_cost": port_summary["total_cost"],
            "total_value": port_summary["total_value"],
            "total_pnl_dollars": port_summary["total_pnl_dollars"],
            "total_pnl_pct": port_summary["total_pnl_pct"],
            "portfolio_equity": portfolio_value,
            "cash": cash,
            "drawdown": drawdown,
            "vix": vix_risk,
            "immediate_exits": immediate_count,
            "positions": port_summary["positions"],
        }

        self.report["portfolio"] = summary
        self._log("risk", f"Portfolio: {port_summary['position_count']} positions, "
                  f"Equity: ${portfolio_value:,.0f}, Cash: ${cash:,.0f}, "
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
        """Determine market regime.

        Uses multi-factor detection when regime_multi_factor_enabled is True,
        otherwise falls back to VIX-only classification.
        """
        if self.regime_detector is not None and self.settings.regime_multi_factor_enabled:
            return self._get_multi_factor_regime()
        return self._get_vix_only_regime()

    def _get_multi_factor_regime(self) -> Dict:
        """Multi-factor regime detection via RegimeDetector."""
        macro = self.regime_detector.detect_regime()

        regime = {
            "name": macro.name,
            "vix": macro.vix_level,
            "sizing_multiplier": macro.sizing_multiplier,
            "entries_allowed": macro.entries_allowed,
            "confidence": macro.confidence,
            "yield_spread": macro.yield_spread,
            "yield_curve": macro.yield_curve_status,
            "breadth_score": macro.breadth_score,
            "dollar_trend": macro.dollar_trend,
            "sector_rotation": macro.sector_rotation,
            "factors": macro.factors,
        }

        self.report["regime"] = regime
        self._log(
            "risk",
            f"Regime: {macro.name} (VIX: {macro.vix_level:.1f}, "
            f"composite: {macro.factors.get('composite_risk', 0):.0f}, "
            f"confidence: {macro.confidence:.0%})"
        )
        return regime

    def _get_vix_only_regime(self) -> Dict:
        """Legacy VIX-only regime classification."""
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
        """Generate list of potential buys from ranked candidates.

        Uses broker account data for portfolio value and cash.
        """
        if not regime["entries_allowed"]:
            self._log("execution", "Entries blocked by regime")
            return []

        portfolio_value = self._get_portfolio_value()
        cash = self._get_cash()
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

            # Position capacity (use real cash from broker)
            if not self.position_manager.can_add_position(portfolio_value, cash):
                self._log("execution", f"Skip {ticker}: no position capacity (cash: ${cash:,.0f})")
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

            cost = round(price * shares, 2)
            buy_list.append({
                "ticker": ticker,
                "price": price,
                "shares": shares,
                "cost": cost,
                "stop": round(stop_price, 2),
                "score": round(pick.composite_score, 1),
                "size_pct": round(size_pct * 100, 1),
                "reasons": reasons,
            })
            entries_today += 1
            cash -= cost  # Deduct from available cash for subsequent checks

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
                    # Track execution quality
                    if self.execution_quality_tracker is not None:
                        self.execution_quality_tracker.record_execution(
                            ticker=entry["ticker"],
                            side="buy",
                            intended_price=entry_price,
                            filled_price=result.filled_price,
                            order_id=result.order_id,
                        )
                    entry_price = result.filled_price
                    entry_shares = int(result.filled_qty)
                    console.print(
                        f"  [green]Broker filled: {entry_shares} shares "
                        f"@ ${entry_price:.2f}[/green]"
                    )

                    # Place server-side GTC stop as crash-proof safety net
                    if self.settings.server_stops_enabled:
                        server_stop = round(
                            entry["stop"] * (1 - self.settings.server_stop_offset_pct), 2
                        )
                        stop_result = self.broker.place_stop_order(
                            entry["ticker"], entry_shares, server_stop
                        )
                        if stop_result.status == "failed":
                            # Retry once
                            import time as _time
                            _time.sleep(1)
                            stop_result = self.broker.place_stop_order(
                                entry["ticker"], entry_shares, server_stop
                            )
                        if stop_result.status != "failed":
                            console.print(
                                f"  Server stop placed: ${server_stop:.2f}"
                            )
                            self._log("execution", f"Server stop for {entry['ticker']} @ ${server_stop:.2f}")
                        else:
                            console.print(
                                f"  [bold red]CRITICAL: Server stop FAILED for "
                                f"{entry['ticker']} - position is UNPROTECTED. "
                                f"Error: {stop_result.error}[/bold red]"
                            )
                            self._log(
                                "execution",
                                f"CRITICAL: Server stop failed for {entry['ticker']}: "
                                f"{stop_result.error}. Position has no server-side safety net.",
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

                # Cancel server-side stop before managed sell
                if self.broker and self.settings.server_stops_enabled:
                    self.broker.cancel_stop_for_ticker(item["ticker"])

                if self.broker:
                    result = self.broker.sell(
                        item["ticker"], position.shares, exit_price
                    )
                    if result.status == "filled":
                        # Track execution quality
                        if self.execution_quality_tracker is not None:
                            self.execution_quality_tracker.record_execution(
                                ticker=item["ticker"],
                                side="sell",
                                intended_price=exit_price,
                                filled_price=result.filled_price,
                                order_id=result.order_id,
                            )
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
            "risk_on": "green",
            "cautious": "yellow",
            "risk_off": "bold yellow",
            "crisis": "bold red",
            "unknown": "dim",
        }
        color = regime_colors.get(regime_name, "white")
        vix_str = f"{vix_level:.1f}" if vix_level else "N/A"
        regime_line = f"\nRegime: [{color}]{regime_name.upper()}[/{color}] (VIX: {vix_str})"
        # Add multi-factor details if available
        if regime.get("yield_curve"):
            regime_line += f" | Yield: {regime['yield_curve']}"
        if regime.get("sector_rotation"):
            regime_line += f" | Rotation: {regime['sector_rotation']}"
        if regime.get("confidence"):
            regime_line += f" | Conf: {regime['confidence']:.0%}"
        console.print(regime_line)

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
        equity = portfolio.get("portfolio_equity")
        cash = portfolio.get("cash")
        if equity is not None:
            console.print(f"\nAccount: Equity ${equity:,.0f} | Cash ${cash:,.0f}")
        if pos_count > 0:
            console.print(
                f"Portfolio: {pos_count} positions | "
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

        # 0. Sync positions from broker (source of truth)
        console.print("\n[bold]0. Broker Sync[/bold]")
        self._sync_broker()
        pv = self._get_portfolio_value()
        cash = self._get_cash()
        console.print(f"   Equity: ${pv:,.0f} | Cash: ${cash:,.0f} | "
                      f"Positions: {self.position_manager.get_position_count()}")

        # 0.5 Prefetch data into cache (runs concurrently, warms cache)
        if self.settings.prefetch_enabled:
            console.print("\n[bold]0.5 Data Prefetch[/bold]")
            try:
                from ..data.universe import Universe
                universe = Universe()
                tickers = universe.get_sp500()
                prefetcher = DataPrefetcher(
                    settings=self.settings,
                    price_provider=self.price_provider,
                    universe=universe,
                )
                with console.status(f"Prefetching data for {len(tickers)} tickers..."):
                    prefetch_report = prefetcher.prefetch_all(tickers)
                console.print(
                    f"   Prefetched in {prefetch_report.duration_seconds}s: "
                    f"prices={prefetch_report.prices_fetched}, "
                    f"earnings={prefetch_report.earnings_fetched}, "
                    f"sentiment={prefetch_report.sentiment_fetched}, "
                    f"fundamentals={prefetch_report.fundamentals_fetched}"
                )
                if prefetch_report.errors:
                    for err in prefetch_report.errors:
                        console.print(f"   [yellow]{err}[/yellow]")
                self._log("data", "Prefetch complete", {
                    "prices": prefetch_report.prices_fetched,
                    "earnings": prefetch_report.earnings_fetched,
                    "sentiment": prefetch_report.sentiment_fetched,
                    "fundamentals": prefetch_report.fundamentals_fetched,
                    "duration": prefetch_report.duration_seconds,
                    "errors": prefetch_report.errors,
                })
            except Exception as e:
                console.print(f"   [yellow]Prefetch skipped: {e}[/yellow]")
                self._log("data", f"Prefetch error: {e}")

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

        # 7b. Portfolio construction (if available)
        if ranked and self.portfolio_constructor is not None:
            console.print("\n[bold]7b. Portfolio Construction[/bold]")
            try:
                proposal = self.portfolio_constructor.select_portfolio(
                    ranked,
                    max_picks=self.settings.daily_picks,
                    max_correlation=self.settings.portfolio_max_pairwise_corr,
                )
                console.print(
                    f"   Selected {len(proposal.selected)}/{len(ranked)} candidates "
                    f"(diversification: {proposal.diversification_ratio:.2f})"
                )
                for _, reason in proposal.rejected:
                    console.print(f"   [dim]Skipped: {reason}[/dim]")

                # Replace ranked with selected candidates' RankedStocks
                ranked = [c.ranked_stock for c in proposal.selected]

                self.report["portfolio_construction"] = {
                    "selected": len(proposal.selected),
                    "rejected": len(proposal.rejected),
                    "sector_exposure": proposal.sector_exposure,
                    "diversification_ratio": proposal.diversification_ratio,
                }
                self._log("analysis", f"Portfolio construction selected {len(ranked)} stocks")
            except Exception as e:
                console.print(f"   [yellow]Portfolio construction skipped: {e}[/yellow]")
                self._log("analysis", f"Portfolio construction error: {e}")

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

        # 0. Sync positions from broker
        console.print("\n[bold]0. Broker Sync[/bold]")
        self._sync_broker()
        console.print(f"   Positions: {self.position_manager.get_position_count()}")

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

        # 0. Sync positions from broker
        console.print("\n[bold]0. Broker Sync[/bold]")
        self._sync_broker()
        console.print(f"   Positions: {self.position_manager.get_position_count()}")

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

        # 4. Post-Mortem analysis (if enabled)
        if self.postmortem_analyzer is not None and self.settings.postmortem_enabled:
            console.print("\n[bold]4. Post-Mortem Analysis[/bold]")
            try:
                from datetime import timedelta as td
                start = datetime.now() - td(days=self.settings.postmortem_lookback_days)
                pm_report = self.postmortem_analyzer.generate_report(start, datetime.now())
                console.print(f"   Analyzed {pm_report.trades_analyzed} trades")
                if pm_report.loss_breakdown:
                    for cat, count in pm_report.loss_breakdown.items():
                        console.print(f"   Loss category: {cat} ({count})")
                if pm_report.recommendations:
                    for rec in pm_report.recommendations[:3]:
                        console.print(f"   [yellow]Rec: {rec}[/yellow]")
                self.report["postmortem"] = {
                    "trades_analyzed": pm_report.trades_analyzed,
                    "loss_breakdown": pm_report.loss_breakdown,
                    "score_correlation": pm_report.score_correlation,
                    "avg_missed_upside": pm_report.avg_missed_upside,
                    "recommendations": pm_report.recommendations,
                }
                self._log("postmortem", f"Analyzed {pm_report.trades_analyzed} trades")
            except Exception as e:
                console.print(f"   [yellow]Post-mortem skipped: {e}[/yellow]")
                self._log("postmortem", f"Error: {e}")

        # 5. Report
        report = self.generate_daily_report("post_market")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")

        self.print_summary(report)
        return report

    def run_screen(
        self, dry_run: bool = False, verbose: bool = False
    ) -> Dict:
        """Pre-open screening: data refresh, regime, risk, exits, screening.

        Saves screen results to data/screen_results/latest.json for the
        entry phase to pick up later. Does NOT execute entries.
        """
        self.log = []
        self.report = {}

        console.print(Panel.fit(
            "[bold]Pre-Open Screening[/bold]",
            border_style="cyan",
        ))

        # 0. Broker sync
        console.print("\n[bold]0. Broker Sync[/bold]")
        self._sync_broker()
        pv = self._get_portfolio_value()
        cash = self._get_cash()
        console.print(f"   Equity: ${pv:,.0f} | Cash: ${cash:,.0f} | "
                      f"Positions: {self.position_manager.get_position_count()}")

        # 0.5 Prefetch
        if self.settings.prefetch_enabled:
            console.print("\n[bold]0.5 Data Prefetch[/bold]")
            try:
                from ..data.universe import Universe
                universe = Universe()
                tickers = universe.get_sp500()
                prefetcher = DataPrefetcher(
                    settings=self.settings,
                    price_provider=self.price_provider,
                    universe=universe,
                )
                with console.status(f"Prefetching data for {len(tickers)} tickers..."):
                    prefetch_report = prefetcher.prefetch_all(tickers)
                console.print(
                    f"   Prefetched in {prefetch_report.duration_seconds}s: "
                    f"prices={prefetch_report.prices_fetched}, "
                    f"earnings={prefetch_report.earnings_fetched}"
                )
                self._log("data", "Prefetch complete", {
                    "prices": prefetch_report.prices_fetched,
                    "duration": prefetch_report.duration_seconds,
                })
            except Exception as e:
                console.print(f"   [yellow]Prefetch skipped: {e}[/yellow]")
                self._log("data", f"Prefetch error: {e}")

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
        console.print(f"   {regime['name'].upper()} (VIX: {regime.get('vix', 'N/A')})")

        # 4. Portfolio risk + exits
        console.print("\n[bold]4. Portfolio Risk[/bold]")
        risk = self.assess_portfolio_risk()
        console.print(f"   Positions: {risk.get('position_count', 0)}/{self.settings.max_positions}")

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

        # 7. Ranking + portfolio construction
        console.print("\n[bold]7. Ranking[/bold]")
        ranked = self.rank_candidates(pipeline)

        if ranked and self.portfolio_constructor is not None:
            console.print("\n[bold]7b. Portfolio Construction[/bold]")
            try:
                proposal = self.portfolio_constructor.select_portfolio(
                    ranked,
                    max_picks=self.settings.daily_picks,
                    max_correlation=self.settings.portfolio_max_pairwise_corr,
                )
                console.print(
                    f"   Selected {len(proposal.selected)}/{len(ranked)} candidates "
                    f"(diversification: {proposal.diversification_ratio:.2f})"
                )
                ranked = [c.ranked_stock for c in proposal.selected]
                self.report["portfolio_construction"] = {
                    "selected": len(proposal.selected),
                    "rejected": len(proposal.rejected),
                    "sector_exposure": proposal.sector_exposure,
                    "diversification_ratio": proposal.diversification_ratio,
                }
            except Exception as e:
                console.print(f"   [yellow]Portfolio construction skipped: {e}[/yellow]")

        buy_list = self.generate_buy_list(ranked, regime)

        # Save screen results for the enter phase (do NOT execute entries)
        screen_dir = Path(self.settings.data_dir) / "screen_results"
        screen_dir.mkdir(parents=True, exist_ok=True)
        screen_data = {
            "timestamp": datetime.now().isoformat(),
            "regime": regime,
            "buy_list": buy_list,
            "ranked_count": len(ranked),
        }
        screen_path = screen_dir / "latest.json"
        with open(screen_path, "w") as f:
            json.dump(screen_data, f, indent=2, default=str)
        console.print(f"\n   Screen results saved: {screen_path}")
        console.print(f"   {len(buy_list)} buy candidates queued for entry phase")

        # Report
        report = self.generate_daily_report("screen")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")
        self.print_summary(report)
        return report

    def run_enter(
        self, dry_run: bool = False, auto_confirm: bool = False, verbose: bool = False
    ) -> Dict:
        """Post-open entry execution.

        Loads screen results from data/screen_results/latest.json,
        validates freshness, executes entries, and places server-side stops.
        """
        self.log = []
        self.report = {}

        console.print(Panel.fit(
            "[bold]Entry Execution[/bold]",
            border_style="green",
        ))

        # 0. Broker sync
        console.print("\n[bold]0. Broker Sync[/bold]")
        self._sync_broker()
        pv = self._get_portfolio_value()
        cash = self._get_cash()
        console.print(f"   Equity: ${pv:,.0f} | Cash: ${cash:,.0f} | "
                      f"Positions: {self.position_manager.get_position_count()}")

        # 1. Quick risk check + exits
        console.print("\n[bold]1. Quick Risk Check[/bold]")
        risk = self.assess_portfolio_risk()
        exit_signals = self.get_exit_signals()
        sell_list = self.generate_sell_list(exit_signals)
        if sell_list:
            console.print(f"   {len(sell_list)} exit signals")
            self.execute_exits(sell_list, dry_run=dry_run)
        else:
            console.print("   No exit signals")

        # 2. Load screen results
        console.print("\n[bold]2. Load Screen Results[/bold]")
        screen_path = Path(self.settings.data_dir) / "screen_results" / "latest.json"
        if not screen_path.exists():
            console.print("   [red]No screen results found. Run 'screen' mode first.[/red]")
            self._log("execution", "No screen results file found")
            report = self.generate_daily_report("enter")
            self.save_report(report)
            return report

        with open(screen_path) as f:
            screen_data = json.load(f)

        # Validate freshness (< 2 hours old)
        screen_ts = datetime.fromisoformat(screen_data["timestamp"])
        age = datetime.now() - screen_ts
        age_hours = age.total_seconds() / 3600
        if age_hours > 2:
            console.print(
                f"   [yellow]Screen results are {age_hours:.1f}h old (stale). "
                f"Skipping entries.[/yellow]"
            )
            self._log("execution", f"Screen results stale ({age_hours:.1f}h)")
            report = self.generate_daily_report("enter")
            self.save_report(report)
            return report

        buy_list = screen_data.get("buy_list", [])
        console.print(f"   Loaded {len(buy_list)} candidates from {screen_ts.strftime('%H:%M')}")

        # 3. Execute entries
        console.print("\n[bold]3. Execute Entries[/bold]")
        if buy_list:
            self.report["entries"] = buy_list
            self.execute_entries(buy_list, dry_run=dry_run, auto_confirm=auto_confirm)
        else:
            console.print("   No buy candidates")

        # 4. Report
        report = self.generate_daily_report("enter")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")
        self.print_summary(report)
        return report

    def run_stop_sync(self, dry_run: bool = False, verbose: bool = False) -> Dict:
        """Pre-close stop sync.

        Updates trailing stops with latest prices and syncs each position's
        server-side GTC stop on Alpaca.
        """
        self.log = []
        self.report = {}

        console.print(Panel.fit(
            "[bold]Pre-Close Stop Sync[/bold]",
            border_style="yellow",
        ))

        # 0. Broker sync
        console.print("\n[bold]0. Broker Sync[/bold]")
        self._sync_broker()
        console.print(f"   Positions: {self.position_manager.get_position_count()}")

        # 1. Update trailing stops with latest prices
        console.print("\n[bold]1. Update Trailing Stops[/bold]")
        positions = self.position_manager.get_all_positions()
        for pos in positions:
            price = self.price_provider.get_latest_price(pos.ticker)
            if price is not None:
                self.position_manager.update_high(pos.ticker, price)

        stop_updates = self.risk_manager.update_stops()
        if stop_updates:
            self._log("risk", f"Updated stops for {len(stop_updates)} positions", stop_updates)
            for ticker, update in stop_updates.items():
                console.print(f"   {ticker}: stop updated")
        else:
            console.print("   No stop updates needed")

        # 2. Sync server-side stops
        synced = 0
        if self.broker and self.settings.server_stops_enabled:
            console.print("\n[bold]2. Sync Server-Side Stops[/bold]")
            positions = self.position_manager.get_all_positions()
            for pos in positions:
                server_stop = round(
                    pos.current_stop * (1 - self.settings.server_stop_offset_pct), 2
                )

                if dry_run:
                    console.print(
                        f"   [DRY RUN] {pos.ticker}: server stop -> ${server_stop:.2f}"
                    )
                    synced += 1
                    continue

                result = self.broker.update_stop_order(
                    pos.ticker, pos.shares, server_stop
                )
                if result.status != "failed":
                    console.print(
                        f"   {pos.ticker}: server stop synced @ ${server_stop:.2f}"
                    )
                    synced += 1
                else:
                    console.print(
                        f"   [yellow]{pos.ticker}: stop sync failed - {result.error}[/yellow]"
                    )
            console.print(f"   Synced {synced}/{len(positions)} stops")
        else:
            console.print("\n   Server-side stops disabled or no broker")

        self.report["stop_sync"] = {
            "local_updates": len(stop_updates) if stop_updates else 0,
            "server_synced": synced,
        }

        # 3. Report
        report = self.generate_daily_report("stop_sync")
        filepath = self.save_report(report)
        console.print(f"\nReport saved: {filepath}")
        return report

    def run_learning(self, dry_run: bool = False, verbose: bool = False) -> Optional[Dict]:
        """Run the self-learning loop for parameter optimization.

        Chains PostMortem -> AlphaDecay -> SignalResearch -> OOS Validation
        -> ConfigWriter to continuously optimize strategy parameters.
        """
        if not self.settings.learning_enabled:
            console.print("[dim]Learning loop disabled in config[/dim]")
            return None

        console.print("\n[bold]Learning Loop[/bold]")
        try:
            from ..learning.loop import LearningLoop

            loop = LearningLoop(settings=self.settings)
            effective_dry_run = dry_run or self.settings.learning_dry_run

            result = loop.run(dry_run=effective_dry_run)

            if result.get("error"):
                console.print(f"   [yellow]Learning: {result['error']}[/yellow]")
            else:
                phases = result.get("phases_run", [])
                console.print(f"   Phases: {', '.join(phases)}")

                apply_result = result.get("apply")
                if apply_result and apply_result.get("applied"):
                    console.print(
                        f"   [green]Applied {apply_result.get('count', 0)} "
                        f"config changes[/green]"
                    )
                elif apply_result and apply_result.get("dry_run"):
                    console.print("   [yellow]Dry run - no changes applied[/yellow]")

                gaps = result.get("gaps", [])
                if gaps:
                    console.print(f"   [yellow]{len(gaps)} knowledge gaps flagged[/yellow]")

            self.report["learning"] = result
            self._log("learning", f"Learning loop complete: {result.get('phases_run', [])}")
            return result

        except Exception as e:
            console.print(f"   [yellow]Learning loop error: {e}[/yellow]")
            self._log("learning", f"Learning loop error: {e}")
            return None

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
