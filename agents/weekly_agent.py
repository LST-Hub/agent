"""
Weekly Agent: Orchestrates weekly timesheet compliances and notifications.
"""
 
import logging
from datetime import date, timedelta
from database.connection import get_db_session
from services.timesheet_service import TimesheetService
from services.email_service import EmailService
from utils.date_utils import get_current_date, is_weekend, is_holiday
from utils.email_resolver import EmailResolver
from config.settings import settings
from typing import List, Dict
import os
from dotenv import load_dotenv
 
load_dotenv()
 
logger = logging.getLogger(__name__)
 
class WeeklyAgent:
    def __init__(self):
        self.email_service = EmailService()
        self.email_resolver = EmailResolver()
        self.ignore_ids = settings.get_ignore_id_list()
        self.pm_email = settings.get_pm_email()  # PM email from config
 
    def _should_ignore_user(self, user_id: int) -> bool:
        """Check if user should be ignored based on ignore list."""
        return user_id in self.ignore_ids
 
    def run(self):
        """
        Executes all weekly checks.
        """
        logger.info("Starting Weekly Agent execution...")
        current_date = get_current_date()
       
        # Calculate date range - check last 10 days for all missing timesheets
        days_to_check = 10  # Look back 10 days
        start_date = current_date - timedelta(days=days_to_check)
        end_date = current_date - timedelta(days=1)  # Up to yesterday
       
        session_gen = get_db_session()
        session = next(session_gen)
       
        try:
            service = TimesheetService(session)
           
            # Send weekly PM notifications for missing timesheets
            self._send_weekly_pm_notifications(service, start_date, end_date)
           
            # 9. Consistent Under-Logging (Weekly Pattern)
            # self._check_consistent_underlogging(service, start_date, end_date)
 
        finally:
            session.close()
            logger.info("Weekly Agent execution finished.")
 
    def _send_weekly_pm_notifications(self, service: TimesheetService, start_date: date, end_date: date):
        """
        Send weekly consolidated notification about missing and incomplete timesheets.
        Single table format showing all resources and their missing/incomplete dates.
        """
        logger.info(f"Sending weekly PM notification for period {start_date} to {end_date}...")
       
        # Generate list of all working days in the period
        dates_to_check = []
        current = start_date
        while current <= end_date:
            if not is_weekend(current) and not is_holiday(current):
                dates_to_check.append(current)
            current += timedelta(days=1)
       
        logger.info(f"Checking {len(dates_to_check)} working days for missing timesheets...")
       
        # Collect all missing and incomplete timesheets across all dates
        user_missing_data = {}  # user_id -> {user, missing_dates, incomplete_dates, projects}
       
        for target_date in dates_to_check:
            # Check for completely missing timesheets
            missing_records = service.get_missing_timesheet_users(target_date)
           
            for record in missing_records:
                user = record['user']
               
                # Skip ignored users
                if self._should_ignore_user(user.id):
                    continue
               
                # Initialize user entry if not exists
                if user.id not in user_missing_data:
                    # Get projects for this user
                    project_ids = service.project_user_repo.get_projects_by_user(user.id)
                    project_names = []
                    for pid in project_ids:
                        project = service.project_repo.get_by_id(pid)
                        if project:
                            project_names.append(project.name)
                   
                    user_missing_data[user.id] = {
                        'user': user,
                        'missing_dates': [],
                        'incomplete_dates': {},
                        'projects': ', '.join(project_names) if project_names else 'N/A'
                    }
               
                # Add missing date
                user_missing_data[user.id]['missing_dates'].append(target_date)
            
            # Check for incomplete timesheets (underlogged hours)
            idle_records = service.get_idle_users(target_date)
            
            for record in idle_records:
                user = record['user']
                
                # Skip ignored users
                if self._should_ignore_user(user.id):
                    continue
                
                # Initialize user entry if not exists
                if user.id not in user_missing_data:
                    # Get projects for this user
                    project_ids = service.project_user_repo.get_projects_by_user(user.id)
                    project_names = []
                    for pid in project_ids:
                        project = service.project_repo.get_by_id(pid)
                        if project:
                            project_names.append(project.name)
                   
                    user_missing_data[user.id] = {
                        'user': user,
                        'missing_dates': [],
                        'incomplete_dates': {},
                        'projects': ', '.join(project_names) if project_names else 'N/A'
                    }
                
                # Only add if not already marked as completely missing
                if target_date not in user_missing_data[user.id]['missing_dates']:
                    # Store idle time in seconds
                    idle_seconds = record['idle_hours']
                    user_missing_data[user.id]['incomplete_dates'][target_date] = idle_seconds
       
        # If no missing timesheets, don't send email
        if not user_missing_data:
            logger.info("No missing timesheets found for this period.")
            return
       
        # Build email body with HTML table
        body_html = self._build_html_email_body(user_missing_data, start_date, end_date)
       
        logger.info(f"Sending weekly PM notification to {self.pm_email} for {len(user_missing_data)} users")
        print(f"\n{'='*80}")
        print(f"PM Email: {self.pm_email}")
        print(body_html)
        print(f"{'='*80}\n")
       
        # Send email with HTML format
        self.email_service.send_alert(
            to_email=self.pm_email,
            subject=f"Weekly Missing Timesheets Report ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})",
            body=body_html,
            is_html=True
        )
 
    def _format_date_indian(self, date_obj: date) -> str:
        """Format date to Indian format DD-MM-YYYY"""
        return date_obj.strftime('%d-%m-%Y')
 
    def _build_html_email_body(self, user_missing_data: Dict, start_date: date, end_date: date) -> str:
        """
        Build HTML email body with single table format for all missing and incomplete timesheets.
        """
        html_lines = []
       
        # Email header
        html_lines.append("<html><body>")
        html_lines.append("<p>Hi,</p>")
        html_lines.append(f"<p>Here is the weekly report for missing/incomplete timesheets from <strong>{self._format_date_indian(start_date)}</strong> to <strong>{self._format_date_indian(end_date)}</strong>:</p>")
        html_lines.append("<br/>")
       
        # Create single table for all users
        html_lines.append('<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1200px;">')
        html_lines.append('<thead>')
        html_lines.append('<tr style="background-color: #4CAF50; color: white;">')
        html_lines.append('<th style="text-align: left; padding: 10px;">Resource Name</th>')
        html_lines.append('<th style="text-align: left; padding: 10px;">Email</th>')
        html_lines.append('<th style="text-align: center; padding: 10px;">Missing Days Count</th>')
        html_lines.append('<th style="text-align: left; padding: 10px;">Missing Timesheet Dates</th>')
        html_lines.append('</tr>')
        html_lines.append('</thead>')
        html_lines.append('<tbody>')
       
        # Sort users by name
        sorted_users = sorted(user_missing_data.items(), key=lambda x: x[1]['user'].name)
       
        # Add rows for each user
        for user_id, data in sorted_users:
            user = data['user']
            missing_dates = sorted(data['missing_dates'])
            incomplete_dates = data['incomplete_dates']
            projects = data['projects']
            
            # Build date strings with missing and incomplete dates
            date_strings = []
            
            # Add completely missing dates
            for missing_date in missing_dates:
                date_strings.append(self._format_date_indian(missing_date))
            
            # Add incomplete dates with hours remaining
            for incomplete_date in sorted(incomplete_dates.keys()):
                idle_seconds = incomplete_dates[incomplete_date]
                idle_hours = idle_seconds / 3600
                
                # Format hours nicely
                if idle_hours >= 1:
                    hours_str = f"{idle_hours:.1f} hrs" if idle_hours != 1 else "1 hr"
                else:
                    minutes = int((idle_hours * 60))
                    hours_str = f"{minutes} mins"
                
                date_strings.append(f"{self._format_date_indian(incomplete_date)} ({hours_str} remaining)")
            
            missing_days_count = len(missing_dates) + len(incomplete_dates)
            dates_str = ", ".join(date_strings)
           
            html_lines.append('<tr>')
            html_lines.append(f'<td style="padding: 10px;">{user.name}</td>')
            html_lines.append(f'<td style="padding: 10px;">{user.email}</td>')
            html_lines.append(f'<td style="padding: 10px; text-align: center; font-weight: bold;">{missing_days_count}</td>')
            html_lines.append(f'<td style="padding: 10px;">{dates_str}</td>')
            html_lines.append('</tr>')
       
        html_lines.append('</tbody>')
        html_lines.append('</table>')
        html_lines.append('<br/>')
       
        # Summary
        total_users = len(user_missing_data)
        total_missing_entries = sum(len(data['missing_dates']) + len(data['incomplete_dates']) for data in user_missing_data.values())
        html_lines.append(f'<p><strong>Summary:</strong> {total_users} resources with {total_missing_entries} missing/incomplete timesheet entries.</p>')
       
        # Email footer
        html_lines.append('<p>Please follow up with the team members to ensure timesheets are submitted.</p>')
        html_lines.append('<p>Thank you,<br/>Timesheet Monitoring System</p>')
        html_lines.append('</body></html>')
       
        return '\n'.join(html_lines)
 
    def _check_consistent_underlogging(self, service: TimesheetService, start_date: date, end_date: date):
        logger.info("Checking consistent under-logging...")
        underlogging_users = service.get_consistent_underlogging(start_date, end_date, threshold_days=3)
       
        for user in underlogging_users:
            # Skip ignored users
            if self._should_ignore_user(user.id):
                logger.debug(f"Skipping ignored user {user.id} - {user.email}")
                continue
               
            logger.info(f"User {user.email} consistently under-logged hours.")
           
            # Resolve office email
            user_email = self.email_resolver.get_office_email(user.id) or user.email
            supervisor_email = None
            if user.supervisorId and not self._should_ignore_user(user.supervisorId):
                supervisor_email = self.email_resolver.get_office_email(user.supervisorId)
           
            # Notify User and Supervisor
            # self.email_service.send_alert(
            #     to_email=user_email,
            #     subject="Weekly Under-Logging Alert",
            #     body=f"Hi {user.firstName}, you have consistently logged fewer hours this week.",
            #     cc_email=supervisor_email
            # )
 
# Test if running directly
# if __name__ == "__main__":
#     obj = WeeklyAgent()
#     obj.run()
