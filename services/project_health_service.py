"""
Project Health Service: rule-based health flags for active projects.

Rules are deliberately transparent (no blended score): each flag carries a
category, severity and the evidence that triggered it.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from database.repositories.project_repo import ProjectRepository
from database.repositories.task_repo import TaskRepository
from utils.constants import OVERRUN_RISK_THRESHOLD, LEAVE_PROJECT_KEYWORDS
import logging

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}
CLOSED_STATUSES = {"completed", "closed", "cancelled"}
HALTED_STATUS = "halted"
FIXED_BILLING_TYPE = "fixed billing"
# For non fixed-billing projects, an end date passed longer ago than this is treated
# as an unmaintained record rather than a schedule risk
STALE_END_DATE_DAYS = 30
UNIT_TO_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}
MAX_LISTED_TASKS = 5


@dataclass
class HealthFlag:
    category: str   # schedule, effort, activity, data-quality
    severity: str   # high, medium, low
    title: str
    evidence: str
    code: str = ""  # Stable identifier for the rule, used to track follow-ups across runs


@dataclass
class ProjectHealth:
    project_id: int
    project_name: str
    pm_id: Optional[int]
    pm_name: str
    status_name: str
    project_type: str = ""
    flags: List[HealthFlag] = field(default_factory=list)
    pm_email: str = ""

    @property
    def risk_flags(self) -> List[HealthFlag]:
        return [f for f in self.flags if f.category != "data-quality"]

    @property
    def data_quality_flags(self) -> List[HealthFlag]:
        return [f for f in self.flags if f.category == "data-quality"]

    @property
    def severity(self) -> Optional[str]:
        """Worst delivery-risk severity; data-quality issues are reported separately."""
        if not self.risk_flags:
            return None
        return max((f.severity for f in self.risk_flags), key=SEVERITY_ORDER.get)


def _fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def _fmt_date(d: date) -> str:
    return d.strftime('%d-%m-%Y')


def _as_date(value) -> Optional[date]:
    """MySQL may return datetime for date-like columns; normalise to date."""
    if value is None:
        return None
    return value.date() if hasattr(value, "date") and callable(value.date) else value


def is_leave_project(name: str) -> bool:
    name = (name or "").strip().lower()
    return any(kw in name for kw in LEAVE_PROJECT_KEYWORDS)


def evaluate_project(
    project: Dict,
    tasks: List[Dict],
    today: date,
    estimate_unit_seconds: int = 1,
    overrun_threshold: float = OVERRUN_RISK_THRESHOLD,
    inactive_days: int = 14,
) -> List[HealthFlag]:
    """Apply health rules to one project and its active tasks."""
    flags: List[HealthFlag] = []
    # Halted projects are paused on purpose: schedule and inactivity rules don't apply
    halted = (project.get("statusName") or "").strip().lower() == HALTED_STATUS

    worked = project.get("workedSeconds") or 0
    estimate = (project.get("estimatedTime") or 0) * estimate_unit_seconds

    # Effort vs estimate
    if estimate <= 0:
        flags.append(HealthFlag("data-quality", "low", "No estimated hours",
                                f"{_fmt_hours(worked)} logged but the project has no estimate.", "no_estimate"))
    else:
        ratio = worked / estimate
        evidence = f"{_fmt_hours(worked)} logged vs {_fmt_hours(estimate)} estimated ({ratio:.0%})."
        if ratio >= 1:
            flags.append(HealthFlag("effort", "high", "Effort over estimate", evidence, "effort_over"))
        elif ratio >= overrun_threshold:
            flags.append(HealthFlag("effort", "medium", "Effort nearing estimate", evidence, "effort_near"))

    # Schedule
    end_date = _as_date(project.get("estimatedEndDate"))
    if end_date is None:
        flags.append(HealthFlag("data-quality", "low", "No estimated end date",
                                "The project has no estimated end date.", "no_end_date"))
    elif end_date < today and not halted:
        days = (today - end_date).days
        evidence = f"Estimated end {_fmt_date(end_date)}, {days} day(s) ago, with no actual end date."
        fixed_billing = (project.get("projectTypeName") or "").strip().lower() == FIXED_BILLING_TYPE
        if fixed_billing:
            flags.append(HealthFlag("schedule", "high", "Past estimated end date", evidence, "past_end"))
        elif days <= STALE_END_DATE_DAYS:
            flags.append(HealthFlag("schedule", "medium", "Past estimated end date", evidence, "past_end"))
        else:
            flags.append(HealthFlag("data-quality", "low", "End date not updated", evidence, "end_date_stale"))

    # Overdue tasks: not finished and past their estimated end date
    overdue = [
        t for t in tasks
        if t.get("actualEndDate") is None
        and _as_date(t.get("estimatedEndDate")) is not None
        and _as_date(t["estimatedEndDate"]) < today
    ]
    if overdue and not halted:
        overdue.sort(key=lambda t: _as_date(t["estimatedEndDate"]))
        listed = ", ".join(f"{t['name']} (due {_fmt_date(_as_date(t['estimatedEndDate']))})"
                           for t in overdue[:MAX_LISTED_TASKS])
        more = f" and {len(overdue) - MAX_LISTED_TASKS} more" if len(overdue) > MAX_LISTED_TASKS else ""
        flags.append(HealthFlag("schedule", "medium", f"{len(overdue)} overdue task(s)", listed + more + ".", "overdue_tasks"))

    # Tasks whose logged effort exceeds their estimate
    overrun = [
        t for t in tasks
        if (t.get("estimatedTime") or 0) > 0
        and (t.get("workedSeconds") or 0) > t["estimatedTime"] * estimate_unit_seconds
    ]
    if overrun:
        overrun.sort(key=lambda t: t["workedSeconds"] - t["estimatedTime"] * estimate_unit_seconds, reverse=True)
        listed = ", ".join(
            f"{t['name']} ({_fmt_hours(t['workedSeconds'])} vs {_fmt_hours(t['estimatedTime'] * estimate_unit_seconds)})"
            for t in overrun[:MAX_LISTED_TASKS]
        )
        more = f" and {len(overrun) - MAX_LISTED_TASKS} more" if len(overrun) > MAX_LISTED_TASKS else ""
        flags.append(HealthFlag("effort", "medium", f"{len(overrun)} task(s) over estimate", listed + more + ".", "tasks_over_estimate"))

    # Activity: started a while ago but no recent time logged
    start_date = _as_date(project.get("startDate"))
    cutoff = today - timedelta(days=inactive_days)
    last_logged = _as_date(project.get("lastLoggedDate"))
    if not halted and start_date is not None and start_date <= cutoff and (last_logged is None or last_logged < cutoff):
        last = _fmt_date(last_logged) if last_logged else "never"
        flags.append(HealthFlag("activity", "low", f"No time logged in {inactive_days} days",
                                f"Last timesheet entry: {last}.", "inactive"))

    return flags


class ProjectHealthService:
    def __init__(self, session: Session, estimated_time_unit: str = "seconds", inactive_days: int = 14):
        if estimated_time_unit not in UNIT_TO_SECONDS:
            raise ValueError(f"ESTIMATED_TIME_UNIT must be one of {list(UNIT_TO_SECONDS)}, got '{estimated_time_unit}'")
        self.project_repo = ProjectRepository(session)
        self.task_repo = TaskRepository(session)
        self.estimate_unit_seconds = UNIT_TO_SECONDS[estimated_time_unit]
        self.inactive_days = inactive_days

    def check_all(self, today: date) -> List[ProjectHealth]:
        """Evaluate every active delivery project; return those with flags, worst first."""
        projects = self.project_repo.get_active_projects_with_effort()
        tasks_by_project: Dict[int, List[Dict]] = {}
        for task in self.task_repo.get_active_tasks_with_effort():
            tasks_by_project.setdefault(task["projectId"], []).append(task)

        results = []
        for project in projects:
            if is_leave_project(project["name"]):
                continue
            if (project.get("statusName") or "").strip().lower() in CLOSED_STATUSES:
                continue
            # A recorded actual end date means the project is finished
            actual_end = _as_date(project.get("actualEndDate"))
            if actual_end is not None and actual_end <= today:
                continue

            flags = evaluate_project(
                project,
                tasks_by_project.get(project["id"], []),
                today,
                estimate_unit_seconds=self.estimate_unit_seconds,
                inactive_days=self.inactive_days,
            )
            if flags:
                results.append(ProjectHealth(
                    project_id=project["id"],
                    project_name=(project["name"] or "").strip(),
                    pm_id=project.get("pmId"),
                    pm_name=project.get("pmName") or "Unassigned",
                    pm_email=project.get("pmEmail") or "",
                    status_name=project.get("statusName") or "",
                    project_type=project.get("projectTypeName") or "Not set",
                    flags=sorted(flags, key=lambda f: SEVERITY_ORDER[f.severity], reverse=True),
                ))

        at_risk = sum(1 for r in results if r.risk_flags)
        logger.info(f"Project health: {at_risk} of {len(projects)} active projects with delivery risks, "
                    f"{sum(1 for r in results if r.data_quality_flags)} with data-quality issues.")
        results.sort(key=lambda r: (SEVERITY_ORDER.get(r.severity, 0), len(r.risk_flags)), reverse=True)
        return results
