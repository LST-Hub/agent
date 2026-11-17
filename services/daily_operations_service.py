"""
Daily Operations Service: allocation and timesheet checks for the management report.

Checks (per active user and working day in the window):
- Overtime: logged time above standard daily capacity.
- Over-allocation: planned time above standard daily capacity.
- Allocation variance: logged time on a project differs from the plan.

"Planned" means PM-tool allocations plus day plans posted in the Teams allocation
chat (saved by the teams-allocations command). The PM tool wins for a person and day.
- Missing tickets: entries without a ticket on projects that require one.
- Short descriptions: billable entries with a missing or too-short description.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Tuple
from sqlalchemy.orm import Session
from database.repositories.allocation_repo import ResourceAllocationRepository
from database.repositories.timesheet_repo import TimesheetRepository
from services.project_health_service import is_leave_project
from services.teams_plans import merge_with_pm, SOURCE_PM
from utils.date_utils import is_weekend, is_holiday
import logging

logger = logging.getLogger(__name__)

STANDARD_DAY_SECONDS = 8 * 3600
VARIANCE_THRESHOLD_SECONDS = 3600   # Ignore allocation vs logged differences under 1 hour
MIN_DESCRIPTION_LENGTH = 10


@dataclass
class DailyOpsResult:
    window: List[date]
    overtime: List[Dict] = field(default_factory=list)
    over_allocations: List[Dict] = field(default_factory=list)
    allocation_variances: List[Dict] = field(default_factory=list)
    missing_tickets: List[Dict] = field(default_factory=list)
    short_descriptions: List[Dict] = field(default_factory=list)


def _as_date(value) -> date:
    return value.date() if hasattr(value, "date") and callable(value.date) else value


def working_days_back(until: date, count: int) -> List[date]:
    """The `count` most recent working days up to and including `until`, oldest first."""
    days = []
    d = until
    while len(days) < count:
        if not is_weekend(d) and not is_holiday(d):
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _sum_by_user_day(rows: List[Dict], days: set) -> Dict[Tuple[int, date], Dict]:
    """Group per-project rows into per user/day totals with a project breakdown."""
    totals: Dict[Tuple[int, date], Dict] = {}
    for r in rows:
        d = _as_date(r["date"])
        if d not in days:
            continue
        entry = totals.setdefault((r["userId"], d), {
            "userId": r["userId"], "userName": r["userName"], "date": d, "seconds": 0, "projects": {},
            "source": r.get("source", SOURCE_PM),
        })
        seconds = int(r["seconds"] or 0)
        entry["seconds"] += seconds
        name = (r.get("projectName") or "Unknown").strip()
        entry["projects"][name] = entry["projects"].get(name, 0) + seconds
    return totals


def find_overtime(logged_rows: List[Dict], days: List[date], capacity: int = STANDARD_DAY_SECONDS) -> List[Dict]:
    # Time booked to leave projects is absence, not work, so it never counts towards overtime
    work_rows = [r for r in logged_rows if not is_leave_project(r.get("projectName"))]
    totals = _sum_by_user_day(work_rows, set(days))
    found = [dict(t, over=t["seconds"] - capacity) for t in totals.values() if t["seconds"] > capacity]
    return sorted(found, key=lambda t: (t["date"], -t["over"]))


def find_over_allocations(alloc_rows: List[Dict], days: List[date], capacity: int = STANDARD_DAY_SECONDS) -> List[Dict]:
    totals = _sum_by_user_day(alloc_rows, set(days))
    found = [dict(t, over=t["seconds"] - capacity) for t in totals.values() if t["seconds"] > capacity]
    return sorted(found, key=lambda t: (t["date"], -t["over"]))


def find_allocation_variances(
    alloc_rows: List[Dict],
    logged_rows: List[Dict],
    days: List[date],
    threshold: int = VARIANCE_THRESHOLD_SECONDS,
) -> List[Dict]:
    """
    Compare each allocation (user, project, day) with the time logged against it.
    Days on which the user logged nothing at all are skipped: that is a missing
    timesheet (handled by the reminder emails), not evidence of zero work.
    """
    day_set = set(days)
    logged = {}
    user_days_with_entries = set()
    for r in logged_rows:
        d = _as_date(r["date"])
        key = (r["userId"], r["projectId"], d)
        logged[key] = logged.get(key, 0) + int(r["seconds"] or 0)
        user_days_with_entries.add((r["userId"], d))

    found = []
    for a in alloc_rows:
        d = _as_date(a["date"])
        if d not in day_set or (a["userId"], d) not in user_days_with_entries:
            continue
        if a.get("hours_stated") is False:
            continue    # Teams plan without hours: nothing to compare against
        allocated = int(a["seconds"] or 0)
        actual = logged.get((a["userId"], a["projectId"], d), 0)
        diff = actual - allocated
        if abs(diff) >= threshold:
            found.append({
                "userId": a["userId"], "userName": a["userName"],
                "projectName": (a.get("projectName") or "Unknown").strip(),
                "pmName": a.get("pmName") or "", "source": a.get("source", SOURCE_PM),
                "date": d, "allocated": allocated, "logged": actual, "diff": diff,
            })
    return sorted(found, key=lambda v: (v["date"], v["userName"], v["projectName"]))


class DailyOperationsService:
    def __init__(self, session: Session, ignore_ids: List[int] = None):
        self.timesheet_repo = TimesheetRepository(session)
        self.allocation_repo = ResourceAllocationRepository(session)
        self.ignore_ids = set(ignore_ids or [])

    def _keep(self, rows: List[Dict]) -> List[Dict]:
        return [r for r in rows if r["userId"] not in self.ignore_ids]

    def run_checks(self, days: List[date], teams_plans: List[Dict] = ()) -> DailyOpsResult:
        start, end = days[0], days[-1]
        logged = self._keep(self.timesheet_repo.get_user_project_day_totals(start, end))
        allocations = merge_with_pm(self._keep(self.allocation_repo.get_user_project_day_allocations(start, end)),
                                    self._keep(list(teams_plans)))
        day_set = set(days)

        result = DailyOpsResult(
            window=days,
            overtime=find_overtime(logged, days),
            over_allocations=find_over_allocations(allocations, days),
            allocation_variances=find_allocation_variances(allocations, logged, days),
            missing_tickets=self._keep(self.timesheet_repo.get_entries_missing_ticket(start, end)),
            short_descriptions=[
                r for r in self._keep(self.timesheet_repo.get_billable_entries_short_description(start, end, MIN_DESCRIPTION_LENGTH))
                if _as_date(r["date"]) in day_set
            ],
        )
        logger.info(
            f"Daily ops {start}..{end}: {len(result.overtime)} overtime, {len(result.over_allocations)} over-allocated, "
            f"{len(result.allocation_variances)} allocation variances, {len(result.missing_tickets)} missing-ticket groups, "
            f"{len(result.short_descriptions)} short descriptions."
        )
        return result
