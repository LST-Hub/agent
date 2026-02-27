"""
Test script to manually trigger weekly summary report for CURRENT WEEK.
"""
import logging
from datetime import datetime, timedelta
from agents.weekly_report_agent import WeeklyReportAgent
from utils.date_utils import get_current_week_start

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    agent = WeeklyReportAgent()

    # ✅ Force current week's start date for testing
    week_start = get_current_week_start()
    week_end = week_start + timedelta(days=6)
    logger.info(f"Testing with current week: {week_start.date()} → {week_end.date()}")

    try:
        ws, timesheet_result, summary_result, project_result = agent.generate_weekly_timesheet_report(
            week_start=week_start
        )

        for label, result in [
            ("📊 Weekly Timesheets", timesheet_result),
            ("📋 Weekly Summary",    summary_result),
            ("📁 Project Summary",   project_result),
        ]:
            if result:
                file_url, excel_bytes, file_name = result[0], result[1], result[2]
                logger.info(f"✅ {label} generated!")
                logger.info(f"   📁 File : {file_name}")
                logger.info(f"   🔗 URL  : {file_url}")
            else:
                logger.warning(f"⚠️  {label} — no data found or skipped.")

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)