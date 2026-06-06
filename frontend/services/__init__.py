"""Dashboard service — domain mixins composed into one facade (P0-5 split).

The public surface (DashboardService methods + get_dashboard_service) is
unchanged from the former monolithic services.py; app.py and tests import from
`frontend.services` exactly as before.
"""
from .base import DashboardServiceBase
from .pipeline import PipelineMixin
from .holdings import HoldingsMixin
from .system import SystemMixin
from .schedules import SchedulesMixin
from .workflow import WorkflowMixin
from .events import EventsMixin
from .user_updates import UserUpdatesMixin
from .controls import ControlsMixin


class DashboardService(PipelineMixin, HoldingsMixin, SystemMixin, SchedulesMixin, WorkflowMixin, EventsMixin, UserUpdatesMixin, ControlsMixin, DashboardServiceBase):
    """Provide live data from the trading system to the dashboard."""
    pass


_service_instance = None


def get_dashboard_service() -> DashboardService:
    """Get or create dashboard service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = DashboardService()
    return _service_instance
