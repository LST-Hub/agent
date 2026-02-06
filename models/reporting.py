"""
Data models for Timesheets and Reporting metrics.
"""

from dataclasses import dataclass
from datetime import date
from utils.constants import TimesheetStatus

@dataclass
class TimesheetEntry:
    """Single timesheet record."""
    id: int
    resource_id: int
    project_id: int
    date: date
    hours_logged: float
    description: str
    status: TimesheetStatus

@dataclass
class UtilizationReport:
    """calculated metrics for a resource."""
    resource_id: int
    period_start: date
    period_end: date
    total_capacity_hours: float
    logged_hours: float
    utilization_percentage: float
    
    @property
    def is_idle(self) -> bool:
        """Returns True if utilization is below acceptable threshold."""
        # This logic could be moved to service, but kept here for convenient property access
        return False # Placeholder
