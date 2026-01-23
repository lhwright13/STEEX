"""What-If Analysis - Explore alternative outcomes."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config.settings import get_settings
from dashboard.components.metrics import format_currency, format_percent
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="What-If Analysis - STEEX",
    page_icon="🔮",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

st.title("What-If Analysis")
st.caption("Explore alternative outcomes and understand your edge")

trades = data_loader.get_all_trades()

if not trades:
    st.info("No completed trades yet. What-if analysis requires trade history.")
    st.stop()

st.divider()

# Trade Replay
st.subheader("Trade Replay: What if you had exited differently?")
st.caption("See how different exit strategies would have performed")

# Select a trade
trade_options = [
    f"{t.ticker} ({t.exit_date[:10]}) - Actual: {format_percent(t.pnl_pct)}"
    for t in sorted(trades, key=lambda x: x.exit_date, reverse=True)
]

selected = st.selectbox("Select a trade to analyze", trade_options)

if selected:
    ticker = selected.split(" (")[0]
    exit_date = selected.split("(")[1].split(")")[0]

    trade = next(
        t for t in trades
        if t.ticker == ticker and t.exit_date.startswith(exit_date)
    )

    # Get price data for the trade period and beyond
    entry_dt = datetime.fromisoformat(trade.entry_date)
    exit_dt = datetime.fromisoformat(trade.exit_date)

    # Fetch extended price data
    price_df = data_loader.get_price_history(trade.ticker, days=365)

    if not price_df.empty and "Close" in price_df.columns:
        # Filter to relevant period
        trade_start = entry_dt - timedelta(days=10)

        col1, col2 = st.columns([2, 1])

        with col1:
            # Interactive chart showing the trade
            fig = go.Figure()

            fig.add_trace(go.Candlestick(
                x=price_df.index,
                open=price_df["Open"],
                high=price_df["High"],
                low=price_df["Low"],
                close=price_df["Close"],
                name=trade.ticker,
            ))

            # Entry point
            fig.add_trace(go.Scatter(
                x=[entry_dt],
                y=[trade.entry_price],
                mode="markers+text",
                marker=dict(size=12, color="green", symbol="triangle-up"),
                text=["Entry"],
                textposition="top center",
                name="Entry",
            ))

            # Actual exit
            fig.add_trace(go.Scatter(
                x=[exit_dt],
                y=[trade.exit_price],
                mode="markers+text",
                marker=dict(size=12, color="red", symbol="triangle-down"),
                text=["Actual Exit"],
                textposition="bottom center",
                name="Actual Exit",
            ))

            # Add stop loss line
            initial_stop = trade.entry_price * (1 - settings.initial_stop_pct)
            fig.add_hline(y=initial_stop, line_dash="dash", line_color="red",
                          annotation_text="Initial Stop")

            fig.update_layout(
                title=f"{trade.ticker} - Trade Analysis",
                xaxis_title="Date",
                yaxis_title="Price",
                template="plotly_white",
                height=450,
                xaxis_rangeslider_visible=False,
            )

            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("### Actual Results")
            st.metric("Entry Price", format_currency(trade.entry_price))
            st.metric("Exit Price", format_currency(trade.exit_price))
            st.metric("P&L", format_percent(trade.pnl_pct))
            st.metric("Exit Reason", trade.exit_reason)
            st.metric("Days Held", trade.hold_days)

        # Alternative exit analysis
        st.subheader("Alternative Exit Analysis")

        # Calculate what-if scenarios
        try:
            # Ensure we handle timezone-aware indexes
            if price_df.index.tz is not None:
                price_df_naive = price_df.copy()
                price_df_naive.index = price_df_naive.index.tz_localize(None)
            else:
                price_df_naive = price_df

            # Get prices after entry
            mask = price_df_naive.index >= entry_dt
            prices_after_entry = price_df_naive[mask]["Close"]

            if len(prices_after_entry) > 0:
                # Calculate running high and various exit scenarios
                running_high = prices_after_entry.cummax()

                scenarios = {}

                # Scenario 1: Different stop losses
                for stop_pct in [0.08, 0.10, 0.12, 0.15]:
                    stop_price = trade.entry_price * (1 - stop_pct)
                    hit_stop = prices_after_entry[prices_after_entry <= stop_price]
                    if len(hit_stop) > 0:
                        exit_date = hit_stop.index[0]
                        exit_price = stop_price
                    else:
                        exit_date = prices_after_entry.index[-1]
                        exit_price = prices_after_entry.iloc[-1]

                    pnl = (exit_price - trade.entry_price) / trade.entry_price
                    days = (exit_date - entry_dt).days

                    scenarios[f"{stop_pct*100:.0f}% Stop"] = {
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "days": days,
                    }

                # Scenario 2: Trailing stops
                for trail_pct in [0.10, 0.15, 0.20]:
                    exit_price = None
                    for i, (date, price) in enumerate(prices_after_entry.items()):
                        high = running_high.iloc[i]
                        trail_stop = high * (1 - trail_pct)
                        if price <= trail_stop and high > trade.entry_price:
                            exit_price = price
                            exit_date = date
                            break

                    if exit_price is None:
                        exit_date = prices_after_entry.index[-1]
                        exit_price = prices_after_entry.iloc[-1]

                    pnl = (exit_price - trade.entry_price) / trade.entry_price
                    days = (exit_date - entry_dt).days

                    scenarios[f"{trail_pct*100:.0f}% Trail"] = {
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "days": days,
                    }

                # Scenario 3: Time-based exits
                for hold_days in [20, 40, 60, 80]:
                    target_date = entry_dt + timedelta(days=hold_days)
                    closest_idx = prices_after_entry.index[prices_after_entry.index <= target_date]
                    if len(closest_idx) > 0:
                        exit_date = closest_idx[-1]
                        exit_price = prices_after_entry.loc[exit_date]
                    else:
                        exit_date = prices_after_entry.index[-1]
                        exit_price = prices_after_entry.iloc[-1]

                    pnl = (exit_price - trade.entry_price) / trade.entry_price
                    days = (exit_date - entry_dt).days

                    scenarios[f"{hold_days}d Hold"] = {
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "days": days,
                    }

                # Display scenarios
                scenario_data = []
                for name, data in scenarios.items():
                    diff = data["pnl"] - trade.pnl_pct
                    scenario_data.append({
                        "Strategy": name,
                        "Exit Price": format_currency(data["exit_price"]),
                        "P&L": format_percent(data["pnl"]),
                        "Days Held": data["days"],
                        "vs Actual": format_percent(diff),
                    })

                df = pd.DataFrame(scenario_data)

                # Highlight best/worst
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Best alternative
                best = max(scenarios.items(), key=lambda x: x[1]["pnl"])
                worst = min(scenarios.items(), key=lambda x: x[1]["pnl"])

                col1, col2 = st.columns(2)
                with col1:
                    st.success(f"**Best Alternative:** {best[0]} with {format_percent(best[1]['pnl'])}")
                with col2:
                    st.error(f"**Worst Alternative:** {worst[0]} with {format_percent(worst[1]['pnl'])}")

        except Exception as e:
            st.warning(f"Could not calculate alternatives: {str(e)}")

st.divider()

# Monte Carlo Simulation
st.subheader("Monte Carlo Simulation")
st.caption("Simulate thousands of possible outcomes based on your trade statistics")

col1, col2 = st.columns([1, 2])

with col1:
    num_simulations = st.slider("Number of Simulations", 100, 10000, 1000, 100)
    num_trades = st.slider("Trades per Simulation", 10, 200, 50)
    starting_capital = st.number_input("Starting Capital", 1000, 1000000, 10000, 1000)

    if st.button("Run Monte Carlo", type="primary"):
        # Calculate trade statistics from actual trades
        pnl_pcts = [t.pnl_pct for t in trades if t.pnl_pct is not None]

        if len(pnl_pcts) < 5:
            st.error("Need at least 5 trades for Monte Carlo simulation")
        else:
            mean_return = np.mean(pnl_pcts)
            std_return = np.std(pnl_pcts)
            win_rate = sum(1 for p in pnl_pcts if p > 0) / len(pnl_pcts)

            # Run simulations
            final_values = []
            max_drawdowns = []

            for _ in range(num_simulations):
                equity = starting_capital
                peak = equity
                max_dd = 0
                equity_curve = [equity]

                for _ in range(num_trades):
                    # Randomly sample from actual distribution
                    trade_return = np.random.choice(pnl_pcts)
                    position_value = equity * settings.position_size_pct
                    pnl = position_value * trade_return
                    equity += pnl

                    if equity > peak:
                        peak = equity
                    dd = (peak - equity) / peak
                    if dd > max_dd:
                        max_dd = dd

                    equity_curve.append(equity)

                final_values.append(equity)
                max_drawdowns.append(max_dd)

            st.session_state["mc_results"] = {
                "final_values": final_values,
                "max_drawdowns": max_drawdowns,
                "starting_capital": starting_capital,
            }

with col2:
    if "mc_results" in st.session_state:
        results = st.session_state["mc_results"]
        final_values = results["final_values"]
        max_drawdowns = results["max_drawdowns"]
        starting = results["starting_capital"]

        # Distribution of outcomes
        fig = make_subplots(rows=1, cols=2, subplot_titles=("Final Portfolio Value", "Maximum Drawdown"))

        fig.add_trace(go.Histogram(x=final_values, nbinsx=50, name="Final Value"), row=1, col=1)
        fig.add_trace(go.Histogram(x=[d * 100 for d in max_drawdowns], nbinsx=50, name="Max DD"), row=1, col=2)

        fig.add_vline(x=starting, line_dash="dash", line_color="green", row=1, col=1)

        fig.update_layout(
            height=350,
            showlegend=False,
            template="plotly_white",
        )

        st.plotly_chart(fig, use_container_width=True)

        # Statistics
        col_a, col_b, col_c, col_d = st.columns(4)

        with col_a:
            median_final = np.median(final_values)
            st.metric("Median Final Value", format_currency(median_final))
        with col_b:
            win_prob = sum(1 for v in final_values if v > starting) / len(final_values) * 100
            st.metric("Probability of Profit", f"{win_prob:.1f}%")
        with col_c:
            percentile_5 = np.percentile(final_values, 5)
            st.metric("5th Percentile", format_currency(percentile_5))
        with col_d:
            median_dd = np.median(max_drawdowns) * 100
            st.metric("Median Max DD", f"{median_dd:.1f}%")

        # Percentile table
        percentiles = [5, 10, 25, 50, 75, 90, 95]
        pct_data = []
        for p in percentiles:
            val = np.percentile(final_values, p)
            ret = (val - starting) / starting * 100
            pct_data.append({
                "Percentile": f"{p}th",
                "Final Value": format_currency(val),
                "Return": f"{ret:+.1f}%",
            })

        df = pd.DataFrame(pct_data)
        st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# Rule Analysis
st.subheader("Rule Impact Analysis")
st.caption("How much did each exit rule contribute to your results?")

# Group trades by exit reason
exit_groups = {}
for t in trades:
    reason = t.exit_reason or "unknown"
    if reason not in exit_groups:
        exit_groups[reason] = []
    exit_groups[reason].append(t)

# Calculate impact of each rule
rule_data = []
for reason, group_trades in exit_groups.items():
    count = len(group_trades)
    total_pnl = sum(t.pnl_dollars for t in group_trades)
    avg_pnl_pct = np.mean([t.pnl_pct for t in group_trades])
    win_rate = sum(1 for t in group_trades if t.pnl_dollars > 0) / count * 100
    avg_days = np.mean([t.hold_days for t in group_trades])

    rule_data.append({
        "Exit Rule": reason,
        "Count": count,
        "Total P&L": format_currency(total_pnl),
        "Avg P&L %": format_percent(avg_pnl_pct),
        "Win Rate": f"{win_rate:.1f}%",
        "Avg Days": f"{avg_days:.0f}",
    })

df = pd.DataFrame(rule_data)
st.dataframe(df, use_container_width=True, hide_index=True)

# Analysis
st.markdown("**Insights:**")

# Find best and worst performing rules
best_rule = max(rule_data, key=lambda x: float(x["Total P&L"].replace("$", "").replace(",", "").replace("-", "")))
worst_rule = min(rule_data, key=lambda x: float(x["Total P&L"].replace("$", "").replace(",", "").replace("-", "")))

col1, col2 = st.columns(2)
with col1:
    st.success(f"**Best Performing Rule:** {best_rule['Exit Rule']} ({best_rule['Total P&L']})")
with col2:
    if float(worst_rule["Total P&L"].replace("$", "").replace(",", "").replace("-", "")) < 0:
        st.error(f"**Worst Performing Rule:** {worst_rule['Exit Rule']} ({worst_rule['Total P&L']})")
    else:
        st.info(f"**Lowest Performing Rule:** {worst_rule['Exit Rule']} ({worst_rule['Total P&L']})")

st.divider()

# Luck vs Skill Analysis
st.subheader("Luck vs Skill Analysis")
st.caption("Is your performance due to skill or random chance?")

if len(trades) >= 20:
    # Calculate actual performance
    actual_pnl = sum(t.pnl_pct for t in trades)
    actual_sharpe = np.mean([t.pnl_pct for t in trades]) / np.std([t.pnl_pct for t in trades]) if np.std([t.pnl_pct for t in trades]) > 0 else 0

    # Run random simulations
    pnl_pcts = [t.pnl_pct for t in trades]
    random_pnls = []
    random_sharpes = []

    for _ in range(1000):
        shuffled = np.random.permutation(pnl_pcts)
        random_pnls.append(sum(shuffled))
        random_sharpes.append(np.mean(shuffled) / np.std(shuffled) if np.std(shuffled) > 0 else 0)

    # Calculate percentile of actual vs random
    pnl_percentile = sum(1 for r in random_pnls if r < actual_pnl) / len(random_pnls) * 100
    sharpe_percentile = sum(1 for r in random_sharpes if r < actual_sharpe) / len(random_sharpes) * 100

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "P&L Percentile vs Random",
            f"{pnl_percentile:.0f}th",
            help="How your total return ranks vs randomized trade order",
        )
        if pnl_percentile > 75:
            st.success("Your sequencing may have added value")
        elif pnl_percentile < 25:
            st.warning("Your sequencing may have hurt returns")

    with col2:
        st.metric(
            "Sharpe Percentile vs Random",
            f"{sharpe_percentile:.0f}th",
            help="How your risk-adjusted return ranks vs randomized trade order",
        )

    st.caption(
        "This analysis shuffles your actual trade returns randomly 1000 times to see if "
        "the order of your trades (timing) added or subtracted value. A percentile > 75 "
        "suggests good timing, while < 25 suggests poor timing."
    )
else:
    st.info("Need at least 20 trades for luck vs skill analysis")
