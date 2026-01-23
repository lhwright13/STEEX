"""Trade Journal - Notes, context, and lessons learned."""

import sys
import json
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
    page_title="Trade Journal - STEEX",
    page_icon="📓",
    layout="wide",
)

settings = get_settings()
data_loader = get_data_loader()

# Journal file
JOURNAL_FILE = PROJECT_ROOT / "data" / "trade_journal.json"


def load_journal() -> dict:
    """Load journal entries from file."""
    if JOURNAL_FILE.exists():
        try:
            with open(JOURNAL_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"entries": {}, "lessons": []}
    return {"entries": {}, "lessons": []}


def save_journal(journal: dict):
    """Save journal to file."""
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_FILE, "w") as f:
        json.dump(journal, f, indent=2)


journal = load_journal()

st.title("Trade Journal")
st.caption("Document your trades, capture context, and track lessons learned")

# Get trades
trades = data_loader.get_all_trades()
positions = data_loader.get_position_details()

st.divider()

# Tabs for different views
tab1, tab2, tab3, tab4 = st.tabs(["Review Trades", "Active Positions", "Lessons Learned", "Statistics"])

with tab1:
    st.subheader("Trade Review")

    if not trades:
        st.info("No completed trades to review yet.")
    else:
        # Trade selector
        trade_options = [
            f"{t.ticker} ({t.exit_date[:10]}) - {format_percent(t.pnl_pct)}"
            for t in sorted(trades, key=lambda x: x.exit_date, reverse=True)
        ]

        selected_trade_str = st.selectbox("Select Trade to Review", trade_options)

        if selected_trade_str:
            # Parse selection
            ticker = selected_trade_str.split(" (")[0]
            exit_date = selected_trade_str.split("(")[1].split(")")[0]

            trade = next(
                t for t in trades
                if t.ticker == ticker and t.exit_date.startswith(exit_date)
            )

            # Create unique key for this trade
            trade_key = f"{trade.ticker}_{trade.entry_date}_{trade.exit_date}"

            # Load existing notes
            existing_entry = journal["entries"].get(trade_key, {})

            col1, col2 = st.columns([2, 1])

            with col1:
                # Trade details
                st.markdown("### Trade Details")

                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.metric("Ticker", trade.ticker)
                    st.metric("Entry Date", trade.entry_date[:10])
                    st.metric("Entry Price", format_currency(trade.entry_price))
                with col_b:
                    st.metric("Exit Date", trade.exit_date[:10])
                    st.metric("Exit Price", format_currency(trade.exit_price))
                    st.metric("Exit Reason", trade.exit_reason)
                with col_c:
                    st.metric("P&L", format_currency(trade.pnl_dollars))
                    st.metric("Return", format_percent(trade.pnl_pct))
                    st.metric("Days Held", trade.hold_days)

                # Price chart with entry/exit markers
                st.markdown("### Price Action")

                entry_dt = datetime.fromisoformat(trade.entry_date)
                exit_dt = datetime.fromisoformat(trade.exit_date)

                # Get price data around the trade
                from datetime import timedelta
                start = entry_dt - timedelta(days=30)
                end = exit_dt + timedelta(days=10)

                price_df = data_loader.get_price_history(trade.ticker, days=180)

                if not price_df.empty:
                    fig = go.Figure()

                    fig.add_trace(go.Candlestick(
                        x=price_df.index,
                        open=price_df["Open"],
                        high=price_df["High"],
                        low=price_df["Low"],
                        close=price_df["Close"],
                        name=trade.ticker,
                    ))

                    # Entry marker
                    fig.add_trace(go.Scatter(
                        x=[entry_dt],
                        y=[trade.entry_price],
                        mode="markers",
                        marker=dict(size=15, color="green", symbol="triangle-up"),
                        name="Entry",
                    ))

                    # Exit marker
                    fig.add_trace(go.Scatter(
                        x=[exit_dt],
                        y=[trade.exit_price],
                        mode="markers",
                        marker=dict(size=15, color="red", symbol="triangle-down"),
                        name="Exit",
                    ))

                    fig.update_layout(
                        title=f"{trade.ticker} - Trade Period",
                        xaxis_title="Date",
                        yaxis_title="Price",
                        template="plotly_white",
                        height=400,
                        xaxis_rangeslider_visible=False,
                    )

                    st.plotly_chart(fig, use_container_width=True)

            with col2:
                # Entry reasons
                st.markdown("### Entry Signals")
                if trade.reasons:
                    for reason in trade.reasons:
                        st.markdown(f"- {reason}")
                else:
                    st.caption("No entry reasons recorded")

                st.metric("Entry Score", f"{trade.score:.1f}")

                # Market context at entry (VIX)
                st.markdown("### Market Context")
                st.caption("(VIX level at entry/exit)")

                # Note: Would need historical VIX data to show actual levels
                st.info("VIX context requires historical data")

            st.divider()

            # Journal Entry Form
            st.markdown("### Your Notes")

            # Pre-trade analysis
            pre_trade = st.text_area(
                "Pre-Trade Analysis",
                value=existing_entry.get("pre_trade", ""),
                placeholder="What was your thesis? Why did you take this trade?",
                height=100,
            )

            # Execution notes
            execution = st.text_area(
                "Execution Notes",
                value=existing_entry.get("execution", ""),
                placeholder="How was the execution? Any issues with entry/exit?",
                height=100,
            )

            # Post-trade review
            post_trade = st.text_area(
                "Post-Trade Review",
                value=existing_entry.get("post_trade", ""),
                placeholder="What went well? What could be improved?",
                height=100,
            )

            # Rating
            rating = st.select_slider(
                "Trade Quality Rating",
                options=["Poor", "Below Average", "Average", "Good", "Excellent"],
                value=existing_entry.get("rating", "Average"),
            )

            # Tags
            tags = st.multiselect(
                "Tags",
                options=[
                    "Followed Rules", "Broke Rules", "Good Entry", "Bad Entry",
                    "Good Exit", "Early Exit", "Late Exit", "Oversized",
                    "Undersized", "Lucky", "Unlucky", "Review Setup",
                ],
                default=existing_entry.get("tags", []),
            )

            # Lesson learned
            lesson = st.text_input(
                "Key Lesson (will be added to Lessons Learned)",
                value=existing_entry.get("lesson", ""),
                placeholder="One key takeaway from this trade",
            )

            if st.button("Save Journal Entry", type="primary"):
                journal["entries"][trade_key] = {
                    "ticker": trade.ticker,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "pnl_pct": trade.pnl_pct,
                    "pre_trade": pre_trade,
                    "execution": execution,
                    "post_trade": post_trade,
                    "rating": rating,
                    "tags": tags,
                    "lesson": lesson,
                    "updated_at": datetime.now().isoformat(),
                }

                # Add lesson if provided
                if lesson and lesson not in journal["lessons"]:
                    journal["lessons"].append({
                        "lesson": lesson,
                        "trade": f"{trade.ticker} ({trade.exit_date[:10]})",
                        "date": datetime.now().isoformat(),
                    })

                save_journal(journal)
                st.success("Journal entry saved!")

with tab2:
    st.subheader("Active Position Notes")

    if not positions:
        st.info("No active positions.")
    else:
        for pos in positions:
            with st.expander(f"{pos['ticker']} - {format_percent(pos['pnl_pct'])}"):
                col1, col2 = st.columns([1, 2])

                with col1:
                    st.metric("Current P&L", format_currency(pos["pnl_dollars"]))
                    st.metric("Days Held", pos["days_held"])
                    st.metric("Stop Distance", format_percent(pos["stop_distance_pct"]))

                with col2:
                    position_key = f"active_{pos['ticker']}"

                    notes = st.text_area(
                        "Position Notes",
                        value=journal["entries"].get(position_key, {}).get("notes", ""),
                        key=f"notes_{pos['ticker']}",
                        placeholder="Current thoughts on this position...",
                    )

                    if st.button("Save Notes", key=f"save_{pos['ticker']}"):
                        journal["entries"][position_key] = {
                            "ticker": pos["ticker"],
                            "notes": notes,
                            "updated_at": datetime.now().isoformat(),
                        }
                        save_journal(journal)
                        st.success("Notes saved!")

with tab3:
    st.subheader("Lessons Learned")
    st.caption("Key insights from your trading experience")

    # Add new lesson
    new_lesson = st.text_input("Add a new lesson learned", placeholder="Enter a trading lesson...")

    if st.button("Add Lesson") and new_lesson:
        journal["lessons"].append({
            "lesson": new_lesson,
            "trade": "Manual entry",
            "date": datetime.now().isoformat(),
        })
        save_journal(journal)
        st.success("Lesson added!")
        st.rerun()

    st.divider()

    # Display lessons
    if journal["lessons"]:
        for i, item in enumerate(reversed(journal["lessons"])):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{i+1}.** {item['lesson']}")
                st.caption(f"From: {item.get('trade', 'Unknown')} | {item.get('date', '')[:10]}")
            with col2:
                if st.button("Delete", key=f"del_{i}"):
                    journal["lessons"].remove(item)
                    save_journal(journal)
                    st.rerun()
    else:
        st.info("No lessons recorded yet. Start adding insights from your trades!")

    st.divider()

    # Export lessons
    if journal["lessons"]:
        lessons_text = "\n".join([
            f"- {item['lesson']} (from {item.get('trade', 'Unknown')})"
            for item in journal["lessons"]
        ])
        st.download_button(
            "Export Lessons",
            data=lessons_text,
            file_name="trading_lessons.txt",
            mime="text/plain",
        )

with tab4:
    st.subheader("Journal Statistics")

    # Count entries
    total_entries = len([e for e in journal["entries"].values() if "pre_trade" in e])
    total_lessons = len(journal["lessons"])

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Journal Entries", total_entries)
    with col2:
        st.metric("Lessons Learned", total_lessons)
    with col3:
        review_rate = total_entries / len(trades) * 100 if trades else 0
        st.metric("Review Rate", f"{review_rate:.0f}%")

    st.divider()

    # Rating distribution
    if total_entries > 0:
        st.subheader("Trade Quality Ratings")

        ratings = [e.get("rating", "Average") for e in journal["entries"].values() if "rating" in e]
        rating_counts = pd.Series(ratings).value_counts()

        fig = go.Figure(data=[
            go.Bar(x=rating_counts.index, y=rating_counts.values)
        ])
        fig.update_layout(
            title="Trade Quality Distribution",
            xaxis_title="Rating",
            yaxis_title="Count",
            template="plotly_white",
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

    # Tag analysis
    st.subheader("Common Tags")

    all_tags = []
    for entry in journal["entries"].values():
        all_tags.extend(entry.get("tags", []))

    if all_tags:
        tag_counts = pd.Series(all_tags).value_counts()

        fig = go.Figure(data=[
            go.Bar(x=tag_counts.values, y=tag_counts.index, orientation="h")
        ])
        fig.update_layout(
            title="Tag Frequency",
            xaxis_title="Count",
            yaxis_title="Tag",
            template="plotly_white",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Identify patterns
        st.subheader("Pattern Analysis")

        broke_rules = all_tags.count("Broke Rules")
        followed_rules = all_tags.count("Followed Rules")

        if broke_rules > 0 or followed_rules > 0:
            rule_adherence = followed_rules / (followed_rules + broke_rules) * 100
            st.metric(
                "Rule Adherence",
                f"{rule_adherence:.0f}%",
                help="Percentage of trades where you followed your rules",
            )

        # Best rated trades
        rated_entries = [
            (k, v) for k, v in journal["entries"].items()
            if v.get("rating") in ["Good", "Excellent"] and "pnl_pct" in v
        ]

        if rated_entries:
            st.markdown("**Well-Executed Trades (Good/Excellent Rating):**")
            for key, entry in rated_entries[:5]:
                st.markdown(f"- {entry['ticker']} ({entry['exit_date'][:10]}): {format_percent(entry['pnl_pct'])}")
    else:
        st.info("No tags recorded yet. Start tagging your trades!")
