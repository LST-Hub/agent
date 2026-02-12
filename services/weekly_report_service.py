from datetime import datetime, timedelta
from typing import List, Dict
from database.repositories.timesheet_repo import TimesheetRepository
from database.repositories.user_repo import UserRepository
from database.repositories.project_repo import ProjectRepository
from services.sharepoint_excel_service import SharePointExcelService
from utils.date_utils import get_week_start_end
import logging
from database.connection import get_db_session

logger = logging.getLogger(__name__)

class WeeklyReportService:
    def __init__(self, session=None):
        if session is None:
            session_gen = get_db_session()
            self.session = next(session_gen)
            self.owns_session = True
        else:
            self.session = session
            self.owns_session = False
            
        self.timesheet_repo = TimesheetRepository(self.session)
        self.user_repo = UserRepository(self.session)
        self.project_repo = ProjectRepository(self.session)
        self.sharepoint_service = SharePointExcelService()
    
    def generate_and_export_weekly_report(self, week_start: datetime) -> str:
        """
        Generate weekly timesheet report and export to SharePoint Excel.
        Returns the SharePoint file URL.
        """
        week_end = week_start + timedelta(days=6)
        
        logger.info(f"Generating weekly report for {week_start.date()} to {week_end.date()}")
        
        # Get timesheet data for the week
        timesheets_data = self.timesheet_repo.get_weekly_timesheets_for_report(
            week_start.date(), week_end.date()
        )
        
        if not timesheets_data:
            logger.warning(f"No timesheets found for week {week_start.date()}")
            return ""
        
        # Export to SharePoint Excel
        file_url = self.sharepoint_service.update_excel_file(timesheets_data, week_start)
        
        logger.info(f"Weekly report exported successfully. URL: {file_url}")
        return file_url
    
    def generate_weekly_report(self, user_id: int, week_start: datetime):
        """Generate comprehensive weekly report for a user"""
        week_end = week_start + timedelta(days=6)
        
        # Get all submitted timesheets for the week
        timesheets = self.timesheet_repo.get_by_user_and_date_range(
            user_id, week_start, week_end, status="submitted"
        )
        
        report = {
            "user_id": user_id,
            "week_start": week_start,
            "week_end": week_end,
            "total_hours": 0,
            "projects": {},
            "tasks": [],
            "idle_hours": 0
        }
        
        expected_hours = 40  # 5 days * 8 hours
        
        for timesheet in timesheets:
            project_id = timesheet.project_id
            hours = timesheet.hours
            
            if project_id not in report["projects"]:
                project = self.project_repo.get_by_id(project_id)
                report["projects"][project_id] = {
                    "name": project.name,
                    "hours": 0,
                    "tasks": []
                }
            
            report["projects"][project_id]["hours"] += hours
            report["projects"][project_id]["tasks"].append({
                "task_id": timesheet.task_id,
                "description": timesheet.description,
                "hours": hours,
                "date": timesheet.date
            })
            report["total_hours"] += hours
        
        report["idle_hours"] = max(0, expected_hours - report["total_hours"])
        
        return report
    
    def get_pending_reports(self, week_start: datetime) -> List[int]:
        """Get users who submitted timesheets but don't have reports yet"""
        return self.timesheet_repo.get_users_with_unreported_timesheets(week_start)