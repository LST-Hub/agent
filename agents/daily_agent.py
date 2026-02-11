"""
Daily Agent: Orchestrates daily timesheet compliances and notifications.
"""

import logging
from datetime import date, timedelta
from database.connection import get_db_session
from services.timesheet_service import TimesheetService
from services.email_service import EmailService
from utils.date_utils import get_current_date
from utils.email_resolver import EmailResolver
from config.settings import settings
from utils.date_utils import is_weekend, is_holiday
from typing import List

logger = logging.getLogger(__name__)

class DailyAgent:
    def __init__(self):
        self.email_service = EmailService()
        self.email_resolver = EmailResolver()
        self.ignore_ids = settings.get_ignore_id_list()

    def _should_ignore_user(self, user_id: int) -> bool:
        """Check if user should be ignored based on ignore list."""
        return user_id in self.ignore_ids

    def run(self):
        """
        Executes all daily checks.
        """
        logger.info("Starting Daily Agent execution...")
        current_date = get_current_date()
        
        # Using a context manager for the session is safer
        session_gen = get_db_session()
        session = next(session_gen)
        
        try:
            service = TimesheetService(session)
            
            # 1. Missing Timesheet Detection
            self._check_missing_timesheets(service, current_date)
            
            # 2. Idle Time Detection
            # self._check_idle_time(service, current_date)
            
            # # 3. Overtime Detection
            # self._check_overtime(service, current_date)
            
            # # 4. Pending Approval Reminder
            # self._check_pending_approvals(service)
            
            # # 5. Rejected Timesheet Follow-up
            # self._check_rejected_followup(service)
            
            # # 6. Missing or Poor Description Check
            # self._check_bad_descriptions(service)
            
            # # 7. Missing Ticket for Billable Work
            # self._check_missing_tickets(service)
            
            # # 8. Allocation vs Actual Mismatch
            # self._check_allocation_mismatch(service, current_date)

        finally:
            session.close()
            logger.info("Daily Agent execution finished.")

    def _check_missing_timesheets(self, service: TimesheetService, current_date: date, days_back: int = 30):
        """
        Check for missing timesheets for the last N days (excluding weekends/holidays).
        Send notifications to users and PMs.
        """
        logger.info(f"Checking missing timesheets for last {days_back} days...")
        
        # Generate date range
        dates_to_check = []
        for i in range(1, days_back + 1):
            check_date = current_date - timedelta(days=i)
            if not is_weekend(check_date) and not is_holiday(check_date):
                dates_to_check.append(check_date)
        
        # Collect missing dates per user
        user_missing_dates = {}
        
        # Check each date
        for target_date in dates_to_check:
            missing_records = service.get_missing_timesheet_users(target_date)
            
            for record in missing_records:
                user = record['user']
                missing_date = record['missing_date']
                
                if self._should_ignore_user(user.id):
                    logger.debug(f"Skipping ignored user {user.id} - {user.email}")
                    continue
                
                if user.id not in user_missing_dates:
                    user_missing_dates[user.id] = {
                        'user': user,
                        'missing_dates': []
                    }
                user_missing_dates[user.id]['missing_dates'].append(missing_date)
        
        # Send emails to users
        for user_id, data in user_missing_dates.items():
            user = data['user']
            missing_dates = sorted(data['missing_dates'])
            
            logger.info(f"User {user.email} missing timesheets for {len(missing_dates)} dates.")
            
            user_email = self.email_resolver.get_office_email(user.id) or user.email
            print(f"User {user.id} ({user.email}): Missing timesheets for {len(missing_dates)} dates: {missing_dates}")
            
            supervisor_email = None
            if user.supervisorId and not self._should_ignore_user(user.supervisorId):
                supervisor_email = self.email_resolver.get_office_email(user.supervisorId)
            
            if len(missing_dates) == 1:
                dates_str = str(missing_dates[0])
                subject = "Missing Timesheet Reminder"
                body = f"Hi {user.firstName}, please submit your timesheet for {dates_str}."
            else:
                dates_str = ", ".join(str(d) for d in missing_dates)
                subject = "Missing Timesheets Reminder"
                body = f"Hi {user.firstName}, please submit your timesheets for the following dates: {dates_str}."
            
            # Uncomment to send email
            # self.email_service.send_alert(
            #     to_email=user_email,
            #     subject=subject,
            #     body=body,
            #     cc_email=supervisor_email
            # )
        
        # Send notifications to PMs
        # self._notify_pms_missing_timesheets(service, dates_to_check)

    # def _check_idle_time(self, service: TimesheetService, target_date: date):
    #     logger.info("Checking idle time...")
    #     idle_records = service.get_idle_users(target_date)
        
    #     for record in idle_records:
    #         user = record['user']
            
    #         # Skip ignored users
    #         if self._should_ignore_user(user.id):
    #             logger.debug(f"Skipping ignored user {user.id} - {user.email}")
    #             continue
                
    #         logger.info(f"User {user.email} has {record['idle_hours']} idle hours.")
            
    #         # Resolve office emails
    #         user_email = self.email_resolver.get_office_email(user.id) or user.email
    #         supervisor_email = None
    #         if user.supervisorId and not self._should_ignore_user(user.supervisorId):
    #             supervisor_email = self.email_resolver.get_office_email(user.supervisorId)

    #         # Notify User and Supervisor
    #         self.email_service.send_alert(
    #             to_email=user_email,
    #             subject="Idle Time Alert",
    #             body=f"Hi {user.firstName}, you have {record['idle_hours']} idle hours for {target_date}.",
    #             cc_email=supervisor_email
    #         )
            
    # def _check_overtime(self, service: TimesheetService, target_date: date):
    #     logger.info("Checking overtime...")
    #     overtime_records = service.get_overtime_users(target_date)
        
    #     for record in overtime_records:
    #         user = record['user']
            
    #         # Skip ignored users
    #         if self._should_ignore_user(user.id):
    #             logger.debug(f"Skipping ignored user {user.id} - {user.email}")
    #             continue
                
    #         logger.info(f"User {user.email} has overtime.")
    #         # TODO: Implement email notification

    # def _check_pending_approvals(self, service: TimesheetService):
    #     logger.info("Checking pending approvals...")
    #     pending = service.get_pending_approvals(days_pending=3)
        
    #     for ts in pending:
    #         # Skip ignored users
    #         if self._should_ignore_user(ts.createdById):
    #             logger.debug(f"Skipping ignored user {ts.createdById}")
    #             continue
    #         # TODO: Notify manager

    # def _check_rejected_followup(self, service: TimesheetService):
    #     logger.info("Checking rejected timesheets...")
    #     rejected = service.get_rejected_needing_followup(days_since=2)
        
    #     for ts in rejected:
    #         # Skip ignored users
    #         if self._should_ignore_user(ts.createdById):
    #             logger.debug(f"Skipping ignored user {ts.createdById}")
    #             continue
    #         # TODO: Notify user

    # def _check_bad_descriptions(self, service: TimesheetService):
    #     logger.info("Checking descriptions...")
    #     bad_desc = service.get_bad_descriptions(min_length=10)
        
    #     for ts in bad_desc:
    #         # Skip ignored users
    #         if self._should_ignore_user(ts.createdById):
    #             logger.debug(f"Skipping ignored user {ts.createdById}")
    #             continue
    #         # TODO: Notify user

    # def _check_missing_tickets(self, service: TimesheetService):
    #     logger.info("Checking missing tickets...")
    #     missing_ticket_ts = service.get_missing_tickets()
        
    #     for ts in missing_ticket_ts:
    #         # Skip ignored users
    #         if self._should_ignore_user(ts.createdById):
    #             logger.debug(f"Skipping ignored user {ts.createdById}")
    #             continue
    #         # TODO: Notify user

    # def _check_allocation_mismatch(self, service: TimesheetService, target_date: date):
    #     logger.info("Checking allocation mismatches...")
    #     mismatches = service.get_allocation_mismatches(target_date)
        
    #     for m in mismatches:
    #         user = m['user']
            
    #         # Skip ignored users
    #         if self._should_ignore_user(user.id):
    #             logger.debug(f"Skipping ignored user {user.id} - {user.email}")
    #             continue
    #         # TODO: Notify PM
    
    # def _notify_pms_missing_timesheets(self, service: TimesheetService, dates_to_check: List[date]):
    #     """
    #     Send consolidated notifications to PMs about missing timesheets in their projects.
    #     """
    #     logger.info("Notifying PMs about missing timesheets...")
        
    #     # Collect PM notifications across all dates
    #     pm_consolidated = {}
        
    #     for target_date in dates_to_check:
    #         pm_notifications = service.get_missing_timesheets_by_pm(target_date)
            
    #         for pm_id, data in pm_notifications.items():
    #             if pm_id not in pm_consolidated:
    #                 pm_consolidated[pm_id] = {
    #                     'pm': data['pm'],
    #                     'projects': {}
    #                 }
                
    #             # Merge project data
    #             for project_id, project_data in data['projects'].items():
    #                 if project_id not in pm_consolidated[pm_id]['projects']:
    #                     pm_consolidated[pm_id]['projects'][project_id] = {
    #                         'project': project_data['project'],
    #                         'users_dates': {}
    #                     }
                    
    #                 # Track missing dates per user
    #                 for user in project_data['missing_users']:
    #                     if user.id not in pm_consolidated[pm_id]['projects'][project_id]['users_dates']:
    #                         pm_consolidated[pm_id]['projects'][project_id]['users_dates'][user.id] = {
    #                             'user': user,
    #                             'dates': []
    #                         }
    #                     pm_consolidated[pm_id]['projects'][project_id]['users_dates'][user.id]['dates'].append(target_date)
        
    #     # Send email to each PM
    #     for pm_id, data in pm_consolidated.items():
    #         pm = data['pm']
    #         pm_email = self.email_resolver.get_office_email(pm_id) or pm.email
            
    #         # Build email body
    #         body_lines = [f"Hi {pm.firstName},\n"]
    #         body_lines.append("The following team members in your projects have missing timesheets:\n")
            
    #         for project_id, project_data in data['projects'].items():
    #             project = project_data['project']
    #             body_lines.append(f"\nProject: {project.name}")
                
    #             for user_id, user_data in project_data['users_dates'].items():
    #                 user = user_data['user']
    #                 dates = sorted(user_data['dates'])
    #                 dates_str = ", ".join(str(d) for d in dates)
    #                 body_lines.append(f"  - {user.name} ({user.email}): {dates_str}")
            
    #         body_lines.append("\nPlease follow up with your team members.")
            
    #         body = "\n".join(body_lines)
            
    #         logger.info(f"Sending PM notification to {pm.email} for {len(data['projects'])} projects")
    #         print(f"\n{'='*80}")
    #         print(f"PM: {pm.name} ({pm.email})")
    #         print(body)
    #         print(f"{'='*80}\n")
            
            # Uncomment to send email
            # self.email_service.send_alert(
            #     to_email=pm_email,
            #     subject="Missing Timesheets in Your Projects",
            #     body=body
            # )

obj = DailyAgent()
obj.run()