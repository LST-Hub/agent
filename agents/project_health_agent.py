"""
Project Health Agent: runs rule-based project health checks and emails
one consolidated report to the configured PM/management recipients.
"""

import html
import logging
import os
from utils.reports import report_path
from typing import List
from database.connection import get_db_session
from services.email_service import EmailService
from services.project_health_service import ProjectHealthService, ProjectHealth, STALE_END_DATE_DAYS
from utils.date_utils import get_current_date
from config.settings import settings

logger = logging.getLogger(__name__)

SEVERITY_COLOURS = {"high": "#d32f2f", "medium": "#f57c00", "low": "#757575"}


class ProjectHealthAgent:
    def __init__(self, dry_run: bool = False):
        self.email_service = EmailService()
        self.pm_email = settings.get_pm_email()
        self.dry_run = dry_run

    def run(self):
        logger.info("Starting Project Health Agent execution...")
        today = get_current_date()

        session_gen = get_db_session()
        session = next(session_gen)
        try:
            service = ProjectHealthService(
                session,
                estimated_time_unit=settings.estimated_time_unit,
                inactive_days=settings.health_inactive_days,
            )
            results = service.check_all(today)
        finally:
            session.close()

        subject = f"Project Health Report - {today.strftime('%d-%m-%Y')}"
        body = self._build_html_email_body(results, today)

        if self.dry_run:
            out_path = report_path(f"project_health_{today.isoformat()}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(body)
            logger.info(f"Dry run: report written to {out_path}")
            return

        if not self.pm_email:
            raise ValueError("PM_EMAIL environment variable is not set")

        if not self.email_service.send_alert(to_email=self.pm_email, subject=subject, body=body, is_html=True):
            raise RuntimeError("Failed to send project health email")
        logger.info("Project Health Agent execution finished.")

    def _build_html_email_body(self, results: List[ProjectHealth], today) -> str:
        at_risk = [r for r in results if r.risk_flags]
        data_issues = [r for r in results if r.data_quality_flags]
        counts = {sev: sum(1 for r in at_risk if r.severity == sev) for sev in ("high", "medium", "low")}

        html_lines = ["<html><body>", "<p>Hi,</p>"]
        html_lines.append(f"<p>Project health check for <strong>{today.strftime('%d-%m-%Y')}</strong>.</p>")
        html_lines.append(
            f"<p><strong>Summary:</strong> {len(at_risk)} project(s) with delivery risks &mdash; "
            f"<span style='color:{SEVERITY_COLOURS['high']}'>{counts['high']} high</span>, "
            f"<span style='color:{SEVERITY_COLOURS['medium']}'>{counts['medium']} medium</span>, "
            f"<span style='color:{SEVERITY_COLOURS['low']}'>{counts['low']} low</span>. "
            f"{len(data_issues)} project(s) with data-quality issues (listed at the end).</p>"
        )

        html_lines.append("<h3>Delivery risks</h3>")
        if not at_risk:
            html_lines.append("<p>No active project triggered a delivery-risk rule.</p>")
        else:
            html_lines.append('<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1200px;">')
            html_lines.append('<thead><tr style="background-color: #4CAF50; color: white;">')
            for heading in ("Project", "Type", "PM", "Status", "Severity", "Findings"):
                html_lines.append(f'<th style="text-align: left; padding: 10px;">{heading}</th>')
            html_lines.append('</tr></thead><tbody>')

            for r in at_risk:
                findings = "".join(
                    f"<li><strong style='color:{SEVERITY_COLOURS[f.severity]}'>{html.escape(f.title)}</strong>"
                    f" <em>({f.category})</em><br/>{html.escape(f.evidence)}</li>"
                    for f in r.risk_flags
                )
                html_lines.append('<tr>')
                html_lines.append(f'<td style="padding: 10px;">{html.escape(r.project_name)}</td>')
                html_lines.append(f'<td style="padding: 10px;">{html.escape(r.project_type)}</td>')
                html_lines.append(f'<td style="padding: 10px;">{html.escape(r.pm_name)}</td>')
                html_lines.append(f'<td style="padding: 10px;">{html.escape(r.status_name)}</td>')
                html_lines.append(f'<td style="padding: 10px; font-weight: bold; color: {SEVERITY_COLOURS[r.severity]};">{r.severity.upper()}</td>')
                html_lines.append(f'<td style="padding: 10px;"><ul style="margin: 0; padding-left: 18px;">{findings}</ul></td>')
                html_lines.append('</tr>')
            html_lines.append('</tbody></table>')

        if data_issues:
            html_lines.append("<h3>Data quality &mdash; please update in the PM tool</h3>")
            html_lines.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1200px; font-size: 13px;">')
            html_lines.append('<thead><tr style="background-color: #9e9e9e; color: white;">')
            for heading in ("Project", "PM", "Issues"):
                html_lines.append(f'<th style="text-align: left; padding: 8px;">{heading}</th>')
            html_lines.append('</tr></thead><tbody>')
            for r in sorted(data_issues, key=lambda r: (r.pm_name, r.project_name)):
                issues = "<br/>".join(
                    f"<strong>{html.escape(f.title)}</strong>: {html.escape(f.evidence)}" for f in r.data_quality_flags
                )
                html_lines.append('<tr>')
                html_lines.append(f'<td style="padding: 8px;">{html.escape(r.project_name)}</td>')
                html_lines.append(f'<td style="padding: 8px;">{html.escape(r.pm_name)}</td>')
                html_lines.append(f'<td style="padding: 8px;">{issues}</td>')
                html_lines.append('</tr>')
            html_lines.append('</tbody></table>')

        html_lines.append(
            "<p style='color:#757575; font-size: 12px;'>Rules: effort over estimate / above "
            "90% of estimate, past estimated end date (high for Fixed billing; for other types, "
            f"medium if passed within {STALE_END_DATE_DAYS} days, otherwise listed as 'End date not updated'), "
            "overdue tasks, tasks over estimate, "
            f"no time logged in {settings.health_inactive_days} days, missing estimates or end dates. "
            "Logged effort excludes rejected timesheets. Completed, closed, cancelled and leave projects "
            "are skipped; halted projects are checked for effort only.</p>"
        )
        html_lines.append('<p>Thank you,<br/>Operations Agent</p>')
        html_lines.append('</body></html>')
        return '\n'.join(html_lines)
