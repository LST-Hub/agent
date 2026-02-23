"""
Billable Project Service: Handles billable project reporting and analysis.
"""

from typing import List, Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database.repositories.project_repo import ProjectRepository
from database.repositories.timesheet_repo import TimesheetRepository
from database.repositories.allocation_repo import ResourceAllocationRepository
from database.repositories.user_repo import UserRepository
from database.repositories.task_repo import TaskRepository
from utils.date_utils import is_weekend, is_holiday
import logging

logger = logging.getLogger(__name__)

class BillableProjectService:
    def __init__(self, session: Session):
        self.session = session
        self.project_repo = ProjectRepository(session)
        self.timesheet_repo = TimesheetRepository(session)
        self.allocation_repo = ResourceAllocationRepository(session)
        self.user_repo = UserRepository(session)
        self.task_repo = TaskRepository(session)
    
    def get_billable_projects_report(self, week_start: datetime) -> List[Dict]:
        """
        Generate billable projects report for a given week.
        
        Returns list of projects with:
        - Project details (name, end date, status)
        - Total allocated hours for the week
        - Total hours worked by resources
        - Approved billable hours
        - Over/under work analysis
        - Resource-wise breakdown with tasks
        """
        week_end = week_start + timedelta(days=6)
        
        # Get all billable projects
        billable_projects = self._get_billable_projects()
        
        report_data = []
        
        for project in billable_projects:
            project_data = self._analyze_project_for_week(
                project, 
                week_start, 
                week_end
            )
            report_data.append(project_data)
        
        return report_data
    
    def _get_billable_projects(self) -> List:
        """Get all active billable projects."""
        projects = self.project_repo.get_active_projects()
        return [p for p in projects if p.billable]
    
    def _analyze_project_for_week(
        self, 
        project, 
        week_start: datetime, 
        week_end: datetime
    ) -> Dict:
        """Analyze a single project for the given week."""
        
        # Get all timesheets for this project in the week
        timesheets = self._get_project_timesheets(
            project.id, 
            week_start.date(), 
            week_end.date()
        )
        
        # Get allocations for this project in the week
        allocations = self._get_project_allocations(
            project.id, 
            week_start.date(), 
            week_end.date()
        )
        
        # Calculate totals
        total_allocated = sum(a.duration for a in allocations) / 3600  # Convert to hours
        total_worked = sum(t.duration for t in timesheets) / 3600
        approved_hours = sum(
            t.duration for t in timesheets if t.approved
        ) / 3600
        
        # Get resource breakdown
        resources = self._get_resource_breakdown(timesheets, allocations)
        
        # Check for missing timesheets
        missing_timesheets = self._check_missing_timesheets(
            project.id, 
            allocations, 
            timesheets, 
            week_start.date(), 
            week_end.date()
        )
        
        # Get PM name
        pm_name = "Unknown"
        if project.pmId:
            pm = self.user_repo.get_by_id(project.pmId)
            if pm:
                pm_name = pm.name
        
        # Get status name
        status_name = self._get_status_name(project.statusId)
        
        return {
            "project_id": project.id,
            "project_name": project.name,
            "project_description": project.description,
            "estimated_end_date": project.estimatedEndDate,
            "actual_end_date": project.actualEndDate,
            "status_id": project.statusId,
            "status_name": status_name,
            "pm_id": project.pmId,
            "pm_name": pm_name,
            "total_allocated_hours": round(total_allocated, 2),
            "total_worked_hours": round(total_worked, 2),
            "approved_billable_hours": round(approved_hours, 2),
            "variance": round(total_worked - total_allocated, 2),
            "variance_type": "Over" if total_worked > total_allocated else "Under",
            "resources": resources,
            "missing_timesheets": missing_timesheets,
            "week_start": week_start.date(),
            "week_end": week_end.date()
        }
    
    def _get_status_name(self, status_id: int) -> str:
        """Get status name from status ID."""
        from sqlalchemy import text
        
        try:
            query = text("SELECT name FROM Status WHERE id = :status_id")
            result = self.session.execute(query, {"status_id": status_id}).fetchone()
            return result.name if result else "Unknown"
        except Exception as e:
            logger.warning(f"Failed to fetch status name for ID {status_id}: {e}")
            return "Unknown"
    
    def _get_project_timesheets(self, project_id: int, start_date, end_date) -> List:
        """Get all timesheets for a project within date range."""
        all_timesheets = self.timesheet_repo.get_by_date_range(start_date, end_date)
        return [t for t in all_timesheets if t.projectId == project_id]
    
    def _get_project_allocations(self, project_id: int, start_date, end_date) -> List:
        """Get all allocations for a project within date range."""
        from datetime import timedelta
        
        allocations = []
        current_date = start_date
        
        while current_date <= end_date:
            daily_allocations = self.allocation_repo.get_by_date(current_date)
            project_allocations = [
                a for a in daily_allocations if a.projectId == project_id
            ]
            allocations.extend(project_allocations)
            current_date += timedelta(days=1)
        
        return allocations
    
    def _get_resource_breakdown(self, timesheets, allocations) -> List[Dict]:
        """Get resource-wise breakdown with tasks."""
        resource_map = {}
        
        # Process timesheets
        for timesheet in timesheets:
            user_id = timesheet.createdById
            
            if user_id not in resource_map:
                user = self.user_repo.get_by_id(user_id)
                resource_map[user_id] = {
                    "user_id": user_id,
                    "user_name": user.name if user else "Unknown",
                    "user_email": user.email if user else "Unknown",
                    "allocated_hours": 0,
                    "worked_hours": 0,
                    "approved_hours": 0,
                    "tasks": {}
                }
            
            # Add worked hours
            resource_map[user_id]["worked_hours"] += timesheet.duration / 3600
            
            if timesheet.approved:
                resource_map[user_id]["approved_hours"] += timesheet.duration / 3600
            
            # Track tasks
            task_id = timesheet.taskId
            if task_id:
                if task_id not in resource_map[user_id]["tasks"]:
                    task = self.task_repo.get_by_id(task_id)
                    resource_map[user_id]["tasks"][task_id] = {
                        "task_id": task_id,
                        "task_name": task.name if task else "Unknown",
                        "hours_spent": 0
                    }
                
                resource_map[user_id]["tasks"][task_id]["hours_spent"] += (
                    timesheet.duration / 3600
                )
        
        # Process allocations
        for allocation in allocations:
            user_id = allocation.allocatedUserId
            
            if user_id not in resource_map:
                user = self.user_repo.get_by_id(user_id)
                resource_map[user_id] = {
                    "user_id": user_id,
                    "user_name": user.name if user else "Unknown",
                    "user_email": user.email if user else "Unknown",
                    "allocated_hours": 0,
                    "worked_hours": 0,
                    "approved_hours": 0,
                    "tasks": {}
                }
            
            resource_map[user_id]["allocated_hours"] += allocation.duration / 3600
        
        # Convert tasks dict to list and round hours
        resources = []
        for resource in resource_map.values():
            resource["tasks"] = list(resource["tasks"].values())
            resource["allocated_hours"] = round(resource["allocated_hours"], 2)
            resource["worked_hours"] = round(resource["worked_hours"], 2)
            resource["approved_hours"] = round(resource["approved_hours"], 2)
            resource["variance"] = round(
                resource["worked_hours"] - resource["allocated_hours"], 2
            )
            
            for task in resource["tasks"]:
                task["hours_spent"] = round(task["hours_spent"], 2)
            
            resources.append(resource)
        
        return resources
    
    def _check_missing_timesheets(
        self, 
        project_id: int, 
        allocations, 
        timesheets, 
        start_date, 
        end_date
    ) -> List[Dict]:
        """Check for allocated resources who haven't submitted timesheets."""
        from datetime import timedelta
        
        missing = []
        
        # Get unique user IDs from allocations
        allocated_users = set(a.allocatedUserId for a in allocations)
        
        # Check each day for each allocated user
        current_date = start_date
        while current_date <= end_date:

            # ✅ Skip weekends and holidays
            if is_weekend(current_date) or is_holiday(current_date):
                current_date += timedelta(days=1)
                continue

            for user_id in allocated_users:
                # Check if user has timesheet for this day
                daily_timesheets = [
                    t for t in timesheets 
                    if t.createdById == user_id and t.date == current_date
                ]
                
                if not daily_timesheets:
                    user = self.user_repo.get_by_id(user_id)
                    missing.append({
                        "user_id": user_id,
                        "user_name": user.name if user else "Unknown",
                        "user_email": user.email if user else "Unknown",
                        "missing_date": current_date
                    })
            
            current_date += timedelta(days=1)
        
        return missing