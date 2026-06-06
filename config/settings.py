"""Strategy parameters and configuration settings."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


CONFIG_FILE = Path(__file__).parent / "config.yaml"


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Load settings from YAML config file."""

    def _load(self) -> Dict[str, Any]:
        if not hasattr(self, "_yaml_data"):
            self._yaml_data = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    self._yaml_data = yaml.safe_load(f) or {}
        return self._yaml_data

    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
        value = self._load().get(field_name)
        return value, field_name, value is not None

    def __call__(self) -> Dict[str, Any]:
        return {k: v for k, v in self._load().items() if v is not None}


class Settings(BaseSettings):
    """All tunable strategy parameters."""

    # Momentum parameters
    momentum_lookback_days: int = Field(
        default=126, description="6 months lookback for momentum calculation"
    )
    short_momentum_days: int = Field(
        default=21, description="1 month lookback for short-term momentum"
    )
    momentum_min_return: float = Field(
        default=0.05, description="Minimum 6-month return (5%)"
    )
    short_momentum_min_return: float = Field(
        default=0.00, description="Minimum 1-month return (0%)"
    )
    overextension_percentile: float = Field(
        default=0.95, description="Top 5% excluded as overextended"
    )
    overextension_filter_enabled: bool = Field(
        default=False, description="Enable overextension percentile filter"
    )

    # Moving averages
    ma_short: int = Field(default=50, description="Short-term moving average period")
    ma_long: int = Field(default=200, description="Long-term moving average period")
    require_dual_ma: bool = Field(
        default=False, description="Require price above both MAs (false = only short MA)"
    )

    # Insider trading parameters
    insider_lookback_days: int = Field(
        default=30, description="Days to look back for insider activity"
    )
    min_cluster_buyers: int = Field(
        default=3, description="Minimum insiders for cluster buy signal"
    )
    min_purchase_value: float = Field(
        default=100_000, description="Minimum purchase value for significant signal"
    )

    # Universe filter parameters
    min_price: float = Field(default=3.0, description="Minimum stock price (aggressive)")
    min_volume: int = Field(default=300_000, description="Minimum average daily volume (aggressive)")
    earnings_blackout_days: int = Field(
        default=5, description="Days before earnings to avoid"
    )
    min_history_days: int = Field(
        default=126, description="Minimum trading history required (6 months)"
    )

    # Position management
    max_positions: int = Field(default=15, description="Maximum concurrent positions (aggressive)")
    daily_picks: int = Field(default=12, description="Number of stocks to pick daily (aggressive)")
    position_size_pct: float = Field(
        default=0.06, description="Position size as fraction of portfolio (6%, aggressive)"
    )
    max_sector_pct: float = Field(
        default=0.45, description="Maximum sector exposure (45%, aggressive)"
    )
    max_single_position_pct: float = Field(
        default=0.12, description="Maximum single position size (12%, aggressive)"
    )
    min_cash_reserve_pct: float = Field(
        default=0.03, description="Minimum cash reserve (3%, aggressive)"
    )

    # Volatility-adjusted position sizing
    vol_sizing_enabled: bool = Field(
        default=True, description="Enable volatility-adjusted position sizing"
    )
    vol_low_threshold: float = Field(
        default=0.03, description="ATR% threshold for low volatility (3%)"
    )
    vol_med_threshold: float = Field(
        default=0.06, description="ATR% threshold for medium volatility (6%)"
    )
    vol_low_position_pct: float = Field(
        default=0.06, description="Position size for low volatility stocks (6%)"
    )
    vol_med_position_pct: float = Field(
        default=0.05, description="Position size for medium volatility stocks (5%)"
    )
    vol_high_position_pct: float = Field(
        default=0.03, description="Position size for high volatility stocks (3%)"
    )

    # Exit parameters
    initial_stop_pct: float = Field(
        default=0.14, description="Initial stop loss percentage (14%, aggressive)"
    )
    max_hold_days: int = Field(
        default=60, description="Maximum holding period in trading days (aggressive)"
    )
    dead_money_days: int = Field(
        default=10, description="Days below entry before exit as dead money"
    )
    dead_money_enabled: bool = Field(
        default=False, description="Enable dead money exit signal"
    )
    cooling_off_days: int = Field(
        default=14, description="Trading days to block re-entry after stop-loss"
    )

    # VIX thresholds
    vix_caution_level: float = Field(
        default=35, description="VIX level to tighten stops (aggressive)"
    )
    vix_exit_level: float = Field(
        default=45, description="VIX level to exit 50% of positions (aggressive)"
    )
    vix_tight_stop_pct: float = Field(
        default=0.05, description="Tighter stop when VIX is elevated (5%)"
    )

    # Trailing stop levels: {gain_threshold: trail_distance}
    trail_stop_10: float = Field(
        default=0.12, description="Trail distance after 10% gain"
    )
    trail_stop_20: float = Field(
        default=0.15, description="Trail distance after 20% gain"
    )
    trail_stop_30: float = Field(
        default=0.20, description="Trail distance after 30% gain (aggressive)"
    )

    # Scoring weights (must sum to 1.0) — aggressive, momentum-led
    weight_momentum: float = Field(default=0.38, description="Momentum weight in score")
    weight_insider: float = Field(default=0.14, description="Insider weight in score")
    weight_volume: float = Field(default=0.16, description="Volume surge weight")
    weight_sentiment: float = Field(default=0.12, description="Sentiment weight")
    weight_fundamental: float = Field(default=0.04, description="Fundamental analysis weight")
    weight_options: float = Field(default=0.16, description="Options intelligence weight")

    # Sentiment analysis parameters
    sentiment_enabled: bool = Field(
        default=True, description="Enable sentiment analysis in screening"
    )
    sentiment_min_score: float = Field(
        default=30.0, description="Minimum sentiment score to pass filter (0-100)"
    )
    geopolitical_enabled: bool = Field(
        default=True, description="Enable geopolitical/macro sentiment"
    )
    geopolitical_weight: float = Field(
        default=0.4, description="Weight of geopolitical vs stock-specific sentiment"
    )
    sentiment_stock_weight: float = Field(
        default=0.6, description="Weight of stock-specific sentiment"
    )
    sector_lookup_yfinance_enabled: bool = Field(
        default=True,
        description="Fall back to yfinance for sectors not in SECTOR_MAPPING (kill switch)",
    )

    # Fundamental analysis parameters
    fundamental_enabled: bool = Field(
        default=True, description="Enable fundamental analysis in screening"
    )
    fundamental_max_pe: float = Field(
        default=50.0, description="Maximum P/E ratio (filter out speculative)"
    )
    fundamental_min_roe: float = Field(
        default=0.05, description="Minimum ROE for quality filter (5%)"
    )
    fundamental_max_debt_equity: float = Field(
        default=2.0, description="Maximum debt/equity ratio"
    )
    fundamental_cache_ttl: int = Field(
        default=86400, description="Fundamental data cache TTL in seconds (24 hours)"
    )

    # Options intelligence parameters
    options_enabled: bool = Field(
        default=True, description="Enable options analysis in screening"
    )
    options_bullish_pc_threshold: float = Field(
        default=0.7, description="Put/call ratio below this is bullish"
    )
    options_bearish_pc_threshold: float = Field(
        default=1.0, description="Put/call ratio above this is bearish"
    )
    options_cache_ttl: int = Field(
        default=3600, description="Options data cache TTL in seconds (1 hour)"
    )

    # Walk-Forward Backtest
    walkforward_train_months: int = Field(
        default=6, description="Walk-forward training window in months"
    )
    walkforward_test_months: int = Field(
        default=2, description="Walk-forward test window in months"
    )
    walkforward_folds: int = Field(
        default=4, description="Number of walk-forward folds"
    )
    walkforward_signal_interval: int = Field(
        default=7, description="Days between signal generations in walk-forward"
    )

    # Portfolio Construction
    portfolio_max_pairwise_corr: float = Field(
        default=0.88, description="Max pairwise correlation for portfolio selection (aggressive)"
    )
    portfolio_risk_parity_enabled: bool = Field(
        default=True, description="Enable inverse-variance risk parity weighting"
    )
    portfolio_correlation_lookback: int = Field(
        default=60, description="Lookback days for correlation matrix"
    )

    # Multi-Factor Regime
    regime_multi_factor_enabled: bool = Field(
        default=True, description="Enable multi-factor regime detection"
    )
    regime_vix_weight: float = Field(
        default=0.40, description="VIX weight in composite regime score"
    )
    regime_yield_weight: float = Field(
        default=0.20, description="Yield curve weight in composite regime score"
    )
    regime_breadth_weight: float = Field(
        default=0.20, description="Market breadth weight in composite regime score"
    )
    regime_other_weight: float = Field(
        default=0.20, description="Dollar/other weight in composite regime score"
    )
    regime_risk_off_threshold: float = Field(
        default=70, description="Composite score threshold for risk_off regime"
    )
    regime_crisis_threshold: float = Field(
        default=85, description="Composite score threshold for crisis regime"
    )

    # Post-Mortem
    postmortem_enabled: bool = Field(
        default=True, description="Enable post-mortem trade analysis"
    )
    postmortem_lookback_days: int = Field(
        default=90, description="Days of trade history to analyze"
    )

    # Signal Research
    research_significance_level: float = Field(
        default=0.05, description="P-value threshold for signal significance"
    )
    research_min_sample_size: int = Field(
        default=30, description="Minimum samples for statistical testing"
    )
    research_redundancy_threshold: float = Field(
        default=0.80, description="Correlation threshold to flag redundant signals"
    )
    research_forward_return_days: int = Field(
        default=21, description="Forward return horizon for signal testing"
    )

    # Alpha Decay
    alpha_monitor_enabled: bool = Field(
        default=True, description="Enable alpha decay monitoring"
    )
    alpha_monitor_window: int = Field(
        default=30, description="Rolling window for alpha decay checks"
    )
    alpha_degradation_threshold: float = Field(
        default=0.15, description="Drop in hit rate that triggers degradation alert"
    )

    # Execution Quality
    execution_quality_enabled: bool = Field(
        default=True, description="Enable execution quality tracking"
    )
    execution_max_acceptable_slippage: float = Field(
        default=0.01, description="Max acceptable slippage before alert (1%)"
    )

    # Data Prefetch
    prefetch_enabled: bool = Field(
        default=True, description="Enable async data prefetching before pipeline"
    )
    prefetch_max_workers: int = Field(
        default=10, description="Max concurrent workers for prefetching"
    )
    prefetch_price_days: int = Field(
        default=320, description="Calendar days of price data to prefetch (covers 200-day MA)"
    )
    prefetch_sentiment_workers: int = Field(
        default=5, description="Concurrent workers for sentiment prefetch (respects Finnhub rate limit)"
    )
    prefetch_fundamentals_workers: int = Field(
        default=10, description="Concurrent workers for fundamentals prefetch"
    )
    prefetch_earnings_workers: int = Field(
        default=20, description="Concurrent workers for earnings calendar prefetch"
    )

    # Drawdown rules
    drawdown_review: float = Field(
        default=0.10, description="Drawdown level to review strategy (10%)"
    )
    drawdown_reduce: float = Field(
        default=0.15, description="Drawdown level to reduce position sizes (15%)"
    )
    drawdown_pause: float = Field(
        default=0.20, description="Drawdown level to pause new entries (20%)"
    )
    drawdown_exit: float = Field(
        default=0.25, description="Drawdown level to exit all positions (25%)"
    )

    # Transaction costs (includes bid-ask spread for small-caps)
    estimated_cost_per_trade: float = Field(
        default=0.005, description="Estimated transaction cost per trade (0.5%)"
    )

    # SQLite cache
    cache_db_path: str = Field(
        default="data/cache.db", description="SQLite cache database file path"
    )
    cache_enabled: bool = Field(
        default=True, description="Enable persistent SQLite cache for data providers"
    )

    # Broker settings
    broker_enabled: bool = Field(
        default=False, description="Enable live broker execution (default off)"
    )
    broker_paper: bool = Field(
        default=True, description="Use paper trading (default on, safety net)"
    )

    # Server-side stops (Alpaca GTC stop orders as crash-proof safety net)
    server_stops_enabled: bool = Field(
        default=True, description="Place GTC stops on Alpaca for each position"
    )
    server_stop_offset_pct: float = Field(
        default=0.005, description="Place server stop 0.5% below local stop (noise buffer)"
    )
    buy_limit_buffer_pct: float = Field(
        default=0.005, description="Buy limit price buffer above screen price (tolerates opening gaps)"
    )

    # Data paths
    data_dir: str = Field(default="data", description="Directory for cached data")
    positions_file: str = Field(
        default="positions.json", description="File for position tracking"
    )
    trades_file: str = Field(default="trades.json", description="File for trade log")

    # Learning loop
    learning_enabled: bool = Field(
        default=True, description="Enable the self-learning loop"
    )
    learning_dry_run: bool = Field(
        default=False, description="Run learning loop without applying changes"
    )
    learning_weight_change_cap: float = Field(
        default=0.10, description="Max weight change per learning cycle"
    )
    learning_min_trades_for_analysis: int = Field(
        default=15, description="Minimum trades required for reliable analysis"
    )
    learning_oos_min_sharpe: float = Field(
        default=0.0, description="Minimum OOS Sharpe ratio for validation"
    )
    learning_oos_min_win_rate: float = Field(
        default=0.50, description="Minimum OOS win rate for validation"
    )
    learning_feature_lookback_months: int = Field(
        default=6, description="Months of feature data for signal research"
    )
    learning_validation_train_months: int = Field(
        default=3, description="Training window for OOS validation folds"
    )
    learning_validation_test_months: int = Field(
        default=1, description="Test window for OOS validation folds"
    )

    # Agent settings (Claude AI multi-agent mode)
    agent_enabled: bool = Field(
        default=False, description="Enable Claude AI agent mode globally"
    )
    agent_max_turns: int = Field(
        default=15, description="Maximum agentic turns per sub-agent"
    )
    agent_timeout_seconds: int = Field(
        default=1200,
        description=(
            "Timeout for each agent invocation. Sized for the parallel analysis "
            "variants: each runs a full ~550s universe screen, and three run "
            "concurrently, so contention pushes individual runs past the old 600s."
        ),
    )
    agent_fallback_deterministic: bool = Field(
        default=True, description="Fall back to QuantManager if agent fails"
    )

    # External MCP servers (supplement STEEX tools with third-party data)
    mcp_alpaca_enabled: bool = Field(
        default=True, description="Enable Alpaca MCP for real-time quotes and order management"
    )
    mcp_polygon_enabled: bool = Field(
        default=False, description="Enable Polygon/Massive MCP for aggregates, news, options flow"
    )
    mcp_alphavantage_enabled: bool = Field(
        default=True, description="Enable Alpha Vantage MCP for technical indicators and economic data"
    )

    # Event-trigger subsystem (news-driven fast-path; see src/strategy/event_trigger.py)
    event_trigger_enabled: bool = Field(
        default=False, description="Enable the news event-trigger fast-path (auto-buy on breaking news)"
    )
    event_watchlist: List[str] = Field(
        default_factory=lambda: [
            "AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
            "META", "GOOGL", "AMD", "NFLX", "JPM",
        ],
        description="Tickers monitored for breaking-news events",
    )
    event_position_pct: float = Field(
        default=0.01, description="Fixed position size for event trades (1% of portfolio)"
    )
    event_sentiment_threshold: float = Field(
        default=40.0, description="Min bullish VADER headline score (-100..100) to act"
    )
    max_event_trades_per_day: int = Field(
        default=2, description="Cap on event-triggered entries per day"
    )
    event_cooldown_minutes: int = Field(
        default=120, description="Minutes before the same ticker can event-trigger again"
    )
    event_news_lookback_days: int = Field(
        default=7, description="How far back to query news on each poll (cursor trims the rest)"
    )
    event_truth_social_enabled: bool = Field(
        default=True,
        description="Use the watchlist-free Truth Social source (LLM-resolved tickers) instead of the Finnhub watchlist poller",
    )
    event_truth_social_account_id: str = Field(
        default="107780257626128497", description="Truth Social account id to watch (default: @realDonaldTrump)"
    )
    event_truth_lookback_hours: int = Field(
        default=24, description="How far back to consider Truth Social posts on each poll"
    )
    event_min_confidence: float = Field(
        default=0.7, description="Min LLM confidence (0-1) that a post is a bullish, correctly-resolved ticker"
    )
    event_resolver_model: str = Field(
        default="haiku",
        description="Claude model for the per-post ticker resolver (cheap; runs on every new post)",
    )

    # ---- Messaging / notifications (P1) -----------------------------------
    messaging_enabled: bool = Field(
        default=False,
        description="Master switch for outbound user notifications (iMessage). Off = dry-run (log only).",
    )
    imessage_to: str = Field(
        default="",
        description="Destination iMessage handle (phone or Apple ID email). Set via IMESSAGE_TO in .env; personal, not committed.",
    )

    # Agent trace settings
    trace_retention_days: int = Field(
        default=30, description="Days to keep agent session traces"
    )

    # Agent evolution settings
    evolution_enabled: bool = Field(
        default=False, description="Enable agent prompt self-improvement"
    )
    evolution_max_rewrites_per_week: int = Field(
        default=1, description="Maximum prompt rewrites per agent per week"
    )

    # Manager settings
    manager_portfolio_value: float = Field(
        default=50000, description="Total portfolio value for position sizing"
    )
    manager_report_dir: str = Field(
        default="data/reports", description="Directory for daily reports"
    )
    manager_max_daily_entries: int = Field(
        default=10, description="Maximum new positions per day (aggressive)"
    )
    manager_min_score_entry: float = Field(
        default=42.0, description="Minimum composite score for entry (aggressive)"
    )
    manager_require_insider: bool = Field(
        default=False, description="Require insider activity for entry"
    )

    @property
    def trailing_stops(self) -> Dict[float, float]:
        """Get trailing stop levels as dictionary."""
        return {
            0.10: self.trail_stop_10,
            0.20: self.trail_stop_20,
            0.30: self.trail_stop_30,
        }

    model_config = {
        "env_prefix": "STEEX_",
        "env_file": ".env",
        "extra": "ignore",
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources to include YAML config.

        Priority (highest to lowest):
        1. Init settings (passed to constructor)
        2. Environment variables
        3. YAML config file
        4. Default values
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            dotenv_settings,
        )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
