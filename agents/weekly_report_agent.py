from datetime import datetime, timedelta
from services.weekly_report_service import WeeklyReportService
from services.email_service import EmailService
from utils.date_utils import is_friday, get_current_week_start, get_previous_week_start_end
from config.settings import settings
from database.connection import get_db_session
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class WeeklyReportAgent:
    def __init__(self):
        self.email_service = EmailService()
        self.pm_email = settings.get_pm_email()
    
    def _resolve_week_start(self, week_start: Optional[datetime] = None) -> datetime:
        if week_start is not None:
            return week_start

        today = datetime.now().date()
        if today.weekday() == 0:  # Monday
            week_start, _ = get_previous_week_start_end()
            logger.info(f"Monday run detected. Using previous week start: {week_start.date()}")
        else:
            week_start = get_current_week_start()
        return week_start

    def generate_weekly_timesheet_report(self, week_start=None):
        week_start = self._resolve_week_start(week_start)
        session_gen = get_db_session()
        session = next(session_gen)
        try:
            report_service = WeeklyReportService(session)
            timesheet_result, summary_result, project_result = report_service.generate_and_export_weekly_report(week_start)
            return week_start, timesheet_result, summary_result, project_result
        finally:
            session.close()

    def send_report_email(self, week_start: datetime, timesheet_result, summary_result, project_result):
        """Send report email to PM with three separate Excel files attached."""
        week_end = week_start + timedelta(days=6)

        if not self.pm_email:
            logger.error("PM_EMAIL not configured in settings!")
            raise ValueError("PM_EMAIL environment variable is not set")

        subject = f"Weekly Timesheet Report - {week_start.strftime('%Y-W%U')}"

        body = f"""
        <html>
        <body>
            <h2>Weekly Timesheet Report</h2>
            <p><strong>Week:</strong> {week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}</p>
            <p>Please find the three weekly report files attached to this email:</p>
            <ul>
                <li><strong>Weekly Timesheets</strong> – detailed per-entry log</li>
                <li><strong>Weekly Summary</strong> – aggregated hours by user/project</li>
                <li><strong>Project Summary</strong> – project-level breakdown</li>
            </ul>
            <p>Best regards,<br>Operations Agent</p>
        </body>
        </html>
        """

        attachments = []
        for result in [timesheet_result, summary_result, project_result]:
            if result:
                _, excel_bytes, file_name = result
                if excel_bytes and file_name:
                    attachments.append({'filename': file_name, 'data': excel_bytes})

        logger.info(f"Sending email with {len(attachments)} attachment(s) to {', '.join(self.pm_email)}...")
        success = self.email_service.send_alert(
            to_email=self.pm_email,
            subject=subject,
            body=body,
            is_html=True,
            attachments=attachments
        )

        if success:
            logger.info(f"Email sent successfully to {', '.join(self.pm_email)}")
        else:
            logger.error(f"Failed to send email to {', '.join(self.pm_email)}")
            raise Exception("Email sending failed")

    def run(self):
        """Run and send weekly timesheet report email."""
        try:
            week_start, timesheet_result, summary_result, project_result = self.generate_weekly_timesheet_report()
            if any([timesheet_result, summary_result, project_result]):
                self.send_report_email(week_start, timesheet_result, summary_result, project_result)
                logger.info("Weekly report email sent successfully.")
            else:
                logger.warning("No report generated - no data available for this week.")
        except Exception as e:
            logger.error(f"Error generating weekly report: {e}", exc_info=True)
            raise