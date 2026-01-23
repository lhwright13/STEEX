"""Settings page - Strategy parameters viewer."""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import yaml

from config.settings import Settings, get_settings

st.set_page_config(
    page_title="Settings - STEEX",
    page_icon="⚙️",
    layout="wide",
)

settings = get_settings()

st.title("Strategy Settings")
st.caption("View current strategy parameters and configuration")

st.divider()

# Momentum Parameters
st.subheader("Momentum Parameters")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Momentum Lookback",
        f"{settings.momentum_lookback_days} days",
        help="6-month lookback for momentum calculation",
    )
    st.metric(
        "Short Momentum",
        f"{settings.short_momentum_days} days",
        help="1-month lookback for short-term momentum",
    )

with col2:
    st.metric(
        "Minimum Return",
        f"{settings.momentum_min_return * 100:.0f}%",
        help="Minimum 6-month return required",
    )
    st.metric(
        "Overextension Percentile",
        f"{settings.overextension_percentile * 100:.0f}%",
        help="Top percentile excluded as overextended",
    )

with col3:
    st.metric(
        "Short MA",
        f"{settings.ma_short} days",
        help="Short-term moving average period",
    )
    st.metric(
        "Long MA",
        f"{settings.ma_long} days",
        help="Long-term moving average period",
    )

st.divider()

# Insider Trading Parameters
st.subheader("Insider Trading Parameters")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Lookback Days",
        f"{settings.insider_lookback_days} days",
        help="Days to look back for insider activity",
    )

with col2:
    st.metric(
        "Min Cluster Buyers",
        str(settings.min_cluster_buyers),
        help="Minimum insiders for cluster buy signal",
    )

with col3:
    st.metric(
        "Min Purchase Value",
        f"${settings.min_purchase_value:,.0f}",
        help="Minimum purchase value for significant signal",
    )

st.divider()

# Position Management
st.subheader("Position Management")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Max Positions",
        str(settings.max_positions),
        help="Maximum concurrent positions",
    )
    st.metric(
        "Daily Picks",
        str(settings.daily_picks),
        help="Number of stocks to pick daily",
    )

with col2:
    st.metric(
        "Position Size",
        f"{settings.position_size_pct * 100:.0f}%",
        help="Default position size as fraction of portfolio",
    )
    st.metric(
        "Max Single Position",
        f"{settings.max_single_position_pct * 100:.0f}%",
        help="Maximum single position size",
    )

with col3:
    st.metric(
        "Max Sector Exposure",
        f"{settings.max_sector_pct * 100:.0f}%",
        help="Maximum sector exposure",
    )
    st.metric(
        "Min Cash Reserve",
        f"{settings.min_cash_reserve_pct * 100:.0f}%",
        help="Minimum cash reserve",
    )

with col4:
    st.metric(
        "Min Price",
        f"${settings.min_price:.0f}",
        help="Minimum stock price",
    )
    st.metric(
        "Min Volume",
        f"{settings.min_volume:,}",
        help="Minimum average daily volume",
    )

st.divider()

# Volatility-Adjusted Sizing
st.subheader("Volatility-Adjusted Position Sizing")

st.markdown(f"**Enabled:** {'Yes' if settings.vol_sizing_enabled else 'No'}")

if settings.vol_sizing_enabled:
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**Low Volatility (<3% ATR)**")
        st.metric("Position Size", f"{settings.vol_low_position_pct * 100:.0f}%")

    with col2:
        st.markdown("**Medium Volatility (3-6% ATR)**")
        st.metric("Position Size", f"{settings.vol_med_position_pct * 100:.0f}%")

    with col3:
        st.markdown("**High Volatility (>6% ATR)**")
        st.metric("Position Size", f"{settings.vol_high_position_pct * 100:.0f}%")

st.divider()

# Exit Parameters
st.subheader("Exit Parameters")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Initial Stop",
        f"{settings.initial_stop_pct * 100:.0f}%",
        help="Initial stop loss percentage",
    )

with col2:
    st.metric(
        "Max Hold Days",
        str(settings.max_hold_days),
        help="Maximum holding period in trading days",
    )

with col3:
    st.metric(
        "Dead Money Days",
        str(settings.dead_money_days),
        help="Days below entry before exit as dead money",
    )

with col4:
    st.metric(
        "Cooling Off Days",
        str(settings.cooling_off_days),
        help="Trading days to block re-entry after stop-loss",
    )

# Trailing stops
st.markdown("**Trailing Stop Levels:**")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(f"After **10%** gain: **{settings.trail_stop_10 * 100:.0f}%** trail")

with col2:
    st.markdown(f"After **20%** gain: **{settings.trail_stop_20 * 100:.0f}%** trail")

with col3:
    st.markdown(f"After **30%** gain: **{settings.trail_stop_30 * 100:.0f}%** trail")

st.divider()

# VIX Thresholds
st.subheader("VIX Thresholds")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Caution Level",
        str(settings.vix_caution_level),
        help="VIX level to tighten stops",
    )

with col2:
    st.metric(
        "Exit Level",
        str(settings.vix_exit_level),
        help="VIX level to exit 50% of positions",
    )

with col3:
    st.metric(
        "Tight Stop",
        f"{settings.vix_tight_stop_pct * 100:.0f}%",
        help="Tighter stop when VIX is elevated",
    )

st.divider()

# Drawdown Rules
st.subheader("Drawdown Rules")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Review Level",
        f"{settings.drawdown_review * 100:.0f}%",
        help="Drawdown level to review strategy",
    )

with col2:
    st.metric(
        "Reduce Level",
        f"{settings.drawdown_reduce * 100:.0f}%",
        help="Drawdown level to reduce position sizes",
    )

with col3:
    st.metric(
        "Pause Level",
        f"{settings.drawdown_pause * 100:.0f}%",
        help="Drawdown level to pause new entries",
    )

with col4:
    st.metric(
        "Exit Level",
        f"{settings.drawdown_exit * 100:.0f}%",
        help="Drawdown level to exit all positions",
    )

st.divider()

# Scoring Weights
st.subheader("Scoring Weights")

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.metric("Momentum", f"{settings.weight_momentum * 100:.0f}%")

with col2:
    st.metric("Insider", f"{settings.weight_insider * 100:.0f}%")

with col3:
    st.metric("Volume", f"{settings.weight_volume * 100:.0f}%")

with col4:
    st.metric("Sentiment", f"{settings.weight_sentiment * 100:.0f}%")

with col5:
    st.metric("Fundamental", f"{settings.weight_fundamental * 100:.0f}%")

with col6:
    st.metric("Options", f"{settings.weight_options * 100:.0f}%")

st.divider()

# Sentiment Analysis Settings
st.subheader("Sentiment Analysis")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Sentiment Enabled",
        "Yes" if settings.sentiment_enabled else "No",
        help="Whether sentiment analysis is used in screening",
    )
    st.metric(
        "Min Sentiment Score",
        f"{settings.sentiment_min_score:.0f}",
        help="Minimum sentiment score to pass Stage 4 filter (0-100)",
    )

with col2:
    st.metric(
        "Stock Sentiment Weight",
        f"{settings.sentiment_stock_weight * 100:.0f}%",
        help="Weight of stock-specific news sentiment",
    )
    st.metric(
        "Geopolitical Weight",
        f"{settings.geopolitical_weight * 100:.0f}%",
        help="Weight of macro/geopolitical sentiment",
    )

with col3:
    st.metric(
        "Geopolitical Enabled",
        "Yes" if settings.geopolitical_enabled else "No",
        help="Whether geopolitical events affect sector sentiment",
    )
    st.metric(
        "Cache TTL",
        f"{settings.sentiment_cache_ttl}s",
        help="How long sentiment data is cached",
    )

st.markdown("""
**Sentiment Sources:**
- **Stock-Specific**: VADER NLP (local, unlimited) + Finnhub/Alpha Vantage (news APIs)
- **Geopolitical**: GDELT (global events database)

**Sector Impact Events:**
- Military conflicts boost Defense, Energy sectors
- Trade wars negatively impact Technology, Industrials
- Interest rate changes affect Financials, Real Estate
""")

st.divider()

# Fundamental Analysis Settings
st.subheader("Fundamental Analysis")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Fundamental Enabled",
        "Yes" if settings.fundamental_enabled else "No",
        help="Whether fundamental analysis is used in screening",
    )
    st.metric(
        "Max P/E Ratio",
        f"{settings.fundamental_max_pe:.0f}",
        help="Maximum P/E ratio allowed (filter out speculation)",
    )

with col2:
    st.metric(
        "Min ROE",
        f"{settings.fundamental_min_roe * 100:.0f}%",
        help="Minimum return on equity for quality filter",
    )
    st.metric(
        "Max Debt/Equity",
        f"{settings.fundamental_max_debt_equity:.1f}",
        help="Maximum debt to equity ratio",
    )

with col3:
    st.metric(
        "Cache TTL",
        f"{settings.fundamental_cache_ttl}s",
        help="How long fundamental data is cached (24 hours)",
    )

st.markdown("""
**Fundamental Metrics:**
- **P/E Ratio**: Valuation relative to earnings
- **PEG Ratio**: Growth-adjusted P/E (< 1.5 is attractive)
- **ROE**: Return on equity (> 15% indicates efficiency)
- **Debt/Equity**: Financial leverage (< 1 is conservative)
- **Revenue Growth**: Year-over-year growth rate
""")

st.divider()

# Options Intelligence Settings
st.subheader("Options Intelligence")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Options Enabled",
        "Yes" if settings.options_enabled else "No",
        help="Whether options analysis is used in scoring",
    )

with col2:
    st.metric(
        "Bullish P/C Threshold",
        f"{settings.options_bullish_pc_threshold:.1f}",
        help="Put/Call ratio below this is considered bullish",
    )

with col3:
    st.metric(
        "Bearish P/C Threshold",
        f"{settings.options_bearish_pc_threshold:.1f}",
        help="Put/Call ratio above this is considered bearish",
    )

st.markdown("""
**Options Metrics:**
- **Put/Call Ratio**: Open interest ratio (< 0.7 bullish, > 1.0 bearish)
- **IV Skew**: Put IV vs Call IV (positive indicates fear)
- **Max Pain**: Strike price where most options expire worthless
- **Unusual Activity**: Volume significantly exceeding open interest
""")

st.divider()

# Config file display
st.subheader("Configuration File")

config_file = Path(__file__).parent.parent.parent / "config" / "config.yaml"

if config_file.exists():
    with open(config_file) as f:
        config_content = f.read()

    with st.expander("View config.yaml"):
        st.code(config_content, language="yaml")
else:
    st.info("No config.yaml file found. Using default settings.")

st.divider()

# Data paths
st.subheader("Data Paths")

col1, col2 = st.columns(2)

with col1:
    st.markdown(f"**Data Directory:** `{settings.data_dir}`")
    st.markdown(f"**Positions File:** `{settings.positions_file}`")

with col2:
    st.markdown(f"**Trades File:** `{settings.trades_file}`")
    st.markdown(f"**Transaction Cost:** `{settings.estimated_cost_per_trade * 100:.1f}%`")

st.divider()

# Parameter documentation
with st.expander("Parameter Documentation"):
    st.markdown("""
    ### Momentum Parameters

    - **Momentum Lookback Days**: The number of calendar days used to calculate 6-month momentum
    - **Short Momentum Days**: The number of calendar days for 1-month momentum
    - **Minimum Return**: Stocks must have at least this return over the momentum period
    - **Overextension Percentile**: Stocks in the top X% of momentum are excluded

    ### Moving Averages

    - **Short MA**: Short-term moving average (typically 50-day)
    - **Long MA**: Long-term moving average (typically 200-day)
    - Stocks must be above both MAs to pass the momentum filter

    ### Insider Trading

    - **Lookback Days**: How far back to look for insider purchases
    - **Min Cluster Buyers**: Number of unique insiders needed for a cluster signal
    - **Min Purchase Value**: Minimum dollar value for a significant purchase

    ### Position Management

    - **Max Positions**: Maximum number of concurrent positions
    - **Position Size**: Default position size as percentage of portfolio
    - **Max Sector Exposure**: Maximum exposure to any single sector
    - **Min Cash Reserve**: Minimum cash to keep available

    ### Exit Rules

    - **Initial Stop**: Stop loss from entry price
    - **Trailing Stops**: Progressively tighter stops as gain increases
    - **Max Hold Days**: Automatic exit after this many trading days
    - **Dead Money Days**: Exit if below entry for this many days

    ### VIX Rules

    - **Caution Level**: When VIX exceeds this, tighten stops
    - **Exit Level**: When VIX exceeds this, begin liquidating positions

    ### Drawdown Rules

    - **Review**: Review the strategy at this drawdown level
    - **Reduce**: Reduce position sizes at this level
    - **Pause**: Stop new entries at this level
    - **Exit**: Exit all positions at this level
    """)
