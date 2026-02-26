"""Post-mortem trade analysis.

Analyzes every completed trade to build a knowledge base of what
works, what doesn't, and why. Classifies losses, checks if we
exited too early, and correlates entry scores with actual returns.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from config.settings import Settings, get_settings
from ..data.price import PriceProvider
from ..data.vix import VixProvider
from .tracker import Trade, TradeTracker


@dataclass
class TradeAnalysis:
    """Analysis of a single completed trade."""

    trade: Trade
    score_accuracy: str          # "accurate", "overestimated", "underestimated"
    loss_category: Optional[str]  # "bad_signal", "bad_timing", "bad_regime", "bad_luck"
    regime_at_entry: str
    vix_at_entry: float
    price_after_exit_5d: float   # price 5 trading days after exit
    price_after_exit_20d: float  # price 20 trading days after exit
    missed_upside: float         # how much more the stock went up after exit
    patterns: List[str] = field(default_factory=list)


@dataclass
class PostMortemReport:
    """Aggregated post-mortem analysis."""

    trades_analyzed: int
    analyses: List[TradeAnalysis]
    loss_breakdown: Dict[str, int]       # category -> count
    score_correlation: float             # entry score vs actual return
    avg_missed_upside: float             # average upside missed after exit
    recommendations: List[str]
    patterns: List[Dict] = field(default_factory=list)


class PostMortemAnalyzer:
    """Analyzes completed trades to build a feedback loop.

    Runs in post_market mode after exits but before report generation.
    Classifies each loss, checks for premature exits, and generates
    actionable recommendations.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        trade_tracker: Optional[TradeTracker] = None,
        price_provider: Optional[PriceProvider] = None,
        vix_provider: Optional[VixProvider] = None,
    ):
        self.settings = settings or get_settings()
        self.trade_tracker = trade_tracker or TradeTracker(self.settings)
        self.price_provider = price_provider or PriceProvider()
        self.vix_provider = vix_provider or VixProvider()

    def _get_vix_at_date(self, date: datetime) -> Optional[float]:
        """Get VIX level near a specific date."""
        vix_data = self.vix_provider.fetch(
            start=date - timedelta(days=10),
            end=date + timedelta(days=5),
        )
        if vix_data.empty:
            return None
        try:
            idx = vix_data.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            mask = idx <= date
            if not mask.any():
                return vix_data["Close"].iloc[0]
            return vix_data.loc[idx[mask][-1], "Close"]
        except (KeyError, IndexError):
            return None

    def _get_regime_name(self, vix: Optional[float]) -> str:
        """Classify regime from VIX level."""
        if vix is None:
            return "unknown"
        if vix < 15:
            return "low_vol"
        if vix <= 25:
            return "normal"
        if vix <= 35:
            return "elevated"
        return "crisis"

    def _get_price_after_exit(
        self, ticker: str, exit_date: datetime, days_after: int,
    ) -> Optional[float]:
        """Get price N trading days after exit."""
        calendar_days = int(days_after * 1.5) + 10
        df = self.price_provider.get_ohlcv(
            ticker,
            start=exit_date,
            end=exit_date + timedelta(days=calendar_days),
        )
        if df.empty:
            return None

        idx = df.index
        if idx.tz is not None:
            idx = idx.tz_localize(None)

        # Get the Nth trading day after exit
        future_dates = idx[idx > exit_date]
        if len(future_dates) < days_after:
            if not future_dates.empty:
                return df.loc[future_dates[-1], "Close"]
            return None

        target_date = future_dates[days_after - 1]
        return df.loc[target_date, "Close"]

    def analyze_trade(self, trade: Trade) -> TradeAnalysis:
        """Analyze a single completed trade.

        Fetches price history around entry/exit, classifies outcome,
        and checks for missed upside after exit.
        """
        entry_date = datetime.fromisoformat(trade.entry_date)
        exit_date = datetime.fromisoformat(trade.exit_date)

        # VIX at entry
        vix = self._get_vix_at_date(entry_date)
        regime = self._get_regime_name(vix)

        # Prices after exit
        price_5d = self._get_price_after_exit(trade.ticker, exit_date, 5)
        price_20d = self._get_price_after_exit(trade.ticker, exit_date, 20)

        # Calculate missed upside
        missed_upside = 0.0
        if price_20d is not None and trade.exit_price > 0:
            missed_upside = (price_20d - trade.exit_price) / trade.exit_price

        # Score accuracy
        if trade.pnl_pct > 0.05 and trade.score >= 60:
            accuracy = "accurate"
        elif trade.pnl_pct < -0.05 and trade.score >= 60:
            accuracy = "overestimated"
        elif trade.pnl_pct > 0.05 and trade.score < 60:
            accuracy = "underestimated"
        else:
            accuracy = "accurate"

        # Loss categorization
        loss_cat = None
        if trade.pnl_pct < 0:
            loss_cat = self.categorize_loss(trade, vix, regime, price_5d)

        # Patterns
        patterns = []
        if missed_upside > 0.10:
            patterns.append("premature_exit")
        if trade.hold_days < 5 and trade.pnl_pct < -0.05:
            patterns.append("quick_loss")
        if regime == "elevated" and trade.pnl_pct < 0:
            patterns.append("entered_in_elevated_vix")

        return TradeAnalysis(
            trade=trade,
            score_accuracy=accuracy,
            loss_category=loss_cat,
            regime_at_entry=regime,
            vix_at_entry=vix if vix is not None else 0.0,
            price_after_exit_5d=price_5d if price_5d is not None else 0.0,
            price_after_exit_20d=price_20d if price_20d is not None else 0.0,
            missed_upside=missed_upside,
            patterns=patterns,
        )

    def categorize_loss(
        self,
        trade: Trade,
        vix: Optional[float],
        regime: str,
        price_5d: Optional[float],
    ) -> str:
        """Categorize why a trade lost money.

        Categories:
        - bad_signal: dropped immediately despite high score
        - bad_timing: right stock but entered at local peak
        - bad_regime: broad market selloff
        - bad_luck: idiosyncratic event
        """
        # Bad regime: if VIX was elevated at entry
        if regime in ("elevated", "crisis"):
            return "bad_regime"

        # Bad signal: high score but lost immediately (within 5 days)
        if trade.score >= 65 and trade.hold_days <= 5 and trade.pnl_pct < -0.05:
            return "bad_signal"

        # Bad timing: stock recovered after exit
        if price_5d is not None and trade.exit_price > 0:
            recovery = (price_5d - trade.exit_price) / trade.exit_price
            if recovery > 0.05:
                return "bad_timing"

        # Default to bad_luck for the rest
        return "bad_luck"

    def generate_report(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> PostMortemReport:
        """Analyze all trades in a period and aggregate patterns.

        Args:
            start_date: Analysis period start
            end_date: Analysis period end

        Returns:
            PostMortemReport with aggregated analysis
        """
        if start_date is None:
            start_date = datetime.now() - timedelta(
                days=self.settings.postmortem_lookback_days
            )
        if end_date is None:
            end_date = datetime.now()

        trades = self.trade_tracker.get_trades_in_range(start_date, end_date)

        if not trades:
            return PostMortemReport(
                trades_analyzed=0,
                analyses=[],
                loss_breakdown={},
                score_correlation=0.0,
                avg_missed_upside=0.0,
                recommendations=[],
            )

        analyses = []
        for trade in trades:
            try:
                analysis = self.analyze_trade(trade)
                analyses.append(analysis)
            except Exception:
                continue

        # Aggregate loss breakdown
        loss_breakdown: Dict[str, int] = {}
        for a in analyses:
            if a.loss_category is not None:
                loss_breakdown[a.loss_category] = loss_breakdown.get(a.loss_category, 0) + 1

        # Score-return correlation
        scores = [a.trade.score for a in analyses if a.trade.score > 0]
        returns = [a.trade.pnl_pct for a in analyses if a.trade.score > 0]
        score_corr = 0.0
        if len(scores) > 5:
            try:
                score_corr = pd.Series(scores).corr(pd.Series(returns))
                if pd.isna(score_corr):
                    score_corr = 0.0
            except Exception:
                pass

        # Average missed upside
        missed_upsides = [a.missed_upside for a in analyses if a.missed_upside > 0]
        avg_missed = sum(missed_upsides) / len(missed_upsides) if missed_upsides else 0.0

        # Generate recommendations
        recommendations = self._generate_recommendations(
            analyses, loss_breakdown, score_corr, avg_missed,
        )

        # Aggregate patterns
        pattern_counts: Dict[str, int] = {}
        for a in analyses:
            for p in a.patterns:
                pattern_counts[p] = pattern_counts.get(p, 0) + 1

        patterns = [
            {"pattern": k, "count": v}
            for k, v in sorted(pattern_counts.items(), key=lambda x: -x[1])
        ]

        return PostMortemReport(
            trades_analyzed=len(analyses),
            analyses=analyses,
            loss_breakdown=loss_breakdown,
            score_correlation=score_corr,
            avg_missed_upside=avg_missed,
            recommendations=recommendations,
            patterns=patterns,
        )

    def _generate_recommendations(
        self,
        analyses: List[TradeAnalysis],
        loss_breakdown: Dict[str, int],
        score_corr: float,
        avg_missed: float,
    ) -> List[str]:
        """Generate actionable recommendations from analysis."""
        recs = []

        total_losses = sum(loss_breakdown.values())
        if total_losses == 0:
            recs.append("No losing trades in period - excellent performance")
            return recs

        # Bad signal check
        bad_signal_pct = loss_breakdown.get("bad_signal", 0) / max(total_losses, 1)
        if bad_signal_pct > 0.3:
            recs.append(
                f"High bad_signal rate ({bad_signal_pct:.0%}): "
                f"consider raising manager_min_score_entry"
            )

        # Bad regime check
        bad_regime_pct = loss_breakdown.get("bad_regime", 0) / max(total_losses, 1)
        if bad_regime_pct > 0.3:
            recs.append(
                f"High bad_regime rate ({bad_regime_pct:.0%}): "
                f"consider tightening regime entry rules"
            )

        # Bad timing check
        bad_timing_pct = loss_breakdown.get("bad_timing", 0) / max(total_losses, 1)
        if bad_timing_pct > 0.3:
            recs.append(
                f"High bad_timing rate ({bad_timing_pct:.0%}): "
                f"consider staggered entries or waiting for pullbacks"
            )

        # Score correlation
        if score_corr < 0.1:
            recs.append(
                f"Low score-return correlation ({score_corr:.2f}): "
                f"scoring weights may need recalibration"
            )

        # Premature exits
        if avg_missed > 0.05:
            recs.append(
                f"Average missed upside after exit: {avg_missed:.1%} "
                f"- consider widening trailing stops"
            )

        # Pattern-specific
        premature_exits = sum(
            1 for a in analyses if "premature_exit" in a.patterns
        )
        if premature_exits > len(analyses) * 0.2:
            recs.append(
                f"{premature_exits}/{len(analyses)} trades showed "
                f"significant upside after exit - review exit timing"
            )

        if not recs:
            recs.append("No significant issues detected")

        return recs

    def save_knowledge(self, report: PostMortemReport) -> Path:
        """Persist post-mortem report to data/postmortem/.

        Args:
            report: PostMortemReport to save

        Returns:
            Path to saved file
        """
        output_dir = Path(self.settings.data_dir) / "postmortem"
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = output_dir / f"postmortem_{timestamp}.json"

        data = {
            "timestamp": datetime.now().isoformat(),
            "trades_analyzed": report.trades_analyzed,
            "loss_breakdown": report.loss_breakdown,
            "score_correlation": report.score_correlation,
            "avg_missed_upside": report.avg_missed_upside,
            "recommendations": report.recommendations,
            "patterns": report.patterns,
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        return filepath
