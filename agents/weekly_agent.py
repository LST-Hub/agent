"""
Weekly Agent: Orchestrates weekly timesheet compliances and notifications.
"""

import logging
from datetime import date, timedelta
from database.connection import get_db_session
from services.timesheet_service import TimesheetService
from services.email_service import EmailService
from utils.date_utils import get_current_date

logger = logging.getLogger(__name__)

class WeeklyAgent:
    def __init__(self):
        self.email_service = EmailService()

    def run(self):
        """
        Executes all weekly checks.
        """
        logger.info("Starting Weekly Agent execution...")
        current_date = get_current_date()
        
        # Calculate date range for the week (e.g., Mon - Fri)
        start_of_week = current_date - timedelta(days=current_date.weekday())
        end_of_week = start_of_week + timedelta(days=4)
        
        session_gen = get_db_session()
        session = next(session_gen)
        
        try:
            service = TimesheetService(session)
            
            # 9. Consistent Under-Logging (Weekly Pattern)
            self._check_consistent_underlogging(service, start_of_week, end_of_week)

        finally:
            session.close()
            logger.info("Weekly Agent execution finished.")

    def _check_consistent_underlogging(self, service: TimesheetService, start_date: date, end_date: date):
        logger.info("Checking consistent under-logging...")
        underlogging_users = service.get_consistent_underlogging(start_date, end_date, threshold_days=3)
        for user in underlogging_users:
            logger.info(f"User {user.email} consistently under-logged hours.")
            # Notify PM Logic here
            self.email_service.send_alert(
                to_email=user.email,
                subject="Weekly Under-Logging Alert",
                body=f"Hi {user.firstName}, you have consistently logged fewer hours this week."
            )
