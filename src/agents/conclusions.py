"""Pydantic models for structured agent conclusions.

Each agent produces a typed conclusion that the ManagerAgent synthesizes
into a final decision. These models define the JSON schema that agents
output at the end of their reasoning.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Self-improvement metadata (optional on every conclusion)
# ---------------------------------------------------------------------------


class AgentMeta(BaseModel):
    """Agent self-improvement suggestions attached to conclusions."""

    prompt_suggestions: List[str] = Field(
        default_factory=list,
        description="Suggestions for improving the agent's prompt/instructions",
    )
    tool_suggestions: List[str] = Field(
        default_factory=list,
        description="Suggestions for new or modified tools",
    )
    process_suggestions: List[str] = Field(
        default_factory=list,
        description="Suggestions for improving the agent's workflow",
    )


# ---------------------------------------------------------------------------
# Sub-agent conclusions
# ---------------------------------------------------------------------------


class DataConclusion(BaseModel):
    """Output from the DataAgent."""

    all_healthy: bool = Field(description="Whether all data sources are healthy")
    sources_checked: int = Field(description="Number of data sources checked")
    sources_healthy: int = Field(description="Number of healthy data sources")
    issues: List[str] = Field(default_factory=list, description="Data health issues found")
    vix_level: Optional[float] = Field(default=None, description="Current VIX level")
    insider_purchases: Optional[int] = Field(default=None, description="Recent insider purchases found")
    prefetch_duration: Optional[float] = Field(default=None, description="Prefetch duration in seconds")
    reasoning: str = Field(description="Agent's analysis of data health")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class ExitRecommendation(BaseModel):
    """A recommended exit from the RiskAgent."""

    ticker: str
    reason: str
    urgency: str = Field(description="immediate, end_of_day, or next_session")
    current_price: float
    pnl_pct: float
    all_reasons: List[str] = Field(default_factory=list)


class PositionSummary(BaseModel):
    """Summary of a single position."""

    ticker: str
    entry_price: float
    current_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    days_held: Optional[int] = None
    current_stop: float


class RiskConclusion(BaseModel):
    """Output from the RiskAgent."""

    regime_name: str = Field(description="Market regime: risk_on, cautious, risk_off, crisis")
    regime_confidence: Optional[float] = Field(default=None, description="Regime confidence 0-1")
    vix_level: Optional[float] = Field(default=None)
    entries_allowed: bool = Field(description="Whether new entries are safe")
    sizing_multiplier: float = Field(description="Position sizing multiplier from regime")
    position_count: int = Field(default=0)
    portfolio_equity: Optional[float] = Field(default=None)
    cash_available: Optional[float] = Field(default=None)
    exits_recommended: List[ExitRecommendation] = Field(default_factory=list)
    risk_alerts: List[str] = Field(default_factory=list)
    reasoning: str = Field(description="Agent's risk assessment reasoning")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class CandidateStock(BaseModel):
    """A screened and ranked stock candidate."""

    ticker: str
    composite_score: float
    momentum_score: float
    insider_score: float
    volume_score: float
    sentiment_score: float
    fundamental_score: float
    reasons: List[str] = Field(default_factory=list)


class AnalysisConclusion(BaseModel):
    """Output from the AnalysisAgent."""

    universe_size: int = Field(description="Total stocks in universe")
    screening_funnel: Dict[str, int] = Field(
        default_factory=dict,
        description="Stage-by-stage pass counts",
    )
    candidates: List[CandidateStock] = Field(default_factory=list)
    portfolio_selected: Optional[int] = Field(
        default=None,
        description="Number selected after portfolio construction",
    )
    diversification_ratio: Optional[float] = Field(default=None)
    reasoning: str = Field(description="Agent's analysis of candidates")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class ConsensusStock(BaseModel):
    """A stock that passed consensus synthesis from multiple analysis variants."""

    ticker: str
    composite_score: float = Field(
        description="Averaged score across variants that agreed"
    )
    variants_agreeing: int = Field(
        description="Number of variants (1-3) that picked this stock"
    )
    high_conviction: bool = Field(
        default=False,
        description="True if all 3 variants agreed",
    )
    reasons: List[str] = Field(
        default_factory=list,
        description="Why each variant picked it",
    )


class MetaAnalysisConclusion(BaseModel):
    """Output from the MetaAnalysisAgent.

    Synthesizes results from conservative, aggressive, and momentum variants
    into a consensus recommendation based on how many variants agree.
    """

    candidates: List[ConsensusStock] = Field(
        default_factory=list,
        description="Consensus picks with multi-variant agreement",
    )
    high_conviction_count: int = Field(
        default=0,
        description="Number of picks with all 3 variants agreeing",
    )
    consensus_count: int = Field(
        default=0,
        description="Total picks with 2+ variants agreeing",
    )
    speculative_excluded: List[str] = Field(
        default_factory=list,
        description="Tickers picked by only 1 variant (excluded)",
    )
    variant_summaries: Dict[str, int] = Field(
        default_factory=dict,
        description="Number of candidates from each variant (conservative, aggressive, momentum)",
    )
    reasoning: str = Field(
        default="",
        description="Explanation of consensus synthesis approach",
    )
    meta: Optional[AgentMeta] = Field(
        default=None,
        description="Self-improvement suggestions",
    )


# ---------------------------------------------------------------------------
# Manager decision (synthesis of sub-agent conclusions)
# ---------------------------------------------------------------------------


class BuyRecommendation(BaseModel):
    """A buy recommendation from the manager."""

    ticker: str
    score: Optional[float] = Field(default=None, description="Composite score")
    price: Optional[float] = Field(default=None, description="Target price (filled at execution)")
    shares: Optional[int] = Field(default=None, description="Share count (filled at execution)")
    cost: Optional[float] = Field(default=None, description="Total cost (filled at execution)")
    stop: Optional[float] = Field(default=None, description="Stop price (filled at execution)")
    size_pct: Optional[float] = Field(default=None, description="Position size pct (filled at execution)")
    reasons: List[str] = Field(default_factory=list)


class SellRecommendation(BaseModel):
    """A sell recommendation from the manager."""

    ticker: str
    reason: str = Field(default="")
    urgency: str = Field(default="next_session")
    price: Optional[float] = Field(default=None, description="Current price")
    shares: Optional[float] = Field(default=None, description="Shares to sell")
    pnl_pct: Optional[float] = Field(default=None, description="P&L percentage")


class ManagerDecision(BaseModel):
    """Synthesized decision from the ManagerAgent."""

    entries_approved: bool = Field(description="Whether to proceed with entries")
    buys: List[BuyRecommendation] = Field(default_factory=list)
    sells: List[SellRecommendation] = Field(default_factory=list)
    regime_name: str = Field(default="unknown")
    alerts: List[str] = Field(default_factory=list)
    reasoning: str = Field(description="Manager's synthesis and reasoning")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


# ---------------------------------------------------------------------------
# Execution and reporting conclusions
# ---------------------------------------------------------------------------


class ExecutionConclusion(BaseModel):
    """Output from the ExecutionAgent."""

    entries_executed: int = Field(default=0)
    exits_executed: int = Field(default=0)
    entries_skipped: int = Field(default=0)
    total_cost: float = Field(default=0.0)
    total_proceeds: float = Field(default=0.0)
    errors: List[str] = Field(default_factory=list)
    reasoning: str = Field(description="Execution summary")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class ResearchConclusion(BaseModel):
    """Output from the ResearchAgent."""

    trades_analyzed: int = Field(default=0)
    win_rate: Optional[float] = Field(default=None)
    signals_degrading: List[str] = Field(default_factory=list)
    weight_changes_proposed: Dict[str, float] = Field(default_factory=dict)
    oos_validated: bool = Field(default=False)
    changes_applied: bool = Field(default=False)
    gaps_flagged: List[str] = Field(default_factory=list)
    reasoning: str = Field(description="Research findings and recommendations")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class EventSummary(BaseModel):
    """Output from the event-summary stage (P1-2): a concise user-facing note."""

    summary: str = Field(description="3-5 sentence plain-language explanation of the event")


class ReportConclusion(BaseModel):
    """Output from the ReportAgent."""

    report_saved: bool = Field(default=False)
    report_path: Optional[str] = Field(default=None)
    summary: str = Field(description="Brief summary of the day's activity")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class TestTraderConclusion(BaseModel):
    """Output from the TestTraderAgent (test_roundtrip mode).

    Reports a paper-mode buy/sell roundtrip on a single ticker via the
    place_paper_order MCP tool. Used to verify end-to-end agent -> MCP
    -> broker plumbing without going through the screener pipeline.
    """

    ticker: str = Field(description="Ticker the roundtrip targeted")
    requested_amount_usd: float = Field(description="Dollar amount requested for buy and sell legs")
    buy_order_id: Optional[str] = Field(default=None, description="Order ID returned from buy leg")
    buy_filled_price: Optional[float] = Field(default=None)
    buy_filled_qty: Optional[float] = Field(default=None)
    sell_order_id: Optional[str] = Field(default=None, description="Order ID returned from sell leg")
    sell_filled_price: Optional[float] = Field(default=None)
    sell_filled_qty: Optional[float] = Field(default=None)
    final_status: str = Field(description="success, partial, or failed")
    errors: List[str] = Field(default_factory=list)
    reasoning: str = Field(description="Summary of what happened during the roundtrip")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class EventTickerResolution(BaseModel):
    """Output from the EventTickerResolver (event_scan ingestion step).

    Reads one social-media post and decides whether it names a specific,
    publicly-traded US company in a bullish/endorsement way worth buying.
    No watchlist — any tradable US ticker qualifies, including small caps.
    """

    mentions_company: bool = Field(
        description="True only if the post names a specific publicly-traded US company"
    )
    company_name: Optional[str] = Field(default=None, description="Company named, if any")
    ticker: Optional[str] = Field(default=None, description="US stock ticker (uppercase), if resolvable")
    is_bullish: bool = Field(
        default=False, description="True if the post is positive/an endorsement to buy that company"
    )
    confidence: float = Field(default=0.0, description="Confidence 0-1 in the ticker + bullish call")
    reasoning: str = Field(description="Brief justification")


class EventReviewConclusion(BaseModel):
    """Output from the EventReviewAgent (event_scan mode).

    The deterministic event-trigger has already auto-bought a position on a
    breaking-news headline. This agent re-examines the headline + the executed
    trade and decides whether to keep it, exit immediately (fake/stale/misread
    signal), or tighten the stop. On 'exit' it should call
    generate_sell_list -> execute_exits for the ticker.
    """

    ticker: str = Field(description="Ticker the event trade targeted")
    verdict: Literal["keep", "exit", "tighten_stop"] = Field(
        description="keep = trade looks sound; exit = close now; tighten_stop = raise the stop"
    )
    confidence: float = Field(description="Confidence 0-1 in the verdict")
    reasoning: str = Field(description="Why this verdict, referencing the headline and trade")
    action_taken: Optional[str] = Field(
        default=None, description="What the agent actually did (e.g. 'sold 10 shares', 'no action')"
    )
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


# ---------------------------------------------------------------------------
# Learning pipeline conclusions
# ---------------------------------------------------------------------------


class PromptEvolutionRecommendation(BaseModel):
    """A data-backed recommendation to evolve an agent's prompt."""

    agent_name: str = Field(description="Agent whose prompt should be updated")
    suggestion: str = Field(description="The specific prompt change to make")
    rationale: str = Field(description="Evidence from trade data backing this change")
    priority: str = Field(
        default="medium",
        description="Priority: high, medium, or low",
    )


class LearningConclusion(BaseModel):
    """Output from the LearningAgent."""

    trades_analyzed: int = Field(default=0)
    win_rate: Optional[float] = Field(default=None)
    signals_degrading: List[str] = Field(default_factory=list)
    weight_changes_proposed: Dict[str, float] = Field(default_factory=dict)
    oos_validated: bool = Field(default=False)
    config_changes_applied: bool = Field(default=False)
    prompt_evolution_recommendations: List[PromptEvolutionRecommendation] = Field(
        default_factory=list,
    )
    agent_insights_used: List[str] = Field(default_factory=list)
    gaps_flagged: List[str] = Field(default_factory=list)
    gaps_resolved: List[str] = Field(default_factory=list)
    reasoning: str = Field(description="Learning findings and recommendations")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")


class LearningManagerDecision(BaseModel):
    """Synthesized decision from the LearningManager."""

    config_changes_approved: bool = Field(default=False)
    prompt_evolutions_approved: List[str] = Field(
        default_factory=list,
        description="Agent names whose prompt evolution is approved",
    )
    prompt_evolutions_rejected: List[str] = Field(
        default_factory=list,
        description="Agent names whose prompt evolution is rejected",
    )
    escalations: List[str] = Field(
        default_factory=list,
        description="Items that need human review",
    )
    reasoning: str = Field(description="Manager's review rationale")
    meta: Optional[AgentMeta] = Field(default=None, description="Self-improvement suggestions")
