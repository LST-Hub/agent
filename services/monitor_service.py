"""
Main Operational Logic Service.
Orchestrates checks for missing timesheets, idle resources, and health.
"""

from datetime import date, timedelta
from typing import List

from database.connection import get_db_session
from database.repositories.timesheet_repo import TimesheetRepository
from services.email_service import EmailService
import logging

logger = logging.getLogger(__name__)

class OperationsMonitor:
    """ Service to run operational checks. """

    def __init__(self):
        self.email_service = EmailService()
        
    def check_missing_timesheets(self):
        """
        Checks for users who haven't logged time for the previous week.
        """
        logger.info("Running missing timesheet check...")
        
        # Example logic
        session = next(get_db_session())
        repo = TimesheetRepository(session)
        
        end_date = date.today()
        start_date = end_date - timedelta(days=7)
        
        missing_resource_ids = repo.get_missing_timesheets(start_date, end_date)
        
        if missing_resource_ids:
            logger.warning(f"Found {len(missing_resource_ids)} missing timesheets.")
            # Trigger notification
            self.email_service.send_alert(
                "admin@example.com", 
                "Missing Timesheets Detected", 
                f"Resources {missing_resource_ids} have not submitted timesheets."
            )
        
    def check_project_health(self):
        """
        Checks for project budget overruns.
        """
        logger.info("Running project health check...")
        # Placeholder for project health logic
        pass
