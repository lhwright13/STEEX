"""Strategy parameters and configuration settings."""

from functools import lru_cache
from typing import Dict

from pydantic import Field
from pydantic_settings import BaseSettings


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
        default=0.10, description="Minimum 6-month return (10%)"
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

    # Exit parameters
    initial_stop_pct: float = Field(
        default=0.07, description="Initial stop loss percentage (7%)"
    )
    max_hold_days: int = Field(
        default=60, description="Maximum holding period in trading days"
    )
    dead_money_days: int = Field(
        default=10, description="Days below entry before exit as dead money"
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
        default=0.10, description="Trail distance after 10% gain"
    )
    trail_stop_20: float = Field(
        default=0.12, description="Trail distance after 20% gain"
    )
    trail_stop_30: float = Field(
        default=0.15, description="Trail distance after 30% gain"
    )

    # Scoring weights
    weight_momentum: float = Field(default=0.40, description="Momentum weight in score")
    weight_insider: float = Field(default=0.30, description="Insider weight in score")
    weight_volume: float = Field(default=0.20, description="Volume surge weight")
    weight_sentiment: float = Field(default=0.10, description="Sentiment weight")

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

    # Transaction costs
    estimated_cost_per_trade: float = Field(
        default=0.001, description="Estimated transaction cost per trade (0.1%)"
    )

    # Data paths
    data_dir: str = Field(default="data", description="Directory for cached data")
    positions_file: str = Field(
        default="positions.json", description="File for position tracking"
    )
    trades_file: str = Field(default="trades.json", description="File for trade log")

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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
