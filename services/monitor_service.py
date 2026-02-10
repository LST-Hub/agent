"""
Main Operational Logic Service.
Orchestrates checks for missing timesheets, idle resources, and health.
"""

from datetime import date, timedelta
from typing import List

from database.connection import get_db_session
from database.repositories.timesheet_repo import TimesheetRepository
from database.repositories.user_repo import UserRepository
from services.email_service import EmailService
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

class OperationsMonitor:
    """ Service to run operational checks. """

    def __init__(self):
        self.email_service = EmailService()
        self.ignore_ids = settings.get_ignore_id_list()
        
    def _should_ignore_user(self, user_id: int) -> bool:
        """Check if user should be ignored based on ignore list."""
        return user_id in self.ignore_ids
        
    def check_missing_timesheets(self):
        """
        Checks for users who haven't logged time for the previous week.
        """
        logger.info("Running missing timesheet check...")
        
        # Example logic
        session = next(get_db_session())
        timesheet_repo = TimesheetRepository(session)
        user_repo = UserRepository(session)
        
        end_date = date.today()
        start_date = end_date - timedelta(days=7)
        print(f"Checking for missing timesheets from {start_date} to {end_date}...")
        
        missing_resource_ids = timesheet_repo.get_missing_timesheets(start_date, end_date)
        
        # Filter out ignored user IDs
        filtered_missing_ids = [uid for uid in missing_resource_ids if not self._should_ignore_user(uid)]
        
        if filtered_missing_ids:
            logger.warning(f"Found {len(filtered_missing_ids)} missing timesheets (after filtering {len(missing_resource_ids) - len(filtered_missing_ids)} ignored users).")
            print(f"Resources with missing timesheets: {filtered_missing_ids}")
            
            # Get user details for filtered IDs
            for user_id in filtered_missing_ids:
                user = user_repo.get_by_id(user_id)
                if user:
                    logger.info(f"Missing timesheet for user: {user.email} (ID: {user.id})")
            
            # Trigger notification
            self.email_service.send_alert(
                "sachin.ubale@lstconsultancy.com", 
                "Missing Timesheets Detected", 
                f"Resources {filtered_missing_ids} have not submitted timesheets."
            )
        else:
            logger.info("No missing timesheets found (after filtering ignored users).")
        
        session.close()
        
    def check_project_health(self):
        """
        Checks for project budget overruns.
        """
        logger.info("Running project health check...")
        # Placeholder for project health logic
        pass

# Test execution when running this file directly
if __name__ == "__main__":
    obj = OperationsMonitor()
    result = obj.check_missing_timesheets()
    print(result)