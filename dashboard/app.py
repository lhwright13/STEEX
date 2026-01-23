"""STEEX Dashboard - Main Entry Point.

A Streamlit-based web dashboard for real-time monitoring of the MIS trading strategy.

Run with: streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config.settings import get_settings
from dashboard.components.alerts import display_compact_alerts, generate_all_alerts
from dashboard.components.metrics import display_vix_indicator, market_status_indicator
from dashboard.services.cache import get_market_status, is_market_open
from dashboard.services.data_loader import get_data_loader

# Page configuration
st.set_page_config(
    page_title="STEEX Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown(
    """
    <style>
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
    }
    .stMetric {
        background-color: #f8f9fa;
        padding: 10px;
        border-radius: 5px;
    }
    .stMetric label {
        color: #666;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Auto-refresh during market hours
if is_market_open():
    st_autorefresh(interval=60000, key="market_refresh")  # 60 seconds

# Initialize
settings = get_settings()
data_loader = get_data_loader()


def render_sidebar():
    """Render the sidebar with market info and alerts."""
    with st.sidebar:
        st.title("STEEX Dashboard")
        st.caption("MIS Trading Strategy Monitor")

        st.divider()

        # Market Status
        st.subheader("Market Status")
        market_status = get_market_status()
        market_status_indicator(market_status)

        st.divider()

        # VIX
        st.subheader("VIX Level")
        vix_level = data_loader.get_current_vix()
        display_vix_indicator(vix_level)

        st.divider()

        # Quick Stats
        st.subheader("Quick Stats")
        positions = data_loader.get_position_details()
        trades = data_loader.get_all_trades()

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Positions", len(positions))
        with col2:
            st.metric("Total Trades", len(trades))

        st.divider()

        # Alerts
        st.subheader("Alerts")
        alerts = generate_all_alerts(
            vix_level=vix_level,
            positions=positions,
            current_value=10000,  # Placeholder
            peak_value=10000,  # Placeholder
            settings=settings,
        )
        display_compact_alerts(alerts)

        st.divider()

        # Refresh button
        if st.button("Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        # Last update time
        from datetime import datetime

        st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")


def main():
    """Main dashboard entry point."""
    render_sidebar()

    # Main content area - redirect to Overview
    st.title("Welcome to STEEX Dashboard")

    st.markdown(
        """
        ### MIS Trading Strategy Monitor

        Use the sidebar navigation to access different sections:

        - **Overview** - Key metrics and daily summary
        - **Positions** - Active positions with live P&L
        - **Signals** - Today's screening results
        - **Performance** - Historical returns and analytics
        - **Trade History** - Completed trades log
        - **Settings** - Strategy parameters

        ---

        **Quick Start:**
        1. Check the **Overview** page for today's status
        2. Review **Positions** to monitor active trades
        3. Check **Signals** for new trading candidates
        """
    )

    # Quick overview cards
    st.subheader("Current Status")

    col1, col2, col3 = st.columns(3)

    with col1:
        positions = data_loader.get_position_details()
        if positions:
            total_value = sum(p["current_value"] for p in positions)
            total_pnl = sum(p["pnl_dollars"] for p in positions)
            st.metric(
                "Portfolio Value",
                f"${total_value:,.2f}",
                delta=f"${total_pnl:,.2f}",
            )
        else:
            st.metric("Portfolio Value", "No positions")

    with col2:
        vix = data_loader.get_current_vix()
        if vix is not None:
            vix_status = "Normal" if vix < 30 else "Elevated" if vix < 40 else "High"
            st.metric("VIX", f"{vix:.1f}", delta=vix_status)
        else:
            st.metric("VIX", "N/A")

    with col3:
        trades = data_loader.get_all_trades()
        if trades:
            metrics = data_loader.get_trade_metrics(trades)
            win_rate = metrics.get("win_rate", 0) * 100
            st.metric(
                "Win Rate",
                f"{win_rate:.1f}%",
                delta=f"{metrics.get('total_trades', 0)} trades",
            )
        else:
            st.metric("Win Rate", "No trades")


if __name__ == "__main__":
    main()
