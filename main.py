"""
Main entry point for the Operations Agent.
Usage:
    python main.py daily
    python main.py weekly
    python main.py weekly-report
    python main.py project-health [--dry-run]
    python main.py daily-ops [--dry-run]
    python main.py followups [--dry-run]
    python main.py followups-list
    python main.py followups-close <id> [note]
    python main.py forecast [--dry-run]
    python main.py forecast-skills-template
    python main.py forecast-skills-import <file.csv> [--practice NAME]
    python main.py teams-allocations [--sample FILE] [--no-save]
    python main.py teams-plan-check [--dry-run] [--dm-to EMAIL]
    python main.py teams-signin [--status]
    python main.py teams-signout

"""
import sys
import os
import logging
from datetime import datetime, timedelta
from agents.daily_agent import DailyAgent
from agents.weekly_agent import WeeklyAgent
from agents.weekly_report_agent import WeeklyReportAgent
from agents.weekly_billable_project_report_agent import BillableReportAgent
from agents.project_health_agent import ProjectHealthAgent
from agents.daily_operations_agent import DailyOperationsAgent
from agents.followup_agent import FollowUpAgent, list_followups, close_followup
from agents.forecast_agent import ForecastAgent, refresh_skills_template, import_skills_sheet
from agents.teams_allocation_agent import TeamsAllocationAgent
from agents.teams_plan_check_agent import TeamsPlanCheckAgent
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

    ws_week_start, timesheet_result, summary_result, project_result = weekly_agent.generate_weekly_timesheet_report(week_start)
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

        <h3>Timesheet Reports</h3>
        <p>Three timesheet files are attached: Weekly Timesheets, Weekly Summary, and Project Summary.</p>

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

    attachments = []
    for result in [timesheet_result, summary_result, project_result]:
        if result:
            _, excel_bytes, file_name = result
            if excel_bytes and file_name:
                attachments.append({'filename': file_name, 'data': excel_bytes})
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
        print("Usage: python main.py [daily|weekly|weekly-report|weekly-billable-report|project-health [--dry-run]|daily-ops [--dry-run]|followups [--dry-run]|followups-list|followups-close <id> [note]|forecast [--dry-run]|forecast-skills-template|teams-allocations [--sample FILE] [--no-save]|monday-summary|tree]")
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
        elif command == "project-health":
            logger.info("Running Project Health Agent...")
            agent = ProjectHealthAgent(dry_run="--dry-run" in sys.argv)
            agent.run()
        elif command == "daily-ops":
            logger.info("Running Daily Operations Agent...")
            agent = DailyOperationsAgent(dry_run="--dry-run" in sys.argv)
            agent.run()
        elif command == "followups":
            logger.info("Running Follow-up Agent...")
            agent = FollowUpAgent(dry_run="--dry-run" in sys.argv)
            agent.run()
        elif command == "followups-list":
            list_followups()
        elif command == "followups-close":
            if len(sys.argv) < 3:
                print("Usage: python main.py followups-close <id> [note]")
                sys.exit(1)
            close_followup(int(sys.argv[2]), " ".join(sys.argv[3:]))
        elif command == "forecast":
            logger.info("Running Forecast Agent...")
            agent = ForecastAgent(dry_run="--dry-run" in sys.argv)
            agent.run()
        elif command == "forecast-skills-template":
            refresh_skills_template()
        elif command == "forecast-skills-import":
            if len(sys.argv) < 3:
                print("Usage: python main.py forecast-skills-import <file.csv> [--practice NAME]")
                sys.exit(1)
            practice = sys.argv[sys.argv.index("--practice") + 1] if "--practice" in sys.argv else None
            import_skills_sheet(sys.argv[2], practice)
        elif command == "teams-allocations":
            logger.info("Running Teams Allocation Agent...")
            sample = sys.argv[sys.argv.index("--sample") + 1] if "--sample" in sys.argv else None
            TeamsAllocationAgent(sample_path=sample, save=False if "--no-save" in sys.argv else None).run()
        elif command == "teams-plan-check":
            logger.info("Running Teams Plan Check Agent...")
            dm_to = sys.argv[sys.argv.index("--dm-to") + 1] if "--dm-to" in sys.argv else None
            TeamsPlanCheckAgent(dry_run="--dry-run" in sys.argv, dm_to=dm_to).run()
        elif command in ("teams-signin", "teams-signout"):
            from database.agent_store import get_agent_session
            from services.teams_sender import login_status, sign_in, sign_out
            store = get_agent_session()
            try:
                if command == "teams-signout":
                    print("Signed out." if sign_out(store) else "Nobody was signed in.")
                elif "--status" in sys.argv:
                    print("Signed in as " + (login_status(store) or "nobody") + ".")
                else:
                    print(f"Teams reminders will be sent as {sign_in(store)}.")
            finally:
                store.close()
        elif command == "monday-summary":
            logger.info("Running Monday Summary Orchestrator...")
            run_monday_summary()
        elif command == "tree":
            print_tree(".")
        else:
            print(f"Unknown command: {command}")
            print("Available commands: daily, weekly, weekly-report, weekly-billable-report, project-health, daily-ops, followups, followups-list, followups-close, forecast, forecast-skills-template, teams-allocations, teams-plan-check, teams-signin, teams-signout, monday-summary, tree")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error executing {command} agent: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
