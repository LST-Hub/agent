"""
Teams Plan Check Agent (11:00 on working days): reads the allocation chat (the same
run as teams-allocations, so plans are saved too), finds who has not posted today's
plan, sends each of them a Teams 1:1 reminder from the signed-in account
(teams-signin), and emails a summary to PM_EMAIL.

Test mode: with TEAMS_DM_TEST_RECIPIENT set (or --dm-to), reminders go only to that
person, at most TEST_MESSAGE_LIMIT per run, each marked with who it was meant for.
Dry run: nothing is sent; the summary is written to an HTML file.
"""

import html
import json
import logging
import os
from utils.reports import report_path
from typing import Dict, List
from agents.teams_allocation_agent import TeamsAllocationAgent
from database.connection import get_db_session
from database.repositories.allocation_repo import ResourceAllocationRepository
from database.repositories.user_repo import UserRepository
from services.allocation_matching import NameMatcher, load_aliases
from services.email_service import EmailService
from services.forecast_service import ForecastService, exclude_users
from services.teams_plan_check import (
    PlanCheckResult, find_missing, over_planned, reminder_html, sender_ids,
)
from services.teams_plans import merge_with_pm
from utils.date_utils import get_current_date, is_floater_holiday, is_holiday, is_weekend
from config.settings import settings

logger = logging.getLogger(__name__)

OFFICE_EMAILS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "office_emails.json")
TEST_MESSAGE_LIMIT = 3
LOG_KIND = "teams_plan_reminder"


def _h(seconds: int) -> str:
    return f"{seconds / 3600:g} h"


def _office_emails() -> Dict[int, str]:
    try:
        with open(OFFICE_EMAILS_FILE, "r", encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items() if v}
    except (OSError, ValueError):
        return {}


class TeamsPlanCheckAgent:
    def __init__(self, dry_run: bool = False, dm_to: str = None):
        self.dry_run = dry_run
        self.test_recipient = (dm_to or settings.teams_dm_test_recipient or "").strip()
        self.email_service = EmailService()

    def run(self):
        today = get_current_date()
        if is_weekend(today) or is_holiday(today):
            logger.info(f"{today}: weekend or company holiday, no plan check.")
            return None

        teams = TeamsAllocationAgent(save=not self.dry_run)
        resolution = teams.run()
        ignored = set(settings.get_ignore_id_list())

        session = next(get_db_session())
        try:
            expected = ForecastService(session, list(ignored)).delivery_staff(today)
            pm_today = exclude_users(ResourceAllocationRepository(session).get_user_project_day_allocations(today, today), ignored)
            pm_emails = {u.id: u.email for u in UserRepository(session).get_active_users() if u.email}
        finally:
            session.close()

        teams_today = [{"userId": p.user_id, "userName": p.user_name, "projectName": p.project_name,
                        "date": p.date, "seconds": p.seconds} for p in resolution.planned if p.date == today]
        person_aliases, _ = load_aliases()
        posted = sender_ids(teams.messages, today, NameMatcher(teams.people, person_aliases).match)
        planned = {r["userId"] for r in teams_today} | {r["userId"] for r in pm_today} | posted

        result = PlanCheckResult(day=today, expected=len(expected))
        result.missing = find_missing(expected, planned, ignored | set(settings.get_teams_plan_ignore_ids()),
                                      today, is_floater_holiday)
        office = _office_emails()
        for m in result.missing:
            m.email = office.get(m.user_id) or pm_emails.get(m.user_id, "")
        result.over = over_planned(merge_with_pm(pm_today, teams_today), today)
        result.review = [i for i in resolution.review if i.sent_at.date() == today]

        if self.dry_run:
            for m in result.missing:
                m.status = "dry run: not sent"
        else:
            self._send_reminders(result)

        body = self._build_summary(result)
        subject = f"Teams plan check - {today:%a %d-%m-%Y}: {len(result.missing)} not posted"
        if self.dry_run:
            out_path = report_path(f"teams_plan_check_{today.isoformat()}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(body)
            logger.info(f"Dry run: summary written to {out_path}")
        else:
            if not settings.get_pm_email():
                raise ValueError("PM_EMAIL environment variable is not set")
            if not self.email_service.send_alert(settings.get_pm_email(), subject, body, is_html=True):
                raise RuntimeError("Failed to send the Teams plan check email")
        logger.info(f"Plan check {today}: {result.expected} expected, {len(result.missing)} not posted, "
                    f"{len(result.over)} over 8 h, {len(result.review)} to review.")
        return result

    def _send_reminders(self, result: PlanCheckResult):
        if not result.missing:
            return
        from database.agent_store import NotificationLog, get_agent_session
        from services.teams_sender import TeamsSender, TeamsSignInError
        store = get_agent_session()
        try:
            try:
                sender = TeamsSender(store)
            except TeamsSignInError as exc:
                result.note = str(exc)
                logger.error(f"Teams reminders not sent: {exc}")
                for m in result.missing:
                    m.status = "not sent (Teams login problem)"
                return
            result.note = f"Reminders sent from the Teams account of {sender.sender_name}" + (
                f" (TEST MODE: all to {self.test_recipient}, at most {TEST_MESSAGE_LIMIT})" if self.test_recipient else "") + "."
            day = result.day.isoformat()
            test_sent = 0
            for m in result.missing:
                subject = f"{day} user {m.user_id}"
                first = m.name.split()[0] if m.name.split() else m.name
                if self.test_recipient:
                    if test_sent >= TEST_MESSAGE_LIMIT:
                        m.status = f"test mode: not sent (limit {TEST_MESSAGE_LIMIT})"
                        continue
                    outcome = sender.send(self.test_recipient, reminder_html(first, result.day, intended_for=m.name))
                    test_sent += 1
                    kind, to = LOG_KIND + "_test", self.test_recipient
                else:
                    already = store.query(NotificationLog).filter_by(kind=LOG_KIND, subject=subject, success=True).first()
                    if already:
                        m.status = "already reminded today"
                        continue
                    if not m.email:
                        m.status = "not sent: no email address in the PM tool"
                        continue
                    outcome = sender.send(m.email, reminder_html(first, result.day))
                    kind, to = LOG_KIND, m.email
                m.status = ("sent" + (f" to {to} (test)" if self.test_recipient else "")) if outcome.ok else f"failed: {outcome.error}"
                if not outcome.ok:
                    logger.warning(f"Reminder for {m.name} failed: {outcome.error}")
                store.add(NotificationLog(kind=kind, recipients=to, subject=subject, success=outcome.ok))
                store.commit()
        finally:
            store.close()

    @staticmethod
    def _table(headings: List[str], rows: List[List[str]]) -> str:
        lines = ['<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">',
                 '<thead><tr style="background-color: #4CAF50; color: white;">']
        lines += [f'<th style="text-align: left;">{h}</th>' for h in headings]
        lines.append('</tr></thead><tbody>')
        lines += ['<tr>' + "".join(f'<td style="vertical-align: top;">{c}</td>' for c in row) + '</tr>' for row in rows]
        lines.append('</tbody></table>')
        return "\n".join(lines)

    def _build_summary(self, r: PlanCheckResult) -> str:
        e = html.escape
        out = ["<html><body style='font-family: Segoe UI, Arial, sans-serif; font-size: 14px;'>", "<p>Hi,</p>",
               f"<p>Teams day plans for <strong>{r.day:%a %d-%m-%Y}</strong> at the 11:00 check: "
               f"{r.expected} people expected, <strong>{len(r.missing)}</strong> have not posted, "
               f"{len(r.over)} planned over 8 h, {len(r.review)} message(s) need review.</p>"]
        if r.note:
            out.append(f"<p><em>{e(r.note)}</em></p>")
        out.append(f"<h3>Not posted ({len(r.missing)})</h3>")
        out.append(self._table(["Person", "Teams reminder"], [[e(m.name), e(m.status)] for m in r.missing])
                   if r.missing else "<p>Everyone has posted.</p>")
        if r.over:
            out.append(f"<h3>Planned over 8 h ({len(r.over)})</h3>")
            out.append(self._table(["Person", "Planned", "Plan"], [
                [e(o["userName"]), _h(o["seconds"]), e(", ".join(f"{p} {_h(s)}" for p, s in o["projects"]))] for o in r.over]))
        if r.review:
            out.append(f"<h3>Needs review ({len(r.review)})</h3>")
            out.append(self._table(["Sent", "By", "Reason", "Message"], [
                [f"{i.sent_at:%H:%M}", e(i.sender), e(i.reason), e(i.evidence)] for i in r.review]))
        out.append("<p style='color:#757575; font-size: 12px;'>Posted means: a Teams plan for today names the person "
                   "(leave counts), the person wrote in the allocation chat today, or the PM tool has an allocation "
                   "for them today. Expected: delivery staff, except IGNORE_ID, TEAMS_PLAN_IGNORE_IDS and floater holidays.</p>")
        out.append("</body></html>")
        return "\n".join(out)
