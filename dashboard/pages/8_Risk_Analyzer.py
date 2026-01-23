"""Risk Analyzer - Portfolio risk analysis and position sizing."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config.settings import get_settings
from dashboard.components.metrics import format_currency, format_percent
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Risk Analyzer - STEEX",
    page_icon="🎯",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

st.title("Risk Analyzer")
st.caption("Portfolio risk analysis, correlations, and position sizing")

# Get position data
positions = data_loader.get_position_details()

st.divider()

# Position Sizing Calculator
st.subheader("Position Sizing Calculator")
st.caption("Calculate optimal position size based on risk parameters")

col1, col2, col3 = st.columns(3)

with col1:
    portfolio_value = st.number_input(
        "Portfolio Value ($)",
        min_value=1000,
        max_value=10000000,
        value=100000,
        step=1000,
    )

    risk_per_trade = st.slider(
        "Risk Per Trade (%)",
        min_value=0.5,
        max_value=5.0,
        value=1.0,
        step=0.25,
        help="Maximum loss you're willing to take on a single trade",
    )

with col2:
    entry_price = st.number_input(
        "Entry Price ($)",
        min_value=0.01,
        max_value=10000.0,
        value=100.0,
        step=0.01,
    )

    stop_price = st.number_input(
        "Stop Price ($)",
        min_value=0.01,
        max_value=10000.0,
        value=88.0,
        step=0.01,
    )

with col3:
    # Calculate
    risk_amount = portfolio_value * (risk_per_trade / 100)
    risk_per_share = entry_price - stop_price
    stop_pct = (entry_price - stop_price) / entry_price * 100

    if risk_per_share > 0:
        shares = int(risk_amount / risk_per_share)
        position_value = shares * entry_price
        position_pct = position_value / portfolio_value * 100
    else:
        shares = 0
        position_value = 0
        position_pct = 0

    st.metric("Recommended Shares", f"{shares:,}")
    st.metric("Position Value", format_currency(position_value))
    st.metric("Position Size", f"{position_pct:.1f}% of portfolio")

st.caption(f"Stop Distance: {stop_pct:.1f}% | Max Loss: {format_currency(risk_amount)}")

st.divider()

# Current Portfolio Risk Analysis
st.subheader("Current Portfolio Risk")

if not positions:
    st.info("No active positions. Add positions to see risk analysis.")
else:
    # Calculate portfolio metrics
    total_value = sum(p["current_value"] for p in positions)
    total_at_risk = sum(
        p["current_value"] - (p["stop_price"] * p["shares"])
        for p in positions
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Exposure", format_currency(total_value))
    with col2:
        st.metric("Amount at Risk", format_currency(total_at_risk))
    with col3:
        risk_pct = total_at_risk / portfolio_value * 100 if portfolio_value > 0 else 0
        st.metric("Portfolio Risk", f"{risk_pct:.1f}%")
    with col4:
        avg_stop_dist = np.mean([p["stop_distance_pct"] for p in positions]) * 100
        st.metric("Avg Stop Distance", f"{avg_stop_dist:.1f}%")

    # Position risk table
    st.subheader("Position Risk Breakdown")

    risk_data = []
    for p in positions:
        value_at_risk = p["current_value"] - (p["stop_price"] * p["shares"])
        risk_data.append({
            "Ticker": p["ticker"],
            "Value": format_currency(p["current_value"]),
            "Weight": f"{p['current_value'] / total_value * 100:.1f}%",
            "Stop Dist": f"{p['stop_distance_pct'] * 100:.1f}%",
            "Value at Risk": format_currency(value_at_risk),
            "Risk Contrib": f"{value_at_risk / total_at_risk * 100:.1f}%" if total_at_risk > 0 else "0%",
        })

    df = pd.DataFrame(risk_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Concentration chart
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Position Concentration")

        fig = px.pie(
            values=[p["current_value"] for p in positions],
            names=[p["ticker"] for p in positions],
            hole=0.4,
        )
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Risk Contribution")

        risk_values = [
            max(0, p["current_value"] - (p["stop_price"] * p["shares"]))
            for p in positions
        ]

        fig = px.pie(
            values=risk_values,
            names=[p["ticker"] for p in positions],
            hole=0.4,
        )
        fig.update_layout(height=350)
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# Correlation Analysis
st.subheader("Correlation Analysis")
st.caption("Understanding how your positions move together")

if len(positions) >= 2:
    tickers = [p["ticker"] for p in positions]

    with st.spinner("Fetching price data for correlation analysis..."):
        # Get price history for all positions
        price_data = {}
        for ticker in tickers:
            df = data_loader.get_price_history(ticker, days=90)
            if not df.empty and "Close" in df.columns:
                price_data[ticker] = df["Close"]

    if len(price_data) >= 2:
        # Create returns DataFrame
        returns_df = pd.DataFrame(price_data).pct_change().dropna()

        if len(returns_df) > 10:
            # Correlation matrix
            corr_matrix = returns_df.corr()

            fig = px.imshow(
                corr_matrix,
                labels=dict(color="Correlation"),
                x=corr_matrix.columns,
                y=corr_matrix.columns,
                color_continuous_scale="RdBu_r",
                zmin=-1,
                zmax=1,
            )
            fig.update_layout(title="90-Day Return Correlations", height=400)
            st.plotly_chart(fig, use_container_width=True)

            # Correlation insights
            st.subheader("Correlation Insights")

            # Find highly correlated pairs
            high_corr_pairs = []
            for i in range(len(corr_matrix.columns)):
                for j in range(i + 1, len(corr_matrix.columns)):
                    corr = corr_matrix.iloc[i, j]
                    if abs(corr) > 0.7:
                        high_corr_pairs.append({
                            "Pair": f"{corr_matrix.columns[i]} - {corr_matrix.columns[j]}",
                            "Correlation": f"{corr:.2f}",
                            "Risk": "High" if corr > 0.7 else "Hedge",
                        })

            if high_corr_pairs:
                st.warning("Highly correlated positions detected:")
                df = pd.DataFrame(high_corr_pairs)
                st.dataframe(df, use_container_width=True, hide_index=True)

                st.caption(
                    "Positions with correlation > 0.7 tend to move together, "
                    "increasing portfolio risk. Consider diversifying."
                )
            else:
                st.success("No highly correlated pairs detected. Portfolio appears diversified.")

            # Average correlation
            avg_corr = corr_matrix.values[np.triu_indices(len(corr_matrix), k=1)].mean()
            st.metric("Average Pairwise Correlation", f"{avg_corr:.2f}")
        else:
            st.info("Insufficient data for correlation analysis")
    else:
        st.warning("Could not fetch price data for all positions")
else:
    st.info("Need at least 2 positions for correlation analysis")

st.divider()

# Beta Analysis
st.subheader("Beta Analysis")
st.caption("How your positions move relative to the market (SPY)")

if positions:
    with st.spinner("Calculating betas..."):
        # Get SPY data
        spy_df = data_loader.get_price_history("SPY", days=90)

        if not spy_df.empty and "Close" in spy_df.columns:
            spy_returns = spy_df["Close"].pct_change().dropna()

            beta_data = []
            for ticker in [p["ticker"] for p in positions]:
                df = data_loader.get_price_history(ticker, days=90)
                if not df.empty and "Close" in df.columns:
                    stock_returns = df["Close"].pct_change().dropna()

                    # Align dates
                    common_idx = stock_returns.index.intersection(spy_returns.index)
                    if len(common_idx) > 20:
                        aligned_stock = stock_returns.loc[common_idx]
                        aligned_spy = spy_returns.loc[common_idx]

                        # Calculate beta
                        covariance = aligned_stock.cov(aligned_spy)
                        variance = aligned_spy.var()
                        beta = covariance / variance if variance > 0 else 0

                        # Calculate correlation
                        correlation = aligned_stock.corr(aligned_spy)

                        beta_data.append({
                            "Ticker": ticker,
                            "Beta": beta,
                            "Correlation to SPY": correlation,
                        })

            if beta_data:
                df = pd.DataFrame(beta_data)

                # Add interpretation
                df["Interpretation"] = df["Beta"].apply(
                    lambda b: "Defensive" if b < 0.8 else "Market-like" if b < 1.2 else "Aggressive"
                )

                st.dataframe(
                    df.style.format({"Beta": "{:.2f}", "Correlation to SPY": "{:.2f}"}),
                    use_container_width=True,
                    hide_index=True,
                )

                # Portfolio beta
                weights = [p["current_value"] for p in positions]
                total_weight = sum(weights)
                betas = [next((b["Beta"] for b in beta_data if b["Ticker"] == p["ticker"]), 1.0) for p in positions]
                portfolio_beta = sum(w * b for w, b in zip(weights, betas)) / total_weight if total_weight > 0 else 1.0

                col1, col2 = st.columns(2)
                with col1:
                    st.metric(
                        "Portfolio Beta",
                        f"{portfolio_beta:.2f}",
                        help="Beta < 1 means less volatile than market, > 1 means more volatile",
                    )
                with col2:
                    interp = "Defensive" if portfolio_beta < 0.8 else "Market-like" if portfolio_beta < 1.2 else "Aggressive"
                    st.metric("Portfolio Style", interp)
        else:
            st.warning("Could not fetch SPY data for beta analysis")

st.divider()

# Value at Risk (VaR)
st.subheader("Value at Risk (VaR)")
st.caption("Estimated worst-case loss at different confidence levels")

if positions:
    total_value = sum(p["current_value"] for p in positions)

    # Get combined returns
    with st.spinner("Calculating VaR..."):
        all_returns = []

        for p in positions:
            df = data_loader.get_price_history(p["ticker"], days=252)
            if not df.empty and "Close" in df.columns:
                returns = df["Close"].pct_change().dropna()
                weight = p["current_value"] / total_value
                all_returns.append(returns * weight)

        if all_returns:
            # Combine weighted returns
            combined_df = pd.concat(all_returns, axis=1).sum(axis=1)

            if len(combined_df) > 20:
                # Calculate VaR at different confidence levels
                var_95 = np.percentile(combined_df, 5) * total_value
                var_99 = np.percentile(combined_df, 1) * total_value

                # Expected shortfall (CVaR)
                es_95 = combined_df[combined_df <= np.percentile(combined_df, 5)].mean() * total_value

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        "1-Day VaR (95%)",
                        format_currency(abs(var_95)),
                        help="You have a 5% chance of losing more than this in a day",
                    )
                with col2:
                    st.metric(
                        "1-Day VaR (99%)",
                        format_currency(abs(var_99)),
                        help="You have a 1% chance of losing more than this in a day",
                    )
                with col3:
                    st.metric(
                        "Expected Shortfall (95%)",
                        format_currency(abs(es_95)),
                        help="Average loss when VaR is exceeded",
                    )

                # Return distribution
                fig = go.Figure()
                fig.add_trace(go.Histogram(
                    x=combined_df * 100,
                    nbinsx=50,
                    name="Daily Returns",
                ))

                fig.add_vline(x=var_95 / total_value * 100, line_dash="dash", line_color="orange",
                              annotation_text="95% VaR")
                fig.add_vline(x=var_99 / total_value * 100, line_dash="dash", line_color="red",
                              annotation_text="99% VaR")

                fig.update_layout(
                    title="Portfolio Return Distribution (252 days)",
                    xaxis_title="Daily Return (%)",
                    yaxis_title="Frequency",
                    template="plotly_white",
                    height=350,
                )

                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for VaR calculation")
        else:
            st.warning("Could not fetch price data for VaR analysis")

st.divider()

# Drawdown Analysis
st.subheader("Historical Drawdown Analysis")

trades = data_loader.get_all_trades()

if trades:
    equity_data = data_loader.get_equity_curve(10000, trades)

    if equity_data:
        df = pd.DataFrame(equity_data)
        df = df[df["date"].notna()].copy()

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

            # Calculate drawdown series
            running_max = df["equity"].cummax()
            drawdown = (df["equity"] - running_max) / running_max

            # Find worst drawdowns
            drawdown_periods = []
            in_drawdown = False
            start_date = None
            peak_val = None

            for date, dd in drawdown.items():
                if dd < 0 and not in_drawdown:
                    in_drawdown = True
                    start_date = date
                    peak_val = running_max.loc[date]
                elif dd == 0 and in_drawdown:
                    in_drawdown = False
                    trough_idx = drawdown.loc[start_date:date].idxmin()
                    trough_val = df.loc[trough_idx, "equity"]
                    max_dd = (trough_val - peak_val) / peak_val

                    drawdown_periods.append({
                        "Start": start_date.strftime("%Y-%m-%d"),
                        "Trough": trough_idx.strftime("%Y-%m-%d"),
                        "Recovery": date.strftime("%Y-%m-%d"),
                        "Max Drawdown": f"{max_dd * 100:.1f}%",
                        "Duration (days)": (date - start_date).days,
                    })

            if drawdown_periods:
                # Sort by drawdown magnitude
                drawdown_periods.sort(key=lambda x: float(x["Max Drawdown"].replace("%", "")))

                st.write("**Worst Drawdown Periods:**")
                df_dd = pd.DataFrame(drawdown_periods[:5])
                st.dataframe(df_dd, use_container_width=True, hide_index=True)

            # Time underwater
            underwater_pct = (drawdown < 0).sum() / len(drawdown) * 100
            st.metric("Time Underwater", f"{underwater_pct:.1f}%", help="Percentage of time below previous peak")
else:
    st.info("No trade history for drawdown analysis")
