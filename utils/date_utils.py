"""
Date utility functions.
"""

from datetime import date, datetime
import json
import os

# Cache for holidays loaded from JSON
_holidays_cache = None

def _load_holidays():
    """Load holidays from JSON file."""
    global _holidays_cache
    if _holidays_cache is not None:
        return _holidays_cache
    
    try:
        # Get the path to the holidays JSON file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        holidays_file = os.path.join(project_root, 'data', 'holidays.json')
        
        if os.path.exists(holidays_file):
            with open(holidays_file, 'r') as f:
                holidays_data = json.load(f)
                # Convert string dates to date objects
                _holidays_cache = set()
                for holiday in holidays_data:
                    holiday_date = datetime.strptime(holiday['date'], '%Y-%m-%d').date()
                    _holidays_cache.add(holiday_date)
                return _holidays_cache
        else:
            # If file doesn't exist, return empty set
            _holidays_cache = set()
            return _holidays_cache
    except Exception as e:
        print(f"Error loading holidays: {e}")
        _holidays_cache = set()
        return _holidays_cache

def is_weekend(d: date) -> bool:
    """Returns True if date is Saturday or Sunday."""
    # Monday is 0, Sunday is 6
    return d.weekday() >= 5

def is_holiday(d: date) -> bool:
    """
    Returns True if date is a holiday.
    Checks against holidays loaded from data/holidays.json.
    """
    holidays = _load_holidays()
    return d in holidays

def get_current_date() -> date:
    """Returns current system date."""
    return datetime.now().date()
