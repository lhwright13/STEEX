"""Alpha decay monitoring.

Tracks the health of each scoring signal over time by comparing
rolling hit rates against historical baselines. Alerts when a
signal is degrading so weights can be recalibrated.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config.settings import Settings, get_settings
from ..portfolio.tracker import Trade, TradeTracker


@dataclass
class SignalHealth:
    """Health assessment for a single signal."""

    signal_name: str
    current_hit_rate: float      # rolling window hit rate
    baseline_hit_rate: float     # full-history hit rate
    trend: str                   # "improving", "stable", "degrading"
    rolling_ic: float            # rolling information coefficient proxy
    alert_level: str             # "healthy", "watch", "degrading"


class AlphaDecayMonitor:
    """Monitors signal health over time to detect alpha decay.

    Compares rolling-window hit rates to historical baselines.
    A signal is considered "degrading" when its recent hit rate
    drops below the baseline by more than the degradation threshold.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        trade_tracker: Optional[TradeTracker] = None,
    ):
        self.settings = settings or get_settings()
        self.trade_tracker = trade_tracker or TradeTracker(self.settings)

    def _get_signal_hit_rate(
        self,
        trades: List[Trade],
        signal_name: str,
    ) -> Optional[float]:
        """Compute hit rate proxy for a signal.

        Since we don't have per-signal attribution in trades,
        we use overall win rate as a proxy. The real signal-level
        IC requires the feature matrix from walk-forward.

        For now, we track overall strategy health per time window.
        """
        if not trades:
            return None
        winners = sum(1 for t in trades if t.pnl_pct > 0)
        return winners / len(trades)

    def check_signal_health(self, signal_name: str) -> SignalHealth:
        """Check health of a specific signal.

        Compares rolling window performance to full history.

        Args:
            signal_name: Name of the signal to check

        Returns:
            SignalHealth assessment
        """
        all_trades = self.trade_tracker.get_all_trades()
        window = self.settings.alpha_monitor_window
        threshold = self.settings.alpha_degradation_threshold

        # Baseline: all trades
        baseline_rate = self._get_signal_hit_rate(all_trades, signal_name)
        if baseline_rate is None:
            return SignalHealth(
                signal_name=signal_name,
                current_hit_rate=0.0,
                baseline_hit_rate=0.0,
                trend="stable",
                rolling_ic=0.0,
                alert_level="healthy",
            )

        # Rolling window: last N trades
        recent_trades = all_trades[-window:] if len(all_trades) > window else all_trades
        current_rate = self._get_signal_hit_rate(recent_trades, signal_name) or 0.0

        # Determine trend
        diff = current_rate - baseline_rate

        if diff > 0.05:
            trend = "improving"
        elif diff < -0.05:
            trend = "degrading"
        else:
            trend = "stable"

        # Alert level
        if diff < -threshold:
            alert_level = "degrading"
        elif diff < -(threshold / 2):
            alert_level = "watch"
        else:
            alert_level = "healthy"

        # Rolling IC proxy (win rate as surrogate)
        rolling_ic = current_rate - 0.5  # Center around 0

        return SignalHealth(
            signal_name=signal_name,
            current_hit_rate=current_rate,
            baseline_hit_rate=baseline_rate,
            trend=trend,
            rolling_ic=rolling_ic,
            alert_level=alert_level,
        )

    def generate_report(self) -> Dict:
        """Generate health report for all signals.

        Returns:
            Dict with signal health assessments and recommendations
        """
        signal_names = [
            "momentum_score",
            "insider_score",
            "volume_score",
            "sentiment_score",
            "fundamental_score",
            "options_score",
            "pysr_score",
        ]

        health_results = []
        degrading = []
        watch_list = []

        for name in signal_names:
            health = self.check_signal_health(name)
            health_results.append({
                "signal": health.signal_name,
                "current_hit_rate": round(health.current_hit_rate, 3),
                "baseline_hit_rate": round(health.baseline_hit_rate, 3),
                "trend": health.trend,
                "alert_level": health.alert_level,
            })

            if health.alert_level == "degrading":
                degrading.append(health.signal_name)
            elif health.alert_level == "watch":
                watch_list.append(health.signal_name)

        recommendations = []
        if degrading:
            recommendations.append(
                f"Degrading signals: {', '.join(degrading)} - "
                f"consider reducing weight or running signal research"
            )
        if watch_list:
            recommendations.append(
                f"Watchlist signals: {', '.join(watch_list)} - "
                f"monitor closely for further degradation"
            )
        if not degrading and not watch_list:
            recommendations.append("All signals healthy - no action needed")

        # Overall strategy health
        all_trades = self.trade_tracker.get_all_trades()
        recent_trades = all_trades[-self.settings.alpha_monitor_window:]
        overall_rate = self._get_signal_hit_rate(recent_trades, "overall") or 0.0

        return {
            "timestamp": datetime.now().isoformat(),
            "signals": health_results,
            "degrading": degrading,
            "watch_list": watch_list,
            "recommendations": recommendations,
            "overall_recent_win_rate": round(overall_rate, 3),
            "total_trades": len(all_trades),
            "window_size": self.settings.alpha_monitor_window,
        }
