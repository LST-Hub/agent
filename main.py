"""
Main entry point for the Operations Agent.
Usage:
    python main.py daily
    python main.py weekly
    python main.py weekly-report

"""
import sys
import os
import logging
from datetime import datetime, timedelta
from agents.daily_agent import DailyAgent
from agents.weekly_agent import WeeklyAgent
from agents.weekly_report_agent import WeeklyReportAgent
from agents.weekly_billable_project_report_agent import BillableReportAgent
from services.email_service import EmailService
from config.settings import settings
from utils.date_utils import get_previous_week_start_end

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def print_tree(startpath):
    for root, dirs, files in os.walk(startpath):
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * (level)
        print('{}{}/'.format(indent, os.path.basename(root)))
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            print('{}{}'.format(subindent, f))

def run_monday_summary():
    """Generate both reports and send one combined PM email."""
    week_start, _ = get_previous_week_start_end()

    weekly_agent = WeeklyReportAgent()
    billable_agent = BillableReportAgent()

    ws_week_start, timesheet_url, timesheet_bytes, timesheet_filename = weekly_agent.generate_weekly_timesheet_report(week_start)
    _, billable_url, billable_data, billable_bytes, billable_filename = billable_agent.generate_billable_report(week_start)

    week_end = ws_week_start + timedelta(days=6)
    total_projects = len(billable_data or [])
    total_allocated = sum(p.get("total_allocated_hours", 0) for p in (billable_data or []))
    total_worked = sum(p.get("total_worked_hours", 0) for p in (billable_data or []))
    total_approved = sum(p.get("approved_billable_hours", 0) for p in (billable_data or []))

    pm_email = settings.get_pm_email()
    if not pm_email:
        raise ValueError("PM_EMAIL environment variable is not set")

    subject = f"Weekly Reports Summary - {ws_week_start.strftime('%Y-W%U')}"
    body = f"""
    <html>
    <body>
        <h2>Weekly Reports Summary</h2>
        <p><strong>Week:</strong> {ws_week_start.strftime('%B %d, %Y')} - {week_end.strftime('%B %d, %Y')}</p>

        <h3>Timesheet Report</h3>
        <p>Please find the weekly timesheet report attached to this email.</p>

        <h3>Billable Projects Report</h3>
        <p>Please find the billable projects report attached to this email.</p>

        <h3>Billable Summary</h3>
        <ul>
            <li><strong>Total Billable Projects:</strong> {total_projects}</li>
            <li><strong>Total Allocated Hours:</strong> {total_allocated:.2f}</li>
            <li><strong>Total Worked Hours:</strong> {total_worked:.2f}</li>
            <li><strong>Total Approved Billable Hours:</strong> {total_approved:.2f}</li>
            <li><strong>Overall Variance:</strong> {(total_worked - total_allocated):.2f} hours</li>
        </ul>

        <p>Best regards,<br>Operations Agent</p>
    </body>
    </html>
    """

    # Build attachments list
    attachments = []
    if timesheet_bytes and timesheet_filename:
        attachments.append({'filename': timesheet_filename, 'data': timesheet_bytes})
    if billable_bytes and billable_filename:
        attachments.append({'filename': billable_filename, 'data': billable_bytes})

    ok = EmailService().send_alert(
        to_email=pm_email,
        subject=subject,
        body=body,
        is_html=True,
        attachments=attachments
    )
    if not ok:
        raise RuntimeError("Failed to send Monday summary email")

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py [daily|weekly|weekly-report|weekly-billable-report|monday-summary|tree]")
        sys.exit(1)

    command = sys.argv[1].lower()

    try:
        if command == "daily":
            logger.info("Running Daily Agent...")
            agent = DailyAgent()
            agent.run()
        elif command == "weekly":
            logger.info("Running Weekly Agent...")
            agent = WeeklyAgent()
            agent.run()
        elif command == "weekly-report":
            logger.info("Running Weekly Report Agent...")
            agent = WeeklyReportAgent()
            agent.run()
        elif command == "weekly-billable-report":
            logger.info("Running Weekly Billable Report Agent...")
            agent = BillableReportAgent()
            agent.run()
        elif command == "monday-summary":
            logger.info("Running Monday Summary Orchestrator...")
            run_monday_summary()
        elif command == "tree":
            print_tree(".")
        else:
            print(f"Unknown command: {command}")
            print("Available commands: daily, weekly, weekly-report, weekly-billable-report, monday-summary, tree")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error executing {command} agent: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
