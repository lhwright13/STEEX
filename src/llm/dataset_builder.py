"""Convert STEEX trading data into JSONL chat format for LFM fine-tuning.

Generates three types of training examples:
1. Screening → Trade Decision: signal data → buy/pass recommendation with reasoning
2. Trade Outcome Analysis: completed trade → post-mortem analysis
3. Market Regime Assessment: VIX + breadth + signals → regime call with action plan
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a quantitative trading analyst for the STEEX systematic trading system. "
    "You analyze stock screening signals, market regime data, and trade outcomes to make "
    "informed trading decisions. You provide structured analysis with clear reasoning, "
    "confidence levels, and risk considerations. Always reference specific signal values "
    "in your analysis."
)


def _format_screening_input(result: Dict[str, Any]) -> str:
    """Format a screening result dict into a natural language prompt."""
    lines = [f"Analyze {result['ticker']} for potential entry:"]
    lines.append("")

    # Momentum
    mom_6m = result.get("momentum_6m")
    mom_1m = result.get("momentum_1m")
    pct = result.get("momentum_percentile")
    lines.append("## Momentum")
    lines.append(f"- 6-month return: {mom_6m:.1%}" if mom_6m is not None else "- 6-month return: N/A")
    lines.append(f"- 1-month return: {mom_1m:.1%}" if mom_1m is not None else "- 1-month return: N/A")
    lines.append(f"- Percentile vs S&P 500: {pct:.0f}th" if pct is not None else "- Percentile: N/A")

    # Technical
    lines.append("")
    lines.append("## Technical")
    lines.append(f"- Above 50-day MA: {'Yes' if result.get('above_ma_50') else 'No'}")
    lines.append(f"- Above 200-day MA: {'Yes' if result.get('above_ma_200') else 'No'}")
    vol = result.get("volume_surge")
    lines.append(f"- Volume surge: {vol:.1%}" if vol is not None else "- Volume surge: N/A")

    # Insider
    lines.append("")
    lines.append("## Insider Activity")
    lines.append(f"- Insider score: {result.get('insider_score', 0):.0f}/100")
    lines.append(f"- Insider buyers: {result.get('insider_buyers', 0)}")
    val = result.get("total_insider_value", 0)
    lines.append(f"- Total insider value: ${val:,.0f}")

    # Sentiment
    lines.append("")
    lines.append("## Sentiment")
    sent = result.get("sentiment_score")
    lines.append(f"- Sentiment score: {sent:.0f}/100" if sent is not None else "- Sentiment score: N/A")
    lines.append(f"- Sentiment label: {result.get('sentiment_label', 'N/A')}")

    # Fundamentals
    lines.append("")
    lines.append("## Fundamentals")
    fs = result.get("fundamental_score")
    lines.append(f"- Fundamental score: {fs:.0f}/100" if fs is not None else "- Fundamental score: N/A")
    pe = result.get("pe_ratio")
    lines.append(f"- P/E ratio: {pe:.1f}" if pe is not None else "- P/E ratio: N/A")
    roe = result.get("roe")
    lines.append(f"- ROE: {roe:.1%}" if roe is not None else "- ROE: N/A")
    de = result.get("debt_to_equity")
    lines.append(f"- Debt/Equity: {de:.2f}" if de is not None else "- Debt/Equity: N/A")

    # Options
    lines.append("")
    lines.append("## Options Flow")
    os_ = result.get("options_score")
    lines.append(f"- Options score: {os_:.0f}/100" if os_ is not None else "- Options score: N/A")
    pcr = result.get("put_call_ratio")
    lines.append(f"- Put/Call ratio: {pcr:.2f}" if pcr is not None else "- Put/Call ratio: N/A")

    # Pipeline stages
    stages = result.get("passed_stages", [])
    failed = result.get("failed_stage")
    lines.append("")
    lines.append(f"## Pipeline: passed {len(stages)} stages")
    if failed:
        lines.append(f"- Failed at: {failed}")

    lines.append("")
    lines.append("Should we enter this position? Provide your analysis and recommendation.")

    return "\n".join(lines)


def _format_screening_output(result: Dict[str, Any], trade: Optional[Dict[str, Any]] = None) -> str:
    """Generate the assistant response for a screening example."""
    ticker = result["ticker"]
    stages = result.get("passed_stages", [])
    failed = result.get("failed_stage")
    lines = []

    # Determine outcome
    if trade:
        profitable = trade["pnl_pct"] > 0
        pnl = trade["pnl_pct"]
        hold = trade["hold_days"]
        exit_reason = trade["exit_reason"]

        lines.append(f"## Recommendation: {'ENTER' if len(stages) >= 4 else 'PASS'}")
        lines.append("")
        lines.append(f"**Actual outcome**: {'Profitable' if profitable else 'Loss'} "
                      f"({pnl:+.1%} over {hold} days, exited via {exit_reason})")
        lines.append("")
    else:
        entered = len(stages) >= 4 and failed is None
        lines.append(f"## Recommendation: {'ENTER' if entered else 'PASS'}")
        lines.append("")

    # Signal analysis
    lines.append("### Signal Breakdown")

    mom_6m = result.get("momentum_6m")
    if mom_6m is not None:
        strength = "strong" if mom_6m > 0.15 else "moderate" if mom_6m > 0.05 else "weak"
        lines.append(f"- **Momentum**: {strength} ({mom_6m:.1%} 6-month). "
                      f"{'Confirms uptrend.' if mom_6m > 0.05 else 'Lacks conviction.'}")

    insider = result.get("insider_score", 0)
    buyers = result.get("insider_buyers", 0)
    if insider > 0:
        lines.append(f"- **Insider**: Score {insider:.0f}/100 with {buyers} buyer(s). "
                      f"{'Strong cluster signal.' if buyers >= 3 else 'Moderate insider interest.'}")

    sent = result.get("sentiment_score")
    if sent is not None:
        bias = "bullish" if sent > 60 else "bearish" if sent < 40 else "neutral"
        lines.append(f"- **Sentiment**: {bias} ({sent:.0f}/100)")

    fs = result.get("fundamental_score")
    if fs is not None:
        quality = "strong" if fs > 70 else "adequate" if fs > 50 else "weak"
        lines.append(f"- **Fundamentals**: {quality} ({fs:.0f}/100)")

    os_ = result.get("options_score")
    if os_ is not None:
        flow = "bullish" if os_ > 60 else "bearish" if os_ < 40 else "neutral"
        lines.append(f"- **Options flow**: {flow} ({os_:.0f}/100)")

    # Risk factors
    lines.append("")
    lines.append("### Risk Factors")
    if result.get("has_earnings_soon"):
        lines.append("- Earnings approaching — elevated binary risk")
    if not result.get("above_ma_200"):
        lines.append("- Below 200-day MA — long-term trend not confirmed")
    if mom_6m is not None and mom_6m < 0:
        lines.append("- Negative 6-month momentum — counter-trend entry")

    if failed:
        lines.append(f"- Failed pipeline stage: {failed}")

    # Confidence
    confidence = min(95, max(20, len(stages) * 15 + (insider / 5)))
    lines.append("")
    lines.append(f"### Confidence: {confidence:.0f}%")

    return "\n".join(lines)


def _format_trade_postmortem_input(trade: Dict[str, Any]) -> str:
    """Format a completed trade into a post-mortem analysis prompt."""
    lines = [f"Analyze this completed trade for {trade['ticker']}:"]
    lines.append("")
    lines.append(f"- Entry: ${trade['entry_price']:.2f} on {trade['entry_date']}")
    lines.append(f"- Exit: ${trade['exit_price']:.2f} on {trade['exit_date']}")
    lines.append(f"- P&L: {trade['pnl_pct']:+.1%} (${trade['pnl_dollars']:+,.2f})")
    lines.append(f"- Hold period: {trade['hold_days']} days")
    lines.append(f"- Exit reason: {trade['exit_reason']}")
    lines.append(f"- Entry score: {trade.get('score', 0):.1f}")
    reasons = trade.get("reasons", [])
    if reasons:
        lines.append(f"- Entry signals: {', '.join(reasons)}")
    lines.append("")
    lines.append("Provide a post-mortem analysis: what worked, what didn't, and lessons learned.")
    return "\n".join(lines)


def _format_trade_postmortem_output(trade: Dict[str, Any]) -> str:
    """Generate post-mortem analysis for a completed trade."""
    pnl = trade["pnl_pct"]
    hold = trade["hold_days"]
    exit_reason = trade["exit_reason"]
    profitable = pnl > 0

    lines = [f"## Post-Mortem: {trade['ticker']}"]
    lines.append("")

    # Outcome
    if profitable:
        if pnl > 0.15:
            lines.append(f"**Strong winner** (+{pnl:.1%}). The entry thesis played out well.")
        elif pnl > 0.05:
            lines.append(f"**Modest gain** (+{pnl:.1%}). Captured partial move.")
        else:
            lines.append(f"**Marginal win** (+{pnl:.1%}). Barely profitable after costs.")
    else:
        if pnl < -0.08:
            lines.append(f"**Significant loss** ({pnl:.1%}). Stop hit — thesis invalidated.")
        else:
            lines.append(f"**Small loss** ({pnl:.1%}). Managed exit limited damage.")

    # Exit analysis
    lines.append("")
    lines.append("### Exit Analysis")
    exit_map = {
        "stop_loss": "Initial stop triggered — price reversed against the entry thesis before any gain.",
        "trailing_stop": "Trailing stop captured profits after an initial move higher. Good risk management.",
        "max_hold_time": f"Hit maximum hold period ({hold} days). Position didn't develop enough momentum.",
        "vix_spike": "Exited due to VIX spike — market-wide risk event. Correct defensive action.",
        "manual": "Manual exit — discretionary decision overrode systematic rules.",
    }
    lines.append(exit_map.get(exit_reason, f"Exit via {exit_reason}."))

    # Score accuracy
    score = trade.get("score", 0)
    lines.append("")
    lines.append("### Score Accuracy")
    if profitable and score > 70:
        lines.append(f"Entry score ({score:.0f}) correctly predicted a winning trade.")
    elif not profitable and score > 70:
        lines.append(f"Entry score ({score:.0f}) was overconfident — signal quality didn't match outcome.")
    elif profitable and score < 50:
        lines.append(f"Entry score ({score:.0f}) underestimated the opportunity.")
    else:
        lines.append(f"Entry score ({score:.0f}) aligned with the {'positive' if profitable else 'negative'} outcome.")

    # Lessons
    lines.append("")
    lines.append("### Lessons")
    if exit_reason == "stop_loss" and hold < 5:
        lines.append("- Quick stop suggests bad timing or false breakout. Consider entry refinement.")
    if exit_reason == "max_hold_time" and pnl > 0:
        lines.append("- Profitable at max hold — trailing stop may have been too tight or hold period too short.")
    if exit_reason == "trailing_stop" and pnl > 0.10:
        lines.append("- Trailing stop worked well to lock in gains. Current parameters are effective.")
    if not profitable:
        lines.append("- Review whether entry signals have been degrading (check alpha decay).")

    return "\n".join(lines)


class LLMDatasetBuilder:
    """Converts STEEX data into JSONL chat format for LFM fine-tuning."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or Path(__file__).parent.parent.parent / "data"

    def _make_chat(self, user: str, assistant: str) -> Dict:
        """Create a single chat training example."""
        return {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        }

    def load_trades(self) -> List[Dict]:
        """Load completed trades from trades.json."""
        trades_file = self.data_dir / "trades.json"
        if not trades_file.exists():
            logger.warning(f"No trades file at {trades_file}")
            return []
        with open(trades_file) as f:
            return json.load(f)

    def load_screening_archive(self) -> List[Dict]:
        """Load archived screening results if available."""
        archive = self.data_dir / "screening_archive.json"
        if not archive.exists():
            logger.info("No screening archive found — will generate synthetic examples")
            return []
        with open(archive) as f:
            return json.load(f)

    def build_screening_examples(
        self,
        screening_results: List[Dict],
        trades: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """Build training examples from screening results paired with trade outcomes."""
        trades_by_ticker = {}
        if trades:
            for t in trades:
                trades_by_ticker.setdefault(t["ticker"], []).append(t)

        examples = []
        for result in screening_results:
            ticker = result["ticker"]
            user_msg = _format_screening_input(result)

            # Try to pair with a trade outcome for richer training signal
            trade = None
            ticker_trades = trades_by_ticker.get(ticker, [])
            if ticker_trades:
                trade = ticker_trades[0]

            assistant_msg = _format_screening_output(result, trade)
            examples.append(self._make_chat(user_msg, assistant_msg))

        return examples

    def build_postmortem_examples(self, trades: List[Dict]) -> List[Dict]:
        """Build training examples from completed trades."""
        examples = []
        for trade in trades:
            user_msg = _format_trade_postmortem_input(trade)
            assistant_msg = _format_trade_postmortem_output(trade)
            examples.append(self._make_chat(user_msg, assistant_msg))
        return examples

    def build_regime_examples(self) -> List[Dict]:
        """Build synthetic regime assessment examples from known market scenarios."""
        scenarios = [
            {
                "vix": 14, "vix_pct": 20, "breadth": 72, "yield_spread": 1.2,
                "regime": "risk_on", "action": "Full allocation. All pipeline stages active. Normal position sizing.",
            },
            {
                "vix": 25, "vix_pct": 60, "breadth": 48, "yield_spread": 0.5,
                "regime": "cautious", "action": "Reduce position sizes by 30%. Tighten stops. Raise entry score threshold.",
            },
            {
                "vix": 32, "vix_pct": 80, "breadth": 35, "yield_spread": -0.1,
                "regime": "risk_off", "action": "No new entries. Monitor existing positions for exit. Server-side stops critical.",
            },
            {
                "vix": 45, "vix_pct": 95, "breadth": 22, "yield_spread": -0.8,
                "regime": "crisis", "action": "Immediate exit of all positions via market orders. Preserve capital.",
            },
            {
                "vix": 18, "vix_pct": 35, "breadth": 65, "yield_spread": 0.9,
                "regime": "risk_on", "action": "Favorable conditions. Normal operations with standard risk parameters.",
            },
            {
                "vix": 28, "vix_pct": 70, "breadth": 42, "yield_spread": 0.2,
                "regime": "cautious", "action": "Elevated volatility. Reduce exposure, favor defensive sectors, widen stops.",
            },
        ]

        examples = []
        for s in scenarios:
            user_msg = (
                f"Assess current market regime:\n\n"
                f"- VIX: {s['vix']:.1f} ({s['vix_pct']:.0f}th percentile)\n"
                f"- Market breadth: {s['breadth']:.0f}% above 200-day MA\n"
                f"- Yield curve spread: {s['yield_spread']:+.2f}%\n\n"
                f"What is the current regime and recommended action?"
            )
            assistant_msg = (
                f"## Regime: {s['regime'].upper()}\n\n"
                f"**VIX at {s['vix']:.1f}** ({s['vix_pct']:.0f}th percentile) — "
                f"{'low volatility, complacency risk' if s['vix'] < 15 else 'normal range' if s['vix'] < 20 else 'elevated, caution warranted' if s['vix'] < 30 else 'high stress, defensive posture required' if s['vix'] < 40 else 'extreme fear, capital preservation mode'}.\n\n"
                f"**Breadth at {s['breadth']}%** — "
                f"{'healthy participation' if s['breadth'] > 60 else 'narrowing, fewer stocks participating' if s['breadth'] > 40 else 'poor breadth, rally is thin'}.\n\n"
                f"**Yield spread {s['yield_spread']:+.2f}%** — "
                f"{'normal, no recession signal' if s['yield_spread'] > 0.5 else 'flattening, monitor closely' if s['yield_spread'] > 0 else 'inverted, recession risk elevated'}.\n\n"
                f"### Action\n{s['action']}"
            )
            examples.append(self._make_chat(user_msg, assistant_msg))

        return examples

    def build_full_dataset(self) -> List[Dict]:
        """Build the complete training dataset from all available sources."""
        trades = self.load_trades()
        screening = self.load_screening_archive()

        examples = []

        # Screening examples (with trade outcome pairing)
        if screening:
            examples.extend(self.build_screening_examples(screening, trades))
            logger.info(f"Built {len(screening)} screening examples")

        # Post-mortem examples
        if trades:
            examples.extend(self.build_postmortem_examples(trades))
            logger.info(f"Built {len(trades)} post-mortem examples")

        # Regime examples (always available — synthetic)
        regime_examples = self.build_regime_examples()
        examples.extend(regime_examples)
        logger.info(f"Built {len(regime_examples)} regime examples")

        logger.info(f"Total training examples: {len(examples)}")
        return examples

    def save_jsonl(self, examples: List[Dict], output_path: Path) -> Path:
        """Save training examples as JSONL (the format Unsloth expects)."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")

        logger.info(f"Saved {len(examples)} examples to {output_path}")
        return output_path

    def save_hf_dataset(self, examples: List[Dict], output_dir: Path) -> Path:
        """Save as HF datasets format (arrow) for direct loading in training."""
        try:
            from datasets import Dataset
        except ImportError:
            logger.warning("datasets not installed, falling back to JSONL")
            return self.save_jsonl(examples, output_dir / "train.jsonl")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        ds = Dataset.from_list(examples)
        ds.save_to_disk(str(output_dir))
        logger.info(f"Saved HF dataset ({len(ds)} examples) to {output_dir}")
        return output_dir
