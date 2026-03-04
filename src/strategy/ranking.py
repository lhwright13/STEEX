"""Composite scoring and ranking for stock selection."""

from dataclasses import dataclass
from typing import Dict, List, Optional

from config.settings import Settings, get_settings

from .screener import ScreeningResult


@dataclass
class RankedStock:
    """A stock with composite score and ranking."""

    ticker: str
    composite_score: float
    momentum_score: float
    insider_score: float
    volume_score: float
    sentiment_score: float
    fundamental_score: float
    options_score: float
    pysr_score: float
    rank: int
    screening_result: ScreeningResult


class StockRanker:
    """Ranks stocks by composite score."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize ranker with settings.

        Args:
            settings: Configuration settings
        """
        self.settings = settings or get_settings()

    def _extract_scores(
        self,
        results: List[ScreeningResult],
        field: str,
        default: float = 50.0,
        scale: float = 1.0,
    ) -> Dict[str, float]:
        """Extract a score field from screening results.

        Args:
            results: List of screening results
            field: Attribute name on ScreeningResult
            default: Default score when field is None
            scale: Multiplier applied to non-None values

        Returns:
            Dict mapping ticker to score (0-100)
        """
        scores = {}
        for r in results:
            val = getattr(r, field, None)
            scores[r.ticker] = val * scale if val is not None else default
        return scores

    def _calculate_volume_percentile_scores(
        self,
        results: List[ScreeningResult],
    ) -> Dict[str, float]:
        """Calculate volume scores as percentile ranks of volume surge."""
        volume_values = {}
        for r in results:
            if r.volume_surge is not None:
                volume_values[r.ticker] = r.volume_surge

        if not volume_values:
            return {r.ticker: 50 for r in results}

        sorted_items = sorted(volume_values.items(), key=lambda x: x[1])
        n = len(sorted_items)
        scores = {}
        for rank, (ticker, _) in enumerate(sorted_items):
            scores[ticker] = ((rank + 1) / n) * 100

        for r in results:
            if r.ticker not in scores:
                scores[r.ticker] = 50

        return scores

    def calculate_composite_score(
        self,
        momentum_score: float,
        insider_score: float,
        volume_score: float,
        sentiment_score: float,
        fundamental_score: float = 50.0,
        options_score: float = 50.0,
        pysr_score: float = 50.0,
    ) -> float:
        """Calculate weighted composite score.

        Args:
            momentum_score: Momentum component (0-100)
            insider_score: Insider component (0-100)
            volume_score: Volume component (0-100)
            sentiment_score: Sentiment component (0-100)
            fundamental_score: Fundamental component (0-100)
            options_score: Options sentiment component (0-100)
            pysr_score: PySR symbolic regression component (0-100)

        Returns:
            Composite score (0-100)
        """
        return (
            self.settings.weight_momentum * momentum_score
            + self.settings.weight_insider * insider_score
            + self.settings.weight_volume * volume_score
            + self.settings.weight_sentiment * sentiment_score
            + self.settings.weight_fundamental * fundamental_score
            + self.settings.weight_options * options_score
            + self.settings.weight_pysr * pysr_score
        )

    def rank_stocks(
        self,
        results: List[ScreeningResult],
    ) -> List[RankedStock]:
        """Rank stocks by composite score.

        Args:
            results: List of screening results

        Returns:
            List of RankedStock sorted by score (highest first)
        """
        if not results:
            return []

        # Calculate component scores
        momentum_scores = self._extract_scores(results, "momentum_percentile", default=0, scale=100)
        insider_scores = self._extract_scores(results, "insider_score", default=0)
        volume_scores = self._calculate_volume_percentile_scores(results)
        sentiment_scores = self._extract_scores(results, "sentiment_score")
        fundamental_scores = self._extract_scores(results, "fundamental_score")
        options_scores = self._extract_scores(results, "options_score")
        pysr_scores = self._extract_scores(results, "pysr_score")

        # Calculate composite scores
        ranked = []
        for r in results:
            ticker = r.ticker
            m_score = momentum_scores.get(ticker, 0)
            i_score = insider_scores.get(ticker, 0)
            v_score = volume_scores.get(ticker, 50)
            s_score = sentiment_scores.get(ticker, 50)
            f_score = fundamental_scores.get(ticker, 50)
            o_score = options_scores.get(ticker, 50)
            p_score = pysr_scores.get(ticker, 50)

            composite = self.calculate_composite_score(
                m_score, i_score, v_score, s_score, f_score, o_score, p_score
            )

            ranked.append(
                RankedStock(
                    ticker=ticker,
                    composite_score=composite,
                    momentum_score=m_score,
                    insider_score=i_score,
                    volume_score=v_score,
                    sentiment_score=s_score,
                    fundamental_score=f_score,
                    options_score=o_score,
                    pysr_score=p_score,
                    rank=0,  # Will be set after sorting
                    screening_result=r,
                )
            )

        # Sort by composite score (highest first)
        ranked.sort(key=lambda x: x.composite_score, reverse=True)

        # Assign ranks
        for i, stock in enumerate(ranked):
            stock.rank = i + 1

        return ranked

    def get_top_picks(
        self,
        results: List[ScreeningResult],
        n: Optional[int] = None,
    ) -> List[RankedStock]:
        """Get top N stock picks.

        Args:
            results: List of screening results
            n: Number of picks (defaults to settings.daily_picks)

        Returns:
            Top N ranked stocks
        """
        n = n or self.settings.daily_picks
        ranked = self.rank_stocks(results)
        return ranked[:n]

    def format_pick_summary(
        self,
        pick: RankedStock,
    ) -> Dict:
        """Format a pick for output/display.

        Args:
            pick: RankedStock to format

        Returns:
            Dict with formatted pick information
        """
        reasons = []

        # Add reasons based on scores
        if pick.momentum_score >= 70:
            reasons.append("Strong momentum")
        elif pick.momentum_score >= 50:
            reasons.append("Good momentum")

        sr = pick.screening_result
        if sr.insider_buyers >= 3:
            reasons.append(f"Cluster buy ({sr.insider_buyers} insiders)")
        elif sr.insider_buyers >= 1:
            reasons.append("Insider buying")

        if sr.total_insider_value >= 500_000:
            reasons.append(f"High insider value (${sr.total_insider_value:,.0f})")

        if sr.volume_surge and sr.volume_surge > 1.5:
            reasons.append(f"Volume surge ({sr.volume_surge:.1f}x)")

        # Add sentiment-based reasons
        if sr.sentiment_score is not None:
            if sr.sentiment_score >= 65:
                reasons.append(f"Bullish sentiment ({sr.sentiment_score:.0f})")
            elif sr.sentiment_score >= 55:
                reasons.append(f"Positive sentiment ({sr.sentiment_score:.0f})")

        # Add fundamental-based reasons
        if sr.fundamental_score is not None:
            if sr.fundamental_score >= 70:
                reasons.append(f"Strong fundamentals ({sr.fundamental_score:.0f})")
            elif sr.fundamental_score >= 60:
                reasons.append(f"Good fundamentals ({sr.fundamental_score:.0f})")

        if sr.peg_ratio is not None and sr.peg_ratio < 1.0:
            reasons.append(f"Low PEG ({sr.peg_ratio:.2f})")

        if sr.roe is not None and sr.roe > 0.20:
            reasons.append(f"High ROE ({sr.roe:.1%})")

        # Add options-based reasons
        if sr.options_score is not None:
            if sr.options_score >= 70:
                reasons.append(f"Bullish options flow ({sr.options_score:.0f})")

        # Add PySR-based reasons
        if sr.pysr_score is not None and sr.pysr_score >= 70:
            reasons.append(f"PySR alpha signal ({sr.pysr_score:.0f})")

        return {
            "ticker": pick.ticker,
            "rank": pick.rank,
            "score": round(pick.composite_score, 1),
            "momentum_6m": f"{(sr.momentum_6m or 0) * 100:.1f}%",
            "insider_buyers": sr.insider_buyers,
            "insider_value": f"${sr.total_insider_value:,.0f}",
            "sentiment": sr.sentiment_label or "N/A",
            "fundamental_score": sr.fundamental_score,
            "pe_ratio": sr.pe_ratio,
            "options_score": sr.options_score,
            "pysr_score": sr.pysr_score,
            "sector": sr.sector or "unknown",
            "reasons": reasons,
        }
