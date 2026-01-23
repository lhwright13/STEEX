"""Trade History page - Completed trades log."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from config.settings import get_settings
from dashboard.components.metrics import format_currency, format_percent
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Trade History - STEEX",
    page_icon="📋",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

st.title("Trade History")
st.caption("Complete log of all closed trades")

# Get all trades
all_trades = data_loader.get_all_trades()

if not all_trades:
    st.info("No completed trades yet.")
    st.stop()

st.divider()

# Filters
st.subheader("Filters")

col1, col2, col3 = st.columns(3)

with col1:
    # Date range filter
    all_exit_dates = [datetime.fromisoformat(t.exit_date) for t in all_trades]
    min_date = min(all_exit_dates).date()
    max_date = max(all_exit_dates).date()

    date_range = st.date_input(
        "Date Range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = min_date
        end_date = max_date

with col2:
    # Exit reason filter
    exit_reasons = list(set(t.exit_reason for t in all_trades))
    exit_reasons.insert(0, "All")

    selected_reason = st.selectbox(
        "Exit Reason",
        options=exit_reasons,
    )

with col3:
    # P&L filter
    pnl_filter = st.selectbox(
        "P&L Filter",
        options=["All", "Winners Only", "Losers Only"],
    )

# Apply filters
filtered_trades = all_trades

# Date filter
start_dt = datetime.combine(start_date, datetime.min.time())
end_dt = datetime.combine(end_date, datetime.max.time())
filtered_trades = [
    t for t in filtered_trades
    if start_dt <= datetime.fromisoformat(t.exit_date) <= end_dt
]

# Exit reason filter
if selected_reason != "All":
    filtered_trades = [t for t in filtered_trades if t.exit_reason == selected_reason]

# P&L filter
if pnl_filter == "Winners Only":
    filtered_trades = [t for t in filtered_trades if t.pnl_dollars > 0]
elif pnl_filter == "Losers Only":
    filtered_trades = [t for t in filtered_trades if t.pnl_dollars <= 0]

st.divider()

# Summary stats for filtered trades
st.subheader(f"Filtered Results ({len(filtered_trades)} trades)")

if filtered_trades:
    metrics = data_loader.get_trade_metrics(filtered_trades)

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Trades", len(filtered_trades))
    with col2:
        st.metric("Winners", metrics.get("winners", 0))
    with col3:
        st.metric("Losers", metrics.get("losers", 0))
    with col4:
        st.metric("Win Rate", format_percent(metrics.get("win_rate", 0), with_sign=False))
    with col5:
        st.metric("Total P&L", format_currency(metrics.get("total_pnl", 0)))

st.divider()

# Trade table
st.subheader("Trade Log")

# Sort options
col1, col2 = st.columns([1, 4])
with col1:
    sort_by = st.selectbox(
        "Sort by",
        options=["Exit Date", "Entry Date", "P&L $", "P&L %", "Days Held", "Ticker"],
    )

sort_map = {
    "Exit Date": ("exit_date", True),
    "Entry Date": ("entry_date", True),
    "P&L $": ("pnl_dollars", True),
    "P&L %": ("pnl_pct", True),
    "Days Held": ("hold_days", True),
    "Ticker": ("ticker", False),
}

sort_field, reverse = sort_map[sort_by]
sorted_trades = sorted(
    filtered_trades,
    key=lambda t: getattr(t, sort_field),
    reverse=reverse,
)

# Create table data
table_data = []
for t in sorted_trades:
    table_data.append({
        "Ticker": t.ticker,
        "Entry Date": t.entry_date[:10],
        "Exit Date": t.exit_date[:10],
        "Entry Price": format_currency(t.entry_price),
        "Exit Price": format_currency(t.exit_price),
        "Shares": int(t.shares),
        "Cost Basis": format_currency(t.cost_basis),
        "Proceeds": format_currency(t.proceeds),
        "P&L $": format_currency(t.pnl_dollars),
        "P&L %": format_percent(t.pnl_pct),
        "Days Held": t.hold_days,
        "Exit Reason": t.exit_reason,
        "Score": f"{t.score:.1f}",
    })

df = pd.DataFrame(table_data)


def highlight_pnl(val):
    """Highlight P&L cells based on value."""
    if isinstance(val, str):
        if val.startswith("-") or val.startswith("($"):
            return "background-color: #FDEDEC"
        elif val.startswith("+") or (val.startswith("$") and not val.startswith("$-") and not val.startswith("$0")):
            return "background-color: #E8F8F5"
    return ""


styled_df = df.style.applymap(
    highlight_pnl,
    subset=["P&L $", "P&L %"],
)

st.dataframe(
    styled_df,
    use_container_width=True,
    hide_index=True,
    height=min(600, len(df) * 40 + 50),
)

st.divider()

# Best and worst trades in filtered set
if len(filtered_trades) >= 2:
    st.subheader("Best and Worst in Selection")

    sorted_by_pnl = sorted(filtered_trades, key=lambda t: t.pnl_pct if t.pnl_pct else 0, reverse=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Best Trade**")
        best = sorted_by_pnl[0]
        st.metric("Ticker", best.ticker)
        st.metric("Return", format_percent(best.pnl_pct))
        st.metric("P&L", format_currency(best.pnl_dollars))
        st.caption(f"{best.entry_date[:10]} - {best.exit_date[:10]} ({best.hold_days} days)")

    with col2:
        st.markdown("**Worst Trade**")
        worst = sorted_by_pnl[-1]
        st.metric("Ticker", worst.ticker)
        st.metric("Return", format_percent(worst.pnl_pct))
        st.metric("P&L", format_currency(worst.pnl_dollars))
        st.caption(f"{worst.entry_date[:10]} - {worst.exit_date[:10]} ({worst.hold_days} days)")

st.divider()

# Export functionality
st.subheader("Export")

col1, col2 = st.columns(2)

with col1:
    # Export filtered trades
    if filtered_trades:
        csv_data = df.to_csv(index=False)
        st.download_button(
            label="Download Filtered Trades CSV",
            data=csv_data,
            file_name=f"trades_{start_date}_{end_date}.csv",
            mime="text/csv",
        )

with col2:
    # Export all trades
    all_data = []
    for t in all_trades:
        all_data.append({
            "ticker": t.ticker,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "shares": t.shares,
            "cost_basis": t.cost_basis,
            "proceeds": t.proceeds,
            "pnl_dollars": t.pnl_dollars,
            "pnl_pct": t.pnl_pct,
            "hold_days": t.hold_days,
            "exit_reason": t.exit_reason,
            "score": t.score,
            "reasons": "; ".join(t.reasons) if t.reasons else "",
        })

    all_df = pd.DataFrame(all_data)
    all_csv = all_df.to_csv(index=False)
    st.download_button(
        label="Download All Trades CSV",
        data=all_csv,
        file_name="all_trades.csv",
        mime="text/csv",
    )

st.divider()

# Trade details expander
with st.expander("View Trade Details"):
    selected_trade = st.selectbox(
        "Select trade",
        options=[f"{t.ticker} ({t.exit_date[:10]})" for t in sorted_trades],
    )

    if selected_trade:
        # Parse selection
        ticker = selected_trade.split(" (")[0]
        exit_date = selected_trade.split("(")[1].rstrip(")")

        trade = next(
            t for t in sorted_trades
            if t.ticker == ticker and t.exit_date.startswith(exit_date)
        )

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**Entry Details**")
            st.write(f"Date: {trade.entry_date[:10]}")
            st.write(f"Price: {format_currency(trade.entry_price)}")
            st.write(f"Shares: {int(trade.shares)}")
            st.write(f"Cost: {format_currency(trade.cost_basis)}")

        with col2:
            st.markdown("**Exit Details**")
            st.write(f"Date: {trade.exit_date[:10]}")
            st.write(f"Price: {format_currency(trade.exit_price)}")
            st.write(f"Proceeds: {format_currency(trade.proceeds)}")
            st.write(f"Reason: {trade.exit_reason}")

        with col3:
            st.markdown("**Performance**")
            st.write(f"P&L: {format_currency(trade.pnl_dollars)}")
            st.write(f"Return: {format_percent(trade.pnl_pct)}")
            st.write(f"Days Held: {trade.hold_days}")
            st.write(f"Score: {trade.score:.1f}")

        if trade.reasons:
            st.markdown("**Entry Reasons:**")
            for reason in trade.reasons:
                st.write(f"- {reason}")
