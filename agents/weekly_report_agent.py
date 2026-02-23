from datetime import datetime, timedelta
from services.weekly_report_service import WeeklyReportService
from services.email_service import EmailService
from utils.date_utils import is_friday, get_current_week_start, get_previous_week_start_end
from config.settings import settings
from database.connection import get_db_session
import logging

logger = logging.getLogger(__name__)

class WeeklyReportAgent:
    def __init__(self):
        self.email_service = EmailService()
        self.pm_email = settings.get_pm_email()
    
    def run(self):
        """Run on Friday to generate reports and send to PM."""
        # Comment out for testing - uncomment for production
        # if not is_friday():
        #     logger.info("Not Friday, skipping weekly report generation.")
        #     return
        
        today = datetime.now().date()
        if today.weekday() == 0:  # Monday
            week_start, _ = get_previous_week_start_end()
            logger.info(f"Monday run detected. Using previous week start: {week_start.date()}")
        else:
            week_start = get_current_week_start()

        logger.info(f"Starting weekly report generation for week starting {week_start.date()}")
        
        # Get database session
        session_gen = get_db_session()
        session = next(session_gen)
        
        try:
            # Create service with session
            report_service = WeeklyReportService(session)
            
            # Generate and export weekly report to Excel
            logger.info("Fetching timesheet data from database...")
            file_url = report_service.generate_and_export_weekly_report(week_start)
            
            if file_url:
                # Send email to PM with Excel file link
                logger.info(f"Report generated successfully. File URL: {file_url}")
                self.send_report_email(file_url, week_start)
                logger.info("Weekly report email sent successfully.")
            else:
                logger.warning("No report generated - no data available for this week.")
                
        except Exception as e:
            logger.error(f"Error generating weekly report: {e}", exc_info=True)
            raise
        finally:
            try:
                session.close()
                logger.info("Database session closed.")
            except Exception as e:
                logger.error(f"Error closing session: {e}")
    
    def send_report_email(self, file_url: str, week_start: datetime):
        """Send report email to PM with Excel file link."""
        week_end = week_start + timedelta(days=6)
        
        if not self.pm_email:
            logger.error("PM_EMAIL not configured in settings!")
            raise ValueError("PM_EMAIL environment variable is not set")
        
        logger.info(f"Preparing email for PM: {', '.join(self.pm_email)}")
        
        subject = f"Weekly Timesheet Report - {week_start.strftime('%Y-W%U')}"
        
        body = f"""
        <html>
        <body>
            <h2>Weekly Timesheet Report</h2>
            <p><strong>Week:</strong> {week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}</p>
            
            <p>Please find the weekly timesheet report at the following link:</p>
            
            <p><a href="{file_url}">View Weekly Timesheet Report</a></p>
            
            <p>This report contains:</p>
            <ul>
                <li>Date</li>
                <li>Duration (hours)</li>
                <li>Description</li>
                <li>Task ID</li>
                <li>Project ID & Name</li>
                <li>Approval Status</li>
                <li>Rejection Details (if any)</li>
                <li>User Information</li>
            </ul>
            
            <p>Best regards,<br>Operations Agent</p>
        </body>
        </html>
        """
        
        logger.info(f"Sending email to {', '.join(self.pm_email)}...")
        success = self.email_service.send_alert(
            to_email=self.pm_email,
            subject=subject,
            body=body,
            is_html=True
        )
        
        if success:
            logger.info(f"Email sent successfully to {', '.join(self.pm_email)}")
        else:
            logger.error(f"Failed to send email to {', '.join(self.pm_email)}")
            raise Exception("Email sending failed")

# REMOVE THESE LINES - Run via main.py instead
# object = WeeklyReportAgent()
# object.run()