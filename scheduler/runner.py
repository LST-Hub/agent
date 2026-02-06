"""
Job Scheduler.
Runs background tasks at defined intervals.
"""

import time
import schedule
import logging
from services.monitor_service import OperationsMonitor
from agent.ai_service import AIAgent

logger = logging.getLogger(__name__)

class JobRunner:
    """
    Manages scheduled tasks.
    """
    
    def __init__(self):
        self.monitor = OperationsMonitor()
        self.ai = AIAgent()
        self._setup_jobs()

    def _setup_jobs(self):
        """Define schedule for jobs."""
        logger.info("Setting up scheduled jobs...")
        
        # Check missing timesheets every Monday at 9:00 AM
        schedule.every().monday.at("09:00").do(self.monitor.check_missing_timesheets)
        
        # Check project health daily at 10:00 AM
        schedule.every().day.at("10:00").do(self.monitor.check_project_health)
        
        logger.info("Jobs scheduled.")

    def run_forever(self):
        """Blocking loop to run pending jobs."""
        logger.info("Scheduler started. Press Ctrl+C to exit.")
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Scheduler stopped.")
