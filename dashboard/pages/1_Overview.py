"""Overview page - Key metrics and daily summary."""

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
from dashboard.components.alerts import display_alerts_panel, generate_all_alerts
from dashboard.components.charts import create_vix_chart
from dashboard.components.metrics import (
    display_vix_indicator,
    format_currency,
    format_percent,
    market_status_indicator,
    portfolio_metrics_row,
)
from dashboard.services.cache import get_market_status, is_market_open
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Overview - STEEX",
    page_icon="📊",
    layout="wide",
)

# Auto-refresh during market hours
if is_market_open():
    st_autorefresh(interval=60000, key="overview_refresh")

settings = get_settings()
data_loader = get_data_loader()

st.title("Overview")
st.caption("Daily summary and key metrics")

# Market status row
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    market_status = get_market_status()
    market_status_indicator(market_status)
with col2:
    vix_level = data_loader.get_current_vix()
    display_vix_indicator(vix_level)
with col3:
    from datetime import datetime
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

st.divider()

# Portfolio metrics
positions = data_loader.get_position_details()
trades = data_loader.get_all_trades()

# Calculate portfolio values
total_value = sum(p["current_value"] for p in positions) if positions else 0
total_cost = sum(p["cost_basis"] for p in positions) if positions else 0
total_pnl = sum(p["pnl_dollars"] for p in positions) if positions else 0
total_pnl_pct = (total_pnl / total_cost) if total_cost > 0 else 0

# Calculate drawdown (simplified - from entry cost)
if trades:
    equity_curve = data_loader.get_equity_curve(10000, trades)
    if equity_curve:
        equity_df = pd.DataFrame(equity_curve)
        equity_df = equity_df[equity_df["date"].notna()]
        if not equity_df.empty:
            peak = equity_df["equity"].max()
            current = equity_df["equity"].iloc[-1]
            drawdown = (current - peak) / peak if peak > 0 else 0
        else:
            drawdown = 0
    else:
        drawdown = 0
else:
    drawdown = 0

# Main metrics
st.subheader("Portfolio Summary")
portfolio_metrics_row(
    portfolio_value=total_value if total_value > 0 else 10000,
    position_count=len(positions),
    todays_pnl=total_pnl,
    todays_pnl_pct=total_pnl_pct,
    drawdown=drawdown,
)

st.divider()

# Two column layout
col_left, col_right = st.columns([3, 2])

with col_left:
    # Top positions by P&L
    st.subheader("Top Positions by P&L")

    if positions:
        sorted_positions = sorted(positions, key=lambda x: x["pnl_dollars"], reverse=True)
        top_5 = sorted_positions[:5]

        pos_data = []
        for p in top_5:
            pos_data.append({
                "Ticker": p["ticker"],
                "Current": format_currency(p["current_price"]),
                "P&L $": format_currency(p["pnl_dollars"]),
                "P&L %": format_percent(p["pnl_pct"]),
                "Days": p["days_held"],
            })

        df = pd.DataFrame(pos_data)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No active positions")

    st.divider()

    # Top signal candidates
    st.subheader("Today's Top Candidates")

    try:
        pipeline_result = data_loader.run_screening()
        candidates = pipeline_result.final_candidates[:3]

        if candidates:
            cand_data = []
            for c in candidates:
                cand_data.append({
                    "Ticker": c.ticker,
                    "Momentum 6M": format_percent(c.momentum_6m) if c.momentum_6m else "N/A",
                    "Momentum 1M": format_percent(c.momentum_1m) if c.momentum_1m else "N/A",
                    "Insider Score": f"{c.insider_score:.1f}",
                    "Buyers": c.insider_buyers,
                    "Sentiment": f"{c.sentiment_score:.0f}" if c.sentiment_score else "N/A",
                    "Sector": c.sector or "N/A",
                })

            df = pd.DataFrame(cand_data)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No candidates passed screening today")
    except Exception as e:
        st.warning(f"Unable to run screening: {str(e)}")

with col_right:
    # Alerts panel
    st.subheader("Active Alerts")

    alerts = generate_all_alerts(
        vix_level=vix_level,
        positions=positions,
        current_value=total_value if total_value > 0 else 10000,
        peak_value=total_value * 1.1 if total_value > 0 else 10000,  # Estimate
        settings=settings,
    )
    display_alerts_panel(alerts)

    st.divider()

    # Macro Sentiment
    st.subheader("Macro Sentiment")
    try:
        from src.data.geopolitical import GeopoliticalSentimentProvider
        geo_provider = GeopoliticalSentimentProvider()
        macro = geo_provider.get_macro_sentiment()

        risk_colors = {
            "low": "#27AE60",
            "medium": "#F39C12",
            "high": "#E67E22",
            "extreme": "#E74C3C",
        }
        risk_color = risk_colors.get(macro.risk_level, "#95A5A6")

        st.markdown(
            f"**Global Risk Level:** <span style='color: {risk_color}; font-weight: bold;'>"
            f"{macro.risk_level.upper()}</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"Global tone: {macro.global_tone:.1f}")

        if macro.active_events:
            st.markdown(f"**Active Events:** {len(macro.active_events)}")
            for event in macro.active_events[:2]:
                st.caption(f"- {event.event_type.replace('_', ' ').title()}")
    except Exception:
        st.info("Macro sentiment unavailable")

    st.divider()

    # VIX chart
    st.subheader("VIX Trend (30 Days)")
    vix_data = data_loader.get_vix_historical(days=30)
    if not vix_data.empty:
        fig = create_vix_chart(vix_data)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("VIX data unavailable")

st.divider()

# Recent trades summary
st.subheader("Recent Trades")

if trades:
    recent_trades = sorted(trades, key=lambda t: t.exit_date, reverse=True)[:5]

    trade_data = []
    for t in recent_trades:
        trade_data.append({
            "Ticker": t.ticker,
            "Exit Date": t.exit_date[:10],
            "P&L $": format_currency(t.pnl_dollars),
            "P&L %": format_percent(t.pnl_pct),
            "Days Held": t.hold_days,
            "Exit Reason": t.exit_reason,
        })

    df = pd.DataFrame(trade_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No completed trades yet")

# Performance summary
if trades:
    st.divider()
    st.subheader("Performance Summary")

    metrics = data_loader.get_trade_metrics(trades)

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Total Trades", metrics.get("total_trades", 0))
    with col2:
        win_rate = metrics.get("win_rate", 0) * 100
        st.metric("Win Rate", f"{win_rate:.1f}%")
    with col3:
        profit_factor = metrics.get("profit_factor", 0)
        st.metric("Profit Factor", f"{profit_factor:.2f}")
    with col4:
        avg_winner = metrics.get("avg_winner_pct", 0) * 100
        st.metric("Avg Winner", f"{avg_winner:+.1f}%")
    with col5:
        avg_loser = metrics.get("avg_loser_pct", 0) * 100
        st.metric("Avg Loser", f"{avg_loser:+.1f}%")
