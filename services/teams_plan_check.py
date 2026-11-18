"""
Daily Teams plan check: who has not posted today's plan, whose plan for today is
over 8 hours, and which of today's messages could not be read.

Someone counts as having planned today when any of these is true:
  - a Teams plan for today names them (posted by them or by someone else; leave counts),
  - they posted in the allocation chat today (even if the message needs review),
  - the PM tool already has an allocation for them today.
Expected to post: delivery staff (the forecast's rule: at least 2 days of project
work in recent weeks), minus IGNORE_ID, TEAMS_PLAN_IGNORE_IDS and anyone on a
floater holiday today. Weekends and company holidays are not checked at all.
"""

import html
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, Iterable, List, Set
from services.allocation_matching import STANDARD_DAY_SECONDS


@dataclass
class MissingPerson:
    user_id: int
    name: str
    email: str = ""
    status: str = ""      # what happened with the reminder


@dataclass
class PlanCheckResult:
    day: date
    expected: int = 0
    missing: List[MissingPerson] = field(default_factory=list)
    over: List[Dict] = field(default_factory=list)      # {"userName", "seconds", "projects"}
    review: List = field(default_factory=list)          # ReviewItem from today's messages
    note: str = ""                                      # e.g. Teams login problem


def find_missing(expected: Dict[int, str], planned_ids: Iterable[int], ignore_ids: Iterable[int],
                 day: date, floater: Callable[[date, int], bool]) -> List[MissingPerson]:
    """Expected people (minus ignored and floater) with no plan for the day, sorted by name."""
    done, ignore = set(planned_ids), set(ignore_ids)
    return sorted((MissingPerson(uid, name) for uid, name in expected.items()
                   if uid not in done and uid not in ignore and not floater(day, uid)),
                  key=lambda m: m.name.lower())


def sender_ids(messages: Iterable, day: date, match: Callable[[str], object]) -> Set[int]:
    """People (by id) who posted in the chat on the given day."""
    ids = set()
    for m in messages:
        if m.sent_at.date() == day:
            hit = match(m.sender)
            if getattr(hit, "id", None) is not None:
                ids.add(hit.id)
    return ids


def over_planned(rows: Iterable[Dict], day: date, capacity: int = STANDARD_DAY_SECONDS) -> List[Dict]:
    """People whose plan rows for the day add up to more than the standard day."""
    by_user: Dict[int, Dict] = {}
    for r in rows:
        d = r["date"].date() if hasattr(r["date"], "date") and callable(r["date"].date) else r["date"]
        if d != day:
            continue
        u = by_user.setdefault(r["userId"], {"userName": r["userName"], "seconds": 0, "projects": [], "source": r.get("source", "")})
        u["seconds"] += int(r["seconds"] or 0)
        u["projects"].append((r["projectName"] or "?", int(r["seconds"] or 0)))
    return sorted((u for u in by_user.values() if u["seconds"] > capacity), key=lambda u: -u["seconds"])


def reminder_html(first_name: str, day: date, intended_for: str = "") -> str:
    test = f"<b>[TEST, meant for {html.escape(intended_for)}]</b><br>" if intended_for else ""
    return (f"{test}<i>Automated reminder:</i> Hi {html.escape(first_name)}, your work plan for today "
            f"({day:%a %d-%m-%Y}) is not posted in the allocation group yet. "
            "Please post your projects and hours there. If you are on leave today, please post that too. Thanks!")
