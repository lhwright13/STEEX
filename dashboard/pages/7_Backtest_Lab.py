"""Backtest Lab - Test and compare strategy parameters."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config.settings import Settings, get_settings
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import calculate_metrics

st.set_page_config(
    page_title="Backtest Lab - STEEX",
    page_icon="🧪",
    layout="wide",
)

st.title("Backtest Lab")
st.caption("Test strategy parameters and compare results")

# Check for historical signals file
SIGNALS_FILE = PROJECT_ROOT / "data" / "backtest_signals.json"

if not SIGNALS_FILE.exists():
    st.warning(
        "No backtest signals file found. To run backtests, you need historical signals.\n\n"
        "Generate signals with: `python scripts/generate_backtest_signals.py`"
    )

    st.info("""
    **What are backtest signals?**

    Historical entry signals that the screener would have generated in the past.
    These include the date, ticker, and score for each potential entry.

    Without historical signals, we can only analyze past trades - not simulate new strategies.
    """)

    # Show existing trades analysis instead
    st.divider()
    st.subheader("Analyze Existing Trades")

    from dashboard.services.data_loader import get_data_loader
    loader = get_data_loader()
    trades = loader.get_all_trades()

    if trades:
        st.write(f"You have {len(trades)} completed trades to analyze.")

        # Group by exit reason
        exit_reasons = {}
        for t in trades:
            reason = t.exit_reason
            if reason not in exit_reasons:
                exit_reasons[reason] = {"count": 0, "pnl": 0, "winners": 0}
            exit_reasons[reason]["count"] += 1
            exit_reasons[reason]["pnl"] += t.pnl_dollars
            if t.pnl_dollars > 0:
                exit_reasons[reason]["winners"] += 1

        st.subheader("Exit Reason Analysis")
        for reason, data in exit_reasons.items():
            win_rate = data["winners"] / data["count"] * 100 if data["count"] > 0 else 0
            st.write(f"**{reason}**: {data['count']} trades, ${data['pnl']:,.2f} P&L, {win_rate:.1f}% win rate")
    else:
        st.info("No trades to analyze yet.")

    st.stop()

# Load signals
with open(SIGNALS_FILE) as f:
    all_signals = json.load(f)

st.success(f"Loaded {len(all_signals)} historical signals")

# Determine date range
signal_dates = [datetime.fromisoformat(s["date"]) for s in all_signals]
min_date = min(signal_dates).date()
max_date = max(signal_dates).date()

st.divider()

# Parameter configuration
st.subheader("Strategy Parameters")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("**Date Range**")
    start_date = st.date_input(
        "Start Date",
        value=min_date,
        min_value=min_date,
        max_value=max_date,
    )
    end_date = st.date_input(
        "End Date",
        value=max_date,
        min_value=min_date,
        max_value=max_date,
    )

    starting_capital = st.number_input(
        "Starting Capital",
        min_value=1000,
        max_value=1000000,
        value=10000,
        step=1000,
    )

with col2:
    st.markdown("**Position Management**")
    max_positions = st.slider(
        "Max Positions",
        min_value=5,
        max_value=50,
        value=20,
        help="Maximum concurrent positions",
    )

    position_size_pct = st.slider(
        "Position Size %",
        min_value=1,
        max_value=20,
        value=5,
        help="Position size as % of portfolio",
    ) / 100

    min_cash_reserve = st.slider(
        "Min Cash Reserve %",
        min_value=0,
        max_value=30,
        value=10,
    ) / 100

with col3:
    st.markdown("**Exit Rules**")
    initial_stop = st.slider(
        "Initial Stop %",
        min_value=5,
        max_value=25,
        value=12,
        help="Stop loss from entry",
    ) / 100

    max_hold_days = st.slider(
        "Max Hold Days",
        min_value=20,
        max_value=120,
        value=60,
        help="Maximum holding period",
    )

    dead_money_days = st.slider(
        "Dead Money Days",
        min_value=5,
        max_value=30,
        value=10,
        help="Exit if below entry for N days",
    )

# Advanced settings expander
with st.expander("Advanced Settings"):
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Trailing Stops**")
        trail_10 = st.slider("Trail after 10% gain", 5, 20, 12) / 100
        trail_20 = st.slider("Trail after 20% gain", 10, 25, 15) / 100
        trail_30 = st.slider("Trail after 30% gain", 15, 30, 18) / 100

    with col2:
        st.markdown("**VIX Rules**")
        vix_caution = st.slider("VIX Caution Level", 20, 40, 30)
        vix_exit = st.slider("VIX Exit Level", 30, 60, 40)

        st.markdown("**Transaction Costs**")
        transaction_cost = st.slider("Cost per Trade %", 0.0, 2.0, 0.5, 0.1) / 100

st.divider()

# Run backtest button
if st.button("Run Backtest", type="primary", use_container_width=True):

    # Create custom settings
    custom_settings = Settings(
        max_positions=max_positions,
        position_size_pct=position_size_pct,
        min_cash_reserve_pct=min_cash_reserve,
        initial_stop_pct=initial_stop,
        max_hold_days=max_hold_days,
        dead_money_days=dead_money_days,
        trail_stop_10=trail_10,
        trail_stop_20=trail_20,
        trail_stop_30=trail_30,
        vix_caution_level=vix_caution,
        vix_exit_level=vix_exit,
        estimated_cost_per_trade=transaction_cost,
    )

    # Filter signals by date
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    filtered_signals = [
        s for s in all_signals
        if start_dt <= datetime.fromisoformat(s["date"]) <= end_dt
    ]

    if not filtered_signals:
        st.error("No signals in selected date range")
        st.stop()

    st.info(f"Running backtest with {len(filtered_signals)} signals...")

    # Run backtest
    with st.spinner("Running backtest..."):
        try:
            engine = BacktestEngine(settings=custom_settings)
            result = engine.run(
                signals=filtered_signals,
                start_date=start_dt,
                end_date=end_dt,
                starting_capital=starting_capital,
                transaction_cost=transaction_cost,
            )

            # Store in session state for comparison
            if "backtest_results" not in st.session_state:
                st.session_state.backtest_results = []

            result_entry = {
                "timestamp": datetime.now().isoformat(),
                "params": {
                    "max_positions": max_positions,
                    "position_size": position_size_pct,
                    "initial_stop": initial_stop,
                    "max_hold_days": max_hold_days,
                },
                "result": result,
            }
            st.session_state.backtest_results.append(result_entry)

            st.success("Backtest complete!")

        except Exception as e:
            st.error(f"Backtest failed: {str(e)}")
            st.stop()

    # Display results
    st.divider()
    st.subheader("Results")

    # Key metrics
    col1, col2, col3, col4, col5 = st.columns(5)

    metrics = result.metrics

    with col1:
        st.metric("Total Return", f"{result.total_return_pct:.1f}%")
    with col2:
        st.metric("Total Trades", metrics.get("total_trades", 0))
    with col3:
        st.metric("Win Rate", f"{metrics.get('win_rate', 0) * 100:.1f}%")
    with col4:
        st.metric("Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0):.2f}")
    with col5:
        st.metric("Max Drawdown", f"{metrics.get('max_drawdown_pct', 0) * 100:.1f}%")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Profit Factor", f"{metrics.get('profit_factor', 0):.2f}")
    with col2:
        st.metric("Avg Winner", f"{metrics.get('avg_winner', 0) * 100:+.1f}%")
    with col3:
        st.metric("Avg Loser", f"{metrics.get('avg_loser', 0) * 100:+.1f}%")
    with col4:
        st.metric("Avg Hold Days", f"{metrics.get('avg_hold_days', 0):.1f}")
    with col5:
        st.metric("CAGR", f"{metrics.get('cagr', 0) * 100:.1f}%")

    # Equity curve
    st.subheader("Equity Curve")

    if not result.equity_curve.empty:
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=result.equity_curve.index,
            y=result.equity_curve["equity"],
            name="Strategy",
            line=dict(color="#2E86AB", width=2),
        ))

        # Add starting capital line
        fig.add_hline(
            y=starting_capital,
            line_dash="dash",
            line_color="gray",
            annotation_text="Starting Capital",
        )

        fig.update_layout(
            title="Portfolio Equity Over Time",
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            template="plotly_white",
            height=400,
        )

        st.plotly_chart(fig, use_container_width=True)

        # Drawdown chart
        equity = result.equity_curve["equity"]
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max * 100

        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=drawdown.index,
            y=drawdown,
            fill="tozeroy",
            fillcolor="rgba(231, 76, 60, 0.3)",
            line=dict(color="#E74C3C", width=1),
            name="Drawdown",
        ))

        fig_dd.update_layout(
            title="Drawdown",
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            template="plotly_white",
            height=250,
        )

        st.plotly_chart(fig_dd, use_container_width=True)

    # Trade breakdown
    st.subheader("Trade Breakdown")

    if result.trades:
        # By exit reason
        exit_reasons = {}
        for t in result.trades:
            reason = t.exit_reason or "unknown"
            if reason not in exit_reasons:
                exit_reasons[reason] = {"count": 0, "pnl": 0, "winners": 0}
            exit_reasons[reason]["count"] += 1
            exit_reasons[reason]["pnl"] += t.pnl or 0
            if (t.pnl or 0) > 0:
                exit_reasons[reason]["winners"] += 1

        exit_data = []
        for reason, data in exit_reasons.items():
            win_rate = data["winners"] / data["count"] * 100 if data["count"] > 0 else 0
            exit_data.append({
                "Exit Reason": reason,
                "Count": data["count"],
                "Total P&L": f"${data['pnl']:,.2f}",
                "Win Rate": f"{win_rate:.1f}%",
            })

        df = pd.DataFrame(exit_data)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Trade list
        with st.expander("View All Trades"):
            trade_data = []
            for t in sorted(result.trades, key=lambda x: x.exit_date or x.entry_date, reverse=True):
                trade_data.append({
                    "Ticker": t.ticker,
                    "Entry": t.entry_date.strftime("%Y-%m-%d") if t.entry_date else "",
                    "Exit": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "",
                    "Days": (t.exit_date - t.entry_date).days if t.exit_date and t.entry_date else 0,
                    "P&L %": f"{(t.pnl_pct or 0) * 100:+.1f}%",
                    "P&L $": f"${t.pnl or 0:,.2f}",
                    "Exit Reason": t.exit_reason or "",
                })

            df = pd.DataFrame(trade_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# Compare previous results
st.subheader("Compare Results")

if "backtest_results" in st.session_state and len(st.session_state.backtest_results) > 1:
    st.write(f"You have {len(st.session_state.backtest_results)} backtest results to compare.")

    compare_data = []
    for i, entry in enumerate(st.session_state.backtest_results[-5:]):  # Last 5
        r = entry["result"]
        p = entry["params"]
        m = r.metrics

        compare_data.append({
            "Run": i + 1,
            "Max Pos": p["max_positions"],
            "Pos Size": f"{p['position_size']*100:.0f}%",
            "Stop": f"{p['initial_stop']*100:.0f}%",
            "Return": f"{r.total_return_pct:.1f}%",
            "Trades": m.get("total_trades", 0),
            "Win Rate": f"{m.get('win_rate', 0)*100:.1f}%",
            "Sharpe": f"{m.get('sharpe_ratio', 0):.2f}",
            "Max DD": f"{m.get('max_drawdown_pct', 0)*100:.1f}%",
        })

    df = pd.DataFrame(compare_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    if st.button("Clear History"):
        st.session_state.backtest_results = []
        st.rerun()
else:
    st.info("Run multiple backtests to compare results side by side.")

st.divider()

# Parameter sensitivity analysis
st.subheader("Quick Sensitivity Analysis")
st.caption("See how changing one parameter affects results")

param_to_test = st.selectbox(
    "Parameter to Test",
    options=["Initial Stop %", "Position Size %", "Max Positions", "Max Hold Days"],
)

if st.button("Run Sensitivity Analysis"):

    base_settings = get_settings()

    # Define parameter ranges
    param_ranges = {
        "Initial Stop %": [0.08, 0.10, 0.12, 0.15, 0.18],
        "Position Size %": [0.03, 0.04, 0.05, 0.06, 0.08],
        "Max Positions": [10, 15, 20, 25, 30],
        "Max Hold Days": [30, 45, 60, 75, 90],
    }

    param_values = param_ranges[param_to_test]
    results = []

    progress = st.progress(0)

    for i, val in enumerate(param_values):
        # Create settings with modified parameter
        if param_to_test == "Initial Stop %":
            test_settings = Settings(initial_stop_pct=val)
        elif param_to_test == "Position Size %":
            test_settings = Settings(position_size_pct=val)
        elif param_to_test == "Max Positions":
            test_settings = Settings(max_positions=int(val))
        else:
            test_settings = Settings(max_hold_days=int(val))

        # Run backtest
        engine = BacktestEngine(settings=test_settings)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        filtered_signals = [
            s for s in all_signals
            if start_dt <= datetime.fromisoformat(s["date"]) <= end_dt
        ]

        try:
            result = engine.run(
                signals=filtered_signals,
                start_date=start_dt,
                end_date=end_dt,
                starting_capital=starting_capital,
            )

            results.append({
                "param_value": val if "%" not in param_to_test else val * 100,
                "return": result.total_return_pct,
                "sharpe": result.metrics.get("sharpe_ratio", 0),
                "max_dd": result.metrics.get("max_drawdown_pct", 0) * 100,
                "win_rate": result.metrics.get("win_rate", 0) * 100,
            })
        except Exception:
            pass

        progress.progress((i + 1) / len(param_values))

    if results:
        # Plot results
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=("Total Return %", "Sharpe Ratio", "Max Drawdown %", "Win Rate %"),
        )

        x_vals = [r["param_value"] for r in results]

        fig.add_trace(go.Scatter(x=x_vals, y=[r["return"] for r in results], mode="lines+markers"), row=1, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=[r["sharpe"] for r in results], mode="lines+markers"), row=1, col=2)
        fig.add_trace(go.Scatter(x=x_vals, y=[r["max_dd"] for r in results], mode="lines+markers"), row=2, col=1)
        fig.add_trace(go.Scatter(x=x_vals, y=[r["win_rate"] for r in results], mode="lines+markers"), row=2, col=2)

        fig.update_layout(
            title=f"Sensitivity to {param_to_test}",
            showlegend=False,
            height=500,
            template="plotly_white",
        )

        st.plotly_chart(fig, use_container_width=True)

        # Summary table
        sens_data = []
        for r in results:
            label = f"{r['param_value']:.0f}%" if "%" in param_to_test else str(int(r["param_value"]))
            sens_data.append({
                param_to_test: label,
                "Return": f"{r['return']:.1f}%",
                "Sharpe": f"{r['sharpe']:.2f}",
                "Max DD": f"{r['max_dd']:.1f}%",
                "Win Rate": f"{r['win_rate']:.1f}%",
            })

        df = pd.DataFrame(sens_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
