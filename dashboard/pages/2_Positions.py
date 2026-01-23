"""Positions page - Active positions with live P&L."""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config.settings import get_settings
from dashboard.components.charts import (
    create_mini_price_chart,
    create_sector_pie,
)
from dashboard.components.metrics import (
    format_currency,
    format_percent,
)
from dashboard.services.cache import is_market_open
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Positions - STEEX",
    page_icon="📊",
    layout="wide",
)

# Auto-refresh during market hours
if is_market_open():
    st_autorefresh(interval=60000, key="positions_refresh")

settings = get_settings()
data_loader = get_data_loader()

st.title("Active Positions")
st.caption("Real-time position tracking with live P&L")

# Get position data
positions = data_loader.get_position_details()

if not positions:
    st.info("No active positions. Check the Signals page for new candidates.")
    st.stop()

# Summary metrics
st.subheader("Portfolio Summary")

total_value = sum(p["current_value"] for p in positions)
total_cost = sum(p["cost_basis"] for p in positions)
total_pnl = sum(p["pnl_dollars"] for p in positions)
total_pnl_pct = (total_pnl / total_cost) if total_cost > 0 else 0

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Total Value", format_currency(total_value))
with col2:
    st.metric("Total Cost", format_currency(total_cost))
with col3:
    delta_color = "normal" if total_pnl >= 0 else "inverse"
    st.metric(
        "Total P&L",
        format_currency(total_pnl),
        delta=format_percent(total_pnl_pct),
    )
with col4:
    st.metric("Position Count", len(positions))
with col5:
    winners = sum(1 for p in positions if p["pnl_dollars"] > 0)
    st.metric("Winners/Losers", f"{winners}/{len(positions) - winners}")

st.divider()

# Positions table
st.subheader("Positions Detail")


def get_row_style(pnl_pct: float, stop_distance_pct: float) -> str:
    """Get background color for row based on status."""
    if stop_distance_pct < 0.02:
        return "background-color: #FEF9E7"  # Yellow - near stop
    elif pnl_pct > 0:
        return "background-color: #E8F8F5"  # Green - profit
    else:
        return "background-color: #FDEDEC"  # Red - loss


# Sort options
sort_col = st.selectbox(
    "Sort by",
    options=["P&L $", "P&L %", "Days Held", "Ticker", "Stop Distance"],
    index=0,
)

sort_map = {
    "P&L $": "pnl_dollars",
    "P&L %": "pnl_pct",
    "Days Held": "days_held",
    "Ticker": "ticker",
    "Stop Distance": "stop_distance_pct",
}

ascending = st.checkbox("Ascending", value=False)
sorted_positions = sorted(
    positions,
    key=lambda x: x[sort_map[sort_col]],
    reverse=not ascending,
)

# Create DataFrame for display
table_data = []
for p in sorted_positions:
    status = ""
    if p["stop_distance_pct"] < 0.02:
        status = "NEAR STOP"
    elif p["pnl_pct"] > 0.20:
        status = "STRONG"
    elif p["pnl_pct"] < -0.05:
        status = "WEAK"

    table_data.append({
        "Ticker": p["ticker"],
        "Shares": int(p["shares"]),
        "Entry": format_currency(p["entry_price"]),
        "Current": format_currency(p["current_price"]),
        "P&L $": format_currency(p["pnl_dollars"]),
        "P&L %": format_percent(p["pnl_pct"]),
        "Days": p["days_held"],
        "Stop": format_currency(p["stop_price"]),
        "Stop Dist": format_percent(p["stop_distance_pct"], with_sign=False),
        "Status": status,
    })

df = pd.DataFrame(table_data)


def highlight_row(row):
    """Apply row styling based on P&L and stop distance."""
    pnl_str = row["P&L %"]
    stop_dist_str = row["Stop Dist"]

    # Parse values
    try:
        pnl_pct = float(pnl_str.replace("%", "").replace("+", "")) / 100
    except ValueError:
        pnl_pct = 0

    try:
        stop_dist = float(stop_dist_str.replace("%", "")) / 100
    except ValueError:
        stop_dist = 1

    if stop_dist < 0.02:
        color = "background-color: #FEF9E7"  # Yellow
    elif pnl_pct > 0:
        color = "background-color: #E8F8F5"  # Green
    else:
        color = "background-color: #FDEDEC"  # Red

    return [color] * len(row)


styled_df = df.style.apply(highlight_row, axis=1)
st.dataframe(
    styled_df,
    use_container_width=True,
    hide_index=True,
    height=min(400, len(df) * 40 + 50),
)

st.divider()

# Two column layout for detail view and pie chart
col_left, col_right = st.columns([2, 1])

with col_left:
    # Position detail expander
    st.subheader("Position Details")

    selected_ticker = st.selectbox(
        "Select position",
        options=[p["ticker"] for p in positions],
    )

    selected_pos = next(p for p in positions if p["ticker"] == selected_ticker)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Entry Price", format_currency(selected_pos["entry_price"]))
        st.metric("Current Price", format_currency(selected_pos["current_price"]))
    with col2:
        st.metric("Shares", int(selected_pos["shares"]))
        st.metric("Cost Basis", format_currency(selected_pos["cost_basis"]))
    with col3:
        st.metric("Days Held", selected_pos["days_held"])
        st.metric("High Since Entry", format_currency(selected_pos["high_since_entry"]))

    # Entry reasons
    if selected_pos.get("reasons"):
        st.markdown("**Entry Reasons:**")
        for reason in selected_pos["reasons"]:
            st.markdown(f"- {reason}")

    # Mini price chart
    st.subheader(f"{selected_ticker} Price Chart")
    try:
        price_data = data_loader.get_price_history(selected_ticker, days=90)
        if not price_data.empty:
            fig = create_mini_price_chart(
                price_data,
                selected_ticker,
                entry_price=selected_pos["entry_price"],
                stop_price=selected_pos["stop_price"],
            )
            fig.update_layout(height=250)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Price data unavailable")
    except Exception as e:
        st.warning(f"Unable to load price chart: {str(e)}")

with col_right:
    # Position allocation pie chart
    st.subheader("Position Allocation")
    fig = create_sector_pie(positions)
    st.plotly_chart(fig, use_container_width=True)

    # Risk summary
    st.subheader("Risk Summary")

    at_risk = [p for p in positions if p["stop_distance_pct"] < 0.05]
    profitable = [p for p in positions if p["pnl_pct"] > 0]
    losing = [p for p in positions if p["pnl_pct"] <= 0]

    st.metric("Positions Near Stop (<5%)", len(at_risk))
    st.metric("Profitable Positions", len(profitable))
    st.metric("Losing Positions", len(losing))

    if at_risk:
        st.warning(f"Positions near stop: {', '.join(p['ticker'] for p in at_risk)}")

st.divider()

# Export functionality
st.subheader("Export")

csv_data = df.to_csv(index=False)
st.download_button(
    label="Download Positions CSV",
    data=csv_data,
    file_name="positions.csv",
    mime="text/csv",
)
