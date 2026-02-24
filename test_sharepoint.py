"""
Test script to manually trigger weekly summary report for LAST WEEK.
"""
import logging
from datetime import datetime, timedelta
from agents.weekly_report_agent import WeeklyReportAgent
from utils.date_utils import get_previous_week_start_end

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    agent = WeeklyReportAgent()

    # ✅ Force last week's start date for testing
    week_start, week_end = get_previous_week_start_end()
    logger.info(f"Testing with last week: {week_start.date()} → {week_end.date()}")

    try:
        week_start, file_url, excel_bytes, file_name = agent.generate_weekly_timesheet_report(
            week_start=week_start  # ✅ Pass last week explicitly
        )

        if file_url:
            logger.info(f"✅ Report generated successfully!")
            logger.info(f"📁 File: {file_name}")
            logger.info(f"🔗 URL: {file_url}")
        else:
            logger.warning("⚠️ No data found for last week.")

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)