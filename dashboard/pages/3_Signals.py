"""Signals page - Daily screening results."""

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
    create_price_chart,
    create_screening_funnel,
)
from dashboard.components.metrics import format_currency, format_percent
from dashboard.services.data_loader import get_data_loader

st.set_page_config(
    page_title="Signals - STEEX",
    page_icon="📡",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

st.title("Screening Signals")
st.caption("Today's screening pipeline results and candidates")

# Run scan button
col1, col2 = st.columns([1, 4])
with col1:
    run_scan = st.button("Run Scan", type="primary", use_container_width=True)

if run_scan:
    st.cache_data.clear()

# Run screening pipeline
with st.spinner("Running screening pipeline..."):
    try:
        pipeline_result = data_loader.run_screening()
    except Exception as e:
        st.error(f"Error running screening: {str(e)}")
        st.stop()

st.divider()

# Pipeline summary
st.subheader("Pipeline Summary")

col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    st.metric("Universe", pipeline_result.universe_size)
with col2:
    pct = (pipeline_result.stage_1_passed / pipeline_result.universe_size * 100) if pipeline_result.universe_size > 0 else 0
    st.metric("Stage 1", pipeline_result.stage_1_passed, delta=f"{pct:.1f}%")
with col3:
    pct = (pipeline_result.stage_2_passed / pipeline_result.stage_1_passed * 100) if pipeline_result.stage_1_passed > 0 else 0
    st.metric("Stage 2", pipeline_result.stage_2_passed, delta=f"{pct:.1f}%")
with col4:
    pct = (pipeline_result.stage_3_passed / pipeline_result.stage_2_passed * 100) if pipeline_result.stage_2_passed > 0 else 0
    st.metric("Stage 3", pipeline_result.stage_3_passed, delta=f"{pct:.1f}%")
with col5:
    pct = (pipeline_result.stage_4_passed / pipeline_result.stage_3_passed * 100) if pipeline_result.stage_3_passed > 0 else 0
    st.metric("Stage 4", pipeline_result.stage_4_passed, delta=f"{pct:.1f}%")
with col6:
    st.metric("Final Candidates", pipeline_result.stage_5_passed)

# Funnel chart
st.subheader("Screening Funnel")
fig = create_screening_funnel(pipeline_result)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# Stage descriptions
with st.expander("Stage Descriptions"):
    st.markdown("""
    **Stage 1 - Universe Filter:**
    - Price > $5
    - Average volume > 500K
    - No earnings within 5 days

    **Stage 2 - Momentum Filter:**
    - 6-month momentum > 15%
    - 1-month momentum > 5%
    - Not in top 5% (overextended)
    - Price above 50-day and 200-day MA

    **Stage 3 - Insider Filter:**
    - CEO/CFO purchase, OR
    - 3+ unique insider buyers, OR
    - Purchase value > $100K

    **Stage 4 - Sentiment Filter:**
    - Combined stock-specific + geopolitical sentiment
    - Uses VADER NLP for headline analysis
    - Minimum sentiment score threshold (default: 30)
    - Sector-based impact from global events

    **Stage 5 - Fundamental Filter:**
    - P/E ratio < 50 (avoid speculation)
    - ROE > 5% (quality filter)
    - Debt/Equity < 2.0 (financial health)
    - Growth stocks with losses allowed if revenue growth > 20%

    **Options Intelligence (Enrichment):**
    - Put/Call ratio analysis
    - Implied volatility assessment
    - Not a hard filter, but informs scoring
    """)

st.divider()

# Final candidates table
st.subheader("Final Candidates")

candidates = pipeline_result.final_candidates

if not candidates:
    st.info("No candidates passed all screening stages today.")
else:
    # Create candidates table
    cand_data = []
    for c in candidates:
        cand_data.append({
            "Ticker": c.ticker,
            "6M Momentum": format_percent(c.momentum_6m) if c.momentum_6m else "N/A",
            "1M Momentum": format_percent(c.momentum_1m) if c.momentum_1m else "N/A",
            "Percentile": f"{c.momentum_percentile:.0f}%" if c.momentum_percentile else "N/A",
            "Above 50MA": "Yes" if c.above_ma_50 else "No",
            "Above 200MA": "Yes" if c.above_ma_200 else "No",
            "Insider Score": f"{c.insider_score:.1f}",
            "Buyers": c.insider_buyers,
            "Insider Value": format_currency(c.total_insider_value),
            "Sentiment": f"{c.sentiment_score:.0f}" if c.sentiment_score else "N/A",
            "Sentiment Label": c.sentiment_label or "N/A",
            "Fundamental": f"{c.fundamental_score:.0f}" if c.fundamental_score else "N/A",
            "P/E": f"{c.pe_ratio:.1f}" if c.pe_ratio else "N/A",
            "Options": f"{c.options_score:.0f}" if c.options_score else "N/A",
            "P/C Ratio": f"{c.put_call_ratio:.2f}" if c.put_call_ratio else "N/A",
            "Sector": c.sector or "N/A",
            "Volume Surge": f"{c.volume_surge:.1f}x" if c.volume_surge else "N/A",
        })

    df = pd.DataFrame(cand_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Candidate detail view
    st.divider()
    st.subheader("Candidate Details")

    selected = st.selectbox(
        "Select candidate",
        options=[c.ticker for c in candidates],
    )

    if selected:
        selected_candidate = next(c for c in candidates if c.ticker == selected)

        col1, col2 = st.columns([2, 1])

        with col1:
            # Price chart
            st.subheader(f"{selected} Price Chart")
            try:
                price_data = data_loader.get_price_history(selected, days=365)
                if not price_data.empty:
                    fig = create_price_chart(price_data, selected, show_ma=True)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Price data unavailable")
            except Exception as e:
                st.warning(f"Unable to load price chart: {str(e)}")

        with col2:
            # Candidate metrics
            st.subheader("Screening Metrics")

            st.metric(
                "6-Month Momentum",
                format_percent(selected_candidate.momentum_6m) if selected_candidate.momentum_6m else "N/A",
            )
            st.metric(
                "1-Month Momentum",
                format_percent(selected_candidate.momentum_1m) if selected_candidate.momentum_1m else "N/A",
            )
            st.metric(
                "Momentum Percentile",
                f"{selected_candidate.momentum_percentile:.0f}%" if selected_candidate.momentum_percentile else "N/A",
            )

            st.divider()

            st.metric("Insider Score", f"{selected_candidate.insider_score:.1f}")
            st.metric("Unique Buyers", selected_candidate.insider_buyers)
            st.metric("Total Insider Value", format_currency(selected_candidate.total_insider_value))

            st.divider()

            # MA status
            st.markdown("**Moving Average Status:**")
            st.markdown(f"- Above 50MA: {'Yes' if selected_candidate.above_ma_50 else 'No'}")
            st.markdown(f"- Above 200MA: {'Yes' if selected_candidate.above_ma_200 else 'No'}")

            st.divider()

            # Sentiment
            st.markdown("**Sentiment Analysis:**")
            if selected_candidate.sentiment_score is not None:
                sentiment_color = (
                    "#27AE60" if selected_candidate.sentiment_score >= 55
                    else "#E74C3C" if selected_candidate.sentiment_score < 45
                    else "#F39C12"
                )
                st.markdown(
                    f"Score: <span style='color: {sentiment_color}; font-weight: bold;'>"
                    f"{selected_candidate.sentiment_score:.0f}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"Label: {selected_candidate.sentiment_label or 'N/A'}")
                st.markdown(f"Sector: {selected_candidate.sector or 'Unknown'}")
            else:
                st.markdown("Sentiment data not available")

            st.divider()

            # Fundamental Analysis
            st.markdown("**Fundamental Analysis:**")
            if selected_candidate.fundamental_score is not None:
                fund_color = (
                    "#27AE60" if selected_candidate.fundamental_score >= 60
                    else "#E74C3C" if selected_candidate.fundamental_score < 40
                    else "#F39C12"
                )
                st.markdown(
                    f"Score: <span style='color: {fund_color}; font-weight: bold;'>"
                    f"{selected_candidate.fundamental_score:.0f}</span>",
                    unsafe_allow_html=True,
                )
                if selected_candidate.pe_ratio:
                    st.markdown(f"P/E Ratio: {selected_candidate.pe_ratio:.1f}")
                if selected_candidate.peg_ratio:
                    st.markdown(f"PEG Ratio: {selected_candidate.peg_ratio:.2f}")
                if selected_candidate.roe:
                    st.markdown(f"ROE: {selected_candidate.roe:.1%}")
                if selected_candidate.debt_to_equity:
                    st.markdown(f"Debt/Equity: {selected_candidate.debt_to_equity:.2f}")
            else:
                st.markdown("Fundamental data not available")

            st.divider()

            # Options Intelligence
            st.markdown("**Options Intelligence:**")
            if selected_candidate.options_score is not None:
                opt_color = (
                    "#27AE60" if selected_candidate.options_score >= 60
                    else "#E74C3C" if selected_candidate.options_score < 40
                    else "#F39C12"
                )
                st.markdown(
                    f"Score: <span style='color: {opt_color}; font-weight: bold;'>"
                    f"{selected_candidate.options_score:.0f}</span>",
                    unsafe_allow_html=True,
                )
                if selected_candidate.put_call_ratio:
                    st.markdown(f"Put/Call Ratio: {selected_candidate.put_call_ratio:.2f}")
            else:
                st.markdown("Options data not available")

            # Volume
            if selected_candidate.volume_surge:
                st.metric("Volume Surge", f"{selected_candidate.volume_surge:.1f}x average")

st.divider()

# All results explorer
with st.expander("Explore All Results"):
    st.markdown("View results for stocks that failed at various stages.")

    all_results = pipeline_result.all_results

    # Filter by failed stage
    failed_stage = st.selectbox(
        "Filter by failed stage",
        options=["All", "stage_1", "stage_2", "stage_3", "stage_4", "stage_5"],
    )

    if failed_stage == "All":
        filtered = list(all_results.values())
    else:
        filtered = [r for r in all_results.values() if r.failed_stage == failed_stage]

    if filtered:
        # Show top by momentum if available
        with_momentum = [r for r in filtered if r.momentum_6m is not None]
        if with_momentum:
            sorted_results = sorted(with_momentum, key=lambda x: x.momentum_6m or 0, reverse=True)[:20]
        else:
            sorted_results = filtered[:20]

        exp_data = []
        for r in sorted_results:
            exp_data.append({
                "Ticker": r.ticker,
                "Failed At": r.failed_stage or "Passed",
                "Stages Passed": ", ".join(r.passed_stages) if r.passed_stages else "None",
                "6M Momentum": format_percent(r.momentum_6m) if r.momentum_6m else "N/A",
                "Insider Score": f"{r.insider_score:.1f}",
                "Sentiment": f"{r.sentiment_score:.0f}" if r.sentiment_score else "N/A",
                "Fundamental": f"{r.fundamental_score:.0f}" if r.fundamental_score else "N/A",
                "P/E": f"{r.pe_ratio:.1f}" if r.pe_ratio else "N/A",
                "Sector": r.sector or "N/A",
            })

        df = pd.DataFrame(exp_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No results for selected filter")

# Export
st.divider()
st.subheader("Export")

if candidates:
    cand_df = pd.DataFrame(cand_data)
    csv_data = cand_df.to_csv(index=False)
    st.download_button(
        label="Download Candidates CSV",
        data=csv_data,
        file_name="screening_candidates.csv",
        mime="text/csv",
    )
