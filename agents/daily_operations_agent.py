"""
Daily Operations Agent: runs allocation and timesheet checks for recent working
days and emails one consolidated report to the configured PM/management recipients.
Plans come from PM-tool allocations and from day plans posted in the Teams chat.
"""

import html
import logging
import os
from utils.reports import report_path
from datetime import timedelta
from typing import List
from database.connection import get_db_session
from services.email_service import EmailService
from services.teams_plans import load_plans_safe
from services.daily_operations_service import (
    DailyOperationsService, DailyOpsResult, working_days_back,
    STANDARD_DAY_SECONDS, VARIANCE_THRESHOLD_SECONDS, MIN_DESCRIPTION_LENGTH,
)
from utils.date_utils import get_current_date
from config.settings import settings

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_EXAMPLES = 3


def _h(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def _d(d) -> str:
    return d.strftime('%d-%m-%Y')


def _breakdown(projects: dict) -> str:
    return ", ".join(f"{name} ({_h(sec)})" for name, sec in sorted(projects.items(), key=lambda p: -p[1]))


class DailyOperationsAgent:
    def __init__(self, dry_run: bool = False):
        self.email_service = EmailService()
        self.pm_email = settings.get_pm_email()
        self.dry_run = dry_run

    def run(self):
        logger.info("Starting Daily Operations Agent execution...")
        today = get_current_date()
        days = working_days_back(today - timedelta(days=1), settings.daily_ops_lookback_days)

        session_gen = get_db_session()
        session = next(session_gen)
        try:
            result = DailyOperationsService(session, settings.get_ignore_id_list()).run_checks(
                days, load_plans_safe(days[0], days[-1]))
        finally:
            session.close()

        period = _d(days[0]) if len(days) == 1 else f"{_d(days[0])} to {_d(days[-1])}"
        subject = f"Daily Operations Report - {period}"
        body = self._build_html_email_body(result, period)

        if self.dry_run:
            out_path = report_path(f"daily_ops_{today.isoformat()}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(body)
            logger.info(f"Dry run: report written to {out_path}")
            return

        if not self.pm_email:
            raise ValueError("PM_EMAIL environment variable is not set")

        if not self.email_service.send_alert(to_email=self.pm_email, subject=subject, body=body, is_html=True):
            raise RuntimeError("Failed to send daily operations email")
        logger.info("Daily Operations Agent execution finished.")

    def _table(self, headings: List[str], rows: List[List[str]]) -> str:
        """Render rows (already HTML-escaped) as a table in the same style as the other reports."""
        lines = ['<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1200px;">',
                 '<thead><tr style="background-color: #4CAF50; color: white;">']
        lines += [f'<th style="text-align: left; padding: 10px;">{h}</th>' for h in headings]
        lines.append('</tr></thead><tbody>')
        for row in rows:
            lines.append('<tr>' + "".join(f'<td style="padding: 10px;">{cell}</td>' for cell in row) + '</tr>')
        lines.append('</tbody></table>')
        return "\n".join(lines)

    def _section(self, title: str, intro: str, headings: List[str], rows: List[List[str]]) -> List[str]:
        lines = [f"<h3>{title} ({len(rows)})</h3>"]
        if not rows:
            lines.append("<p>None.</p>")
        else:
            lines.append(f"<p>{intro}</p>")
            lines.append(self._table(headings, rows))
        return lines

    def _build_html_email_body(self, result: DailyOpsResult, period: str) -> str:
        e = html.escape
        cap = _h(STANDARD_DAY_SECONDS)

        html_lines = ["<html><body>", "<p>Hi,</p>",
                      f"<p>Daily allocation and timesheet checks for <strong>{period}</strong>.</p>"]
        html_lines.append(
            "<p><strong>Summary:</strong> "
            f"{len(result.overtime)} overtime, "
            f"{len(result.over_allocations)} over-allocated, "
            f"{len(result.allocation_variances)} plan vs logged differences, "
            f"{len(result.missing_tickets)} missing ticket(s), "
            f"{len(result.short_descriptions)} short description(s).</p>"
        )

        html_lines += self._section(
            "Overtime", f"Logged more than {cap} in a day.",
            ["Date", "Resource", "Logged", "Over by", "Projects"],
            [[_d(r["date"]), e(r["userName"]), _h(r["seconds"]), f"<strong>{_h(r['over'])}</strong>", e(_breakdown(r["projects"]))]
             for r in result.overtime],
        )
        html_lines += self._section(
            "Over-allocated", f"Planned more than {cap} in a day (PM-tool allocation or Teams plan).",
            ["Date", "Resource", "Planned", "Over by", "Plan", "Source"],
            [[_d(r["date"]), e(r["userName"]), _h(r["seconds"]), f"<strong>{_h(r['over'])}</strong>", e(_breakdown(r["projects"])),
              e(r["source"])]
             for r in result.over_allocations],
        )
        html_lines += self._section(
            "Plan vs logged",
            f"Time logged on a project differs from the plan by {_h(VARIANCE_THRESHOLD_SECONDS)} or more. "
            "The plan is the PM-tool allocation, or the day plan posted in the Teams allocation chat when the PM "
            "tool has none for that person and day. Days with no timesheet at all are skipped (those are chased "
            "by the missing-timesheet reminders).",
            ["Date", "Resource", "Project", "PM", "Planned", "Logged", "Difference", "Source"],
            [[_d(r["date"]), e(r["userName"]), e(r["projectName"]), e(r["pmName"]) or "&ndash;", _h(r["allocated"]), _h(r["logged"]),
              f"<strong>{'+' if r['diff'] > 0 else '-'}{_h(abs(r['diff']))}</strong> ({'over' if r['diff'] > 0 else 'under'})",
              e(r["source"])]
             for r in result.allocation_variances],
        )
        html_lines += self._section(
            "Missing tickets", "Entries without a ticket on projects that require one.",
            ["Resource", "Project", "Entries", "Hours"],
            [[e(r["userName"]), e(r["projectName"]), str(r["entries"]), _h(r["seconds"] or 0)]
             for r in sorted(result.missing_tickets, key=lambda r: -r["entries"])],
        )

        # Short descriptions: one row per resource with a few examples
        by_user = {}
        for r in result.short_descriptions:
            by_user.setdefault(r["userName"], []).append(r)
        html_lines += self._section(
            "Short descriptions",
            f"Billable entries with no description or fewer than {MIN_DESCRIPTION_LENGTH} characters.",
            ["Resource", "Entries", "Examples"],
            [[e(name), str(len(entries)),
              "<br/>".join(f"{_d(x['date'])} &middot; {e(x['projectName'])} &middot; &ldquo;{e((x['description'] or '').strip())}&rdquo;"
                           for x in entries[:MAX_DESCRIPTION_EXAMPLES])]
             for name, entries in sorted(by_user.items(), key=lambda kv: -len(kv[1]))],
        )

        html_lines.append(
            "<p style='color:#757575; font-size: 12px;'>Weekends and company holidays are excluded. "
            f"Standard capacity is {cap} per day. Leave entries do not count towards overtime. "
            "Rejected timesheets are not counted; draft entries are. "
            "Users in the ignore list are skipped.</p>"
        )
        html_lines.append('<p>Thank you,<br/>Operations Agent</p>')
        html_lines.append('</body></html>')
        return '\n'.join(html_lines)
