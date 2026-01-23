"""Signal detection and scoring for insider trading patterns."""

from collections import defaultdict
from typing import Dict, List

from ..models import InsiderTransaction


def find_cluster_buys(
    transactions: List[InsiderTransaction], min_insiders: int = 2
) -> Dict[str, List[InsiderTransaction]]:
    """
    Find cluster buys - multiple insiders buying the same stock.

    Args:
        transactions: List of insider transactions
        min_insiders: Minimum unique insiders to qualify as cluster

    Returns:
        Dict mapping ticker to list of transactions
    """
    by_ticker = defaultdict(list)
    for tx in transactions:
        if tx.ticker:
            by_ticker[tx.ticker].append(tx)

    clusters = {}
    for ticker, tx_list in by_ticker.items():
        unique_insiders = set(t.insider_cik for t in tx_list)
        if len(unique_insiders) >= min_insiders:
            tx_list.sort(key=lambda x: x.transaction_date, reverse=True)
            clusters[ticker] = tx_list

    return clusters


def calculate_cluster_score(transactions: List[InsiderTransaction]) -> dict:
    """
    Calculate signal strength score for a cluster buy.

    Scoring factors (enhanced):
    - +10 per unique insider
    - +50 for CEO/CFO buy
    - +30 per officer (non-CEO/CFO)
    - +15 per director
    - -10 per 10% owner (less meaningful signal)
    - +20/+40/+60 for total value >$100k/$500k/$1M
    - +25 bonus for cluster (3+ unique insiders)

    Args:
        transactions: List of transactions for one ticker

    Returns:
        Dict with score and breakdown factors
    """
    if not transactions:
        return {"score": 0, "factors": {}}

    unique_insiders = set(t.insider_cik for t in transactions)
    total_value = sum(t.total_value for t in transactions)
    total_shares = sum(t.shares for t in transactions)

    directors = sum(1 for t in transactions if t.is_director)
    officers = sum(1 for t in transactions if t.is_officer)
    ten_pct_owners = sum(1 for t in transactions if t.is_ten_percent_owner)

    # Check for CEO/CFO buys
    ceo_cfo_count = sum(
        1 for t in transactions
        if t.officer_title and ("CEO" in t.officer_title.upper() or "CFO" in t.officer_title.upper())
    )
    other_officers = officers - ceo_cfo_count

    # Calculate score with enhanced weights
    score = len(unique_insiders) * 10  # Base points per insider

    # CEO/CFO gets strong weight
    score += ceo_cfo_count * 50

    # Other officers
    score += other_officers * 30

    # Directors
    score += directors * 15

    # 10% owners penalized (often institutional, less meaningful)
    score -= ten_pct_owners * 10

    # Value-based bonuses (enhanced)
    if total_value > 1_000_000:
        score += 60
    elif total_value > 500_000:
        score += 40
    elif total_value > 100_000:
        score += 20

    # Cluster bonus (3+ unique insiders)
    if len(unique_insiders) >= 3:
        score += 25

    return {
        "score": min(score, 100),
        "factors": {
            "unique_insiders": len(unique_insiders),
            "total_value": total_value,
            "total_shares": total_shares,
            "directors": directors,
            "officers": officers,
            "ceo_cfo_count": ceo_cfo_count,
            "ten_pct_owners": ten_pct_owners,
        },
    }
