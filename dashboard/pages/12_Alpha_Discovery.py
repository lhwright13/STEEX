"""Alpha Discovery - PySR symbolic regression insights."""

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
import streamlit as st

from config.settings import get_settings
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Alpha Discovery - STEEX",
    page_icon="",
    layout="wide",
)

st.title("Alpha Discovery")
st.caption("PySR symbolic regression - discovered equations and predictions")

settings = get_settings()
loader = get_data_loader()

if not settings.pysr_enabled:
    st.info(
        "PySR symbolic regression is disabled. "
        "Enable it in config.yaml by setting `pysr_enabled: true` and `weight_pysr` > 0."
    )
    st.markdown("""
    **Getting started:**
    1. Install PySR: `pip install pysr`
    2. Build a dataset: `python scripts/build_pysr_dataset.py --start 2025-01-01 --end 2025-12-31`
    3. Train models: `python scripts/train_pysr.py --dataset data/ml/datasets/latest --horizons 21d`
    4. Enable in config: set `pysr_enabled: true` and `weight_pysr: 0.15`
    5. Restart the dashboard
    """)
    st.stop()

# Check if predictor is available
predictor = loader.pysr_predictor
has_models = predictor is not None and predictor.is_available()

if not has_models:
    st.warning(
        "No trained PySR models found. "
        "Train models first with `python scripts/train_pysr.py`."
    )

# Tabs
tab_backtest, tab_equations, tab_predictions, tab_walkforward, tab_info = st.tabs([
    "Predicted vs Actual",
    "Discovered Equations",
    "Live Predictions",
    "Walk-Forward Results",
    "Training Info",
])


# ---------------------------------------------------------------------------
# Tab: Predicted vs Actual
# Show 2 weeks of history, then the model's chained prediction vs reality
# ---------------------------------------------------------------------------
with tab_backtest:
    if not has_models:
        st.info("Train models first to run a backtest.")
    else:
        model = next(iter(predictor._models.values()))

        col_split, col_days = st.columns(2)
        with col_split:
            split_date = st.date_input(
                "Prediction starts on",
                value=datetime(2026, 2, 14).date(),
                key="split_date",
            )
        with col_days:
            pred_days = st.number_input(
                "Days to predict",
                min_value=1,
                max_value=10,
                value=4,
                key="pred_days",
            )

        run_bt = st.button("Run", type="primary", key="run_bt")

        if run_bt:
            with st.spinner("Running..."):
                from src.ml.backtest import run_backtest
                from src.ml.equations import equation_to_python

                split_dt = datetime.combine(split_date, datetime.min.time())
                # Fetch enough for 2 weeks of history + prediction days
                history_start = split_dt - timedelta(days=25)
                fetch_end = split_dt + timedelta(days=pred_days * 2 + 5)

                bt_df = run_backtest(
                    model=model,
                    ticker="SPY",
                    start_date=history_start,
                    end_date=fetch_end,
                    horizon_days=1,
                )

                if bt_df.empty:
                    st.error("No data returned for SPY.")
                else:
                    st.session_state["bt_results"] = bt_df
                    st.session_state["bt_split"] = split_dt
                    st.session_state["bt_pred_days"] = pred_days

        if "bt_results" in st.session_state and not st.session_state["bt_results"].empty:
            bt_df = st.session_state["bt_results"]
            split_dt = st.session_state["bt_split"]
            pred_days_val = st.session_state["bt_pred_days"]

            # Split into history and prediction zones
            split_ts = pd.Timestamp(split_dt)
            if bt_df.index.tz is not None:
                split_ts = split_ts.tz_localize(bt_df.index.tz)

            history = bt_df[bt_df.index < split_ts]
            future = bt_df[bt_df.index >= split_ts].head(pred_days_val)

            if future.empty:
                st.error("No trading days found after split date.")
            else:
                # Chain predicted prices forward from last actual close
                anchor_price = history["close"].iloc[-1]
                anchor_date = history.index[-1]

                predicted_prices = [anchor_price]
                predicted_dates = [anchor_date]
                current_price = anchor_price

                for i in range(len(future)):
                    pred_return = future["predicted_return"].iloc[i]
                    current_price = current_price * (1 + pred_return)
                    predicted_prices.append(current_price)
                    predicted_dates.append(future.index[i])

                # Build the chart
                fig = go.Figure()

                # 1) History - solid gray line
                fig.add_trace(go.Scatter(
                    x=history.index,
                    y=history["close"],
                    name="History",
                    line=dict(color="#888888", width=2),
                    mode="lines",
                ))

                # 2) Actual future prices - orange, continuing from anchor
                actual_future_dates = [anchor_date] + list(future.index)
                actual_future_prices = [anchor_price] + list(future["close"])
                fig.add_trace(go.Scatter(
                    x=actual_future_dates,
                    y=actual_future_prices,
                    name="Actual",
                    line=dict(color="#FF5722", width=2.5),
                    mode="lines+markers",
                    marker=dict(size=8),
                ))

                # 3) Predicted future prices - blue dashed
                fig.add_trace(go.Scatter(
                    x=predicted_dates,
                    y=predicted_prices,
                    name="Predicted",
                    line=dict(color="#2196F3", width=2.5, dash="dash"),
                    mode="lines+markers",
                    marker=dict(size=8, symbol="diamond"),
                ))

                # Vertical line at split point
                anchor_str = str(anchor_date)
                fig.add_shape(
                    type="line",
                    x0=anchor_str, x1=anchor_str,
                    y0=0, y1=1,
                    yref="paper",
                    line=dict(color="#E91E63", width=2, dash="dot"),
                )
                fig.add_annotation(
                    x=anchor_str, y=0.02, yref="paper",
                    text="Prediction starts",
                    showarrow=False,
                    font=dict(color="#E91E63", size=11),
                    xanchor="left",
                    xshift=6,
                )

                # Shade the prediction zone
                fig.add_shape(
                    type="rect",
                    x0=anchor_str, x1=str(future.index[-1]),
                    y0=0, y1=1,
                    yref="paper",
                    fillcolor="rgba(33,150,243,0.08)",
                    line_width=0,
                )

                eq = model.equations[model.selected_index]
                fig.update_layout(
                    title="S&P 500 (SPY) - History + Forward Prediction",
                    xaxis_title="Date",
                    yaxis_title="Price ($)",
                    height=600,
                    legend=dict(
                        orientation="h",
                        yanchor="bottom", y=1.02,
                        xanchor="right", x=1,
                    ),
                    margin=dict(l=60, r=20, t=60, b=40),
                    hovermode="x unified",
                )

                st.plotly_chart(fig, use_container_width=True)

                st.caption(f"Equation: `{eq.expression}`")

                # Show the numbers
                comparison = pd.DataFrame({
                    "Actual ($)": [f"{p:.2f}" for p in actual_future_prices[1:]],
                    "Predicted ($)": [f"{p:.2f}" for p in predicted_prices[1:]],
                    "Error ($)": [f"{a - p:+.2f}" for a, p in zip(actual_future_prices[1:], predicted_prices[1:])],
                    "Error (%)": [f"{(a/p - 1)*100:+.2f}%" for a, p in zip(actual_future_prices[1:], predicted_prices[1:])],
                }, index=[d.strftime("%Y-%m-%d") for d in list(future.index)])
                comparison.index.name = "Date"
                st.dataframe(comparison, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab: Discovered Equations
# ---------------------------------------------------------------------------
with tab_equations:
    st.subheader("Selected Equations")

    if has_models:
        equations = loader.get_pysr_equations()
        if equations:
            for horizon, eq in equations.items():
                with st.expander(f"Horizon: {horizon}", expanded=True):
                    st.markdown(f"**Expression:** `{eq.expression}`")

                    try:
                        from src.ml.equations import equation_to_latex, substitute_feature_names
                        latex_str = equation_to_latex(eq.expression)
                        st.latex(latex_str)
                        readable = substitute_feature_names(eq.expression, eq.feature_names)
                        st.markdown(f"**Readable:** `{readable}`")
                    except Exception:
                        pass

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Complexity", eq.complexity)
                    col2.metric("R-squared", f"{eq.r_squared:.4f}")
                    if eq.validation_r_squared is not None:
                        col3.metric("Val R-squared", f"{eq.validation_r_squared:.4f}")

            # Feature importance
            st.subheader("Feature Importance")
            try:
                from src.ml.equations import extract_feature_importance
                all_equations = []
                for eq in equations.values():
                    all_equations.append(eq)
                importance = extract_feature_importance(all_equations)

                if importance:
                    fig = go.Figure(go.Bar(
                        x=list(importance.values()),
                        y=list(importance.keys()),
                        orientation="h",
                    ))
                    fig.update_layout(
                        title="Feature Importance (weighted by R-squared)",
                        xaxis_title="Importance",
                        yaxis_title="Feature",
                        height=400,
                    )
                    st.plotly_chart(fig, use_container_width=True)
            except Exception:
                st.info("Could not compute feature importance.")

            # Pareto front
            st.subheader("Pareto Front")
            if has_models and predictor._models:
                for horizon, model in predictor._models.items():
                    if model.equations:
                        pareto_data = pd.DataFrame([
                            {
                                "Complexity": eq.complexity,
                                "R-squared": eq.r_squared,
                                "Expression": eq.expression[:60],
                                "Selected": i == model.selected_index,
                            }
                            for i, eq in enumerate(model.equations)
                        ])

                        fig = go.Figure()
                        non_selected = pareto_data[~pareto_data["Selected"]]
                        selected = pareto_data[pareto_data["Selected"]]

                        fig.add_trace(go.Scatter(
                            x=non_selected["Complexity"],
                            y=non_selected["R-squared"],
                            mode="markers",
                            name="Pareto front",
                            text=non_selected["Expression"],
                            marker=dict(size=8),
                        ))
                        if not selected.empty:
                            fig.add_trace(go.Scatter(
                                x=selected["Complexity"],
                                y=selected["R-squared"],
                                mode="markers",
                                name="Selected",
                                text=selected["Expression"],
                                marker=dict(size=14, symbol="star"),
                            ))

                        fig.update_layout(
                            title=f"Pareto Front - {horizon}",
                            xaxis_title="Complexity (nodes)",
                            yaxis_title="R-squared",
                            height=350,
                        )
                        st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No equations available.")
    else:
        st.info("Train models first to see discovered equations.")


# ---------------------------------------------------------------------------
# Tab: Live Predictions
# ---------------------------------------------------------------------------
with tab_predictions:
    st.subheader("Current Predictions")

    if has_models:
        ticker_input = st.text_input(
            "Enter tickers (comma-separated)",
            value="AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,JPM,JNJ",
        )
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

        if tickers:
            predictions = loader.get_pysr_predictions(tickers)
            if predictions:
                rows = []
                for ticker, pred in predictions.items():
                    rows.append({
                        "Ticker": ticker,
                        "PySR Score": f"{pred.pysr_score:.1f}",
                        "Pred Return 5d": f"{pred.predicted_return_5d:.3%}" if pred.predicted_return_5d else "N/A",
                        "Pred Return 21d": f"{pred.predicted_return_21d:.3%}" if pred.predicted_return_21d else "N/A",
                        "Pred Return 63d": f"{pred.predicted_return_63d:.3%}" if pred.predicted_return_63d else "N/A",
                        "Confidence": f"{pred.confidence:.2f}",
                    })

                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True)

                # Score distribution
                score_values = [p.pysr_score for p in predictions.values()]
                fig = go.Figure(go.Histogram(x=score_values, nbinsx=20))
                fig.update_layout(
                    title="PySR Score Distribution",
                    xaxis_title="Score (0-100)",
                    yaxis_title="Count",
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Failed to generate predictions.")
    else:
        st.info("Train models first to see predictions.")


# ---------------------------------------------------------------------------
# Tab: Walk-Forward Results
# ---------------------------------------------------------------------------
with tab_walkforward:
    st.subheader("Walk-Forward Validation")

    wf_results = loader.get_pysr_walk_forward_results()
    if wf_results:
        st.json(wf_results)
    else:
        if has_models:
            st.info(
                "Walk-forward results file not found. "
                "Results are shown after training completes."
            )

            # Show model-level R-squared as a summary
            for horizon, model in predictor._models.items():
                col1, col2 = st.columns(2)
                col1.metric(f"{horizon} Train R-squared", f"{model.train_r_squared:.4f}")
                col2.metric(f"{horizon} Val R-squared", f"{model.val_r_squared:.4f}")
        else:
            st.info("No walk-forward results available. Train models first.")


# ---------------------------------------------------------------------------
# Tab: Training Info
# ---------------------------------------------------------------------------
with tab_info:
    st.subheader("Model Status")

    if has_models:
        for horizon, model in predictor._models.items():
            with st.expander(f"Model: {horizon}", expanded=True):
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Horizon", horizon)
                col2.metric("Trained", model.trained_date or "Unknown")
                col3.metric("Val R-squared", f"{model.val_r_squared:.4f}")
                col4.metric("Features", len(model.feature_names))

                eq = model.selected_equation
                if eq:
                    st.markdown(f"**Selected equation:** `{eq.expression}`")
                    st.markdown(f"Complexity: {eq.complexity}, Loss: {eq.loss:.6f}")

                st.markdown(f"**Feature names:** {', '.join(model.feature_names[:10])}...")
    else:
        st.info("No models loaded.")

    st.subheader("Configuration")
    config_data = {
        "pysr_enabled": settings.pysr_enabled,
        "weight_pysr": settings.weight_pysr,
        "pysr_model_dir": settings.pysr_model_dir,
        "pysr_niterations": settings.pysr_niterations,
        "pysr_max_complexity": settings.pysr_max_complexity,
        "pysr_max_selected_complexity": settings.pysr_max_selected_complexity,
        "pysr_timeout": settings.pysr_timeout,
        "pysr_walk_forward_folds": settings.pysr_walk_forward_folds,
    }
    st.json(config_data)

    # Dataset info
    st.subheader("Dataset Info")
    dataset_dir = Path(settings.pysr_dataset_dir) / "latest"
    if dataset_dir.exists():
        metadata_file = dataset_dir / "metadata.json"
        if metadata_file.exists():
            import json
            with open(metadata_file) as f:
                metadata = json.load(f)
            st.json(metadata)
        else:
            st.info(f"Dataset directory exists at {dataset_dir} but no metadata found.")
    else:
        st.info("No dataset found. Build one with `python scripts/build_pysr_dataset.py`.")
