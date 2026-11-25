"""
The connector's actions. Each one reads TaskSprint (read-only) and/or the agent-db and returns plain
data that Claude turns into an answer. The summarise_* functions are pure so they can be unit tested.
Hours are returned as decimal hours (the DB stores seconds).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional

from config.settings import settings
from utils.date_utils import get_current_date

logger = logging.getLogger(__name__)

MAX_ROWS = 100            # rows per list returned to Claude
MAX_RANGE_DAYS = 92       # widest date range for hour lookups
SECOND_KEYS = {"seconds": "hours", "over": "over_hours", "allocated": "allocated_hours",
               "logged": "logged_hours", "diff": "diff_hours"}


# ------------------------------------------------------------------ helpers

def hours(seconds) -> float:
    return round(int(seconds or 0) / 3600, 2)


def parse_day(value: Optional[str], default: date) -> date:
    if not value:
        return default
    text = str(value).strip().lower()
    today = get_current_date()
    if text == "today":
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Could not read the date '{value}'. Use YYYY-MM-DD.")


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def clean(row: Dict) -> Dict:
    """JSON-friendly copy: dates as ISO text, second counts as hours, project breakdowns in hours."""
    out = {}
    for key, value in row.items():
        if key in SECOND_KEYS:
            out[SECOND_KEYS[key]] = hours(value)
        elif key == "projects" and isinstance(value, dict):
            out["projects_hours"] = {name: hours(s) for name, s in value.items()}
        elif isinstance(value, (date, datetime)):
            out[key] = _as_date(value).isoformat()
        else:
            out[key] = value
    return out


def _matches(text: Optional[str], needle: Optional[str]) -> bool:
    return not needle or needle.strip().lower() in (text or "").lower()


def _limited(rows: List[Dict], limit: int = MAX_ROWS) -> Dict:
    return {"count": len(rows), "rows": [clean(r) for r in rows[:limit]],
            **({"note": f"only the first {limit} of {len(rows)} are listed"} if len(rows) > limit else {})}


def _pm_session():
    from database.connection import get_db_session
    return next(get_db_session())


# ------------------------------------------------------------------ project health

def summarise_health(results, project: str = None, pm: str = None, include_data_quality: bool = True) -> Dict:
    picked = [r for r in results if _matches(r.project_name, project) and _matches(r.pm_name, pm)]
    projects = []
    for r in picked:
        flags = r.flags if include_data_quality else r.risk_flags
        if not flags:
            continue
        projects.append({
            "project": r.project_name, "pm": r.pm_name, "status": r.status_name,
            "worst_risk": r.severity or "none",
            "flags": [{"type": f.category, "severity": f.severity, "issue": f.title, "evidence": f.evidence}
                      for f in flags],
        })
    return {
        "projects_with_issues": len(projects),
        "with_delivery_risk": sum(1 for r in picked if r.risk_flags),
        "with_data_quality_issues": sum(1 for r in picked if r.data_quality_flags),
        "projects": projects[:MAX_ROWS],
    }


def project_health(project: str = None, pm: str = None, include_data_quality: bool = True) -> Dict:
    from services.project_health_service import ProjectHealthService
    today = get_current_date()
    session = _pm_session()
    try:
        results = ProjectHealthService(session, estimated_time_unit=settings.estimated_time_unit,
                                       inactive_days=settings.health_inactive_days).check_all(today)
    finally:
        session.close()
    return {"date": today.isoformat(), **summarise_health(results, project, pm, include_data_quality)}


# ------------------------------------------------------------------ Teams allocations

def summarise_plans(rows: Iterable[Dict], person: str = None, project: str = None) -> Dict:
    picked = [r for r in rows if _matches(r.get("userName"), person) and _matches(r.get("projectName"), project)]
    picked.sort(key=lambda r: ((r.get("userName") or ""), (r.get("projectName") or "")))
    plans = [{"person": r.get("userName"), "project": r.get("projectName"),
              "hours": hours(r.get("seconds")) if r.get("hours_stated", True) else None,
              "task": r.get("task") or ""} for r in picked]
    return {"people": len({p["person"] for p in plans}), "plans": plans[:MAX_ROWS * 2]}


def teams_allocations(day: str = None, person: str = None, project: str = None, refresh: bool = False) -> Dict:
    from services.teams_plans import load_plans_safe
    today = get_current_date()
    d = parse_day(day, today)
    rows = load_plans_safe(d, d)
    refreshed = False
    if d == today and (refresh or not rows):
        from agents.teams_allocation_agent import TeamsAllocationAgent
        TeamsAllocationAgent(save=True).run()    # reads the chat, saves today's plans to the agent-db
        rows, refreshed = load_plans_safe(d, d), True
    return {"date": d.isoformat(), "read_from_chat_now": refreshed,
            "hours_none_means": "the person did not state hours",
            **summarise_plans(rows, person, project)}


# ------------------------------------------------------------------ daily operations

def weekdays_between(start: date, end: date) -> List[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def summarise_daily_ops(result, person: str = None) -> Dict:
    def keep(rows):
        return [r for r in rows if _matches(r.get("userName"), person)]
    return {
        "days": [d.isoformat() for d in result.window],
        "overtime_over_8h": _limited(keep(result.overtime)),
        "planned_over_8h": _limited(keep(result.over_allocations)),
        "plan_vs_logged_gaps": _limited(keep(result.allocation_variances)),
        "entries_missing_ticket": _limited(keep(result.missing_tickets)),
        "billable_short_descriptions": _limited([dict(r, description=(r.get("description") or "")[:80])
                                                 for r in keep(result.short_descriptions)]),
    }


def daily_operations(start: str = None, end: str = None, person: str = None) -> Dict:
    from services.daily_operations_service import DailyOperationsService, working_days_back
    from services.teams_plans import load_plans_safe
    today = get_current_date()
    if start or end:
        last = parse_day(end, today - timedelta(days=1))
        first = parse_day(start, last)
        if (last - first).days > 31:
            raise ValueError("Use at most 31 days for daily operations.")
        days = weekdays_between(first, last)
    else:
        days = working_days_back(today - timedelta(days=1), settings.daily_ops_lookback_days)
    if not days:
        raise ValueError("No working days in that range.")
    session = _pm_session()
    try:
        result = DailyOperationsService(session, settings.get_ignore_id_list()).run_checks(
            days, load_plans_safe(days[0], days[-1]))
    finally:
        session.close()
    return summarise_daily_ops(result, person)


# ------------------------------------------------------------------ forecast

def summarise_forecast(f, skill: str = None, person: str = None) -> Dict:
    people = [p for p in f.people if _matches(p.skill, skill) and _matches(p.name, person)]
    people.sort(key=lambda p: -(p.utilisation or 0))
    cap = sum(p.capacity for p in people)
    dem = sum(p.demand for p in people)
    return {
        "window": f"{f.days[0].isoformat()} to {f.days[-1].isoformat()} ({len(f.days)} working days)",
        "people": len(people),
        "capacity_hours": hours(cap),
        "confirmed_hours": hours(sum(p.confirmed for p in people)),
        "projected_hours": hours(sum(p.projected for p in people)),
        "free_hours": hours(cap - dem),
        "utilisation_pct": round(100 * dem / cap) if cap else None,
        "per_person": [{"person": p.name, "skill": p.skill, "role": p.role, "capacity_hours": hours(p.capacity),
                        "confirmed_hours": hours(p.confirmed), "projected_hours": hours(p.projected),
                        "free_hours": hours(p.free),
                        "utilisation_pct": round(100 * p.utilisation) if p.utilisation is not None else None,
                        "projects_hours": {name: hours(v.get("confirmed", 0) + v.get("projected", 0))
                                           for name, v in p.projects.items()}}
                       for p in people[:MAX_ROWS]],
        "notes": "confirmed = PM-tool allocations and Teams day plans; projected = recent run rate",
    }


def resource_forecast(skill: str = None, person: str = None) -> Dict:
    from services.forecast_service import ForecastService
    from services.teams_plans import load_plans_safe
    session = _pm_session()
    try:
        f = ForecastService(session, settings.get_ignore_id_list()).run(get_current_date(), teams_loader=load_plans_safe)
    finally:
        session.close()
    return summarise_forecast(f, skill, person)


# ------------------------------------------------------------------ logged hours

def summarise_hours(rows: Iterable[Dict], person: str = None, project: str = None, group_by: str = "person") -> Dict:
    ignored = set(settings.get_ignore_id_list())
    picked = [r for r in rows if r["userId"] not in ignored
              and _matches(r.get("userName"), person) and _matches(r.get("projectName"), project)]
    key = {"person": "userName", "project": "projectName", "day": "date"}.get(group_by)
    if key is None:
        raise ValueError("group_by must be person, project or day.")
    groups: Dict = {}
    for r in picked:
        k = _as_date(r[key]) if key == "date" else (r.get(key) or "Unknown")
        g = groups.setdefault(k, {"seconds": 0, "breakdown": {}})
        g["seconds"] += int(r["seconds"] or 0)
        sub = r.get("projectName") if group_by != "project" else r.get("userName")
        g["breakdown"][sub or "Unknown"] = g["breakdown"].get(sub or "Unknown", 0) + int(r["seconds"] or 0)
    ordered = sorted(groups.items(), key=lambda kv: kv[0] if group_by == "day" else -kv[1]["seconds"])
    return {
        "total_hours": hours(sum(g["seconds"] for _, g in ordered)),
        "groups": [{group_by: (k.isoformat() if isinstance(k, date) else k), "hours": hours(g["seconds"]),
                    ("by_project" if group_by != "project" else "by_person"):
                        {n: hours(s) for n, s in sorted(g["breakdown"].items(), key=lambda kv: -kv[1])}}
                   for k, g in ordered[:MAX_ROWS]],
    }


def logged_hours(start: str, end: str = None, person: str = None, project: str = None, group_by: str = "person") -> Dict:
    from database.repositories.timesheet_repo import TimesheetRepository
    first = parse_day(start, get_current_date())
    last = parse_day(end, first)
    if last < first:
        first, last = last, first
    if (last - first).days > MAX_RANGE_DAYS:
        raise ValueError(f"Use at most {MAX_RANGE_DAYS} days.")
    session = _pm_session()
    try:
        rows = TimesheetRepository(session).get_user_project_day_totals(first, last)
    finally:
        session.close()
    return {"from": first.isoformat(), "to": last.isoformat(),
            "note": "approved and pending timesheets of active people; rejected entries excluded",
            **summarise_hours(rows, person, project, group_by)}


# ------------------------------------------------------------------ email a report to the asker

REPORTS = ("project-health", "daily-ops", "forecast")


def email_report(report: str, to_email: str) -> str:
    """Build one of the standard HTML reports and email it to the person asking (only to them)."""
    from services.email_service import EmailService
    report = (report or "").strip().lower()
    today = get_current_date()
    session = _pm_session()
    try:
        if report == "project-health":
            from agents.project_health_agent import ProjectHealthAgent
            from services.project_health_service import ProjectHealthService
            results = ProjectHealthService(session, estimated_time_unit=settings.estimated_time_unit,
                                           inactive_days=settings.health_inactive_days).check_all(today)
            subject = f"Project Health Report - {today.strftime('%d-%m-%Y')}"
            body = ProjectHealthAgent()._build_html_email_body(results, today)
        elif report == "daily-ops":
            from agents.daily_operations_agent import DailyOperationsAgent, _d
            from services.daily_operations_service import DailyOperationsService, working_days_back
            from services.teams_plans import load_plans_safe
            days = working_days_back(today - timedelta(days=1), settings.daily_ops_lookback_days)
            result = DailyOperationsService(session, settings.get_ignore_id_list()).run_checks(
                days, load_plans_safe(days[0], days[-1]))
            period = _d(days[0]) if len(days) == 1 else f"{_d(days[0])} to {_d(days[-1])}"
            subject = f"Daily Operations Report - {period}"
            body = DailyOperationsAgent()._build_html_email_body(result, period)
        elif report == "forecast":
            from agents.forecast_agent import ForecastAgent, _d
            from services.forecast_service import ForecastService
            from services.teams_plans import load_plans_safe
            f = ForecastService(session, settings.get_ignore_id_list()).run(today, teams_loader=load_plans_safe)
            subject = f"Resource Forecast - {_d(f.days[0])} to {_d(f.days[-1])}"
            body = ForecastAgent()._build_html_email_body(f)
        else:
            raise ValueError(f"report must be one of: {', '.join(REPORTS)}")
    finally:
        session.close()

    EmailService.reset_run()          # the server is long-running: a fresh send budget per request
    try:
        ok = EmailService().send_alert(to_email=to_email, subject=subject, body=body, is_html=True)
    finally:
        EmailService.close()
    if not ok:
        raise RuntimeError("The email could not be sent. Please try again later.")
    redirected = settings.email_test_recipient
    return f"Sent '{subject}' to {redirected or to_email}" + (" (test mode)" if redirected else "") + "."
