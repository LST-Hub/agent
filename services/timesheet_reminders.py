"""
Delivery of missing / incomplete timesheet reminders (the `daily` command).

TIMESHEET_REMINDER_CHANNEL:
    email  - email only (the old behaviour, default)
    teams  - Teams 1:1 message from the signed-in account (teams-signin); email only when the Teams
             message cannot be sent (login problem, person not found)
    both   - Teams and email
Test mode: TEAMS_DM_TEST_RECIPIENT (or --dm-to) sends Teams reminders only to that person, at most
TEST_MESSAGE_LIMIT, marked with who they were meant for; emails follow EMAIL_TEST_RECIPIENT as usual.
A real Teams reminder goes to each person at most once per day (notification_log).
"""

import html
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List

logger = logging.getLogger(__name__)

CHANNELS = ("email", "teams", "both")
TEST_MESSAGE_LIMIT = 3
LOG_KIND = "teams_timesheet_reminder"


@dataclass
class Reminder:
    user_id: int
    first_name: str
    name: str
    email: str
    missing: List[date] = field(default_factory=list)
    incomplete: Dict[date, int] = field(default_factory=dict)   # date -> seconds still to log
    status: str = ""


def remaining_text(seconds: int) -> str:
    h = seconds / 3600
    if h >= 1:
        return "1 hour" if h == 1 else f"{h:.1f} hours"
    return f"{int(h * 60)} minutes"


def date_strings(missing, incomplete: Dict[date, int]) -> List[str]:
    """Same wording as the reminder email: missing dates, then incomplete ones with the time left."""
    return [str(d) for d in sorted(missing)] + [
        f"{d} ({remaining_text(s)} remaining)" for d, s in sorted(incomplete.items())]


def email_text(r: Reminder):
    items = date_strings(r.missing, r.incomplete)
    if len(items) == 1:
        return ("Missing/Incomplete Timesheet Reminder",
                f"Hi {r.first_name}, please submit/complete your timesheet for {items[0]}.")
    return ("Missing/Incomplete Timesheets Reminder",
            f"Hi {r.first_name}, please submit/complete your timesheets for the following dates {', '.join(items)}.")


def teams_html(r: Reminder, intended_for: str = "") -> str:
    e = html.escape
    lines = [f"<b>[TEST, meant for {e(intended_for)}]</b><br>" if intended_for else "",
             f"<i>Automated reminder:</i> Hi {e(r.first_name)}, your TaskSprint timesheet is not complete for:<br>"]
    lines += [f"&bull; {d:%a %d-%m-%Y} - not submitted (missing or still in Draft)<br>" for d in sorted(r.missing)]
    lines += [f"&bull; {d:%a %d-%m-%Y} - {e(remaining_text(s))} still to log<br>" for d, s in sorted(r.incomplete.items())]
    lines.append("Please fill in and submit it in TaskSprint today. If you were on leave, please log the leave. Thanks!")
    return "".join(lines)


def deliver(reminders: List[Reminder], today: date, channel: str, email_service, test_recipient: str = "",
            store_factory=None, sender_factory=None) -> str:
    """Send every reminder on the chosen channel; sets each reminder's status. Returns a one-line note."""
    if channel not in CHANNELS:
        raise ValueError(f"TIMESHEET_REMINDER_CHANNEL must be one of {CHANNELS}, got '{channel}'")

    def send_email(r: Reminder) -> bool:
        if not r.email:
            return False
        subject, body = email_text(r)
        return bool(email_service.send_alert(to_email=r.email, subject=subject, body=body))

    if channel == "email" or not reminders:
        for r in reminders:
            r.status = "email sent" if send_email(r) else "email failed"
        return "Reminders sent by email."

    if store_factory is None:
        from database.agent_store import get_agent_session as store_factory
    if sender_factory is None:
        from services.teams_sender import TeamsSender as sender_factory
    from database.agent_store import NotificationLog
    from services.teams_sender import TeamsSignInError

    store = store_factory()
    try:
        try:
            sender = sender_factory(store)
        except TeamsSignInError as exc:
            logger.error(f"Teams timesheet reminders not sent ({exc}); falling back to email.")
            for r in reminders:
                r.status = "Teams login problem; " + ("email sent" if send_email(r) else "email failed")
            return f"Teams login problem ({exc}); reminders sent by email instead."

        test_sent = 0
        for r in reminders:
            subject = f"{today.isoformat()} user {r.user_id}"
            if test_recipient:
                if test_sent >= TEST_MESSAGE_LIMIT:
                    r.status = f"test mode: Teams not sent (limit {TEST_MESSAGE_LIMIT})"
                    continue
                outcome = sender.send(test_recipient, teams_html(r, intended_for=r.name))
                test_sent += 1
                store.add(NotificationLog(kind=LOG_KIND + "_test", recipients=test_recipient, subject=subject,
                                          success=outcome.ok))
                store.commit()
                r.status = f"Teams test to {test_recipient}" if outcome.ok else f"Teams test failed: {outcome.error}"
                continue

            already = store.query(NotificationLog).filter_by(kind=LOG_KIND, subject=subject, success=True).first()
            if already:
                r.status = "already reminded on Teams today"
                continue
            if not r.email:
                outcome_ok, error = False, "no email address"
            else:
                outcome = sender.send(r.email, teams_html(r))
                outcome_ok, error = outcome.ok, outcome.error
                store.add(NotificationLog(kind=LOG_KIND, recipients=r.email, subject=subject, success=outcome_ok))
                store.commit()
            if outcome_ok:
                r.status = "Teams sent"
                if channel == "both":
                    r.status += "; " + ("email sent" if send_email(r) else "email failed")
            else:
                logger.warning(f"Teams timesheet reminder for {r.name} failed: {error}; sending email.")
                r.status = f"Teams failed ({error}); " + ("email sent" if send_email(r) else "email failed")
        mode = f" (TEST MODE: Teams only to {test_recipient}, at most {TEST_MESSAGE_LIMIT})" if test_recipient else ""
        return f"Teams reminders sent from {sender.sender_name}{mode}."
    finally:
        store.close()
