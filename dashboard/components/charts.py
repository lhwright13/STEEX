"""Plotly chart components for the dashboard."""

from typing import Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def create_equity_curve(
    equity_data: List[Dict],
    spy_data: Optional[pd.DataFrame] = None,
    title: str = "Portfolio Equity Curve",
) -> go.Figure:
    """Create an equity curve chart with optional SPY comparison.

    Args:
        equity_data: List of equity points from TradeTracker
        spy_data: Optional SPY comparison DataFrame
        title: Chart title

    Returns:
        Plotly figure
    """
    if not equity_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No equity data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    df = pd.DataFrame(equity_data)
    df = df[df["date"].notna()].copy()

    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No equity data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    df["date"] = pd.to_datetime(df["date"])

    fig = go.Figure()

    # Strategy equity
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["equity"],
            name="Strategy",
            line=dict(color="#2E86AB", width=2),
            hovertemplate="Date: %{x}<br>Value: $%{y:,.2f}<extra></extra>",
        )
    )

    # SPY comparison
    if spy_data is not None and not spy_data.empty:
        spy_aligned = spy_data.reindex(df["date"], method="ffill")
        if not spy_aligned.empty:
            fig.add_trace(
                go.Scatter(
                    x=spy_aligned.index,
                    y=spy_aligned["spy_equity"],
                    name="SPY (Buy & Hold)",
                    line=dict(color="#A23B72", width=2, dash="dash"),
                    hovertemplate="Date: %{x}<br>Value: $%{y:,.2f}<extra></extra>",
                )
            )

    fig.update_layout(
        title=title,
        xaxis_title="Date",
        yaxis_title="Portfolio Value ($)",
        hovermode="x unified",
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        template="plotly_white",
        height=400,
    )

    return fig


def create_drawdown_chart(equity_data: List[Dict]) -> go.Figure:
    """Create a drawdown chart.

    Args:
        equity_data: List of equity points

    Returns:
        Plotly figure
    """
    if not equity_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No equity data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    df = pd.DataFrame(equity_data)
    df = df[df["date"].notna()].copy()

    if df.empty:
        fig = go.Figure()
        return fig

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")

    # Calculate drawdown
    running_max = df["equity"].cummax()
    drawdown = (df["equity"] - running_max) / running_max * 100

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=drawdown.index,
            y=drawdown,
            fill="tozeroy",
            fillcolor="rgba(231, 76, 60, 0.3)",
            line=dict(color="#E74C3C", width=1),
            name="Drawdown",
            hovertemplate="Date: %{x}<br>Drawdown: %{y:.1f}%<extra></extra>",
        )
    )

    fig.update_layout(
        title="Portfolio Drawdown",
        xaxis_title="Date",
        yaxis_title="Drawdown (%)",
        template="plotly_white",
        height=250,
        yaxis=dict(range=[min(drawdown.min() * 1.1, -1), 0]),
    )

    return fig


def create_monthly_heatmap(monthly_data: Dict[str, Dict]) -> go.Figure:
    """Create a monthly returns heatmap.

    Args:
        monthly_data: Dict mapping YYYY-MM to metrics

    Returns:
        Plotly figure
    """
    if not monthly_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No monthly data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    # Parse into year/month structure
    years = {}
    for month_str, metrics in monthly_data.items():
        year = month_str[:4]
        month = int(month_str[5:7])
        if year not in years:
            years[year] = [None] * 12
        pnl_pct = metrics.get("avg_pnl_pct", 0) * 100
        years[year][month - 1] = pnl_pct

    # Create matrix
    year_labels = sorted(years.keys())
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    z_data = [years[year] for year in year_labels]

    fig = go.Figure(
        data=go.Heatmap(
            z=z_data,
            x=month_labels,
            y=year_labels,
            colorscale=[
                [0, "#E74C3C"],
                [0.5, "#FFFFFF"],
                [1, "#27AE60"],
            ],
            zmid=0,
            text=[[f"{v:.1f}%" if v is not None else "" for v in row] for row in z_data],
            texttemplate="%{text}",
            hovertemplate="Year: %{y}<br>Month: %{x}<br>Return: %{text}<extra></extra>",
            showscale=True,
            colorbar=dict(title="Return %"),
        )
    )

    fig.update_layout(
        title="Monthly Returns Heatmap",
        xaxis_title="Month",
        yaxis_title="Year",
        template="plotly_white",
        height=max(200, len(year_labels) * 40 + 100),
    )

    return fig


def create_exit_reason_chart(breakdown: Dict[str, Dict]) -> go.Figure:
    """Create exit reason breakdown chart.

    Args:
        breakdown: Dict mapping exit reason to stats

    Returns:
        Plotly figure
    """
    if not breakdown:
        fig = go.Figure()
        fig.add_annotation(
            text="No exit data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    reasons = list(breakdown.keys())
    counts = [breakdown[r]["count"] for r in reasons]
    pnls = [breakdown[r]["total_pnl"] for r in reasons]
    win_rates = [breakdown[r]["win_rate"] * 100 for r in reasons]

    colors = ["#27AE60" if p >= 0 else "#E74C3C" for p in pnls]

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Count by Exit Reason", "P&L by Exit Reason"),
    )

    fig.add_trace(
        go.Bar(
            x=reasons,
            y=counts,
            name="Count",
            marker_color="#3498DB",
            hovertemplate="Reason: %{x}<br>Count: %{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=reasons,
            y=pnls,
            name="P&L",
            marker_color=colors,
            hovertemplate="Reason: %{x}<br>P&L: $%{y:,.2f}<extra></extra>",
        ),
        row=1,
        col=2,
    )

    fig.update_layout(
        title="Exit Reason Analysis",
        template="plotly_white",
        height=350,
        showlegend=False,
    )

    return fig


def create_pnl_distribution(trades: List) -> go.Figure:
    """Create P&L distribution histogram.

    Args:
        trades: List of Trade objects

    Returns:
        Plotly figure
    """
    if not trades:
        fig = go.Figure()
        fig.add_annotation(
            text="No trade data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    pnl_pcts = [t.pnl_pct * 100 for t in trades if t.pnl_pct is not None]

    if not pnl_pcts:
        fig = go.Figure()
        return fig

    colors = ["#27AE60" if p >= 0 else "#E74C3C" for p in pnl_pcts]

    fig = go.Figure(
        data=go.Histogram(
            x=pnl_pcts,
            nbinsx=30,
            marker_color="#3498DB",
            hovertemplate="Return Range: %{x:.1f}%<br>Count: %{y}<extra></extra>",
        )
    )

    # Add vertical line at 0
    fig.add_vline(x=0, line_dash="dash", line_color="gray")

    fig.update_layout(
        title="Trade Return Distribution",
        xaxis_title="Return (%)",
        yaxis_title="Count",
        template="plotly_white",
        height=300,
    )

    return fig


def create_hold_time_histogram(trades: List) -> go.Figure:
    """Create trade duration histogram.

    Args:
        trades: List of Trade objects

    Returns:
        Plotly figure
    """
    if not trades:
        fig = go.Figure()
        fig.add_annotation(
            text="No trade data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    hold_days = [t.hold_days for t in trades if hasattr(t, "hold_days")]

    if not hold_days:
        fig = go.Figure()
        return fig

    fig = go.Figure(
        data=go.Histogram(
            x=hold_days,
            nbinsx=20,
            marker_color="#9B59B6",
            hovertemplate="Days Held: %{x}<br>Count: %{y}<extra></extra>",
        )
    )

    fig.update_layout(
        title="Trade Duration Distribution",
        xaxis_title="Days Held",
        yaxis_title="Count",
        template="plotly_white",
        height=300,
    )

    return fig


def create_price_chart(
    df: pd.DataFrame,
    ticker: str,
    show_ma: bool = True,
) -> go.Figure:
    """Create a candlestick price chart with optional MAs.

    Args:
        df: OHLCV DataFrame
        ticker: Ticker symbol
        show_ma: Whether to show moving averages

    Returns:
        Plotly figure
    """
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text=f"No price data for {ticker}",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    fig = go.Figure()

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name=ticker,
        )
    )

    if show_ma and len(df) >= 50:
        # 50-day MA
        ma_50 = df["Close"].rolling(window=50).mean()
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=ma_50,
                name="50 MA",
                line=dict(color="#F39C12", width=1),
            )
        )

        # 200-day MA
        if len(df) >= 200:
            ma_200 = df["Close"].rolling(window=200).mean()
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=ma_200,
                    name="200 MA",
                    line=dict(color="#9B59B6", width=1),
                )
            )

    fig.update_layout(
        title=f"{ticker} Price Chart",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        template="plotly_white",
        height=400,
        xaxis_rangeslider_visible=False,
    )

    return fig


def create_mini_price_chart(
    df: pd.DataFrame,
    ticker: str,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
) -> go.Figure:
    """Create a mini price chart for position detail view.

    Args:
        df: OHLCV DataFrame
        ticker: Ticker symbol
        entry_price: Entry price to show as line
        stop_price: Stop price to show as line

    Returns:
        Plotly figure
    """
    if df.empty:
        fig = go.Figure()
        return fig

    # Use last 60 days
    df = df.tail(60)

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["Close"],
            name=ticker,
            line=dict(color="#2E86AB", width=2),
            fill="tozeroy",
            fillcolor="rgba(46, 134, 171, 0.1)",
        )
    )

    if entry_price is not None:
        fig.add_hline(
            y=entry_price,
            line_dash="dash",
            line_color="#3498DB",
            annotation_text="Entry",
        )

    if stop_price is not None:
        fig.add_hline(
            y=stop_price,
            line_dash="dash",
            line_color="#E74C3C",
            annotation_text="Stop",
        )

    fig.update_layout(
        title=None,
        showlegend=False,
        template="plotly_white",
        height=150,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(showticklabels=False),
        yaxis=dict(showticklabels=True),
    )

    return fig


def create_sector_pie(positions: List[dict]) -> go.Figure:
    """Create sector exposure pie chart.

    Args:
        positions: List of position dicts

    Returns:
        Plotly figure
    """
    if not positions:
        fig = go.Figure()
        fig.add_annotation(
            text="No positions",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    # Try to use sector data from geopolitical module
    try:
        from src.data.geopolitical import get_ticker_sector

        # Group by sector
        sector_values = {}
        for p in positions:
            sector = get_ticker_sector(p["ticker"])
            if sector == "unknown":
                sector = "Other"
            sector_values[sector] = sector_values.get(sector, 0) + p["current_value"]

        labels = list(sector_values.keys())
        values = list(sector_values.values())
    except ImportError:
        # Fallback to ticker-based allocation
        values = [p["current_value"] for p in positions]
        labels = [p["ticker"] for p in positions]

    fig = go.Figure(
        data=go.Pie(
            labels=labels,
            values=values,
            hole=0.4,
            hovertemplate="<b>%{label}</b><br>Value: $%{value:,.2f}<br>%{percent}<extra></extra>",
        )
    )

    fig.update_layout(
        title="Sector Allocation",
        template="plotly_white",
        height=300,
    )

    return fig


def create_screening_funnel(pipeline_result) -> go.Figure:
    """Create screening pipeline funnel chart.

    Args:
        pipeline_result: ScreeningPipelineResult

    Returns:
        Plotly figure
    """
    stages = ["Universe", "Stage 1", "Stage 2", "Stage 3", "Stage 4"]
    values = [
        pipeline_result.universe_size,
        pipeline_result.stage_1_passed,
        pipeline_result.stage_2_passed,
        pipeline_result.stage_3_passed,
        pipeline_result.stage_4_passed,
    ]

    fig = go.Figure(
        go.Funnel(
            y=stages,
            x=values,
            textinfo="value+percent initial",
            marker=dict(
                color=["#3498DB", "#2ECC71", "#F1C40F", "#E67E22", "#9B59B6"]
            ),
        )
    )

    fig.update_layout(
        title="Screening Pipeline Results",
        template="plotly_white",
        height=350,
    )

    return fig


def create_vix_chart(vix_data: pd.DataFrame) -> go.Figure:
    """Create VIX historical chart with threshold lines.

    Args:
        vix_data: VIX DataFrame

    Returns:
        Plotly figure
    """
    if vix_data.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No VIX data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return fig

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=vix_data.index,
            y=vix_data["Close"],
            name="VIX",
            line=dict(color="#2E86AB", width=2),
            fill="tozeroy",
            fillcolor="rgba(46, 134, 171, 0.1)",
        )
    )

    # Threshold lines
    fig.add_hline(y=30, line_dash="dash", line_color="#F39C12",
                  annotation_text="Caution (30)")
    fig.add_hline(y=40, line_dash="dash", line_color="#E74C3C",
                  annotation_text="Exit (40)")

    fig.update_layout(
        title="VIX Index",
        xaxis_title="Date",
        yaxis_title="VIX Level",
        template="plotly_white",
        height=300,
    )

    return fig
