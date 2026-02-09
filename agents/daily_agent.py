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
            self._check_idle_time(service, current_date)
            
            # 3. Overtime Detection
            self._check_overtime(service, current_date)
            
            # 4. Pending Approval Reminder
            self._check_pending_approvals(service)
            
            # 5. Rejected Timesheet Follow-up
            self._check_rejected_followup(service)
            
            # 6. Missing or Poor Description Check
            self._check_bad_descriptions(service)
            
            # 7. Missing Ticket for Billable Work
            self._check_missing_tickets(service)
            
            # 8. Allocation vs Actual Mismatch
            self._check_allocation_mismatch(service, current_date)

        finally:
            session.close()
            logger.info("Daily Agent execution finished.")

    def _check_missing_timesheets(self, service: TimesheetService, target_date: date):
        logger.info("Checking missing timesheets...")
        missing_users = service.get_missing_timesheet_users(target_date)
        print(f"missing users: {missing_users}")
        
        for user in missing_users:
            # Skip ignored users
            if self._should_ignore_user(user.id):
                logger.debug(f"Skipping ignored user {user.id} - {user.email}")
                continue
                
            logger.info(f"User {user.email} missing timesheet.")
            
            # Resolve office emails
            user_email = self.email_resolver.get_office_email(user.id) or user.email
            supervisor_email = None
            if user.supervisorId and not self._should_ignore_user(user.supervisorId):
                supervisor_email = self.email_resolver.get_office_email(user.supervisorId)
            
            # Send alert
            # self.email_service.send_alert(
            #     to_email=user_email,
            #     subject="Missing Timesheet Reminder",
            #     body=f"Hi {user.firstName}, please submit your timesheet for {target_date}.",
            #     cc_email=supervisor_email
            # )

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
