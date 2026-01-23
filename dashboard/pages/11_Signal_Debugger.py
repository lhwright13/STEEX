"""Signal Debugger - Understand why stocks pass or fail screening."""

import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.settings import get_settings
from dashboard.components.charts import create_price_chart
from dashboard.components.metrics import format_currency, format_percent
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Signal Debugger - STEEX",
    page_icon="🔍",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

st.title("Signal Debugger")
st.caption("Understand exactly why a stock passes or fails each screening stage")

st.divider()

# Ticker input
ticker = st.text_input("Enter Ticker Symbol", value="AAPL").upper()

if st.button("Analyze", type="primary") or ticker:

    with st.spinner(f"Analyzing {ticker}..."):

        # Fetch all necessary data
        try:
            price_df = data_loader.get_price_history(ticker, days=365)
        except Exception:
            price_df = pd.DataFrame()

        if price_df.empty:
            st.error(f"Could not fetch data for {ticker}")
            st.stop()

        # Current price
        current_price = price_df["Close"].iloc[-1] if "Close" in price_df.columns else None

        st.subheader(f"Analysis for {ticker}")

        if current_price:
            st.metric("Current Price", format_currency(current_price))

        # Stage-by-stage analysis
        st.divider()

        # STAGE 1: Universe Filter
        st.subheader("Stage 1: Universe Filter")

        stage1_results = []

        # Price check
        min_price = settings.min_price
        price_pass = current_price >= min_price if current_price else False
        stage1_results.append({
            "Check": "Minimum Price",
            "Requirement": f">= ${min_price:.2f}",
            "Actual": format_currency(current_price) if current_price else "N/A",
            "Pass": "PASS" if price_pass else "FAIL",
        })

        # Volume check
        if "Volume" in price_df.columns:
            avg_volume = price_df["Volume"].tail(20).mean()
            volume_pass = avg_volume >= settings.min_volume
            stage1_results.append({
                "Check": "Minimum Volume (20-day avg)",
                "Requirement": f">= {settings.min_volume:,}",
                "Actual": f"{avg_volume:,.0f}",
                "Pass": "PASS" if volume_pass else "FAIL",
            })
        else:
            volume_pass = False
            stage1_results.append({
                "Check": "Minimum Volume",
                "Requirement": f">= {settings.min_volume:,}",
                "Actual": "N/A",
                "Pass": "FAIL",
            })

        # Earnings blackout (simplified - just show the check)
        stage1_results.append({
            "Check": "No Earnings in Next 5 Days",
            "Requirement": "No earnings within blackout",
            "Actual": "Check manually",
            "Pass": "CHECK",
        })

        df1 = pd.DataFrame(stage1_results)
        st.dataframe(df1, use_container_width=True, hide_index=True)

        stage1_pass = price_pass and volume_pass

        if stage1_pass:
            st.success("Stage 1: PASSED")
        else:
            st.error("Stage 1: FAILED")
            st.caption("Stock would be filtered out at Stage 1")

        # STAGE 2: Momentum Filter
        st.divider()
        st.subheader("Stage 2: Momentum Filter")

        stage2_results = []

        if "Close" in price_df.columns and len(price_df) >= settings.momentum_lookback_days:
            closes = price_df["Close"]

            # 6-month momentum
            mom_6m = (closes.iloc[-1] - closes.iloc[-126]) / closes.iloc[-126] if len(closes) >= 126 else None
            mom_6m_pass = mom_6m >= settings.momentum_min_return if mom_6m is not None else False
            stage2_results.append({
                "Check": "6-Month Momentum",
                "Requirement": f">= {settings.momentum_min_return * 100:.0f}%",
                "Actual": format_percent(mom_6m) if mom_6m else "N/A",
                "Pass": "PASS" if mom_6m_pass else "FAIL",
            })

            # 1-month momentum
            mom_1m = (closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21] if len(closes) >= 21 else None
            mom_1m_pass = mom_1m >= 0.05 if mom_1m is not None else False  # 5% requirement
            stage2_results.append({
                "Check": "1-Month Momentum",
                "Requirement": ">= 5%",
                "Actual": format_percent(mom_1m) if mom_1m else "N/A",
                "Pass": "PASS" if mom_1m_pass else "FAIL",
            })

            # 50-day MA
            ma_50 = closes.tail(50).mean()
            above_ma50 = closes.iloc[-1] > ma_50
            stage2_results.append({
                "Check": "Above 50-Day MA",
                "Requirement": f"Price > {format_currency(ma_50)}",
                "Actual": format_currency(closes.iloc[-1]),
                "Pass": "PASS" if above_ma50 else "FAIL",
            })

            # 200-day MA
            if len(closes) >= 200:
                ma_200 = closes.tail(200).mean()
                above_ma200 = closes.iloc[-1] > ma_200
                stage2_results.append({
                    "Check": "Above 200-Day MA",
                    "Requirement": f"Price > {format_currency(ma_200)}",
                    "Actual": format_currency(closes.iloc[-1]),
                    "Pass": "PASS" if above_ma200 else "FAIL",
                })
            else:
                above_ma200 = False
                stage2_results.append({
                    "Check": "Above 200-Day MA",
                    "Requirement": "N/A (insufficient data)",
                    "Actual": "N/A",
                    "Pass": "FAIL",
                })

            # Overextension check (simplified)
            stage2_results.append({
                "Check": "Not Overextended (top 5%)",
                "Requirement": "Not in top 5% of momentum",
                "Actual": "Requires universe comparison",
                "Pass": "CHECK",
            })

        else:
            mom_6m_pass = False
            mom_1m_pass = False
            above_ma50 = False
            above_ma200 = False
            stage2_results.append({
                "Check": "Momentum Checks",
                "Requirement": "Need 126+ days of data",
                "Actual": f"{len(price_df)} days available",
                "Pass": "FAIL",
            })

        df2 = pd.DataFrame(stage2_results)
        st.dataframe(df2, use_container_width=True, hide_index=True)

        stage2_pass = mom_6m_pass and mom_1m_pass and above_ma50 and above_ma200

        if stage2_pass:
            st.success("Stage 2: PASSED")
        else:
            st.error("Stage 2: FAILED")
            failures = []
            if not mom_6m_pass:
                failures.append("6-month momentum too low")
            if not mom_1m_pass:
                failures.append("1-month momentum too low")
            if not above_ma50:
                failures.append("Below 50-day MA")
            if not above_ma200:
                failures.append("Below 200-day MA")
            st.caption(f"Reasons: {', '.join(failures)}")

        # STAGE 3: Insider Filter
        st.divider()
        st.subheader("Stage 3: Insider Filter")

        st.info(
            "Insider data requires SEC filing scan. "
            "Run the full screening pipeline to check insider activity."
        )

        stage3_results = []
        stage3_results.append({
            "Check": "CEO/CFO Purchase",
            "Requirement": "Any C-suite purchase in last 30 days",
            "Actual": "Check via full scan",
            "Pass": "CHECK",
        })
        stage3_results.append({
            "Check": "Cluster Buy (3+ insiders)",
            "Requirement": f">= {settings.min_cluster_buyers} unique buyers",
            "Actual": "Check via full scan",
            "Pass": "CHECK",
        })
        stage3_results.append({
            "Check": "High-Value Purchase",
            "Requirement": f">= {format_currency(settings.min_purchase_value)}",
            "Actual": "Check via full scan",
            "Pass": "CHECK",
        })

        df3 = pd.DataFrame(stage3_results)
        st.dataframe(df3, use_container_width=True, hide_index=True)

        # Visual analysis
        st.divider()
        st.subheader("Technical Analysis")

        if "Close" in price_df.columns and len(price_df) >= 50:
            fig = go.Figure()

            # Price
            fig.add_trace(go.Candlestick(
                x=price_df.index,
                open=price_df["Open"],
                high=price_df["High"],
                low=price_df["Low"],
                close=price_df["Close"],
                name=ticker,
            ))

            # 50-day MA
            ma_50 = price_df["Close"].rolling(window=50).mean()
            fig.add_trace(go.Scatter(
                x=price_df.index,
                y=ma_50,
                name="50-Day MA",
                line=dict(color="orange", width=1),
            ))

            # 200-day MA
            if len(price_df) >= 200:
                ma_200 = price_df["Close"].rolling(window=200).mean()
                fig.add_trace(go.Scatter(
                    x=price_df.index,
                    y=ma_200,
                    name="200-Day MA",
                    line=dict(color="purple", width=1),
                ))

            fig.update_layout(
                title=f"{ticker} - Price with Moving Averages",
                xaxis_title="Date",
                yaxis_title="Price",
                template="plotly_white",
                height=450,
                xaxis_rangeslider_visible=False,
            )

            st.plotly_chart(fig, use_container_width=True)

        # Summary
        st.divider()
        st.subheader("Summary")

        col1, col2, col3 = st.columns(3)

        with col1:
            if stage1_pass:
                st.success("Stage 1: PASS")
            else:
                st.error("Stage 1: FAIL")

        with col2:
            if stage2_pass:
                st.success("Stage 2: PASS")
            else:
                st.error("Stage 2: FAIL")

        with col3:
            st.info("Stage 3: Needs Scan")

        # Recommendation
        st.divider()

        if stage1_pass and stage2_pass:
            st.success(
                f"**{ticker} passes technical screens.** "
                "Run full pipeline to check insider activity."
            )
        else:
            st.warning(
                f"**{ticker} does not currently meet screening criteria.** "
                "See failed checks above for details."
            )

        # What would need to change?
        if not stage2_pass:
            st.subheader("What Would Need to Change?")

            changes = []

            if mom_6m is not None and not mom_6m_pass:
                needed_price = price_df["Close"].iloc[-126] * (1 + settings.momentum_min_return)
                changes.append(f"Price needs to reach {format_currency(needed_price)} for 6M momentum requirement")

            if not above_ma50:
                changes.append(f"Price needs to rise above 50-day MA ({format_currency(ma_50)})")

            if not above_ma200 and len(price_df) >= 200:
                changes.append(f"Price needs to rise above 200-day MA ({format_currency(ma_200)})")

            for change in changes:
                st.markdown(f"- {change}")

st.divider()

# Quick lookup for multiple tickers
st.subheader("Batch Check")
st.caption("Check multiple tickers at once")

tickers_input = st.text_area(
    "Enter tickers (one per line or comma-separated)",
    placeholder="AAPL\nMSFT\nGOOG",
)

if st.button("Check All") and tickers_input:
    # Parse tickers
    tickers = [t.strip().upper() for t in tickers_input.replace(",", "\n").split("\n") if t.strip()]

    results = []

    progress = st.progress(0)

    for i, t in enumerate(tickers):
        try:
            df = data_loader.get_price_history(t, days=365)

            if df.empty or "Close" not in df.columns:
                results.append({
                    "Ticker": t,
                    "Status": "No Data",
                    "Price": "N/A",
                    "6M Mom": "N/A",
                    "Above 50MA": "N/A",
                    "Above 200MA": "N/A",
                })
                continue

            closes = df["Close"]
            current = closes.iloc[-1]

            # Calculate metrics
            mom_6m = (closes.iloc[-1] - closes.iloc[-126]) / closes.iloc[-126] if len(closes) >= 126 else None
            ma_50 = closes.tail(50).mean()
            ma_200 = closes.tail(200).mean() if len(closes) >= 200 else None

            above_50 = current > ma_50
            above_200 = current > ma_200 if ma_200 else False

            # Determine status
            passes = []
            if current >= settings.min_price:
                passes.append("price")
            if mom_6m and mom_6m >= settings.momentum_min_return:
                passes.append("momentum")
            if above_50:
                passes.append("50ma")
            if above_200:
                passes.append("200ma")

            status = "POTENTIAL" if len(passes) >= 3 else "UNLIKELY"

            results.append({
                "Ticker": t,
                "Status": status,
                "Price": format_currency(current),
                "6M Mom": format_percent(mom_6m) if mom_6m else "N/A",
                "Above 50MA": "Yes" if above_50 else "No",
                "Above 200MA": "Yes" if above_200 else "No",
            })

        except Exception:
            results.append({
                "Ticker": t,
                "Status": "Error",
                "Price": "N/A",
                "6M Mom": "N/A",
                "Above 50MA": "N/A",
                "Above 200MA": "N/A",
            })

        progress.progress((i + 1) / len(tickers))

    df = pd.DataFrame(results)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Summary
    potential = [r for r in results if r["Status"] == "POTENTIAL"]
    st.write(f"**{len(potential)} of {len(results)} tickers show potential** (pass 3+ technical checks)")
