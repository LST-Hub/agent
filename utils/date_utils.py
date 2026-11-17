"""
Date utility functions.
"""

from datetime import date, datetime, timedelta
import json
import os

# Cache for holidays loaded from JSON
_holidays_cache = None
_floater_holidays_cache = None

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
                    if holiday.get('type') == 'floater':
                        continue
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

def _load_floater_holidays():
    """Load floater holidays mapped by date -> set of employee IDs."""
    global _floater_holidays_cache
    if _floater_holidays_cache is not None:
        return _floater_holidays_cache
    
    try:
        # Get the path to the holidays JSON file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        holidays_file = os.path.join(project_root, 'data', 'holidays.json')
        
        if os.path.exists(holidays_file):
            with open(holidays_file, 'r') as f:
                holidays_data = json.load(f)
                # Convert string dates to date objects
                _floater_holidays_cache = {}
                for holiday in holidays_data:
                    if holiday.get('type') == "floater":
                        #date_str = holiday['date']
                        holiday_date = datetime.strptime(holiday['date'], '%Y-%m-%d').date()
                        employee_ids = set(holiday.get('employee_ids', []))
                        _floater_holidays_cache[holiday_date] = employee_ids
                return _floater_holidays_cache
        else:
            # If file doesn't exist, return empty set
            _floater_holidays_cache = {}
            return _floater_holidays_cache
    except Exception as e:
        print(f"Error loading floater holidays: {e}")
        _floater_holidays_cache = {}
        return _floater_holidays_cache

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

def is_floater_holiday(d: date, user_id: int) -> bool:
    """Returns True if date is a floater holiday for this specific user."""
    floaters = _load_floater_holidays()
    return d in floaters and user_id in floaters[d]

def get_current_date() -> date:
    """
    Returns current system date.
    For local testing, AGENT_TODAY=YYYY-MM-DD pretends today is that date.
    """
    override = (os.getenv("AGENT_TODAY") or "").strip()
    if override:
        return datetime.strptime(override, "%Y-%m-%d").date()
    return datetime.now().date()

def is_friday(d: date = None) -> bool:
    """
    Returns True if the given date (or today) is Friday.
    
    Args:
        d: Date to check. If None, uses current date.
    
    Returns:
        True if the date is Friday, False otherwise.
    """
    if d is None:
        d = get_current_date()
    # Monday is 0, Friday is 4
    return d.weekday() == 4

def get_week_start_end(d: date = None) -> tuple[datetime, datetime]:
    """
    Get the start (Monday) and end (Sunday) of the week for a given date.
    
    Args:
        d: Date to get week for. If None, uses current date.
    
    Returns:
        Tuple of (week_start, week_end) as datetime objects.
    """
    if d is None:
        d = get_current_date()
    
    # Convert to datetime if it's a date
    if isinstance(d, date) and not isinstance(d, datetime):
        d = datetime.combine(d, datetime.min.time())
    
    # Get the Monday of the current week (weekday 0)
    days_since_monday = d.weekday()
    week_start = d - timedelta(days=days_since_monday)
    
    # Get the Sunday of the current week (6 days after Monday)
    week_end = week_start + timedelta(days=6)
    
    # Reset time to start of day for week_start and end of day for week_end
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_end.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    return week_start, week_end

def get_current_week_start(d: date = None) -> datetime:
    """
    Get the start of the week (Monday) for a given date.
    
    Args:
        d: Date to get week start for. If None, uses current date.
    
    Returns:
        Monday of the week as datetime object.
    """
    week_start, _ = get_week_start_end(d)
    return week_start

def get_current_week_end(d: date = None) -> datetime:
    """
    Get the end of the week (Sunday) for a given date.
    
    Args:
        d: Date to get week end for. If None, uses current date.
    
    Returns:
        Sunday of the week as datetime object.
    """
    _, week_end = get_week_start_end(d)
    return week_end

def get_previous_week_start_end() -> tuple[datetime, datetime]:
    """
    Get the start and end dates of the previous week.
    
    Returns:
        Tuple of (previous_week_start, previous_week_end) as datetime objects.
    """
    today = get_current_date()
    last_week = today - timedelta(days=7)
    return get_week_start_end(last_week)
