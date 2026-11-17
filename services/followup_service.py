"""
Follow-up Service: decides which PM follow-ups to open, remind, escalate or resolve.

Lifecycle (with the default of 2 PM emails):
    finding appears        -> open follow-up, email PM (email 1)
    N working days, no fix -> reminder to PM (email 2)
    N working days, no fix -> escalate to management, stop emailing the PM
    finding disappears     -> resolved (at any stage)

A PM "responds" by fixing the underlying data in the PM tool; the next run sees
the finding gone and resolves the follow-up. Follow-ups can also be closed manually.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Tuple
from services.project_health_service import ProjectHealth
from database.agent_store import STATUS_OPEN, STATUS_CLOSED
from utils.date_utils import is_weekend, is_holiday

# Health rules that warrant a PM follow-up. "inactive" is informational only.
FOLLOWUP_CODES = {
    "past_end", "effort_over", "effort_near", "overdue_tasks", "tasks_over_estimate",
    "no_estimate", "no_end_date", "end_date_stale",
}
MAX_PM_EMAILS = 2   # Follow-up + 1 reminder, then escalate


@dataclass
class FollowUpPlan:
    new: List[Dict] = field(default_factory=list)        # findings needing a new follow-up
    remind: List = field(default_factory=list)           # open follow-ups due a reminder
    escalate: List = field(default_factory=list)         # open follow-ups to escalate
    resolve: List = field(default_factory=list)          # active follow-ups whose finding is gone
    still_active: List[Tuple] = field(default_factory=list)  # (follow-up, latest finding) pairs


def working_days_between(start: date, end: date) -> int:
    """Working days after `start` up to and including `end`."""
    count, d = 0, start + timedelta(days=1)
    while d <= end:
        if not is_weekend(d) and not is_holiday(d):
            count += 1
        d += timedelta(days=1)
    return count


def findings_from_health(results: List[ProjectHealth], ignore_pm_ids=()) -> List[Dict]:
    """Flatten health results into follow-up candidates (one per project and rule)."""
    findings = []
    for r in results:
        if r.pm_id is None or r.pm_id in ignore_pm_ids:
            continue
        for f in r.flags:
            if f.code in FOLLOWUP_CODES:
                findings.append({
                    "project_id": r.project_id, "project_name": r.project_name,
                    "pm_id": r.pm_id, "pm_name": r.pm_name, "pm_email": r.pm_email,
                    "code": f.code, "title": f.title, "evidence": f.evidence, "severity": f.severity,
                })
    return findings


def plan_followups(active, findings: List[Dict], today: date,
                   reminder_days: int, max_pm_emails: int = MAX_PM_EMAILS) -> FollowUpPlan:
    """
    Compare tracked follow-ups (objects with project_id, finding_code, status,
    emails_sent, last_sent_on) with the current findings. `active` should hold
    open, escalated and manually closed follow-ups: a closed one suppresses a new
    follow-up for the same finding until the finding disappears.
    """
    plan = FollowUpPlan()
    current = {(f["project_id"], f["code"]): f for f in findings}
    tracked = set()

    for fu in active:
        key = (fu.project_id, fu.finding_code)
        tracked.add(key)
        finding = current.get(key)
        if finding is None:
            plan.resolve.append(fu)
            continue
        if fu.status == STATUS_CLOSED:
            continue
        plan.still_active.append((fu, finding))
        if fu.status != STATUS_OPEN or fu.last_sent_on is None:
            continue
        if working_days_between(fu.last_sent_on, today) >= reminder_days:
            if fu.emails_sent < max_pm_emails:
                plan.remind.append(fu)
            else:
                plan.escalate.append(fu)

    plan.new = [f for key, f in current.items() if key not in tracked]
    return plan


def escalation_due_on(last_sent: date, reminder_days: int) -> date:
    """The working day on which a follow-up last emailed on `last_sent` would escalate."""
    d, remaining = last_sent, reminder_days
    while remaining > 0:
        d += timedelta(days=1)
        if not is_weekend(d) and not is_holiday(d):
            remaining -= 1
    return d
