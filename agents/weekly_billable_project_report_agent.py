"""
Billable Project Report Agent - Generates weekly billable project reports.
"""

from datetime import datetime, timedelta
from typing import List, Dict
from services.billable_project_service import BillableProjectService
from services.billable_excel_service import BillableExcelService
from services.email_service import EmailService
from utils.date_utils import get_current_week_start
from config.settings import settings
from database.connection import get_db_session
import logging

logger = logging.getLogger(__name__)

class BillableReportAgent:
    def __init__(self):
        self.excel_service = BillableExcelService()
        self.email_service = EmailService()
        self.pm_email = settings.get_pm_email()
    
    def run(self):
        """Generate billable project report for the current week."""
        week_start = get_current_week_start()
        logger.info(f"Generating billable project report for week {week_start.date()}")
        
        session_gen = get_db_session()
        session = next(session_gen)
        
        try:
            # Generate report data
            service = BillableProjectService(session)
            report_data = service.get_billable_projects_report(week_start)
            
            if not report_data:
                logger.warning("No billable projects found for this week")
                return
            
            # Export to Excel on SharePoint
            file_url = self.excel_service.export_billable_report(
                report_data, 
                week_start
            )
            
            # Send notification email
            self.send_notification_email(file_url, week_start, report_data)
            
            logger.info("Billable project report completed successfully")
            
        except Exception as e:
            logger.error(f"Error generating billable report: {e}", exc_info=True)
            raise
        finally:
            session.close()
    
    def send_notification_email(
        self, 
        file_url: str, 
        week_start: datetime,
        report_data: List[Dict]
    ):
        """Send email notification with report summary."""
        week_end = week_start + timedelta(days=6)
        
        # Calculate summary statistics
        total_projects = len(report_data)
        total_allocated = sum(p["total_allocated_hours"] for p in report_data)
        total_worked = sum(p["total_worked_hours"] for p in report_data)
        total_approved = sum(p["approved_billable_hours"] for p in report_data)
        
        subject = f"Billable Projects Report - Week {week_start.strftime('%Y-W%U')}"
        
        body = f"""
        <html>
        <body>
            <h2>Billable Projects Weekly Report</h2>
            <p><strong>Week:</strong> {week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}</p>
            
            <h3>Summary</h3>
            <ul>
                <li><strong>Total Billable Projects:</strong> {total_projects}</li>
                <li><strong>Total Allocated Hours:</strong> {total_allocated:.2f}</li>
                <li><strong>Total Worked Hours:</strong> {total_worked:.2f}</li>
                <li><strong>Total Approved Billable Hours:</strong> {total_approved:.2f}</li>
                <li><strong>Overall Variance:</strong> {(total_worked - total_allocated):.2f} hours</li>
            </ul>
            
            <p><a href="{file_url}">View Complete Billable Projects Report</a></p>
            
            <h3>Report Contains:</h3>
            <ul>
                <li><strong>Project Summary:</strong> Overview of all billable projects</li>
                <li><strong>Resource Details:</strong> Resource-wise breakdown with tasks</li>
                <li><strong>Missing Timesheets:</strong> Allocated resources with missing entries</li>
            </ul>
            
            <p>Best regards,<br>Operations Agent</p>
        </body>
        </html>
        """
        
        self.email_service.send_alert(
            to_email=self.pm_email,
            subject=subject,
            body=body,
            is_html=True
        )

if __name__ == "__main__":
    agent = BillableReportAgent()
    agent.run()