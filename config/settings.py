"""Strategy parameters and configuration settings."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


CONFIG_FILE = Path(__file__).parent / "config.yaml"


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Load settings from YAML config file."""

    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
        """Get field value from YAML config."""
        if not hasattr(self, "_yaml_data"):
            self._yaml_data = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    self._yaml_data = yaml.safe_load(f) or {}

        value = self._yaml_data.get(field_name)
        return value, field_name, value is not None

    def __call__(self) -> Dict[str, Any]:
        """Return all values from YAML."""
        if not hasattr(self, "_yaml_data"):
            self._yaml_data = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    self._yaml_data = yaml.safe_load(f) or {}
        return {k: v for k, v in self._yaml_data.items() if v is not None}


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
        default=0.15, description="Minimum 6-month return (15%)"
    )
    overextension_percentile: float = Field(
        default=0.95, description="Top 5% excluded as overextended"
    )

    # Moving averages
    ma_short: int = Field(default=50, description="Short-term moving average period")
    ma_long: int = Field(default=200, description="Long-term moving average period")

    # Insider trading parameters
    insider_lookback_days: int = Field(
        default=30, description="Days to look back for insider activity"
    )
    min_insider_buyers: int = Field(
        default=1, description="Minimum number of insider buyers"
    )
    min_cluster_buyers: int = Field(
        default=3, description="Minimum insiders for cluster buy signal"
    )
    min_purchase_value: float = Field(
        default=100_000, description="Minimum purchase value for significant signal"
    )

    # Universe filter parameters
    min_price: float = Field(default=5.0, description="Minimum stock price")
    min_volume: int = Field(default=500_000, description="Minimum average daily volume")
    earnings_blackout_days: int = Field(
        default=5, description="Days before earnings to avoid"
    )
    min_history_days: int = Field(
        default=126, description="Minimum trading history required (6 months)"
    )

    # Position management
    max_positions: int = Field(default=20, description="Maximum concurrent positions")
    daily_picks: int = Field(default=2, description="Number of stocks to pick daily")
    position_size_pct: float = Field(
        default=0.05, description="Position size as fraction of portfolio (5%)"
    )
    max_sector_pct: float = Field(
        default=0.30, description="Maximum sector exposure (30%)"
    )
    max_single_position_pct: float = Field(
        default=0.10, description="Maximum single position size (10%)"
    )
    min_cash_reserve_pct: float = Field(
        default=0.10, description="Minimum cash reserve (10%)"
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
        default=0.12, description="Initial stop loss percentage (12%)"
    )
    max_hold_days: int = Field(
        default=60, description="Maximum holding period in trading days"
    )
    dead_money_days: int = Field(
        default=10, description="Days below entry before exit as dead money"
    )
    cooling_off_days: int = Field(
        default=14, description="Trading days to block re-entry after stop-loss"
    )

    # VIX thresholds
    vix_caution_level: float = Field(
        default=30, description="VIX level to tighten stops"
    )
    vix_exit_level: float = Field(
        default=40, description="VIX level to exit 50% of positions"
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
        default=0.18, description="Trail distance after 30% gain"
    )

    # Scoring weights (must sum to 1.0)
    weight_momentum: float = Field(default=0.30, description="Momentum weight in score")
    weight_insider: float = Field(default=0.25, description="Insider weight in score")
    weight_volume: float = Field(default=0.15, description="Volume surge weight")
    weight_sentiment: float = Field(default=0.15, description="Sentiment weight")
    weight_fundamental: float = Field(default=0.10, description="Fundamental analysis weight")
    weight_options: float = Field(default=0.05, description="Options intelligence weight")

    # Sentiment analysis parameters
    sentiment_enabled: bool = Field(
        default=True, description="Enable sentiment analysis in screening"
    )
    sentiment_min_score: float = Field(
        default=30.0, description="Minimum sentiment score to pass filter (0-100)"
    )
    sentiment_cache_ttl: int = Field(
        default=3600, description="Sentiment cache TTL in seconds (1 hour)"
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

    # PySR symbolic regression parameters
    pysr_enabled: bool = Field(
        default=False, description="Enable PySR symbolic regression scoring"
    )
    pysr_model_dir: str = Field(
        default="data/ml/models", description="Directory for trained PySR models"
    )
    pysr_dataset_dir: str = Field(
        default="data/ml/datasets", description="Directory for training datasets"
    )
    pysr_niterations: int = Field(
        default=40, description="PySR number of iterations"
    )
    pysr_max_complexity: int = Field(
        default=25, description="Maximum equation complexity in PySR search"
    )
    pysr_max_selected_complexity: int = Field(
        default=20, description="Maximum complexity for selected equation"
    )
    pysr_populations: int = Field(
        default=15, description="Number of populations in PySR"
    )
    pysr_parsimony: float = Field(
        default=0.0032, description="Parsimony coefficient (prefer simpler equations)"
    )
    pysr_timeout: int = Field(
        default=3600, description="PySR training timeout in seconds"
    )
    pysr_train_months: int = Field(
        default=12, description="Training window in months for walk-forward"
    )
    pysr_val_months: int = Field(
        default=1, description="Validation window in months for walk-forward"
    )
    pysr_walk_forward_folds: int = Field(
        default=6, description="Number of walk-forward folds"
    )
    pysr_sample_frequency_days: int = Field(
        default=5, description="Days between training samples"
    )
    weight_pysr: float = Field(
        default=0.0, description="PySR score weight in composite (0 = disabled)"
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

    # Data paths
    data_dir: str = Field(default="data", description="Directory for cached data")
    positions_file: str = Field(
        default="positions.json", description="File for position tracking"
    )
    trades_file: str = Field(default="trades.json", description="File for trade log")

    # Manager settings
    manager_portfolio_value: float = Field(
        default=50000, description="Total portfolio value for position sizing"
    )
    manager_report_dir: str = Field(
        default="data/reports", description="Directory for daily reports"
    )
    manager_max_daily_entries: int = Field(
        default=2, description="Maximum new positions per day"
    )
    manager_min_score_entry: float = Field(
        default=55.0, description="Minimum composite score for entry"
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
