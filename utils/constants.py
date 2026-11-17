"""
Centralized constants for the application.
"""

from enum import Enum

class ProjectStatus(Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    AT_RISK = "AT_RISK"

class TimesheetStatus(Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    MISSING = "MISSING"

# Thresholds
UTILIZATION_WARNING_THRESHOLD = 0.85  # 85% utilization
IDLE_THRESHOLD_HOURS = 40             # Warn if idle for more than 40 hours in a period
OVERRUN_RISK_THRESHOLD = 0.90         # Warning when budget/hours usage crosses 90%

# Project names containing these keywords are internal leave buckets, not delivery projects
LEAVE_PROJECT_KEYWORDS = {"leave", "full day leave", "half day leave", "sick leave", "annual leave", "public holiday"}
