"""
Follow-up Agent: emails PMs about project health findings, reminds them once,
then escalates unresolved items to management (PM_EMAIL).

One email per PM per run. State lives in the agent store (AGENT_DATABASE_URL).
"""

import html
import logging
import os
from utils.reports import report_path
from typing import Dict, List
from database.connection import get_db_session
from database.agent_store import (
    get_agent_session, FollowUp, NotificationLog,
    ACTIVE_STATUSES, STATUS_ESCALATED, STATUS_RESOLVED, STATUS_CLOSED,
)
from services.email_service import EmailService
from services.project_health_service import ProjectHealthService
from services.followup_service import (
    findings_from_health, plan_followups, escalation_due_on, MAX_PM_EMAILS,
)
from utils.date_utils import get_current_date
from utils.email_resolver import EmailResolver
from config.settings import settings

logger = logging.getLogger(__name__)

SEVERITY_COLOURS = {"high": "#d32f2f", "medium": "#f57c00", "low": "#757575"}
SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def _d(d) -> str:
    return d.strftime('%d-%m-%Y') if d else ""


class FollowUpAgent:
    def __init__(self, dry_run: bool = False):
        self.email_service = EmailService()
        self.email_resolver = EmailResolver()
        self.pm_email = settings.get_pm_email()
        self.test_recipient = settings.followup_test_recipient
        self.dry_run = dry_run
        self.previews: List[str] = []

    # ------------------------------------------------------------------ run

    def run(self):
        logger.info("Starting Follow-up Agent execution...")
        today = get_current_date()

        session_gen = get_db_session()
        pm_session = next(session_gen)
        try:
            health = ProjectHealthService(
                pm_session,
                estimated_time_unit=settings.estimated_time_unit,
                inactive_days=settings.health_inactive_days,
            ).check_all(today)
        finally:
            pm_session.close()

        findings = findings_from_health(health, ignore_pm_ids=set(settings.get_followup_ignore_pm_ids()))
        store = self._open_store()
        try:
            tracked_statuses = ACTIVE_STATUSES + (STATUS_CLOSED,)
            tracked = store.query(FollowUp).filter(FollowUp.status.in_(tracked_statuses)).all() if store else []
            plan = plan_followups(tracked, findings, today, settings.followup_reminder_days)
            logger.info(f"Follow-ups: {len(plan.new)} new, {len(plan.remind)} reminders, "
                        f"{len(plan.escalate)} escalations, {len(plan.resolve)} resolved.")

            for fu in plan.resolve:
                if fu.status != STATUS_CLOSED:  # keep the manual close note and date
                    fu.resolved_on = today
                    fu.resolution_note = "Issue no longer detected by the health checks."
                fu.status = STATUS_RESOLVED
            for fu, finding in plan.still_active:
                self._refresh(fu, finding)

            new_followups = [self._new_followup(f) for f in plan.new]
            self._send_pm_emails(store, new_followups, plan.remind, today)
            self._send_escalation(store, plan.escalate, today)

            if self.dry_run:
                self._write_preview(today)
                if store:
                    store.rollback()
            else:
                store.commit()
        finally:
            if store:
                store.close()
        logger.info("Follow-up Agent execution finished.")

    def _open_store(self):
        if self.dry_run and not settings.agent_database_url:
            logger.warning("Dry run without AGENT_DATABASE_URL: treating every finding as new.")
            return None
        return get_agent_session()

    @staticmethod
    def _refresh(fu: FollowUp, finding: Dict):
        """Keep wording and PM details current while a follow-up is active."""
        fu.title, fu.evidence, fu.severity = finding["title"], finding["evidence"], finding["severity"]
        fu.project_name = finding["project_name"]
        fu.pm_id, fu.pm_name, fu.pm_email = finding["pm_id"], finding["pm_name"], finding["pm_email"]

    @staticmethod
    def _new_followup(finding: Dict) -> FollowUp:
        return FollowUp(
            project_id=finding["project_id"], project_name=finding["project_name"],
            pm_id=finding["pm_id"], pm_name=finding["pm_name"], pm_email=finding["pm_email"],
            finding_code=finding["code"], title=finding["title"], evidence=finding["evidence"],
            severity=finding["severity"], emails_sent=0,
        )

    # --------------------------------------------------------------- sending

    def _resolve_pm_email(self, fu: FollowUp) -> str:
        return self.email_resolver.get_office_email(fu.pm_id) or fu.pm_email or ""

    def _send(self, store, kind: str, to: List[str], subject: str, body: str, items: List[FollowUp]) -> bool:
        original = ", ".join(to)
        if self.test_recipient:
            body = (f"<p style='background:#fff3cd;padding:8px;'><strong>Pilot mode:</strong> "
                    f"this email would have been sent to {html.escape(original)}.</p>") + body
            to = [self.test_recipient]

        if self.dry_run:
            self.previews.append(f"<h2>{html.escape(kind)} &rarr; {html.escape(', '.join(to))}</h2>"
                                 f"<p><strong>Subject:</strong> {html.escape(subject)}</p>{body}<hr/>")
            return True

        ok = self.email_service.send_alert(to_email=to, subject=subject, body=body, is_html=True)
        store.add(NotificationLog(
            kind=kind, recipients=", ".join(to), subject=subject, success=ok,
            followup_ids=",".join(str(fu.id) for fu in items if fu.id),
        ))
        return ok

    def _send_pm_emails(self, store, new_items: List[FollowUp], reminders: List[FollowUp], today):
        by_pm: Dict[int, Dict[str, List[FollowUp]]] = {}
        for fu in new_items:
            by_pm.setdefault(fu.pm_id, {"new": [], "remind": []})["new"].append(fu)
        for fu in reminders:
            by_pm.setdefault(fu.pm_id, {"new": [], "remind": []})["remind"].append(fu)

        for pm_id, groups in by_pm.items():
            items = groups["new"] + groups["remind"]
            to = self._resolve_pm_email(items[0])
            if not to:
                logger.warning(f"No email for PM {items[0].pm_name} (id {pm_id}); {len(items)} follow-up(s) not sent.")
                continue

            if store:
                for fu in groups["new"]:
                    store.add(fu)
                store.flush()  # assign ids for the notification log

            subject = (f"Reminder: {len(groups['remind'])} project item(s) still need updating"
                       if not groups["new"] else
                       f"Action needed: {len(items)} project item(s) to update")
            body = self._build_pm_body(items[0].pm_name, groups["new"], groups["remind"], today)

            if self._send(store, "followup", [to], subject, body, items):
                for fu in items:
                    fu.emails_sent = (fu.emails_sent or 0) + 1
                    fu.first_sent_on = fu.first_sent_on or today
                    fu.last_sent_on = today
            elif store:
                # Not delivered: drop new follow-ups so the next run retries them
                for fu in groups["new"]:
                    store.delete(fu)
                logger.error(f"Failed to email PM {items[0].pm_name}; will retry next run.")

    def _send_escalation(self, store, items: List[FollowUp], today):
        if not items:
            return
        if not self.pm_email:
            raise ValueError("PM_EMAIL environment variable is not set")
        subject = f"Escalation: {len(items)} project item(s) unresolved after {MAX_PM_EMAILS} PM emails"
        if self._send(store, "escalation", self.pm_email, subject, self._build_escalation_body(items), items):
            for fu in items:
                fu.status, fu.escalated_on = STATUS_ESCALATED, today

    def _write_preview(self, today):
        out_path = report_path(f"followups_{today.isoformat()}.html")
        content = "".join(self.previews) or "<p>No emails would be sent.</p>"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"<html><body><h1>Follow-up dry run</h1>{content}</body></html>")
        logger.info(f"Dry run: {len(self.previews)} email(s) previewed in {out_path}")

    # ------------------------------------------------------------ email bodies

    @staticmethod
    def _rows(items: List[FollowUp], show_pm: bool = False) -> str:
        head = (["PM"] if show_pm else []) + ["Project", "Issue", "Details", "First raised"]
        lines = ['<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1200px;">',
                 '<thead><tr style="background-color: #4CAF50; color: white;">']
        lines += [f'<th style="text-align: left; padding: 10px;">{h}</th>' for h in head]
        lines.append('</tr></thead><tbody>')
        ordered = sorted(items, key=lambda f: ((f.pm_name or "") if show_pm else "", f.project_name,
                                               -SEVERITY_ORDER.get(f.severity, 0)))
        for fu in ordered:
            cells = ([html.escape(fu.pm_name or "")] if show_pm else []) + [
                html.escape(fu.project_name),
                f"<strong style='color:{SEVERITY_COLOURS.get(fu.severity, '#000')}'>{html.escape(fu.title)}</strong>",
                html.escape(fu.evidence or ""),
                _d(fu.first_sent_on) or "Today",
            ]
            lines.append('<tr>' + "".join(f'<td style="padding: 10px;">{c}</td>' for c in cells) + '</tr>')
        lines.append('</tbody></table>')
        return "\n".join(lines)

    def _build_pm_body(self, pm_name: str, new_items: List[FollowUp], reminders: List[FollowUp], today) -> str:
        first_name = html.escape((pm_name or "").split(" ")[0] or "there")
        lines = ["<html><body>", f"<p>Hi {first_name},</p>"]
        if reminders:
            escalate_on = escalation_due_on(today, settings.followup_reminder_days)
            lines.append(f"<h3>Reminder ({len(reminders)})</h3>")
            lines.append("<p>These items were raised earlier and are still showing. This is the final reminder: "
                         f"anything unresolved on <strong>{_d(escalate_on)}</strong> will be escalated to management.</p>")
            lines.append(self._rows(reminders))
        if new_items:
            lines.append(f"<h3>New ({len(new_items)})</h3>")
            lines.append("<p>The project health checks found the following in your projects:</p>")
            lines.append(self._rows(new_items))
        lines.append("<p>Please update the project or task in the PM tool. Items close automatically once the "
                     "data is corrected &mdash; no reply is needed. If an item is not valid, let the operations "
                     "team know so it can be closed.</p>")
        lines.append("<p>Thank you,<br/>Operations Agent</p></body></html>")
        return "\n".join(lines)

    def _build_escalation_body(self, items: List[FollowUp]) -> str:
        lines = ["<html><body>", "<p>Hi,</p>",
                 f"<p>The following items are still unresolved after {MAX_PM_EMAILS} emails to the project manager "
                 f"({settings.followup_reminder_days} working days apart). The PM will not be emailed about them again; "
                 "they close automatically once the data is corrected.</p>",
                 self._rows(items, show_pm=True),
                 "<p>Thank you,<br/>Operations Agent</p></body></html>"]
        return "\n".join(lines)


# ------------------------------------------------------------- manual commands

def list_followups():
    store = get_agent_session()
    try:
        rows = (store.query(FollowUp).filter(FollowUp.status.in_(ACTIVE_STATUSES))
                .order_by(FollowUp.pm_name, FollowUp.project_name).all())
        if not rows:
            print("No active follow-ups.")
        for fu in rows:
            print(f"#{fu.id:<5} {fu.status:<9} emails={fu.emails_sent} last={_d(fu.last_sent_on):<10} "
                  f"{fu.pm_name} | {fu.project_name} | {fu.title}")
    finally:
        store.close()


def close_followup(followup_id: int, note: str):
    store = get_agent_session()
    try:
        fu = store.get(FollowUp, followup_id)
        if fu is None:
            raise ValueError(f"Follow-up #{followup_id} not found")
        if fu.status not in ACTIVE_STATUSES:
            print(f"Follow-up #{followup_id} is already {fu.status}.")
            return
        fu.status, fu.resolved_on = STATUS_CLOSED, get_current_date()
        fu.resolution_note = note or "Closed manually."
        store.commit()
        print(f"Closed follow-up #{followup_id}: {fu.project_name} | {fu.title}")
    finally:
        store.close()
