"""Multi-factor regime detection.

Replaces VIX-only regime classification with a weighted composite
of VIX, yield curve, market breadth, dollar strength, and sector rotation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import yfinance as yf

from config.settings import Settings, get_settings
from ..data.vix import VixProvider
from ..data.macro import (
    DollarStrengthProvider,
    MarketBreadthProvider,
    YieldCurveProvider,
)


@dataclass
class MacroRegime:
    """Multi-factor regime classification."""

    name: str                    # "risk_on", "cautious", "risk_off", "crisis"
    confidence: float            # 0-1 confidence in classification
    vix_level: float
    yield_spread: float
    yield_curve_status: str      # "normal", "flat", "inverted"
    breadth_score: float
    dollar_trend: str
    sector_rotation: str         # "cyclical", "defensive", "mixed"
    sizing_multiplier: float
    entries_allowed: bool
    factors: Dict[str, Any] = field(default_factory=dict)


class RegimeDetector:
    """Multi-factor market regime detector.

    Weighted score: VIX 40%, yield curve 20%, breadth 20%, other 20%.
    Produces a composite regime that is richer than VIX-only classification.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        vix_provider: Optional[VixProvider] = None,
        yield_provider: Optional[YieldCurveProvider] = None,
        breadth_provider: Optional[MarketBreadthProvider] = None,
        dollar_provider: Optional[DollarStrengthProvider] = None,
    ):
        self.settings = settings or get_settings()
        self.vix_provider = vix_provider or VixProvider()
        self.yield_provider = yield_provider or YieldCurveProvider()
        self.breadth_provider = breadth_provider or MarketBreadthProvider()
        self.dollar_provider = dollar_provider or DollarStrengthProvider()

    def _vix_risk_score(self) -> Tuple[float, Optional[float]]:
        """Score VIX from 0 (safe) to 100 (crisis).

        Returns:
            (risk_score, vix_level)
        """
        vix = self.vix_provider.get_current()
        if vix is None:
            return 50.0, None  # Neutral if unavailable

        if vix < 15:
            score = vix / 15 * 20  # 0-20
        elif vix < 25:
            score = 20 + (vix - 15) / 10 * 20  # 20-40
        elif vix < 35:
            score = 40 + (vix - 25) / 10 * 30  # 40-70
        else:
            score = 70 + min((vix - 35) / 15 * 30, 30)  # 70-100

        return score, vix

    def _yield_risk_score(self) -> Tuple[float, Optional[float], str]:
        """Score yield curve from 0 (safe) to 100 (risk).

        Returns:
            (risk_score, spread, status)
        """
        spread = self.yield_provider.get_current_spread()
        status = self.yield_provider.get_yield_curve_status()

        if spread is None:
            return 50.0, None, "unknown"

        # Normal spread (>0.5) is low risk, inverted (<-0.2) is high risk
        if spread > 1.0:
            score = 10.0
        elif spread > 0.5:
            score = 10 + (1.0 - spread) / 0.5 * 15  # 10-25
        elif spread > 0:
            score = 25 + (0.5 - spread) / 0.5 * 25  # 25-50
        elif spread > -0.2:
            score = 50 + abs(spread) / 0.2 * 20  # 50-70
        else:
            score = 70 + min(abs(spread + 0.2) / 0.5 * 30, 30)  # 70-100

        return score, spread, status

    def _breadth_risk_score(self) -> Tuple[float, Optional[float]]:
        """Score breadth from 0 (healthy) to 100 (unhealthy).

        Returns:
            (risk_score, breadth_score)
        """
        breadth = self.breadth_provider.get_breadth_score()
        if breadth is None:
            return 50.0, None

        # Invert: high breadth score (100) = low risk (0)
        risk = 100.0 - breadth
        return risk, breadth

    def _dollar_risk_score(self) -> Tuple[float, str]:
        """Score dollar from 0 (tailwind) to 100 (headwind).

        Returns:
            (risk_score, trend)
        """
        trend = self.dollar_provider.get_dollar_trend()

        scores = {
            "weakening": 25.0,    # Tailwind for equities
            "neutral": 50.0,
            "strengthening": 75.0,  # Headwind for equities
            "unknown": 50.0,
        }
        return scores.get(trend, 50.0), trend

    def get_sector_rotation(self) -> str:
        """Determine sector rotation signal from cyclical vs defensive performance.

        Uses XLY/XLP (consumer discretionary vs staples) and XLK/XLU
        (tech vs utilities) relative performance over 20 days.
        """
        try:
            pairs = [("XLY", "XLP"), ("XLK", "XLU")]
            cyclical_score = 0

            for cyc_ticker, def_ticker in pairs:
                cyc = yf.Ticker(cyc_ticker).history(period="1mo")
                dfs = yf.Ticker(def_ticker).history(period="1mo")

                if cyc.empty or dfs.empty or len(cyc) < 5 or len(dfs) < 5:
                    continue

                cyc_ret = (cyc["Close"].iloc[-1] / cyc["Close"].iloc[0]) - 1
                def_ret = (dfs["Close"].iloc[-1] / dfs["Close"].iloc[0]) - 1

                if cyc_ret > def_ret:
                    cyclical_score += 1
                else:
                    cyclical_score -= 1

            if cyclical_score > 0:
                return "cyclical"
            if cyclical_score < 0:
                return "defensive"
            return "mixed"
        except Exception:
            return "mixed"

    def detect_regime(self) -> MacroRegime:
        """Detect current market regime using multiple factors.

        Weighted composite: VIX 40%, yield 20%, breadth 20%, other 20%.
        """
        vix_weight = self.settings.regime_vix_weight
        yield_weight = self.settings.regime_yield_weight
        breadth_weight = self.settings.regime_breadth_weight
        other_weight = self.settings.regime_other_weight

        vix_score, vix_level = self._vix_risk_score()
        yield_score, spread, curve_status = self._yield_risk_score()
        breadth_risk, breadth_value = self._breadth_risk_score()
        dollar_score, dollar_trend = self._dollar_risk_score()

        # Composite risk score (0 = all clear, 100 = crisis)
        composite = (
            vix_weight * vix_score
            + yield_weight * yield_score
            + breadth_weight * breadth_risk
            + other_weight * dollar_score
        )

        # Classify regime
        risk_off_threshold = self.settings.regime_risk_off_threshold
        crisis_threshold = self.settings.regime_crisis_threshold

        # Aggressive sizing: lever up in benign regimes, stay less defensive in
        # risk_off. Crisis stays frozen (multiplier 0, entries blocked) as the
        # hard safety floor — also the kill-switch the event-trigger path relies on.
        if composite >= crisis_threshold:
            name = "crisis"
            sizing_multiplier = 0.0
            entries_allowed = False
            confidence = min((composite - crisis_threshold) / 15.0 + 0.7, 1.0)
        elif composite >= risk_off_threshold:
            name = "risk_off"
            sizing_multiplier = 0.5
            entries_allowed = True
            confidence = 0.6 + (composite - risk_off_threshold) / (crisis_threshold - risk_off_threshold) * 0.2
        elif composite >= 40:
            name = "cautious"
            sizing_multiplier = 0.75
            entries_allowed = True
            confidence = 0.5 + (composite - 40) / (risk_off_threshold - 40) * 0.2
        else:
            name = "risk_on"
            sizing_multiplier = 1.25
            entries_allowed = True
            confidence = 0.6 + (40 - composite) / 40 * 0.3

        sector_rotation = self.get_sector_rotation()

        return MacroRegime(
            name=name,
            confidence=min(confidence, 1.0),
            vix_level=vix_level if vix_level is not None else 0.0,
            yield_spread=spread if spread is not None else 0.0,
            yield_curve_status=curve_status,
            breadth_score=breadth_value if breadth_value is not None else 50.0,
            dollar_trend=dollar_trend,
            sector_rotation=sector_rotation,
            sizing_multiplier=sizing_multiplier,
            entries_allowed=entries_allowed,
            factors={
                "vix_risk": round(vix_score, 1),
                "yield_risk": round(yield_score, 1),
                "breadth_risk": round(breadth_risk, 1),
                "dollar_risk": round(dollar_score, 1),
                "composite_risk": round(composite, 1),
            },
        )
