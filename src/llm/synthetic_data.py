"""Generate synthetic training data for LLM fine-tuning on market analysis tasks.

Produces four categories of training examples:
1. Screening decisions (2000+) - signal analysis with trade outcomes
2. Regime assessments (500+) - VIX/breadth/yield regime classification
3. Trade post-mortems (1000+) - completed trade analysis
4. Position management (500+) - stop/sizing/portfolio decisions

Uses realistic distributions calibrated to STEEX's historical parameters and
the formatters from dataset_builder.py where applicable.
"""

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .dataset_builder import (
    SYSTEM_PROMPT,
    _format_screening_input,
    _format_screening_output,
    _format_trade_postmortem_input,
    _format_trade_postmortem_output,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ticker universe for synthetic generation
# ---------------------------------------------------------------------------
_SP500_SAMPLE = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK.B", "UNH",
    "JNJ", "V", "XOM", "JPM", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "LLY",
    "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
    "DHR", "NEE", "LIN", "TXN", "PM", "UPS", "RTX", "HON", "LOW", "AMGN",
    "IBM", "GS", "CAT", "BA", "SBUX", "BLK", "INTU", "AMD", "DE", "ISRG",
    "MDLZ", "ADP", "GILD", "ADI", "SYK", "MMC", "TJX", "VRTX", "PLD", "CI",
    "REGN", "ZTS", "CB", "SO", "DUK", "BDX", "CL", "CME", "ITW", "MO",
    "FISV", "FIS", "EQIX", "NSC", "SHW", "HUM", "PNC", "USB", "AON", "ICE",
    "APD", "EMR", "GM", "F", "NFLX", "CRM", "NOW", "PYPL", "ADBE", "ORCL",
    "QCOM", "INTC", "MU", "LRCX", "AMAT", "KLAC", "MRVL", "SNPS", "CDNS",
    "PANW", "CRWD", "ZS", "DDOG", "SNOW", "NET", "FTNT", "ABNB", "DASH",
    "COIN", "RIVN", "SOFI", "PLTR", "RBLX", "U", "TTD", "ROKU", "SQ",
    "SHOP", "MELI", "SE", "BABA", "JD", "PDD", "NIO", "XPEV", "LI",
    "ENPH", "FSLR", "SEDG", "RUN", "NOVA", "ARRY", "STEM", "CHPT", "LCID",
    "PLUG", "FCEL", "BE", "BLNK", "VLDR", "QS", "MVST", "DNA", "IONQ",
    "GOOG", "BRK.A", "WFC", "BAC", "C", "AXP", "SCHW", "TFC", "COF", "DFS",
    "SLB", "OXY", "EOG", "COP", "MPC", "VLO", "PSX", "HES", "DVN", "FANG",
]

_SECTORS = [
    "Technology", "Healthcare", "Financials", "Consumer Discretionary",
    "Communication Services", "Industrials", "Consumer Staples", "Energy",
    "Utilities", "Real Estate", "Materials",
]

_SENTIMENT_LABELS = ["Bearish", "Somewhat Bearish", "Neutral", "Somewhat Bullish", "Bullish"]

_PIPELINE_STAGES = ["stage_1", "stage_2", "stage_3", "stage_4", "stage_5"]

_EXIT_REASONS = ["stop_loss", "trailing_stop", "max_hold_time", "vix_spike", "manual"]

_ENTRY_SIGNALS = [
    "Insider cluster buy (3+ buyers)",
    "Strong 6-month momentum",
    "Above 50-day and 200-day MA",
    "Bullish sentiment (75/100)",
    "Volume surge +40%",
    "CEO/CFO purchase >$500k",
    "Positive earnings surprise",
    "Sector rotation into technology",
    "Breakout above resistance",
    "Bullish options flow (P/C 0.6)",
    "High fundamental score (80/100)",
    "Strong ROE (25%+)",
    "Low debt/equity (0.3)",
    "Revenue growth >15%",
    "PySR model bullish",
]


# ---------------------------------------------------------------------------
# Helper: make a chat example dict
# ---------------------------------------------------------------------------
def _make_chat(user: str, assistant: str) -> Dict[str, Any]:
    """Create a single chat training example in JSONL chat format."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ]
    }


# ---------------------------------------------------------------------------
# 1. Screening example generation
# ---------------------------------------------------------------------------

def _generate_screening_result(rng: np.random.Generator, profile: str) -> Dict[str, Any]:
    """Generate a single synthetic ScreeningResult dict.

    Args:
        rng: numpy random generator for reproducibility.
        profile: one of "strong_buy", "marginal_pass", "reject", "edge_case".

    Returns:
        Dict matching the shape consumed by _format_screening_input.
    """
    ticker = rng.choice(_SP500_SAMPLE)

    if profile == "strong_buy":
        mom_6m = rng.uniform(0.15, 0.80)
        mom_1m = rng.uniform(0.02, 0.15)
        pct = rng.uniform(60, 95)
        above_50 = True
        above_200 = True
        insider = rng.uniform(60, 100)
        buyers = int(rng.integers(3, 8))
        insider_val = float(rng.integers(200_000, 5_000_000))
        volume = rng.uniform(0.10, 1.50)
        sentiment = rng.uniform(60, 95)
        fund_score = rng.uniform(60, 95)
        options = rng.uniform(55, 90)
        n_stages = int(rng.choice([4, 5], p=[0.3, 0.7]))
        failed = None
        earnings = False

    elif profile == "marginal_pass":
        mom_6m = rng.uniform(0.05, 0.20)
        mom_1m = rng.uniform(-0.02, 0.05)
        pct = rng.uniform(40, 70)
        above_50 = True
        above_200 = bool(rng.choice([True, False], p=[0.6, 0.4]))
        insider = rng.uniform(20, 65)
        buyers = int(rng.integers(1, 4))
        insider_val = float(rng.integers(50_000, 500_000))
        volume = rng.uniform(-0.10, 0.40)
        sentiment = rng.uniform(45, 65)
        fund_score = rng.uniform(45, 70)
        options = rng.uniform(40, 65)
        n_stages = int(rng.choice([3, 4], p=[0.4, 0.6]))
        failed = "stage_5" if n_stages == 3 else None
        earnings = bool(rng.choice([True, False], p=[0.15, 0.85]))

    elif profile == "reject":
        mom_6m = rng.uniform(-0.30, 0.08)
        mom_1m = rng.uniform(-0.15, 0.02)
        pct = rng.uniform(5, 50)
        above_50 = bool(rng.choice([True, False], p=[0.3, 0.7]))
        above_200 = bool(rng.choice([True, False], p=[0.2, 0.8]))
        insider = rng.uniform(0, 30)
        buyers = int(rng.integers(0, 2))
        insider_val = float(rng.integers(0, 100_000))
        volume = rng.uniform(-0.30, 0.10)
        sentiment = rng.uniform(10, 45)
        fund_score = rng.uniform(15, 50)
        options = rng.uniform(15, 45)
        n_stages = int(rng.choice([0, 1, 2], p=[0.3, 0.4, 0.3]))
        stage_fail_options = [s for s in _PIPELINE_STAGES if _PIPELINE_STAGES.index(s) >= n_stages]
        failed = rng.choice(stage_fail_options) if stage_fail_options else "stage_1"
        earnings = bool(rng.choice([True, False], p=[0.25, 0.75]))

    else:  # edge_case
        edge_type = rng.choice([
            "earnings_blackout", "vix_spike_context", "contradictory_signals",
            "high_momentum_bad_fundamentals", "low_momentum_strong_insider",
            "extreme_volume_no_catalyst",
        ])

        if edge_type == "earnings_blackout":
            mom_6m = rng.uniform(0.10, 0.40)
            mom_1m = rng.uniform(0.02, 0.10)
            pct = rng.uniform(55, 85)
            above_50, above_200 = True, True
            insider = rng.uniform(50, 85)
            buyers = int(rng.integers(2, 5))
            insider_val = float(rng.integers(100_000, 2_000_000))
            volume = rng.uniform(0.20, 0.80)
            sentiment = rng.uniform(55, 80)
            fund_score = rng.uniform(55, 80)
            options = rng.uniform(50, 75)
            n_stages = 3
            failed = "stage_1"
            earnings = True

        elif edge_type == "vix_spike_context":
            mom_6m = rng.uniform(0.05, 0.25)
            mom_1m = rng.uniform(-0.10, -0.02)
            pct = rng.uniform(30, 60)
            above_50 = bool(rng.choice([True, False]))
            above_200 = True
            insider = rng.uniform(40, 80)
            buyers = int(rng.integers(2, 6))
            insider_val = float(rng.integers(200_000, 3_000_000))
            volume = rng.uniform(0.30, 1.50)
            sentiment = rng.uniform(20, 40)
            fund_score = rng.uniform(50, 75)
            options = rng.uniform(20, 40)
            n_stages = int(rng.choice([2, 3]))
            failed = "stage_4" if n_stages == 2 else None
            earnings = False

        elif edge_type == "contradictory_signals":
            mom_6m = rng.uniform(0.15, 0.50)
            mom_1m = rng.uniform(0.03, 0.12)
            pct = rng.uniform(60, 90)
            above_50, above_200 = True, True
            insider = rng.uniform(0, 15)
            buyers = 0
            insider_val = 0.0
            volume = rng.uniform(-0.20, 0.05)
            sentiment = rng.uniform(15, 35)
            fund_score = rng.uniform(20, 40)
            options = rng.uniform(60, 85)
            n_stages = int(rng.choice([3, 4]))
            failed = "stage_4" if n_stages == 3 else None
            earnings = False

        elif edge_type == "high_momentum_bad_fundamentals":
            mom_6m = rng.uniform(0.30, 0.80)
            mom_1m = rng.uniform(0.05, 0.20)
            pct = rng.uniform(80, 98)
            above_50, above_200 = True, True
            insider = rng.uniform(30, 60)
            buyers = int(rng.integers(1, 3))
            insider_val = float(rng.integers(50_000, 300_000))
            volume = rng.uniform(0.10, 0.60)
            sentiment = rng.uniform(55, 75)
            fund_score = rng.uniform(10, 30)
            options = rng.uniform(50, 70)
            n_stages = int(rng.choice([3, 4]))
            failed = "stage_5" if n_stages == 3 else None
            earnings = False

        elif edge_type == "low_momentum_strong_insider":
            mom_6m = rng.uniform(-0.05, 0.08)
            mom_1m = rng.uniform(-0.05, 0.02)
            pct = rng.uniform(20, 45)
            above_50 = bool(rng.choice([True, False]))
            above_200 = False
            insider = rng.uniform(80, 100)
            buyers = int(rng.integers(4, 8))
            insider_val = float(rng.integers(500_000, 10_000_000))
            volume = rng.uniform(0.05, 0.40)
            sentiment = rng.uniform(45, 65)
            fund_score = rng.uniform(55, 80)
            options = rng.uniform(45, 65)
            n_stages = int(rng.choice([1, 2]))
            failed = "stage_2"
            earnings = False

        else:  # extreme_volume_no_catalyst
            mom_6m = rng.uniform(0.02, 0.12)
            mom_1m = rng.uniform(-0.02, 0.05)
            pct = rng.uniform(35, 55)
            above_50 = True
            above_200 = bool(rng.choice([True, False]))
            insider = rng.uniform(0, 20)
            buyers = 0
            insider_val = 0.0
            volume = rng.uniform(1.00, 5.00)
            sentiment = rng.uniform(40, 60)
            fund_score = rng.uniform(40, 60)
            options = rng.uniform(40, 60)
            n_stages = int(rng.choice([2, 3]))
            failed = "stage_3" if n_stages == 2 else "stage_4"
            earnings = False

    # Derive sentiment label from score
    if sentiment < 35:
        sent_label = "Bearish"
    elif sentiment < 45:
        sent_label = "Somewhat Bearish"
    elif sentiment < 55:
        sent_label = "Neutral"
    elif sentiment < 65:
        sent_label = "Somewhat Bullish"
    else:
        sent_label = "Bullish"

    # Generate realistic fundamental fields
    pe = float(rng.uniform(5, 80)) if fund_score > 20 else None
    roe = float(rng.uniform(-0.05, 0.45)) if fund_score > 15 else None
    de = float(rng.uniform(0.0, 3.5)) if fund_score > 15 else None

    # Generate options detail fields
    pcr = float(rng.uniform(0.3, 2.0)) if options > 10 else None

    passed = _PIPELINE_STAGES[:n_stages]

    return {
        "ticker": ticker,
        "momentum_6m": mom_6m,
        "momentum_1m": mom_1m,
        "momentum_percentile": pct,
        "above_ma_50": above_50,
        "above_ma_200": above_200,
        "insider_score": insider,
        "insider_buyers": buyers,
        "total_insider_value": insider_val,
        "volume_surge": volume,
        "has_earnings_soon": earnings,
        "sentiment_score": sentiment,
        "sentiment_label": sent_label,
        "fundamental_score": fund_score,
        "pe_ratio": pe,
        "roe": roe,
        "debt_to_equity": de,
        "options_score": options,
        "put_call_ratio": pcr,
        "passed_stages": passed,
        "failed_stage": failed,
    }


def _generate_trade_outcome(
    rng: np.random.Generator,
    screening: Dict[str, Any],
) -> Dict[str, Any]:
    """Generate a realistic trade outcome paired with a screening result.

    Win probability is correlated with signal quality: more passed stages and
    higher scores yield higher win rates.
    """
    n_stages = len(screening.get("passed_stages", []))
    insider = screening.get("insider_score", 0)
    mom = screening.get("momentum_6m", 0)

    # Win probability scales with signal quality: 40% for weak to 65% for strong
    base_win_prob = 0.40 + 0.05 * n_stages + 0.001 * insider
    base_win_prob = min(0.70, max(0.35, base_win_prob))
    is_win = rng.random() < base_win_prob

    entry_price = float(rng.uniform(15, 500))

    if is_win:
        pnl_pct = float(rng.choice([
            rng.uniform(0.005, 0.03),   # marginal
            rng.uniform(0.03, 0.08),    # modest
            rng.uniform(0.08, 0.20),    # solid
            rng.uniform(0.20, 0.50),    # strong
        ], p=[0.15, 0.35, 0.35, 0.15]))
        exit_reasons_w = ["trailing_stop", "max_hold_time", "manual"]
        exit_probs_w = [0.55, 0.30, 0.15]
    else:
        pnl_pct = float(rng.choice([
            rng.uniform(-0.03, -0.005),   # small
            rng.uniform(-0.06, -0.03),    # moderate
            rng.uniform(-0.10, -0.06),    # significant (near stop)
            rng.uniform(-0.15, -0.10),    # stop hit
        ], p=[0.15, 0.30, 0.35, 0.20]))
        exit_reasons_l = ["stop_loss", "trailing_stop", "max_hold_time", "vix_spike"]
        exit_probs_l = [0.50, 0.10, 0.20, 0.20]

    exit_price = entry_price * (1.0 + pnl_pct)
    shares = max(1, int(round(2000 / entry_price)))
    cost_basis = entry_price * shares
    proceeds = exit_price * shares
    pnl_dollars = proceeds - cost_basis

    hold_days = int(rng.integers(1, 31))
    if not is_win and pnl_pct < -0.08:
        hold_days = int(rng.integers(1, 10))  # stops hit fast

    exit_reason = str(rng.choice(
        exit_reasons_w if is_win else exit_reasons_l,
        p=exit_probs_w if is_win else exit_probs_l,
    ))

    # Entry score: correlated with signal quality but noisy
    base_score = 30.0 + n_stages * 10.0 + insider * 0.15 + mom * 30.0
    score = float(np.clip(base_score + rng.normal(0, 8), 20, 98))

    # Random entry/exit dates within last 18 months
    exit_dt = datetime(2026, 3, 1) - timedelta(days=int(rng.integers(1, 540)))
    entry_dt = exit_dt - timedelta(days=hold_days)

    n_reasons = int(rng.integers(2, 5))
    reasons = list(rng.choice(_ENTRY_SIGNALS, size=n_reasons, replace=False))

    return {
        "ticker": screening["ticker"],
        "entry_date": entry_dt.strftime("%Y-%m-%d"),
        "exit_date": exit_dt.strftime("%Y-%m-%d"),
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "shares": shares,
        "cost_basis": round(cost_basis, 2),
        "proceeds": round(proceeds, 2),
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_pct": round(pnl_pct, 4),
        "hold_days": hold_days,
        "exit_reason": exit_reason,
        "score": round(score, 1),
        "reasons": reasons,
    }


def generate_screening_examples(
    rng: np.random.Generator,
    count: int = 2200,
) -> List[Dict[str, Any]]:
    """Generate screening decision training examples.

    Distribution: ~30% strong buys, ~25% marginal passes, ~30% rejects, ~15% edge cases.
    """
    profiles = rng.choice(
        ["strong_buy", "marginal_pass", "reject", "edge_case"],
        size=count,
        p=[0.30, 0.25, 0.30, 0.15],
    )

    examples = []
    for profile in profiles:
        screening = _generate_screening_result(rng, profile)

        # ~70% of examples include a trade outcome for richer training signal
        include_trade = rng.random() < 0.70
        trade = _generate_trade_outcome(rng, screening) if include_trade else None

        user_msg = _format_screening_input(screening)
        assistant_msg = _format_screening_output(screening, trade)
        examples.append(_make_chat(user_msg, assistant_msg))

    return examples


# ---------------------------------------------------------------------------
# 2. Regime assessment generation
# ---------------------------------------------------------------------------

_REGIME_DEFINITIONS = {
    "risk_on": {
        "vix_range": (9, 20),
        "vix_pct_range": (5, 40),
        "breadth_range": (55, 85),
        "yield_range": (0.5, 2.5),
        "dollar_range": (-3, 2),
    },
    "cautious": {
        "vix_range": (20, 30),
        "vix_pct_range": (40, 75),
        "breadth_range": (35, 60),
        "yield_range": (0.0, 1.0),
        "dollar_range": (-2, 4),
    },
    "risk_off": {
        "vix_range": (28, 40),
        "vix_pct_range": (70, 92),
        "breadth_range": (20, 40),
        "yield_range": (-0.5, 0.3),
        "dollar_range": (1, 6),
    },
    "crisis": {
        "vix_range": (38, 80),
        "vix_pct_range": (90, 99),
        "breadth_range": (8, 25),
        "yield_range": (-2.0, -0.2),
        "dollar_range": (3, 10),
    },
}

_REGIME_ACTIONS = {
    "risk_on": [
        "Full allocation. All pipeline stages active. Normal position sizing.",
        "Standard operations. Deploy capital as opportunities arise. Monitor breadth for early warnings.",
        "Favorable conditions. Run full screening pipeline with standard parameters.",
        "Normal position sizes (4%). Up to 10 positions. No regime adjustment needed.",
    ],
    "cautious": [
        "Reduce position sizes by 30%. Tighten stops. Raise entry score threshold to 65.",
        "Elevated caution. Reduce new entries to 1/day. Favor defensive sectors. Widen trailing stops.",
        "Trim weakest positions. No new speculative entries. Increase cash reserve to 20%.",
        "Moderate risk reduction. Position sizes to 3%. Require 5-stage pass for new entries.",
    ],
    "risk_off": [
        "No new entries. Monitor existing positions for exit. Server-side stops critical.",
        "Defensive posture. Exit positions below entry price. Tighten all stops to 5%.",
        "Halt all new entries. Liquidate positions with negative momentum. Raise cash to 40%.",
        "Risk-off mode. Only trailing-stop exits allowed. No manual overrides.",
    ],
    "crisis": [
        "Immediate exit of all positions via market orders. Preserve capital.",
        "Emergency liquidation. Market sell all positions. Move to 100% cash.",
        "Crisis mode: exit everything. No exceptions. Re-enter only when VIX drops below 30.",
        "Full capital preservation. Liquidate all positions. Review entry criteria after VIX normalizes.",
    ],
}


def _format_regime_input(scenario: Dict[str, Any]) -> str:
    """Format a regime scenario into a user prompt."""
    lines = ["Assess current market regime:"]
    lines.append("")
    lines.append(f"- VIX: {scenario['vix']:.1f} ({scenario['vix_pct']:.0f}th percentile)")
    lines.append(f"- Market breadth: {scenario['breadth']:.0f}% above 200-day MA")
    lines.append(f"- Yield curve spread: {scenario['yield_spread']:+.2f}%")

    if "dollar_index_chg" in scenario:
        lines.append(f"- Dollar index 3-month change: {scenario['dollar_index_chg']:+.1f}%")

    if scenario.get("transitioning"):
        lines.append(f"- Previous regime: {scenario['prev_regime'].upper()}")
        lines.append(f"- Transition signal: {scenario['transition_note']}")

    if scenario.get("nuance_note"):
        lines.append(f"- Note: {scenario['nuance_note']}")

    lines.append("")
    lines.append("What is the current regime and recommended action?")
    return "\n".join(lines)


def _format_regime_output(scenario: Dict[str, Any]) -> str:
    """Format a regime assessment assistant response."""
    regime = scenario["regime"]
    vix = scenario["vix"]
    breadth = scenario["breadth"]
    yld = scenario["yield_spread"]
    action = scenario["action"]

    lines = [f"## Regime: {regime.upper()}"]
    lines.append("")

    # VIX assessment
    if vix < 15:
        vix_note = "low volatility, complacency risk"
    elif vix < 20:
        vix_note = "normal range, favorable for entries"
    elif vix < 30:
        vix_note = "elevated, caution warranted"
    elif vix < 40:
        vix_note = "high stress, defensive posture required"
    else:
        vix_note = "extreme fear, capital preservation mode"
    lines.append(f"**VIX at {vix:.1f}** ({scenario['vix_pct']:.0f}th percentile) -- {vix_note}.")
    lines.append("")

    # Breadth assessment
    if breadth > 60:
        breadth_note = "healthy participation, broad-based advance"
    elif breadth > 40:
        breadth_note = "narrowing participation, fewer stocks leading"
    elif breadth > 25:
        breadth_note = "poor breadth, rally is fragile and narrow"
    else:
        breadth_note = "very weak breadth, widespread selling pressure"
    lines.append(f"**Breadth at {breadth:.0f}%** -- {breadth_note}.")
    lines.append("")

    # Yield assessment
    if yld > 1.0:
        yield_note = "healthy spread, no recession signal"
    elif yld > 0.5:
        yield_note = "normal, monitoring for flattening"
    elif yld > 0:
        yield_note = "flattening, monitor closely for inversion"
    elif yld > -0.5:
        yield_note = "inverted, recession risk elevated"
    else:
        yield_note = "deeply inverted, strong recession signal"
    lines.append(f"**Yield spread {yld:+.2f}%** -- {yield_note}.")
    lines.append("")

    # Dollar assessment if present
    if "dollar_index_chg" in scenario:
        dchg = scenario["dollar_index_chg"]
        if dchg > 3:
            dollar_note = "strong dollar, headwind for multinationals"
        elif dchg > 0:
            dollar_note = "modest dollar strength, minor drag"
        elif dchg > -3:
            dollar_note = "weakening dollar, tailwind for risk assets"
        else:
            dollar_note = "sharp dollar decline, potential instability"
        lines.append(f"**Dollar change {dchg:+.1f}%** -- {dollar_note}.")
        lines.append("")

    # Transition note
    if scenario.get("transitioning"):
        lines.append(f"**Transition**: Moving from {scenario['prev_regime'].upper()} "
                      f"to {regime.upper()}. {scenario['transition_note']}")
        lines.append("")

    # Nuance note
    if scenario.get("nuance_note"):
        lines.append(f"**Notable divergence**: {scenario['nuance_note']}")
        lines.append("")

    lines.append(f"### Action\n{action}")

    return "\n".join(lines)


def generate_regime_examples(
    rng: np.random.Generator,
    count: int = 550,
) -> List[Dict[str, Any]]:
    """Generate regime assessment training examples.

    Includes standard regime examples, transition scenarios, and nuanced
    edge cases with contradictory indicators.
    """
    regimes = list(_REGIME_DEFINITIONS.keys())
    examples: List[Dict[str, Any]] = []

    # Standard regime examples: ~60%
    n_standard = int(count * 0.60)
    for _ in range(n_standard):
        regime = str(rng.choice(regimes))
        defn = _REGIME_DEFINITIONS[regime]

        vix = float(rng.uniform(*defn["vix_range"]))
        vix_pct = float(rng.uniform(*defn["vix_pct_range"]))
        breadth = float(rng.uniform(*defn["breadth_range"]))
        yld = float(rng.uniform(*defn["yield_range"]))
        dollar = float(rng.uniform(*defn["dollar_range"]))
        action = str(rng.choice(_REGIME_ACTIONS[regime]))

        scenario = {
            "vix": vix, "vix_pct": vix_pct, "breadth": breadth,
            "yield_spread": yld, "dollar_index_chg": dollar,
            "regime": regime, "action": action,
            "transitioning": False, "nuance_note": None,
        }
        user_msg = _format_regime_input(scenario)
        assistant_msg = _format_regime_output(scenario)
        examples.append(_make_chat(user_msg, assistant_msg))

    # Transition scenarios: ~20%
    n_transition = int(count * 0.20)
    _transition_notes = {
        ("risk_on", "cautious"): "VIX rising from low base, breadth starting to narrow.",
        ("cautious", "risk_on"): "VIX declining, breadth expanding. Gradual risk-on shift.",
        ("cautious", "risk_off"): "VIX accelerating higher, breadth deteriorating rapidly.",
        ("risk_off", "cautious"): "VIX pulling back from highs, breadth stabilizing.",
        ("risk_off", "crisis"): "VIX spiking, yield curve inverting further. Panic escalating.",
        ("crisis", "risk_off"): "VIX retreating from extremes, selling pressure easing.",
        ("risk_on", "risk_off"): "Sudden shock event. VIX jumped 15+ points in one session.",
        ("crisis", "cautious"): "Massive central bank intervention. VIX dropping but remains elevated.",
    }
    transitions = list(_transition_notes.keys())

    for _ in range(n_transition):
        prev, curr = transitions[int(rng.integers(0, len(transitions)))]
        defn = _REGIME_DEFINITIONS[curr]

        vix = float(rng.uniform(*defn["vix_range"]))
        vix_pct = float(rng.uniform(*defn["vix_pct_range"]))
        breadth = float(rng.uniform(*defn["breadth_range"]))
        yld = float(rng.uniform(*defn["yield_range"]))
        dollar = float(rng.uniform(*defn["dollar_range"]))
        action = str(rng.choice(_REGIME_ACTIONS[curr]))

        scenario = {
            "vix": vix, "vix_pct": vix_pct, "breadth": breadth,
            "yield_spread": yld, "dollar_index_chg": dollar,
            "regime": curr, "action": action,
            "transitioning": True, "prev_regime": prev,
            "transition_note": _transition_notes[(prev, curr)],
            "nuance_note": None,
        }
        user_msg = _format_regime_input(scenario)
        assistant_msg = _format_regime_output(scenario)
        examples.append(_make_chat(user_msg, assistant_msg))

    # Nuanced / contradictory indicator examples: ~20%
    n_nuance = count - n_standard - n_transition
    _nuance_scenarios = [
        {
            "desc": "VIX elevated but breadth healthy",
            "regime": "cautious",
            "vix": (22, 32), "vix_pct": (50, 80),
            "breadth": (60, 78), "yield": (0.3, 1.5),
            "note": "VIX elevated despite broad market participation -- possible hedging demand rather than panic.",
        },
        {
            "desc": "Inverted yield but low VIX",
            "regime": "cautious",
            "vix": (12, 18), "vix_pct": (10, 35),
            "breadth": (50, 70), "yield": (-0.8, -0.1),
            "note": "Yield curve inverted but equity vol low -- market pricing recession but not yet panicking.",
        },
        {
            "desc": "Low VIX with deteriorating breadth",
            "regime": "cautious",
            "vix": (11, 16), "vix_pct": (5, 25),
            "breadth": (25, 40), "yield": (0.5, 1.5),
            "note": "Calm VIX masking narrow leadership -- few mega-caps driving index. Elevated index concentration risk.",
        },
        {
            "desc": "High VIX recovering with poor breadth",
            "regime": "risk_off",
            "vix": (28, 38), "vix_pct": (65, 88),
            "breadth": (20, 35), "yield": (-0.3, 0.2),
            "note": "VIX retreating from spike but breadth not recovering -- dead-cat bounce risk.",
        },
        {
            "desc": "Strong dollar with healthy domestic signals",
            "regime": "cautious",
            "vix": (14, 20), "vix_pct": (15, 40),
            "breadth": (55, 72), "yield": (0.5, 1.8),
            "note": "Dollar surging (+5%+) while domestic breadth healthy -- international earnings at risk, favor domestic revenue names.",
        },
        {
            "desc": "VIX very low with yield inversion and weak breadth",
            "regime": "risk_off",
            "vix": (10, 14), "vix_pct": (2, 15),
            "breadth": (28, 38), "yield": (-1.0, -0.3),
            "note": "Extreme complacency (VIX <15) with inverted yield and poor breadth -- classic late-cycle divergence. High asymmetric risk.",
        },
    ]

    for _ in range(n_nuance):
        ns = _nuance_scenarios[int(rng.integers(0, len(_nuance_scenarios)))]
        vix = float(rng.uniform(*ns["vix"]))
        vix_pct = float(rng.uniform(*ns["vix_pct"]))
        breadth = float(rng.uniform(*ns["breadth"]))
        yld = float(rng.uniform(*ns["yield"]))
        dollar = float(rng.uniform(-4, 7))
        action = str(rng.choice(_REGIME_ACTIONS[ns["regime"]]))

        scenario = {
            "vix": vix, "vix_pct": vix_pct, "breadth": breadth,
            "yield_spread": yld, "dollar_index_chg": dollar,
            "regime": ns["regime"], "action": action,
            "transitioning": False, "nuance_note": ns["note"],
        }
        user_msg = _format_regime_input(scenario)
        assistant_msg = _format_regime_output(scenario)
        examples.append(_make_chat(user_msg, assistant_msg))

    return examples


# ---------------------------------------------------------------------------
# 3. Trade post-mortem generation
# ---------------------------------------------------------------------------

def _generate_trade_for_postmortem(rng: np.random.Generator) -> Dict[str, Any]:
    """Generate a single realistic completed trade dict for post-mortem analysis."""
    ticker = rng.choice(_SP500_SAMPLE)
    entry_price = float(rng.uniform(10, 600))

    # Realistic P&L distribution: ~55% winners, skewed
    is_win = rng.random() < 0.55

    if is_win:
        bucket = rng.choice(["marginal", "modest", "solid", "strong"], p=[0.20, 0.35, 0.30, 0.15])
        if bucket == "marginal":
            pnl_pct = float(rng.uniform(0.002, 0.03))
        elif bucket == "modest":
            pnl_pct = float(rng.uniform(0.03, 0.08))
        elif bucket == "solid":
            pnl_pct = float(rng.uniform(0.08, 0.15))
        else:
            pnl_pct = float(rng.uniform(0.15, 0.50))
    else:
        bucket = rng.choice(["small", "moderate", "significant", "full_stop"], p=[0.15, 0.30, 0.35, 0.20])
        if bucket == "small":
            pnl_pct = float(rng.uniform(-0.03, -0.005))
        elif bucket == "moderate":
            pnl_pct = float(rng.uniform(-0.06, -0.03))
        elif bucket == "significant":
            pnl_pct = float(rng.uniform(-0.10, -0.06))
        else:
            pnl_pct = float(rng.uniform(-0.15, -0.10))

    exit_price = entry_price * (1 + pnl_pct)
    shares = max(1, int(round(2500 / entry_price)))
    cost_basis = entry_price * shares
    proceeds = exit_price * shares
    pnl_dollars = proceeds - cost_basis

    # Exit reason depends on outcome
    if is_win:
        if pnl_pct > 0.10:
            exit_reason = str(rng.choice(
                ["trailing_stop", "max_hold_time", "manual"],
                p=[0.60, 0.25, 0.15],
            ))
        else:
            exit_reason = str(rng.choice(
                ["trailing_stop", "max_hold_time", "manual"],
                p=[0.35, 0.45, 0.20],
            ))
    else:
        if pnl_pct < -0.08:
            exit_reason = str(rng.choice(
                ["stop_loss", "vix_spike"],
                p=[0.75, 0.25],
            ))
        else:
            exit_reason = str(rng.choice(
                ["stop_loss", "trailing_stop", "max_hold_time", "vix_spike"],
                p=[0.40, 0.15, 0.25, 0.20],
            ))

    # Hold days: correlated with exit reason
    if exit_reason == "stop_loss":
        hold_days = int(rng.integers(1, 15))
    elif exit_reason == "trailing_stop":
        hold_days = int(rng.integers(5, 30))
    elif exit_reason == "max_hold_time":
        hold_days = int(rng.integers(25, 31))
    elif exit_reason == "vix_spike":
        hold_days = int(rng.integers(1, 20))
    else:
        hold_days = int(rng.integers(3, 28))

    exit_dt = datetime(2026, 3, 1) - timedelta(days=int(rng.integers(1, 540)))
    entry_dt = exit_dt - timedelta(days=hold_days)

    # Score: higher for winners (with noise)
    if is_win:
        score = float(np.clip(rng.normal(70, 12), 30, 98))
    else:
        score = float(np.clip(rng.normal(55, 15), 20, 95))

    n_reasons = int(rng.integers(2, 5))
    reasons = list(rng.choice(_ENTRY_SIGNALS, size=n_reasons, replace=False))

    return {
        "ticker": ticker,
        "entry_date": entry_dt.strftime("%Y-%m-%d"),
        "exit_date": exit_dt.strftime("%Y-%m-%d"),
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "shares": shares,
        "cost_basis": round(cost_basis, 2),
        "proceeds": round(proceeds, 2),
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_pct": round(pnl_pct, 4),
        "hold_days": hold_days,
        "exit_reason": exit_reason,
        "score": round(score, 1),
        "reasons": reasons,
    }


def generate_postmortem_examples(
    rng: np.random.Generator,
    count: int = 1100,
) -> List[Dict[str, Any]]:
    """Generate trade post-mortem training examples."""
    examples = []
    for _ in range(count):
        trade = _generate_trade_for_postmortem(rng)
        user_msg = _format_trade_postmortem_input(trade)
        assistant_msg = _format_trade_postmortem_output(trade)
        examples.append(_make_chat(user_msg, assistant_msg))
    return examples


# ---------------------------------------------------------------------------
# 4. Position management generation
# ---------------------------------------------------------------------------

def _generate_position(rng: np.random.Generator) -> Dict[str, Any]:
    """Generate a single open position for management scenarios."""
    ticker = str(rng.choice(_SP500_SAMPLE))
    entry_price = float(rng.uniform(15, 500))
    current_pnl = float(rng.uniform(-0.12, 0.35))
    current_price = entry_price * (1 + current_pnl)
    hold_days = int(rng.integers(1, 30))
    high_since_entry = current_price * float(rng.uniform(1.0, 1.08))
    atr_pct = float(rng.uniform(0.015, 0.08))
    score = float(np.clip(rng.normal(65, 12), 25, 98))
    sector = str(rng.choice(_SECTORS))

    # Stop levels
    initial_stop = entry_price * 0.90  # 10% stop
    if current_pnl > 0.10:
        trailing_stop = high_since_entry * (1 - 0.12)
    elif current_pnl > 0.20:
        trailing_stop = high_since_entry * (1 - 0.15)
    else:
        trailing_stop = initial_stop
    current_stop = max(initial_stop, trailing_stop) if current_pnl > 0.10 else initial_stop

    shares = max(1, int(round(2000 / entry_price)))
    position_value = current_price * shares

    return {
        "ticker": ticker,
        "entry_price": round(entry_price, 2),
        "current_price": round(current_price, 2),
        "high_since_entry": round(high_since_entry, 2),
        "current_pnl_pct": round(current_pnl, 4),
        "hold_days": hold_days,
        "atr_pct": round(atr_pct, 4),
        "entry_score": round(score, 1),
        "sector": sector,
        "shares": shares,
        "position_value": round(position_value, 2),
        "current_stop": round(current_stop, 2),
        "initial_stop": round(initial_stop, 2),
    }


def _format_stop_tighten_input(position: Dict[str, Any], vix: float) -> str:
    """Format a stop tightening decision prompt."""
    p = position
    lines = [f"Should I tighten stops on {p['ticker']}?"]
    lines.append("")
    lines.append(f"- Entry: ${p['entry_price']:.2f}, Current: ${p['current_price']:.2f} ({p['current_pnl_pct']:+.1%})")
    lines.append(f"- High since entry: ${p['high_since_entry']:.2f}")
    lines.append(f"- Hold period: {p['hold_days']} days")
    lines.append(f"- Current stop: ${p['current_stop']:.2f} ({(p['current_stop'] / p['current_price'] - 1):+.1%} from current)")
    lines.append(f"- ATR%: {p['atr_pct']:.1%} (daily volatility)")
    lines.append(f"- Entry score: {p['entry_score']:.0f}")
    lines.append(f"- Current VIX: {vix:.1f}")
    lines.append("")
    lines.append("Analyze and recommend stop adjustment.")
    return "\n".join(lines)


def _format_stop_tighten_output(position: Dict[str, Any], vix: float) -> str:
    """Generate stop tightening recommendation."""
    p = position
    pnl = p["current_pnl_pct"]
    hold = p["hold_days"]
    atr = p["atr_pct"]
    lines = []

    # Determine recommendation
    if pnl > 0.20:
        recommendation = "TIGHTEN"
        new_stop_pct = 0.15 if atr < 0.04 else 0.18
        new_stop = p["high_since_entry"] * (1 - new_stop_pct)
        rationale = (
            f"Significant unrealized gain ({pnl:+.1%}). Lock in profits with a "
            f"trailing stop at {new_stop_pct:.0%} below the high of ${p['high_since_entry']:.2f}."
        )
    elif pnl > 0.10:
        recommendation = "TIGHTEN"
        new_stop_pct = 0.12
        new_stop = p["high_since_entry"] * (1 - new_stop_pct)
        rationale = (
            f"Solid gain ({pnl:+.1%}). Move to trailing stop at {new_stop_pct:.0%} "
            f"below high to protect profits while allowing room for continuation."
        )
    elif pnl > 0.05 and vix > 30:
        recommendation = "TIGHTEN"
        new_stop_pct = 0.05
        new_stop = p["current_price"] * (1 - new_stop_pct)
        rationale = (
            f"Moderate gain ({pnl:+.1%}) in elevated VIX ({vix:.0f}) environment. "
            f"Tighten to {new_stop_pct:.0%} below current price for capital protection."
        )
    elif pnl < -0.05 and hold > 15:
        recommendation = "CONSIDER EXIT"
        new_stop = p["current_price"] * 0.97
        rationale = (
            f"Losing position ({pnl:+.1%}) held for {hold} days with no recovery. "
            f"Consider exiting to free capital for better opportunities."
        )
    elif pnl < 0 and vix > 35:
        recommendation = "TIGHTEN"
        new_stop = p["current_price"] * 0.95
        rationale = (
            f"Underwater ({pnl:+.1%}) in high-VIX ({vix:.0f}) environment. "
            f"Tighten stop to 5% below current to limit further damage."
        )
    else:
        recommendation = "HOLD CURRENT STOP"
        new_stop = p["current_stop"]
        rationale = (
            f"Position at {pnl:+.1%} with {hold} days held. Current stop at "
            f"${p['current_stop']:.2f} is appropriate. No adjustment needed."
        )

    lines.append(f"## Recommendation: {recommendation}")
    lines.append("")
    lines.append(rationale)
    lines.append("")

    # New stop level
    if recommendation != "HOLD CURRENT STOP":
        lines.append(f"**New stop**: ${new_stop:.2f} "
                      f"({(new_stop / p['current_price'] - 1):+.1%} from current price)")
        lines.append(f"**Previous stop**: ${p['current_stop']:.2f}")
    else:
        lines.append(f"**Current stop**: ${p['current_stop']:.2f} "
                      f"({(p['current_stop'] / p['current_price'] - 1):+.1%} from current price)")
    lines.append("")

    # Risk context
    lines.append("### Risk Context")
    lines.append(f"- Daily volatility (ATR%): {atr:.1%}")
    if atr > 0.05:
        lines.append("- High volatility -- allow wider stops to avoid noise exits")
    elif atr < 0.025:
        lines.append("- Low volatility -- tighter stops are practical")

    if vix > 30:
        lines.append(f"- VIX at {vix:.0f} -- elevated market stress, favor tighter stops")
    elif vix < 15:
        lines.append(f"- VIX at {vix:.0f} -- calm market, standard stop widths sufficient")

    return "\n".join(lines)


def _format_portfolio_risk_input(positions: List[Dict[str, Any]], account: Dict[str, Any]) -> str:
    """Format a multi-position portfolio risk assessment prompt."""
    lines = ["Assess risk across my current portfolio:"]
    lines.append("")
    lines.append(f"**Account**: ${account['equity']:,.0f} equity, "
                 f"${account['cash']:,.0f} cash ({account['cash_pct']:.0f}% reserve)")
    lines.append(f"**Positions**: {len(positions)} open")
    lines.append("")

    for i, p in enumerate(positions, 1):
        lines.append(f"{i}. **{p['ticker']}** ({p['sector']}): "
                     f"${p['position_value']:,.0f} | {p['current_pnl_pct']:+.1%} | "
                     f"{p['hold_days']}d held | ATR {p['atr_pct']:.1%}")

    lines.append("")
    lines.append("Identify concentration risks, correlation concerns, and recommended actions.")
    return "\n".join(lines)


def _format_portfolio_risk_output(
    positions: List[Dict[str, Any]],
    account: Dict[str, Any],
) -> str:
    """Generate portfolio risk assessment."""
    lines = ["## Portfolio Risk Assessment"]
    lines.append("")

    total_value = sum(p["position_value"] for p in positions)
    equity = account["equity"]

    # Concentration analysis
    lines.append("### Concentration")
    sector_exposure: Dict[str, float] = {}
    for p in positions:
        sector_exposure[p["sector"]] = sector_exposure.get(p["sector"], 0) + p["position_value"]

    max_sector = max(sector_exposure, key=sector_exposure.get) if sector_exposure else "N/A"
    max_sector_pct = sector_exposure.get(max_sector, 0) / equity if equity > 0 else 0

    lines.append(f"- Total invested: ${total_value:,.0f} ({total_value / equity:.0%} of equity)")
    lines.append(f"- Largest sector: {max_sector} ({max_sector_pct:.0%} of equity)")

    if max_sector_pct > 0.30:
        lines.append(f"  - **WARNING**: {max_sector} exceeds 30% sector limit. Consider trimming.")
    lines.append(f"- Sectors represented: {len(sector_exposure)}")
    if len(sector_exposure) < 3 and len(positions) >= 4:
        lines.append("  - **WARNING**: Low sector diversity. Add positions in under-represented sectors.")
    lines.append("")

    # Position-level risks
    lines.append("### Position-Level Risks")
    losers = [p for p in positions if p["current_pnl_pct"] < -0.05]
    high_vol = [p for p in positions if p["atr_pct"] > 0.05]
    extended = [p for p in positions if p["hold_days"] > 25]

    if losers:
        tickers = ", ".join(p["ticker"] for p in losers)
        lines.append(f"- **Underwater positions**: {tickers} -- review for exit or stop tighten")
    if high_vol:
        tickers = ", ".join(p["ticker"] for p in high_vol)
        lines.append(f"- **High volatility**: {tickers} (ATR >5%) -- ensure position sized appropriately")
    if extended:
        tickers = ", ".join(p["ticker"] for p in extended)
        lines.append(f"- **Approaching max hold**: {tickers} -- will auto-exit at 30 days")
    if not losers and not high_vol and not extended:
        lines.append("- No immediate position-level concerns.")
    lines.append("")

    # Cash reserve
    lines.append("### Cash Reserve")
    cash_pct = account["cash_pct"]
    if cash_pct < 10:
        lines.append(f"- **WARNING**: Cash at {cash_pct:.0f}% -- below 10% minimum. "
                      f"Exit weakest position to rebuild reserve.")
    elif cash_pct > 40:
        lines.append(f"- Cash at {cash_pct:.0f}% -- consider deploying if regime is risk_on.")
    else:
        lines.append(f"- Cash at {cash_pct:.0f}% -- within acceptable range.")

    lines.append("")
    lines.append("### Summary")
    n_issues = (1 if max_sector_pct > 0.30 else 0) + len(losers) + (1 if cash_pct < 10 else 0)
    if n_issues == 0:
        lines.append("Portfolio is well-balanced. No immediate action required.")
    else:
        lines.append(f"{n_issues} risk item(s) identified. Address highest priority items first.")

    return "\n".join(lines)


def _format_sizing_input(
    ticker: str,
    entry_price: float,
    atr_pct: float,
    account_equity: float,
    current_positions: int,
    vix: float,
    score: float,
) -> str:
    """Format a position sizing question prompt."""
    lines = [f"What position size should I use for {ticker}?"]
    lines.append("")
    lines.append(f"- Entry price: ${entry_price:.2f}")
    lines.append(f"- Daily ATR%: {atr_pct:.1%}")
    lines.append(f"- Account equity: ${account_equity:,.0f}")
    lines.append(f"- Current positions: {current_positions}/10")
    lines.append(f"- VIX: {vix:.1f}")
    lines.append(f"- Entry score: {score:.0f}")
    lines.append("")
    lines.append("Calculate the appropriate position size and number of shares.")
    return "\n".join(lines)


def _format_sizing_output(
    ticker: str,
    entry_price: float,
    atr_pct: float,
    account_equity: float,
    current_positions: int,
    vix: float,
    score: float,
) -> str:
    """Generate position sizing recommendation."""
    lines = [f"## Position Sizing: {ticker}"]
    lines.append("")

    # Volatility-adjusted base size
    if atr_pct < 0.03:
        base_pct = 0.06
        vol_label = "low"
    elif atr_pct < 0.06:
        base_pct = 0.05
        vol_label = "medium"
    else:
        base_pct = 0.03
        vol_label = "high"

    lines.append(f"**Volatility bucket**: {vol_label} (ATR {atr_pct:.1%}) -> base size {base_pct:.0%}")

    # VIX adjustment
    vix_adj = 1.0
    if vix > 30:
        vix_adj = 0.70
        lines.append(f"**VIX adjustment**: {vix:.0f} (elevated) -> reduce by 30%")
    elif vix > 25:
        vix_adj = 0.85
        lines.append(f"**VIX adjustment**: {vix:.0f} (cautious) -> reduce by 15%")
    else:
        lines.append(f"**VIX adjustment**: {vix:.0f} (normal) -> no adjustment")

    # Capacity check
    if current_positions >= 9:
        cap_adj = 0.50
        lines.append(f"**Capacity**: {current_positions}/10 slots used -> half size for diversification")
    elif current_positions >= 7:
        cap_adj = 0.75
        lines.append(f"**Capacity**: {current_positions}/10 slots used -> 75% size")
    else:
        cap_adj = 1.0
        lines.append(f"**Capacity**: {current_positions}/10 slots used -> full size")

    final_pct = base_pct * vix_adj * cap_adj
    # Cap at 10% of equity
    final_pct = min(final_pct, 0.10)
    position_dollars = account_equity * final_pct
    shares = max(1, int(position_dollars / entry_price))
    actual_dollars = shares * entry_price
    actual_pct = actual_dollars / account_equity

    lines.append("")
    lines.append(f"### Result")
    lines.append(f"- Position size: {final_pct:.1%} of equity = ${position_dollars:,.0f}")
    lines.append(f"- Shares: {shares} x ${entry_price:.2f} = ${actual_dollars:,.0f} ({actual_pct:.1%} of equity)")
    lines.append(f"- Risk per share (10% stop): ${entry_price * 0.10:.2f}")
    lines.append(f"- Total risk: ${actual_dollars * 0.10:,.0f} ({actual_dollars * 0.10 / account_equity:.1%} of equity)")

    return "\n".join(lines)


def generate_position_management_examples(
    rng: np.random.Generator,
    count: int = 550,
) -> List[Dict[str, Any]]:
    """Generate position management training examples.

    Distribution: ~40% stop tightening, ~30% portfolio risk, ~30% position sizing.
    """
    examples: List[Dict[str, Any]] = []

    # Stop tightening scenarios
    n_stops = int(count * 0.40)
    for _ in range(n_stops):
        pos = _generate_position(rng)
        vix = float(rng.uniform(10, 45))
        user_msg = _format_stop_tighten_input(pos, vix)
        assistant_msg = _format_stop_tighten_output(pos, vix)
        examples.append(_make_chat(user_msg, assistant_msg))

    # Portfolio risk assessment scenarios
    n_portfolio = int(count * 0.30)
    for _ in range(n_portfolio):
        n_pos = int(rng.integers(3, 11))
        positions = [_generate_position(rng) for _ in range(n_pos)]
        total_invested = sum(p["position_value"] for p in positions)
        equity = total_invested * float(rng.uniform(1.05, 1.60))
        cash = equity - total_invested
        cash_pct = (cash / equity) * 100 if equity > 0 else 0

        account = {
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "cash_pct": round(cash_pct, 1),
        }
        user_msg = _format_portfolio_risk_input(positions, account)
        assistant_msg = _format_portfolio_risk_output(positions, account)
        examples.append(_make_chat(user_msg, assistant_msg))

    # Position sizing scenarios
    n_sizing = count - n_stops - n_portfolio
    for _ in range(n_sizing):
        ticker = str(rng.choice(_SP500_SAMPLE))
        entry_price = float(rng.uniform(10, 500))
        atr_pct = float(rng.uniform(0.015, 0.09))
        equity = float(rng.choice([25000, 50000, 75000, 100000, 200000]))
        current_pos = int(rng.integers(0, 10))
        vix = float(rng.uniform(10, 40))
        score = float(np.clip(rng.normal(65, 12), 30, 98))

        user_msg = _format_sizing_input(ticker, entry_price, atr_pct, equity, current_pos, vix, score)
        assistant_msg = _format_sizing_output(ticker, entry_price, atr_pct, equity, current_pos, vix, score)
        examples.append(_make_chat(user_msg, assistant_msg))

    return examples


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(
    seed: int = 42,
    output_path: Optional[str] = None,
    screening_count: int = 2200,
    regime_count: int = 550,
    postmortem_count: int = 1100,
    position_count: int = 550,
) -> Path:
    """Generate all synthetic training examples and save to JSONL.

    Args:
        seed: Random seed for reproducibility.
        output_path: Output file path. Defaults to data/llm/train.jsonl.
        screening_count: Number of screening examples to generate.
        regime_count: Number of regime assessment examples.
        postmortem_count: Number of trade post-mortem examples.
        position_count: Number of position management examples.

    Returns:
        Path to the saved JSONL file.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rng = np.random.default_rng(seed)

    out = Path(output_path) if output_path else Path(__file__).parent.parent.parent / "data" / "llm" / "train.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Generating synthetic training data (seed=%d)...", seed)

    # Generate each category
    logger.info("Generating %d screening examples...", screening_count)
    screening_examples = generate_screening_examples(rng, screening_count)

    logger.info("Generating %d regime examples...", regime_count)
    regime_examples = generate_regime_examples(rng, regime_count)

    logger.info("Generating %d post-mortem examples...", postmortem_count)
    postmortem_examples = generate_postmortem_examples(rng, postmortem_count)

    logger.info("Generating %d position management examples...", position_count)
    position_examples = generate_position_management_examples(rng, position_count)

    # Combine and shuffle
    all_examples = screening_examples + regime_examples + postmortem_examples + position_examples
    rng.shuffle(all_examples)

    # Save
    with open(out, "w") as f:
        for ex in all_examples:
            f.write(json.dumps(ex) + "\n")

    total = len(all_examples)
    logger.info(
        "Saved %d examples to %s (screening=%d, regime=%d, postmortem=%d, position=%d)",
        total, out, len(screening_examples), len(regime_examples),
        len(postmortem_examples), len(position_examples),
    )

    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate synthetic LLM training data for STEEX")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--output", type=str, default=None, help="Output JSONL path")
    parser.add_argument("--screening", type=int, default=2200, help="Number of screening examples")
    parser.add_argument("--regime", type=int, default=550, help="Number of regime examples")
    parser.add_argument("--postmortem", type=int, default=1100, help="Number of post-mortem examples")
    parser.add_argument("--position", type=int, default=550, help="Number of position management examples")
    args = parser.parse_args()

    result_path = main(
        seed=args.seed,
        output_path=args.output,
        screening_count=args.screening,
        regime_count=args.regime,
        postmortem_count=args.postmortem,
        position_count=args.position,
    )
    print(f"Done. {result_path}")
