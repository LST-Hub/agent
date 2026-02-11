"""
Job Scheduler.
Runs background tasks at defined intervals.
"""

import time
import schedule
import logging
from services.monitor_service import OperationsMonitor
from agent.ai_service import AIAgent
from agents.daily_agent import DailyAgent
from agents.weekly_agent import WeeklyAgent

logger = logging.getLogger(__name__)

class JobRunner:
    """
    Manages scheduled tasks.
    """
    
    def __init__(self):
        self.monitor = OperationsMonitor()
        self.ai = AIAgent()
        self.daily_agent = DailyAgent()
        self.weekly_agent = WeeklyAgent()
        self._setup_jobs()

    def _setup_jobs(self):
        """Define schedule for jobs."""
        logger.info("Setting up scheduled jobs...")
        
        # Daily: Check missing timesheets every working day (Mon-Fri) at 6:00 PM
        # This sends notifications to individual users
        schedule.every().monday.at("18:00").do(self.daily_agent.run)
        schedule.every().tuesday.at("18:00").do(self.daily_agent.run)
        schedule.every().wednesday.at("18:00").do(self.daily_agent.run)
        schedule.every().thursday.at("18:00").do(self.daily_agent.run)
        schedule.every().friday.at("18:00").do(self.daily_agent.run)
        
        # Weekly: Send consolidated PM report every Friday at 6:00 PM
        schedule.every().friday.at("18:00").do(self.weekly_agent.run)
        
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

# Run the scheduler
if __name__ == "__main__":
    runner = JobRunner()
    runner.run_forever()
