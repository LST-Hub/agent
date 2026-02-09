"""
Timesheet Service: Implements core business logic for timesheet validations.
"""

from typing import List, Dict, Tuple
from datetime import date, timedelta
from sqlalchemy.orm import Session
from models.schemas import User, Timesheet, Project
from database.repositories.timesheet_repo import TimesheetRepository
from database.repositories.user_repo import UserRepository
from database.repositories.project_repo import ProjectRepository
from database.repositories.allocation_repo import ResourceAllocationRepository
from database.repositories.task_repo import TaskRepository
from utils.date_utils import is_weekend, is_holiday
from config.settings import settings

class TimesheetService:
    def __init__(self, session: Session):
        self.session = session
        self.timesheet_repo = TimesheetRepository(session)
        self.user_repo = UserRepository(session)
        self.project_repo = ProjectRepository(session)
        self.allocation_repo = ResourceAllocationRepository(session)
        self.task_repo = TaskRepository(session)
        self.ignore_ids = settings.get_ignore_id_list()

    def _filter_ignored_users(self, users: List[User]) -> List[User]:
        """Filter out users whose IDs are in the ignore list."""
        return [user for user in users if user.id not in self.ignore_ids]

    def get_missing_timesheet_users(self, target_date: date) -> List[User]:
        """
        Identify active users who have not submitted a timesheet for the given date.
        """
        if is_weekend(target_date) or is_holiday(target_date):
            return []

        active_users = self.user_repo.get_active_users()
        active_users = self._filter_ignored_users(active_users)
        missing_users = []

        for user in active_users:
            timesheets = self.timesheet_repo.get_by_user_and_date(user.id, target_date)
            
            # Missing if no timesheets found
            if not timesheets:
                missing_users.append(user)
                continue
            
            # Or if ALL timesheets for the day are 'Draft'
            # Assuming 'Draft' status implies not submitted
            all_draft = all(t.status.value == 'Draft' for t in timesheets)
            if all_draft:
                missing_users.append(user)
        
        return missing_users

    def get_total_duration(self, timesheets: List[Timesheet]) -> int:
        return sum(t.duration for t in timesheets)

    def get_idle_users(self, target_date: date, threshold_hours: int = 8) -> List[Dict]:
        """
        Identify users with total duration < 8 hours (or specific threshold).
        """
        if is_weekend(target_date) or is_holiday(target_date):
            return []

        active_users = self.user_repo.get_active_users()
        active_users = self._filter_ignored_users(active_users)
        idle_records = []

        for user in active_users:
            timesheets = self.timesheet_repo.get_by_user_and_date(user.id, target_date)
            total_duration = self.get_total_duration(timesheets)
            
            idle_hours = threshold_hours - total_duration
            
            if idle_hours > 0:
                # Breakdown by project
                breakdown = {}
                for t in timesheets:
                    proj = self.project_repo.get_by_id(t.projectId)
                    p_name = proj.name if proj else f"Project {t.projectId}"
                    breakdown[p_name] = breakdown.get(p_name, 0) + t.duration

                idle_records.append({
                    "user": user,
                    "total_worked": total_duration,
                    "idle_hours": idle_hours,
                    "breakdown": breakdown
                })
        
        return idle_records

    def get_overtime_users(self, target_date: date, threshold_hours: int = 8) -> List[Dict]:
        """
        Identify users with total duration > 8 hours.
        """
        if is_weekend(target_date) or is_holiday(target_date):
            return []

        active_users = self.user_repo.get_active_users()
        active_users = self._filter_ignored_users(active_users)
        overtime_records = []

        for user in active_users:
            timesheets = self.timesheet_repo.get_by_user_and_date(user.id, target_date)
            total_duration = self.get_total_duration(timesheets)
            
            if total_duration > threshold_hours:
                overtime_records.append({
                    "user": user,
                    "total_worked": total_duration,
                    "overtime_hours": total_duration - threshold_hours
                })
        
        return overtime_records

    def get_pending_approvals(self, days_pending: int) -> List[Timesheet]:
        return self.timesheet_repo.get_pending_approvals(days_pending)

    def get_rejected_needing_followup(self, days_since: int) -> List[Timesheet]:
        return self.timesheet_repo.get_rejected_no_update(days_since)

    def get_bad_descriptions(self, min_length: int = 10) -> List[Timesheet]:
        return self.timesheet_repo.get_bad_descriptions(min_length)

    def get_missing_tickets(self) -> List[Timesheet]:
        return self.timesheet_repo.get_missing_tickets()

    def get_allocation_mismatches(self, target_date: date) -> List[Dict]:
        """
        Compare ResourceAllocation vs Actual Timesheet duration.
        """
        allocations = self.allocation_repo.get_by_date(target_date)
        mismatches = []
        
        # Group allocations by (User, Project) -> Duration
        alloc_map = {} 
        for alloc in allocations:
            key = (alloc.allocatedUserId, alloc.projectId)
            alloc_map[key] = alloc_map.get(key, 0) + alloc.duration

        for (user_id, project_id), alloc_duration in alloc_map.items():
            # Get actuals
            timesheets = self.timesheet_repo.get_by_user_and_date(user_id, target_date)
            # Filter for project
            actual_duration = sum(t.duration for t in timesheets if t.projectId == project_id)
            
            if alloc_duration > actual_duration:
                user = self.user_repo.get_by_id(user_id)
                project = self.project_repo.get_by_id(project_id)
                if user and project:
                    mismatches.append({
                        "user": user,
                        "project": project,
                        "allocated": alloc_duration,
                        "actual": actual_duration,
                        "idle_on_project": alloc_duration - actual_duration
                    })
                
        return mismatches

    def get_consistent_underlogging(self, start_date: date, end_date: date, threshold_days: int = 3) -> List[User]:
        """
        Identify users with idle_hours > 2 on >= 3 days in the period.
        """
        active_users = self.user_repo.get_active_users()
        active_users = self._filter_ignored_users(active_users)
        underlogging_users = []
        
        # Calculate date range once
        delta_days = (end_date - start_date).days
        date_list = [start_date + timedelta(days=i) for i in range(delta_days + 1)]
        
        for user in active_users:
            timesheets = self.timesheet_repo.get_by_user_date_range(user.id, start_date, end_date)
            
            idle_days_count = 0
            for d in date_list:
                if is_weekend(d) or is_holiday(d):
                    continue
                
                daily_total = sum(t.duration for t in timesheets if t.date == d)
                idle_hours = 8 - daily_total
                
                if idle_hours > 2:
                    idle_days_count += 1
            
            if idle_days_count >= threshold_days:
                underlogging_users.append(user)
                
        return underlogging_users

from database.connection import db_manager
session = db_manager.get_session()
try:
        timesheet_service = TimesheetService(session)
        target_date = date(2026, 2, 6)
        
        missing_users = timesheet_service.get_missing_timesheet_users(target_date)
        
        print(f"Missing timesheets for {target_date}:")
        for user in missing_users:
            print(f"  - {user.name} ({user.email})")
        
        print(f"\nTotal: {len(missing_users)} users")
        
finally:
    session.close()