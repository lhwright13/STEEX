"""Performance page - Historical returns and analytics."""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from config.settings import get_settings
from dashboard.components.charts import (
    create_drawdown_chart,
    create_equity_curve,
    create_exit_reason_chart,
    create_hold_time_histogram,
    create_monthly_heatmap,
    create_pnl_distribution,
)
from dashboard.components.metrics import (
    format_currency,
    format_percent,
    performance_metrics_row,
    trade_metrics_row,
)
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Performance - STEEX",
    page_icon="📈",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

st.title("Performance Analytics")
st.caption("Historical returns, metrics, and trade analysis")

# Get trade data
trades = data_loader.get_all_trades()

if not trades:
    st.info("No completed trades yet. Performance data will appear once you have closed trades.")
    st.stop()

# Starting capital input
starting_capital = st.number_input(
    "Starting Capital",
    min_value=1000,
    max_value=10000000,
    value=10000,
    step=1000,
    help="Enter your starting capital for equity curve calculation",
)

st.divider()

# Key metrics
st.subheader("Key Performance Metrics")

metrics = data_loader.get_trade_metrics(trades)
performance_metrics_row(metrics)

st.divider()

# Equity curve
st.subheader("Equity Curve")

equity_data = data_loader.get_equity_curve(starting_capital, trades)

if equity_data:
    equity_df = pd.DataFrame(equity_data)
    equity_df = equity_df[equity_df["date"].notna()]

    if not equity_df.empty:
        # Get SPY comparison
        equity_df["date"] = pd.to_datetime(equity_df["date"])
        equity_df = equity_df.set_index("date")

        spy_data = data_loader.get_spy_comparison(equity_df, starting_capital)

        fig = create_equity_curve(equity_data, spy_data)
        st.plotly_chart(fig, use_container_width=True)

        # Drawdown chart
        fig_dd = create_drawdown_chart(equity_data)
        st.plotly_chart(fig_dd, use_container_width=True)

        # Summary stats
        col1, col2, col3, col4 = st.columns(4)

        start_equity = equity_df["equity"].iloc[0]
        end_equity = equity_df["equity"].iloc[-1]
        total_return = (end_equity - start_equity) / start_equity

        with col1:
            st.metric("Starting Value", format_currency(start_equity))
        with col2:
            st.metric("Ending Value", format_currency(end_equity))
        with col3:
            st.metric("Total Return", format_percent(total_return))
        with col4:
            # Calculate total P&L
            total_pnl = sum(t.pnl_dollars for t in trades)
            st.metric("Total P&L", format_currency(total_pnl))
else:
    st.info("Insufficient data for equity curve")

st.divider()

# Trade statistics
st.subheader("Trade Statistics")
trade_metrics_row(metrics)

col1, col2 = st.columns(2)

with col1:
    st.metric("Profit Factor", f"{metrics.get('profit_factor', 0):.2f}")
    st.metric("Average Hold Days", f"{metrics.get('avg_hold_days', 0):.1f}")

with col2:
    st.metric("Total P&L", format_currency(metrics.get("total_pnl", 0)))
    st.metric("Average P&L %", format_percent(metrics.get("avg_pnl_pct", 0)))

st.divider()

# Monthly returns heatmap
st.subheader("Monthly Returns")

monthly_summary = data_loader.get_monthly_summary(trades)

if monthly_summary:
    fig = create_monthly_heatmap(monthly_summary)
    st.plotly_chart(fig, use_container_width=True)

    # Monthly details table
    with st.expander("Monthly Details"):
        monthly_data = []
        for month, data in sorted(monthly_summary.items(), reverse=True):
            monthly_data.append({
                "Month": month,
                "Trades": data.get("total_trades", 0),
                "Winners": data.get("winners", 0),
                "Losers": data.get("losers", 0),
                "Win Rate": format_percent(data.get("win_rate", 0), with_sign=False),
                "Total P&L": format_currency(data.get("total_pnl", 0)),
                "Avg P&L %": format_percent(data.get("avg_pnl_pct", 0)),
            })

        df = pd.DataFrame(monthly_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("Insufficient data for monthly breakdown")

st.divider()

# Exit reason analysis
st.subheader("Exit Reason Analysis")

exit_breakdown = data_loader.get_exit_reason_breakdown(trades)

if exit_breakdown:
    fig = create_exit_reason_chart(exit_breakdown)
    st.plotly_chart(fig, use_container_width=True)

    # Exit details table
    with st.expander("Exit Reason Details"):
        exit_data = []
        for reason, data in exit_breakdown.items():
            exit_data.append({
                "Exit Reason": reason,
                "Count": data.get("count", 0),
                "Winners": data.get("winners", 0),
                "Losers": data.get("losers", 0),
                "Win Rate": format_percent(data.get("win_rate", 0), with_sign=False),
                "Total P&L": format_currency(data.get("total_pnl", 0)),
            })

        df = pd.DataFrame(exit_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No exit data available")

st.divider()

# Distribution charts
st.subheader("Trade Distributions")

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Return Distribution**")
    fig = create_pnl_distribution(trades)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.markdown("**Trade Duration Distribution**")
    fig = create_hold_time_histogram(trades)
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# Best and worst trades
st.subheader("Best and Worst Trades")

sorted_by_pnl = sorted(trades, key=lambda t: t.pnl_pct if t.pnl_pct else 0, reverse=True)

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Top 5 Best Trades**")
    best = sorted_by_pnl[:5]
    best_data = []
    for t in best:
        best_data.append({
            "Ticker": t.ticker,
            "Entry": t.entry_date[:10],
            "Exit": t.exit_date[:10],
            "Return": format_percent(t.pnl_pct),
            "P&L": format_currency(t.pnl_dollars),
            "Days": t.hold_days,
        })
    df = pd.DataFrame(best_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

with col2:
    st.markdown("**Top 5 Worst Trades**")
    worst = sorted_by_pnl[-5:][::-1]
    worst_data = []
    for t in worst:
        worst_data.append({
            "Ticker": t.ticker,
            "Entry": t.entry_date[:10],
            "Exit": t.exit_date[:10],
            "Return": format_percent(t.pnl_pct),
            "P&L": format_currency(t.pnl_dollars),
            "Days": t.hold_days,
        })
    df = pd.DataFrame(worst_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# Risk metrics summary
st.subheader("Risk Metrics Summary")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Sharpe Ratio",
        f"{metrics.get('sharpe_ratio', 0):.2f}",
        help="Risk-adjusted return (> 1 is good, > 2 is excellent)",
    )
with col2:
    st.metric(
        "Sortino Ratio",
        f"{metrics.get('sortino_ratio', 0):.2f}",
        help="Downside risk-adjusted return",
    )
with col3:
    st.metric(
        "Calmar Ratio",
        f"{metrics.get('calmar_ratio', 0):.2f}",
        help="CAGR / Max Drawdown",
    )
with col4:
    st.metric(
        "Max Drawdown",
        format_percent(-metrics.get('max_drawdown_pct', 0), with_sign=False),
        help="Maximum peak-to-trough decline",
    )
