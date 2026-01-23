"""Metric display components for the dashboard."""

from typing import Optional

import streamlit as st


def metric_card(
    label: str,
    value: str,
    delta: Optional[str] = None,
    delta_color: str = "normal",
    help_text: Optional[str] = None,
):
    """Display a metric card.

    Args:
        label: Metric label
        value: Metric value (formatted string)
        delta: Optional delta value
        delta_color: Color for delta ("normal", "inverse", or "off")
        help_text: Optional help tooltip
    """
    st.metric(
        label=label,
        value=value,
        delta=delta,
        delta_color=delta_color,
        help=help_text,
    )


def format_currency(value: float, decimals: int = 2) -> str:
    """Format a value as currency.

    Args:
        value: Numeric value
        decimals: Decimal places

    Returns:
        Formatted string
    """
    if value >= 0:
        return f"${value:,.{decimals}f}"
    return f"-${abs(value):,.{decimals}f}"


def format_percent(value: float, decimals: int = 1, with_sign: bool = True) -> str:
    """Format a value as percentage.

    Args:
        value: Numeric value (as decimal, e.g., 0.15 for 15%)
        decimals: Decimal places
        with_sign: Whether to include +/- sign

    Returns:
        Formatted string
    """
    pct = value * 100
    if with_sign:
        return f"{pct:+.{decimals}f}%"
    return f"{pct:.{decimals}f}%"


def format_number(value: float, decimals: int = 2) -> str:
    """Format a number with commas.

    Args:
        value: Numeric value
        decimals: Decimal places

    Returns:
        Formatted string
    """
    return f"{value:,.{decimals}f}"


def portfolio_metrics_row(
    portfolio_value: float,
    position_count: int,
    todays_pnl: float,
    todays_pnl_pct: float,
    drawdown: float,
):
    """Display the main portfolio metrics row.

    Args:
        portfolio_value: Total portfolio value
        position_count: Number of open positions
        todays_pnl: Today's P&L in dollars
        todays_pnl_pct: Today's P&L percentage
        drawdown: Current drawdown percentage (as decimal)
    """
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        metric_card(
            "Portfolio Value",
            format_currency(portfolio_value),
            help_text="Total portfolio value including cash",
        )

    with col2:
        metric_card(
            "Active Positions",
            str(position_count),
            help_text="Number of open positions",
        )

    with col3:
        delta_color = "normal" if todays_pnl >= 0 else "inverse"
        metric_card(
            "Today's P&L",
            format_currency(todays_pnl),
            delta=format_percent(todays_pnl_pct),
            delta_color=delta_color,
        )

    with col4:
        metric_card(
            "Drawdown",
            format_percent(drawdown, with_sign=False),
            delta_color="inverse" if drawdown < -0.10 else "off",
            help_text="Current drawdown from peak",
        )


def performance_metrics_row(metrics: dict):
    """Display key performance metrics.

    Args:
        metrics: Dict with performance metrics
    """
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        sharpe = metrics.get("sharpe_ratio", 0)
        metric_card(
            "Sharpe Ratio",
            format_number(sharpe),
            help_text="Risk-adjusted return (higher is better)",
        )

    with col2:
        sortino = metrics.get("sortino_ratio", 0)
        metric_card(
            "Sortino Ratio",
            format_number(sortino),
            help_text="Downside risk-adjusted return",
        )

    with col3:
        cagr = metrics.get("cagr", 0)
        metric_card(
            "CAGR",
            format_percent(cagr),
            help_text="Compound Annual Growth Rate",
        )

    with col4:
        max_dd = metrics.get("max_drawdown_pct", 0)
        metric_card(
            "Max Drawdown",
            format_percent(-max_dd if max_dd > 0 else max_dd, with_sign=False),
            help_text="Maximum peak-to-trough decline",
        )

    with col5:
        win_rate = metrics.get("win_rate", 0)
        metric_card(
            "Win Rate",
            format_percent(win_rate, with_sign=False),
            help_text="Percentage of winning trades",
        )


def trade_metrics_row(metrics: dict):
    """Display trade statistics metrics.

    Args:
        metrics: Dict with trade metrics
    """
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        metric_card(
            "Total Trades",
            str(metrics.get("total_trades", 0)),
        )

    with col2:
        metric_card(
            "Winners",
            str(metrics.get("winners", 0)),
        )

    with col3:
        metric_card(
            "Losers",
            str(metrics.get("losers", 0)),
        )

    with col4:
        avg_winner = metrics.get("avg_winner", 0)
        metric_card(
            "Avg Winner",
            format_percent(avg_winner),
        )

    with col5:
        avg_loser = metrics.get("avg_loser", 0)
        metric_card(
            "Avg Loser",
            format_percent(avg_loser),
        )


def position_row_color(pnl_pct: float, stop_distance_pct: float) -> str:
    """Get row color for position based on P&L and stop distance.

    Args:
        pnl_pct: P&L percentage (as decimal)
        stop_distance_pct: Distance to stop as percentage

    Returns:
        CSS color string
    """
    if stop_distance_pct < 0.02:  # Within 2% of stop
        return "background-color: rgba(241, 196, 15, 0.2)"  # Yellow
    elif pnl_pct > 0:
        return "background-color: rgba(39, 174, 96, 0.1)"  # Green
    else:
        return "background-color: rgba(231, 76, 60, 0.1)"  # Red


def vix_status_badge(vix_level: float) -> str:
    """Get VIX status badge based on level.

    Args:
        vix_level: Current VIX level

    Returns:
        Status string with emoji
    """
    if vix_level > 40:
        return "CRITICAL"
    elif vix_level > 30:
        return "ELEVATED"
    elif vix_level > 20:
        return "NORMAL"
    else:
        return "LOW"


def vix_color(vix_level: float) -> str:
    """Get VIX color based on level.

    Args:
        vix_level: Current VIX level

    Returns:
        Color hex code
    """
    if vix_level > 40:
        return "#E74C3C"  # Red
    elif vix_level > 30:
        return "#F39C12"  # Orange
    elif vix_level > 20:
        return "#3498DB"  # Blue
    else:
        return "#27AE60"  # Green


def display_vix_indicator(vix_level: Optional[float]):
    """Display VIX indicator with color coding.

    Args:
        vix_level: Current VIX level
    """
    if vix_level is None:
        st.metric("VIX", "N/A")
        return

    status = vix_status_badge(vix_level)
    color = vix_color(vix_level)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.metric("VIX", f"{vix_level:.1f}")
    with col2:
        st.markdown(
            f'<span style="color: {color}; font-weight: bold;">{status}</span>',
            unsafe_allow_html=True,
        )


def market_status_indicator(status: dict):
    """Display market status indicator.

    Args:
        status: Market status dict with 'status' and 'reason' keys
    """
    status_text = status.get("status", "unknown")
    reason = status.get("reason", "")

    colors = {
        "open": "#27AE60",
        "pre_market": "#F39C12",
        "after_hours": "#F39C12",
        "closed": "#95A5A6",
    }

    color = colors.get(status_text, "#95A5A6")
    display_text = status_text.upper().replace("_", " ")

    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 8px;">
            <div style="width: 12px; height: 12px; border-radius: 50%; background-color: {color};"></div>
            <span style="font-weight: bold;">{display_text}</span>
            <span style="color: #666;">({reason})</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
