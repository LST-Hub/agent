"""
Data models for Project and Resource entities.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional
from utils.constants import ProjectStatus

@dataclass
class Resource:
    """Represents a team member or resource."""
    id: int
    name: str
    email: str
    role: str
    hourly_cost: float
    is_active: bool = True

@dataclass
class Project:
    """Represents a trackable project."""
    id: int
    name: str
    start_date: date
    end_date: Optional[date]
    budget_hours: float
    status: ProjectStatus
    manager_id: int
