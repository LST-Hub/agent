"""
Forecast Service: capacity vs demand for the next 15 working days.

Demand comes from two sources, always reported separately:
- Confirmed: future allocations entered in the PM tool (ResourceAllocation).
- Projected: each person's average daily hours per project over the last
  HISTORY_WORKING_DAYS, assumed to continue for projects that are still active.
  This is a run-rate planning rule, not a commitment.

Capacity is the standard working day on working days, minus company holidays and
the person's floater holidays. Planned leave is not recorded anywhere yet, so it is
not subtracted (the report says so).
"""

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from database.repositories.allocation_repo import ResourceAllocationRepository
from database.repositories.project_repo import ProjectRepository
from database.repositories.timesheet_repo import TimesheetRepository
from services.project_health_service import is_leave_project, CLOSED_STATUSES, HALTED_STATUS
from services.daily_operations_service import working_days_back
from utils.date_utils import is_weekend, is_holiday, is_floater_holiday
from services.teams_plans import merge_with_pm, SOURCE_TEAMS
import logging

logger = logging.getLogger(__name__)

FORECAST_WORKING_DAYS = 15
HISTORY_WORKING_DAYS = 20
STANDARD_DAY_SECONDS = 8 * 3600
MIN_HISTORY_WORK_SECONDS = 16 * 3600   # At least 2 days of project work to count as delivery staff
OVERLOAD_UTILISATION = 1.0             # Demand above capacity
SPARE_UTILISATION = 0.75               # Demand below 75% of capacity
IDLE_PROJECT_NAMES = {"idle", "unknown"}
UNMAPPED_SKILL = "Not mapped"
SKILLS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "employee_skills.json")


@dataclass
class PersonForecast:
    user_id: int
    name: str
    skill: str
    role: str = ""
    capacity: int = 0
    confirmed: int = 0
    projected: int = 0
    projects: Dict[str, Dict[str, int]] = field(default_factory=dict)  # name -> {"confirmed", "projected"}

    @property
    def demand(self) -> int:
        return self.confirmed + self.projected

    @property
    def free(self) -> int:
        return self.capacity - self.demand

    @property
    def utilisation(self) -> Optional[float]:
        return self.demand / self.capacity if self.capacity else None


@dataclass
class Forecast:
    days: List[date]
    history: List[date]
    people: List[PersonForecast]
    daily: List[Dict]                       # per day team totals
    projects: List[Dict]                    # per project totals over the window
    skills: List[Dict]                      # per primary skill totals over the window
    skills_mapped: int = 0
    roles: List[Dict] = field(default_factory=list)  # per role totals over the window
    roles_mapped: int = 0
    confirmed_teams: int = 0                # part of confirmed that comes from Teams day plans


def _as_date(value) -> date:
    return value.date() if hasattr(value, "date") and callable(value.date) else value


def working_days_forward(start: date, count: int) -> List[date]:
    """The first `count` working days from `start` (inclusive)."""
    days, d = [], start
    while len(days) < count:
        if not is_weekend(d) and not is_holiday(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def is_forecastable_project(info: Optional[Dict]) -> bool:
    """Active project that is not finished, cancelled or halted."""
    if info is None:  # inactive projects are not returned by the active-project query
        return False
    status = (info.get("statusName") or "").strip().lower()
    return status not in CLOSED_STATUSES and status != HALTED_STATUS and info.get("actualEndDate") is None


def load_skills(path: str = SKILLS_FILE) -> Dict[int, List[str]]:
    """user_id -> skills (first = primary). Missing file or blank entries mean 'not mapped'."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return {}
    return {int(uid): [s.strip() for s in entry.get("skills", []) if s and s.strip()]
            for uid, entry in data.get("employees", {}).items()}


def load_roles(path: str = SKILLS_FILE) -> Dict[int, str]:
    """user_id -> role (e.g. 'NetSuite Developer'). People without a role are left out."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return {}
    return {int(uid): entry["role"].strip() for uid, entry in data.get("employees", {}).items()
            if (entry.get("role") or "").strip()}


SKILLS_FILE_NOTE = (
    "List each person's skills, primary skill first (e.g. NetSuite, Salesforce, Custom Development, QA, DevOps). "
    "The forecast counts a person's hours once, under the primary skill, and also groups people by role. "
    "Leave skills empty if unknown. New delivery staff are added by `python main.py forecast-skills-template`; "
    "existing skills are never overwritten. A practice skill sheet (CSV) is imported with "
    "`python main.py forecast-skills-import <file.csv> [--practice NAME]`, which updates the people it matches."
)


def parse_skill_cell(cell: str) -> List[str]:
    """
    Skills from one sheet cell: either comma-separated, or an indented outline
    (headings followed by indented items), in which case only the items are kept.
    """
    lines = [line.rstrip() for line in (cell or "").replace("\r", "").split("\n")]
    lines = [line for line in lines if line.strip()]
    if len(lines) <= 1:
        items = (lines[0] if lines else "").split(",")
    else:
        indent = [len(line) - len(line.lstrip(" \t")) for line in lines]
        items = []
        for i, line in enumerate(lines):
            is_heading = i + 1 < len(lines) and indent[i + 1] > indent[i]
            if not is_heading:
                items.extend(line.split(","))
    seen, skills = set(), []
    for item in items:
        skill = " ".join(item.split())
        if skill and skill.lower() not in seen:
            seen.add(skill.lower())
            skills.append(skill)
    return skills


@dataclass
class SkillImportResult:
    updated: List[Tuple[str, int, str]] = field(default_factory=list)  # (sheet name, user id, PM name)
    not_matched: List[Tuple[str, str]] = field(default_factory=list)   # (sheet name, reason)


def import_skills_csv(csv_path: str, practice: str, path: str = SKILLS_FILE) -> SkillImportResult:
    """
    Update the skills file from a practice skill sheet with columns
    'Resource Name', 'Role', 'Skill Sets', 'Expertise Level'.

    Names are matched to the people already in the skills file (unique first name,
    full name, or close spelling). Unclear names are reported, never guessed. A
    matched person's skills become [practice, *sheet skills]; role and level are set.
    """
    import csv
    from services.allocation_matching import NameMatcher

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(csv_path, "r", encoding=encoding, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    rows = [{(k or "").strip().lower(): (v or "") for k, v in r.items()} for r in rows]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    employees = data.setdefault("employees", {})
    matcher = NameMatcher({int(uid): e.get("name", "") for uid, e in employees.items()})

    result = SkillImportResult()
    for r in rows:
        sheet_name = " ".join(r.get("resource name", "").split())
        if not sheet_name:
            continue
        m = matcher.match(sheet_name)
        if m.id is None:
            reason = (f"could be {', '.join(m.candidates)}" if m.method == "ambiguous"
                      else "not in the skills file (not counted as delivery staff yet)")
            result.not_matched.append((sheet_name, reason))
            continue
        skills = [s for s in parse_skill_cell(r.get("skill sets", "")) if s.lower() != practice.lower()]
        entry = employees[str(m.id)]
        entry["role"] = " ".join(r.get("role", "").split())
        entry["level"] = " ".join(r.get("expertise level", "").split())
        entry["skills"] = [practice] + skills
        result.updated.append((sheet_name, m.id, m.name))

    data["_note"] = SKILLS_FILE_NOTE
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"_note": data.pop("_note"), **data}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return result


def update_skills_template(names: Dict[int, str], path: str = SKILLS_FILE) -> int:
    """Add missing people to the skills file without touching existing entries. Returns how many were added."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    employees = data.setdefault("employees", {})
    added = 0
    for uid, name in sorted(names.items(), key=lambda kv: kv[1]):
        if str(uid) not in employees:
            employees[str(uid)] = {"name": name, "skills": []}
            added += 1
    data["_note"] = SKILLS_FILE_NOTE
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"_note": data.pop("_note"), **data}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return added


def build_run_rates(
    logged_rows: List[Dict],
    history: List[date],
    project_info: Dict[int, Dict],
    floater: Callable[[date, int], bool] = is_floater_holiday,
) -> Tuple[Dict[int, Dict[int, float]], Dict[int, str]]:
    """
    Average seconds per available day for each (user, project) over the history window.

    Returns ({user_id: {project_id: seconds_per_day}}, {user_id: name}) for delivery
    staff only. A user's available days exclude floater holidays and days spent fully
    on leave. Leave and idle time are not demand.
    """
    history_set = set(history)
    work: Dict[int, Dict[int, int]] = {}
    leave_by_day: Dict[Tuple[int, date], int] = {}
    names: Dict[int, str] = {}

    for r in logged_rows:
        d = _as_date(r["date"])
        if d not in history_set:
            continue
        uid, seconds = r["userId"], int(r["seconds"] or 0)
        names[uid] = r["userName"]
        project_name = (r.get("projectName") or "").strip()
        if is_leave_project(project_name):
            leave_by_day[(uid, d)] = leave_by_day.get((uid, d), 0) + seconds
        elif r.get("projectId") is not None and project_name.lower() not in IDLE_PROJECT_NAMES:
            work.setdefault(uid, {})
            work[uid][r["projectId"]] = work[uid].get(r["projectId"], 0) + seconds

    rates: Dict[int, Dict[int, float]] = {}
    for uid, by_project in work.items():
        if sum(by_project.values()) < MIN_HISTORY_WORK_SECONDS:
            continue
        available = sum(1 for d in history
                        if not floater(d, uid) and leave_by_day.get((uid, d), 0) < STANDARD_DAY_SECONDS)
        if available == 0:
            continue
        rates[uid] = {pid: sec / available for pid, sec in by_project.items()
                      if is_forecastable_project(project_info.get(pid))}
    return rates, {uid: names[uid] for uid in rates}


def build_forecast(
    days: List[date],
    history: List[date],
    run_rates: Dict[int, Dict[int, float]],
    names: Dict[int, str],
    allocations: List[Dict],
    project_info: Dict[int, Dict],
    skills: Dict[int, List[str]],
    floater: Callable[[date, int], bool] = is_floater_holiday,
    capacity_per_day: int = STANDARD_DAY_SECONDS,
    roles: Dict[int, str] = None,
) -> Forecast:
    roles = roles or {}
    day_set = set(days)
    confirmed: Dict[Tuple[int, int, date], int] = {}
    alloc_project_names: Dict[int, str] = {}
    # A Teams day plan covers the person's whole day, so it replaces every projection for that day
    planned_days = set()
    for a in allocations:
        d = _as_date(a["date"])
        if d not in day_set:
            continue
        if a.get("source") == SOURCE_TEAMS:
            if not int(a["seconds"] or 0):
                continue    # Teams plan without hours: not demand, the projection stays
            planned_days.add((a["userId"], d))
        key = (a["userId"], a["projectId"], d)
        confirmed[key] = confirmed.get(key, 0) + int(a["seconds"] or 0)
        names.setdefault(a["userId"], a["userName"])
        alloc_project_names[a["projectId"]] = (a.get("projectName") or "Unknown").strip()

    def project_name(pid: int) -> str:
        info = project_info.get(pid)
        return (info["name"].strip() if info else alloc_project_names.get(pid)) or f"Project {pid}"

    people: Dict[int, PersonForecast] = {}
    daily = {d: {"date": d, "capacity": 0, "confirmed": 0, "projected": 0} for d in days}
    projects: Dict[str, Dict] = {}

    for uid, name in names.items():
        person_skills = skills.get(uid) or []
        p = people[uid] = PersonForecast(uid, name, person_skills[0] if person_skills else UNMAPPED_SKILL,
                                         roles.get(uid, UNMAPPED_SKILL))
        user_projects = set(run_rates.get(uid, {})) | {pid for (u, pid, _d) in confirmed if u == uid}

        for d in days:
            cap = 0 if floater(d, uid) else capacity_per_day
            p.capacity += cap
            daily[d]["capacity"] += cap
            for pid in user_projects:
                c = confirmed.get((uid, pid, d))
                # A confirmed allocation replaces the projection for that project and day
                if c is not None:
                    kind, seconds = "confirmed", c
                elif cap == 0 or (uid, d) in planned_days:
                    continue
                else:
                    kind, seconds = "projected", run_rates.get(uid, {}).get(pid, 0)
                if not seconds:
                    continue
                seconds = int(round(seconds))
                setattr(p, kind, getattr(p, kind) + seconds)
                daily[d][kind] += seconds
                pname = project_name(pid)
                p.projects.setdefault(pname, {"confirmed": 0, "projected": 0})[kind] += seconds
                proj = projects.setdefault(pname, {"name": pname, "confirmed": 0, "projected": 0, "people": set(),
                                                   "billable": bool((project_info.get(pid) or {}).get("billable"))})
                proj[kind] += seconds
                proj["people"].add(name)

    def rollup(key: str) -> List[Dict]:
        rows: Dict[str, Dict] = {}
        for p in people.values():
            value = getattr(p, key)
            s = rows.setdefault(value, {key: value, "people": 0, "capacity": 0, "confirmed": 0, "projected": 0})
            s["people"] += 1
            s["capacity"] += p.capacity
            s["confirmed"] += p.confirmed
            s["projected"] += p.projected
        return sorted(rows.values(), key=lambda s: (s[key] == UNMAPPED_SKILL, s[key]))

    return Forecast(
        days=days,
        history=history,
        people=sorted(people.values(), key=lambda p: -(p.utilisation or 0)),
        daily=[daily[d] for d in days],
        projects=sorted(projects.values(), key=lambda x: -(x["confirmed"] + x["projected"])),
        skills=rollup("skill"),
        skills_mapped=sum(1 for p in people.values() if p.skill != UNMAPPED_SKILL),
        roles=rollup("role"),
        roles_mapped=sum(1 for p in people.values() if p.role != UNMAPPED_SKILL),
    )


def exclude_users(rows: List[Dict], ignore_ids) -> List[Dict]:
    """Drop rows of users on the IGNORE_ID list (excluded from every report)."""
    ignore = set(ignore_ids or ())
    return [r for r in rows if r["userId"] not in ignore]


class ForecastService:
    def __init__(self, session: Session, ignore_ids: List[int] = None):
        self.timesheet_repo = TimesheetRepository(session)
        self.allocation_repo = ResourceAllocationRepository(session)
        self.project_repo = ProjectRepository(session)
        self.ignore_ids = set(ignore_ids or ())

    def delivery_staff(self, today: date) -> Dict[int, str]:
        """Users who count as delivery staff on the current history window."""
        history = working_days_back(today - timedelta(days=1), HISTORY_WORKING_DAYS)
        project_info = {p["id"]: p for p in self.project_repo.get_active_projects_with_effort()}
        logged = exclude_users(self.timesheet_repo.get_user_project_day_totals(history[0], history[-1]), self.ignore_ids)
        return build_run_rates(logged, history, project_info)[1]

    def run(self, today: date, skills: Dict[int, List[str]] = None,
            teams_loader: Callable[[date, date], List[Dict]] = None) -> Forecast:
        """teams_loader(start, end) returns saved Teams day plans; they count as confirmed demand
        for the person-days that have no PM-tool allocation."""
        days = working_days_forward(today, FORECAST_WORKING_DAYS)
        history = working_days_back(today - timedelta(days=1), HISTORY_WORKING_DAYS)
        project_info = {p["id"]: p for p in self.project_repo.get_active_projects_with_effort()}

        logged = exclude_users(self.timesheet_repo.get_user_project_day_totals(history[0], history[-1]), self.ignore_ids)
        run_rates, names = build_run_rates(logged, history, project_info)
        allocations = merge_with_pm(
            exclude_users(self.allocation_repo.get_user_project_day_allocations(days[0], days[-1]), self.ignore_ids),
            exclude_users(teams_loader(days[0], days[-1]) if teams_loader else [], self.ignore_ids))

        forecast = build_forecast(days, history, run_rates, names, allocations, project_info,
                                  load_skills() if skills is None else skills,
                                  roles=load_roles() if skills is None else {})
        day_set = set(days)
        forecast.confirmed_teams = sum(int(a["seconds"] or 0) for a in allocations
                                       if a["source"] == SOURCE_TEAMS and _as_date(a["date"]) in day_set)
        logger.info(f"Forecast {days[0]}..{days[-1]}: {len(forecast.people)} people, "
                    f"{sum(p.capacity for p in forecast.people) / 3600:.0f} h capacity, "
                    f"{sum(p.confirmed for p in forecast.people) / 3600:.0f} h confirmed "
                    f"({forecast.confirmed_teams / 3600:.0f} h from Teams), "
                    f"{sum(p.projected for p in forecast.people) / 3600:.0f} h projected.")
        return forecast
