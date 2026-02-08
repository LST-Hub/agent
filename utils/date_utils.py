"""
Date utility functions.
"""

from datetime import date, datetime

def is_weekend(d: date) -> bool:
    """Returns True if date is Saturday or Sunday."""
    # Monday is 0, Sunday is 6
    return d.weekday() >= 5

def is_holiday(d: date) -> bool:
    """
    Returns True if date is a holiday.
    Placeholder for actual holiday logic.
    """
    # TODO: Implement holiday lookup (e.g. from DB or config)
    # For now, return False
    return False

def get_current_date() -> date:
    """Returns current system date."""
    return datetime.now().date()
