"""STEEX - SEC Trading Analysis Tools with MIS Strategy."""

__version__ = "0.1.0"

# Re-export main components for easier access
from .data import PriceProvider, Universe, VixProvider, EarningsCalendar
from .indicators import MomentumCalculator, TechnicalIndicators
from .strategy import StockScreener, StockRanker, SignalGenerator
from .portfolio import Position, PositionManager, RiskManager, TradeTracker
from .backtest import BacktestEngine, BacktestResult

__all__ = [
    # Data providers
    "PriceProvider",
    "Universe",
    "VixProvider",
    "EarningsCalendar",
    # Indicators
    "MomentumCalculator",
    "TechnicalIndicators",
    # Strategy
    "StockScreener",
    "StockRanker",
    "SignalGenerator",
    # Portfolio
    "Position",
    "PositionManager",
    "RiskManager",
    "TradeTracker",
    # Backtest
    "BacktestEngine",
    "BacktestResult",
]
