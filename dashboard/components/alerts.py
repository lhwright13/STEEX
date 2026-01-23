"""Alert system for the dashboard."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

import streamlit as st


class AlertLevel(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """A dashboard alert."""

    level: AlertLevel
    title: str
    message: str
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


def check_vix_alerts(vix_level: Optional[float]) -> List[Alert]:
    """Check for VIX-related alerts.

    Args:
        vix_level: Current VIX level

    Returns:
        List of alerts
    """
    alerts = []

    if vix_level is None:
        return alerts

    if vix_level > 40:
        alerts.append(
            Alert(
                level=AlertLevel.CRITICAL,
                title="VIX Spike",
                message=f"VIX at {vix_level:.1f} - exceeds exit threshold (40). Consider exiting positions.",
            )
        )
    elif vix_level > 30:
        alerts.append(
            Alert(
                level=AlertLevel.WARNING,
                title="VIX Elevated",
                message=f"VIX at {vix_level:.1f} - above caution level (30). Tighten stops.",
            )
        )

    return alerts


def check_position_alerts(positions: List[dict]) -> List[Alert]:
    """Check for position-related alerts.

    Args:
        positions: List of position dicts with stop info

    Returns:
        List of alerts
    """
    alerts = []

    for pos in positions:
        ticker = pos.get("ticker", "")
        current_price = pos.get("current_price", 0)
        stop_price = pos.get("stop_price", 0)
        entry_price = pos.get("entry_price", 0)
        days_held = pos.get("days_held", 0)

        if current_price <= 0 or stop_price <= 0:
            continue

        stop_distance_pct = (current_price - stop_price) / current_price

        # Check if below stop
        if current_price < stop_price:
            alerts.append(
                Alert(
                    level=AlertLevel.CRITICAL,
                    title=f"{ticker} Below Stop",
                    message=f"{ticker} at ${current_price:.2f} is below stop (${stop_price:.2f}). Exit immediately.",
                )
            )
        # Check if near stop
        elif stop_distance_pct < 0.02:
            alerts.append(
                Alert(
                    level=AlertLevel.WARNING,
                    title=f"{ticker} Near Stop",
                    message=f"{ticker} at ${current_price:.2f} is within 2% of stop (${stop_price:.2f}).",
                )
            )

        # Check for dead money
        if days_held >= 10 and current_price < entry_price:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            alerts.append(
                Alert(
                    level=AlertLevel.INFO,
                    title=f"{ticker} Dead Money",
                    message=f"{ticker} below entry for {days_held} days ({pnl_pct:.1f}%). Consider exit.",
                )
            )

    return alerts


def check_drawdown_alerts(
    current_value: float,
    peak_value: float,
    thresholds: dict,
) -> List[Alert]:
    """Check for drawdown-related alerts.

    Args:
        current_value: Current portfolio value
        peak_value: Peak portfolio value
        thresholds: Dict with drawdown thresholds

    Returns:
        List of alerts
    """
    alerts = []

    if peak_value <= 0:
        return alerts

    drawdown = (peak_value - current_value) / peak_value

    if drawdown >= thresholds.get("exit", 0.25):
        alerts.append(
            Alert(
                level=AlertLevel.CRITICAL,
                title="Drawdown Exit Level",
                message=f"Portfolio drawdown at {drawdown*100:.1f}% - exceeds exit threshold. Liquidate positions.",
            )
        )
    elif drawdown >= thresholds.get("pause", 0.20):
        alerts.append(
            Alert(
                level=AlertLevel.CRITICAL,
                title="Drawdown Pause Level",
                message=f"Portfolio drawdown at {drawdown*100:.1f}% - pause new entries.",
            )
        )
    elif drawdown >= thresholds.get("reduce", 0.15):
        alerts.append(
            Alert(
                level=AlertLevel.WARNING,
                title="Drawdown Reduce Level",
                message=f"Portfolio drawdown at {drawdown*100:.1f}% - reduce position sizes.",
            )
        )
    elif drawdown >= thresholds.get("review", 0.10):
        alerts.append(
            Alert(
                level=AlertLevel.INFO,
                title="Drawdown Review Level",
                message=f"Portfolio drawdown at {drawdown*100:.1f}% - review strategy.",
            )
        )

    return alerts


def generate_all_alerts(
    vix_level: Optional[float],
    positions: List[dict],
    current_value: float,
    peak_value: float,
    settings,
) -> List[Alert]:
    """Generate all alerts.

    Args:
        vix_level: Current VIX level
        positions: List of position dicts
        current_value: Current portfolio value
        peak_value: Peak portfolio value
        settings: Strategy settings

    Returns:
        List of all alerts, sorted by severity
    """
    all_alerts = []

    # VIX alerts
    all_alerts.extend(check_vix_alerts(vix_level))

    # Position alerts
    all_alerts.extend(check_position_alerts(positions))

    # Drawdown alerts
    thresholds = {
        "review": settings.drawdown_review,
        "reduce": settings.drawdown_reduce,
        "pause": settings.drawdown_pause,
        "exit": settings.drawdown_exit,
    }
    all_alerts.extend(check_drawdown_alerts(current_value, peak_value, thresholds))

    # Sort by severity (critical first)
    severity_order = {
        AlertLevel.CRITICAL: 0,
        AlertLevel.WARNING: 1,
        AlertLevel.INFO: 2,
    }
    all_alerts.sort(key=lambda a: severity_order.get(a.level, 99))

    return all_alerts


def display_alert(alert: Alert):
    """Display a single alert.

    Args:
        alert: Alert to display
    """
    if alert.level == AlertLevel.CRITICAL:
        st.error(f"**{alert.title}**: {alert.message}")
    elif alert.level == AlertLevel.WARNING:
        st.warning(f"**{alert.title}**: {alert.message}")
    else:
        st.info(f"**{alert.title}**: {alert.message}")


def display_alerts_panel(alerts: List[Alert], max_alerts: int = 5):
    """Display alerts panel.

    Args:
        alerts: List of alerts to display
        max_alerts: Maximum number of alerts to show
    """
    if not alerts:
        st.success("No active alerts")
        return

    # Count by level
    critical_count = sum(1 for a in alerts if a.level == AlertLevel.CRITICAL)
    warning_count = sum(1 for a in alerts if a.level == AlertLevel.WARNING)
    info_count = sum(1 for a in alerts if a.level == AlertLevel.INFO)

    # Summary
    summary_parts = []
    if critical_count > 0:
        summary_parts.append(f"{critical_count} critical")
    if warning_count > 0:
        summary_parts.append(f"{warning_count} warning")
    if info_count > 0:
        summary_parts.append(f"{info_count} info")

    st.markdown(f"**{len(alerts)} Active Alerts** ({', '.join(summary_parts)})")

    # Display alerts
    for alert in alerts[:max_alerts]:
        display_alert(alert)

    if len(alerts) > max_alerts:
        st.caption(f"... and {len(alerts) - max_alerts} more alerts")


def display_compact_alerts(alerts: List[Alert], max_alerts: int = 3):
    """Display compact alerts for sidebar or overview.

    Args:
        alerts: List of alerts
        max_alerts: Maximum to show
    """
    if not alerts:
        return

    critical = [a for a in alerts if a.level == AlertLevel.CRITICAL]
    warnings = [a for a in alerts if a.level == AlertLevel.WARNING]

    shown = 0

    for alert in critical[:max_alerts]:
        st.error(f"{alert.title}")
        shown += 1
        if shown >= max_alerts:
            break

    if shown < max_alerts:
        for alert in warnings[: max_alerts - shown]:
            st.warning(f"{alert.title}")
            shown += 1
            if shown >= max_alerts:
                break

    remaining = len(alerts) - shown
    if remaining > 0:
        st.caption(f"+{remaining} more alerts")
