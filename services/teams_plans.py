"""
Teams day plans: save matched plans to the agent store, load them back, and merge
them with PM-tool allocations for the forecast and daily-ops reports.

Rule when both sources exist: the PM tool wins for that person and day, so a day
is never counted twice.
"""

import logging
from datetime import date
from typing import Dict, Iterable, List, Set, Tuple
from config.settings import settings

logger = logging.getLogger(__name__)

SOURCE_PM = "PM tool"
SOURCE_TEAMS = "Teams"


def ids_to_replace(existing: Iterable, planned: Iterable, read_message_ids: Set[str]) -> List[int]:
    """
    Stored rows that this run supersedes: rows from a message read again in this run
    (its plan has been worked out afresh, including later changes and cancellations),
    and rows for a person and day that this run has a plan for.
    `existing` items need id, user_id, work_date and message_id; `planned` items need
    user_id and date.
    """
    new_days = {(p.user_id, p.date) for p in planned}
    return [r.id for r in existing
            if r.message_id in read_message_ids or (r.user_id, r.work_date) in new_days]


def save_plans(session, planned: List, read_message_ids: Set[str]) -> Tuple[int, int]:
    """Replace the superseded rows with this run's plans. Returns (removed, saved)."""
    from database.agent_store import TeamsPlan
    planned = list(planned)
    days = {p.date for p in planned}
    existing = session.query(TeamsPlan).filter(
        (TeamsPlan.message_id.in_(read_message_ids or {""})) | (TeamsPlan.work_date.in_(days or {date.min}))
    ).all()
    remove = set(ids_to_replace(existing, planned, read_message_ids))
    for row in existing:
        if row.id in remove:
            session.delete(row)
    for p in planned:
        session.add(TeamsPlan(
            user_id=p.user_id, user_name=p.user_name.strip(), project_id=p.project_id,
            project_name=p.project_name.strip(), work_date=p.date, seconds=int(p.seconds),
            hours_stated=bool(p.hours_stated), confidence=p.confidence, task=p.task or None,
            message_id=p.message_id, sent_at=p.sent_at, sender=p.sender, evidence=p.evidence,
            match_note=p.match_note or None,
        ))
    session.commit()
    return len(remove), len(planned)


def load_plans(session, start: date, end: date) -> List[Dict]:
    """Saved plans between two dates, shaped like PM-tool allocation rows."""
    from database.agent_store import TeamsPlan
    rows = session.query(TeamsPlan).filter(TeamsPlan.work_date >= start, TeamsPlan.work_date <= end).all()
    return [{"userId": r.user_id, "userName": r.user_name, "projectId": r.project_id,
             "projectName": r.project_name, "pmName": "", "date": r.work_date, "seconds": r.seconds,
             "source": SOURCE_TEAMS, "task": r.task or "", "hours_stated": bool(r.hours_stated)} for r in rows]


def load_plans_safe(start: date, end: date) -> List[Dict]:
    """load_plans, but a missing or unreachable agent store only logs a warning (reports still run)."""
    if not settings.agent_database_url:
        logger.info("AGENT_DATABASE_URL not set: Teams plans not used.")
        return []
    try:
        from database.agent_store import get_agent_session
        session = get_agent_session()
        try:
            return load_plans(session, start, end)
        finally:
            session.close()
    except Exception as exc:  # the report is still useful without the plans
        logger.warning(f"Could not load Teams plans from the agent store: {exc}")
        return []


def merge_with_pm(pm_rows: List[Dict], teams_rows: List[Dict]) -> List[Dict]:
    """
    PM-tool allocations plus Teams plans for the person-days the PM tool does not cover.
    Every returned row has a `source` key.
    """
    def day(r):
        d = r["date"]
        return d.date() if hasattr(d, "date") and callable(d.date) else d

    pm_days = {(r["userId"], day(r)) for r in pm_rows}
    merged = [dict(r, source=r.get("source", SOURCE_PM)) for r in pm_rows]
    merged += [dict(r, source=SOURCE_TEAMS) for r in teams_rows if (r["userId"], day(r)) not in pm_days]
    return merged
