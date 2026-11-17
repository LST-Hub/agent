"""
Turns extracted Teams allocations into per-day rows tied to PM-tool users and projects.

Steps, all deterministic:
1. Match each person and project name to a PM-tool record (alias file, exact,
   unique partial match, then close spelling). Anything unclear goes to review;
   it is never guessed.
2. Expand date ranges to the person's working days (no weekends, company holidays
   or their floater holidays).
3. Apply later messages: a message that replaces an earlier plan clears that
   person's earlier rows for the same day; a newer row for the same person,
   project and day overrides the older one.
4. Hours not stated in the message are recorded as 0 h (never guessed).
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from services.allocation_extraction import RawAllocation
from services.teams_chat_service import ChatMessage
from utils.date_utils import is_weekend, is_holiday, is_floater_holiday
import logging

logger = logging.getLogger(__name__)

STANDARD_DAY_SECONDS = 8 * 3600
MAX_RANGE_DAYS = 31           # Longer ranges are sent to review instead of expanded
FUZZY_MIN_RATIO = 0.85
FUZZY_MIN_MARGIN = 0.05
ALIASES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "teams_aliases.json")


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (name or "").lower())).strip()


@dataclass
class Match:
    id: Optional[int]
    name: Optional[str]
    method: str                     # alias, exact, partial, fuzzy, ambiguous, none
    candidates: List[str] = field(default_factory=list)


class NameMatcher:
    """Matches free-text names to {id: name} records."""

    def __init__(self, records: Dict[int, str], aliases: Dict[str, str] = None, preferred: set = None):
        self.records = {rid: name.strip() for rid, name in records.items() if name and name.strip()}
        # When several records fit and exactly one of them is preferred (e.g. the only active project), it wins
        self.preferred = set(preferred or ())
        self.by_norm: Dict[str, List[int]] = {}
        for rid, name in self.records.items():
            self.by_norm.setdefault(normalize(name), []).append(rid)
        # alias text (compared without spaces, so "SOC2" = "SOC 2") -> exact record name from the aliases file
        self.aliases: Dict[str, int] = {}
        for alias, target in (aliases or {}).items():
            ids = self.by_norm.get(normalize(target), [])
            if len(ids) > 1 and self.preferred:
                ids = [i for i in ids if i in self.preferred] or ids
            if len(ids) == 1:
                self.aliases[normalize(alias).replace(" ", "")] = ids[0]
            else:
                logger.warning(f"Alias '{alias}' points to '{target}', which is not a unique record; ignored.")

    def _result(self, ids: List[int], method: str) -> Match:
        if len(ids) > 1 and self.preferred:
            ids = [i for i in ids if i in self.preferred] or ids
        if len(ids) == 1:
            return Match(ids[0], self.records[ids[0]], method)
        return Match(None, None, "ambiguous", sorted(self.records[i] for i in ids))

    def match(self, text: str) -> Match:
        key = normalize(text)
        if not key:
            return Match(None, None, "none")
        alias_key = key.replace(" ", "")
        if alias_key in self.aliases:
            rid = self.aliases[alias_key]
            return Match(rid, self.records[rid], "alias")
        if key in self.by_norm:
            return self._result(self.by_norm[key], "exact")

        # Every word typed is the start of a word in the record: "Mayur" -> "Mayur Malode", "Order Pilot" -> "Order Pilot - Sales Order"
        # Also the same words without spaces: "OrderPilot" -> "Order Pilot - Sales Order" and "OrderPilot - Accora".
        # Both kinds are pooled, so every record the words could mean is considered together.
        tokens = key.split()
        compact = key.replace(" ", "")
        partial = [rid for rid, name in self.records.items()
                   if all(any(w.startswith(t) for w in normalize(name).split()) for t in tokens)
                   or normalize(name).replace(" ", "").startswith(compact)]
        if partial:
            return self._result(partial, "partial")

        scored = sorted(((SequenceMatcher(None, key, normalize(name)).ratio(), rid)
                         for rid, name in self.records.items()), reverse=True)
        if scored and scored[0][0] >= FUZZY_MIN_RATIO and (len(scored) == 1 or scored[0][0] - scored[1][0] >= FUZZY_MIN_MARGIN):
            return Match(scored[0][1], self.records[scored[0][1]], "fuzzy")
        return Match(None, None, "none", [self.records[rid] for ratio, rid in scored[:3] if ratio >= 0.6])


def load_aliases(path: str = ALIASES_FILE) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(people aliases, project aliases): alias text -> exact PM-tool name."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, {}
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return {}, {}
    return data.get("people", {}) or {}, data.get("projects", {}) or {}


@dataclass
class PlannedDay:
    """One person, project and day from Teams."""
    user_id: int
    user_name: str
    project_id: int
    project_name: str
    date: date
    seconds: int
    hours_stated: bool
    confidence: str
    message_id: str
    sent_at: datetime
    sender: str
    evidence: str
    match_note: str = ""
    task: str = ""


@dataclass
class ReviewItem:
    message_id: str
    sent_at: datetime
    sender: str
    person: str
    project: Optional[str]
    reason: str
    evidence: str


@dataclass
class Resolution:
    planned: List[PlannedDay]
    review: List[ReviewItem]


def _why_unmatched(kind: str, raw: str, m: Match) -> str:
    if m.method == "ambiguous":
        return f"{kind} '{raw}' could be: {', '.join(m.candidates)}"
    hint = f" (closest: {', '.join(m.candidates)})" if m.candidates else ""
    return f"{kind} '{raw}' not found in the PM tool{hint}"


def check_name(kind: str, chosen: str, written: str, matcher: NameMatcher) -> Tuple[Optional[Match], str, str]:
    """
    Verify the AI's choice against the words in the message.
    Returns (match or None, review reason, note). The AI's pick is only accepted
    when the written words do not point to a different or second record.
    """
    written = written or chosen
    by_ai = matcher.match(chosen)
    by_text = by_ai if normalize(written) == normalize(chosen) else matcher.match(written)
    if by_text.method == "alias":
        return by_text, "", ""     # an alias someone defined always wins over the AI's choice

    if by_ai.id is None:
        if by_text.id is not None:
            return by_text, "", (f"'{written}' read as {by_text.name} (close spelling)" if by_text.method == "fuzzy" else "")
        return None, _why_unmatched(kind, written, by_text if by_text.method == "ambiguous" else by_ai), ""
    if by_text.method == "ambiguous":
        return None, _why_unmatched(kind, written, by_text), ""
    if by_text.id is not None and by_text.id != by_ai.id:
        return None, f"{kind} '{written}' was read by AI as {by_ai.name}, but matches {by_text.name}", ""
    if by_text.id is None:
        return by_ai, "", f"'{written}' read as {by_ai.name} by AI"
    if by_text.method == "fuzzy":
        return by_ai, "", f"'{written}' read as {by_ai.name} (close spelling)"
    return by_ai, "", ""   # exact, alias or a unique partial name: nothing worth flagging


def _task_label(raw: RawAllocation) -> str:
    """'Development (2h)' when several tasks share one project row, so the split stays visible."""
    return f"{raw.task} ({raw.hours_per_day:g}h)" if raw.hours_per_day is not None else raw.task


def working_days_for(start: date, end: date, user_id: int,
                     floater: Callable[[date, int], bool] = is_floater_holiday) -> List[date]:
    days, d = [], start
    while d <= end:
        if not is_weekend(d) and not is_holiday(d) and not floater(d, user_id):
            days.append(d)
        d += timedelta(days=1)
    return days


def resolve_allocations(
    raw_rows: Sequence[RawAllocation],
    messages: Sequence[ChatMessage],
    people: NameMatcher,
    projects: NameMatcher,
    floater: Callable[[date, int], bool] = is_floater_holiday,
) -> Resolution:
    by_id = {m.id: m for m in messages}
    review: List[ReviewItem] = []

    def to_review(raw: RawAllocation, reason: str):
        msg = by_id[raw.message_id]
        review.append(ReviewItem(raw.message_id, msg.sent_at, msg.sender, raw.person, raw.project, reason, raw.evidence))

    # 1-2. Match names and expand to days. Each candidate keeps its hours (None = not stated).
    candidates: List[Tuple[RawAllocation, Match, Optional[Match], date]] = []
    notes_by_row: Dict[int, List[str]] = {}
    for raw in raw_rows:
        pm, reason, person_note = check_name("Person", raw.person, raw.person_text, people)
        if pm is None:
            to_review(raw, reason)
            continue
        prj, project_note = None, ""
        if raw.project is not None:
            prj, reason, project_note = check_name("Project", raw.project, raw.project_text, projects)
            if prj is None:
                to_review(raw, reason)
                continue
        notes_by_row[id(raw)] = [n for n in (person_note, project_note) if n]
        if (raw.end_date - raw.start_date).days >= MAX_RANGE_DAYS:
            to_review(raw, f"Date range {raw.start_date:%d-%m-%Y} to {raw.end_date:%d-%m-%Y} is longer than {MAX_RANGE_DAYS} days")
            continue
        days = working_days_for(raw.start_date, raw.end_date, pm.id, floater)
        if not days:
            to_review(raw, f"No working days between {raw.start_date:%d-%m-%Y} and {raw.end_date:%d-%m-%Y}")
            continue
        candidates.extend((raw, pm, prj, d) for d in days)

    # 3. Apply messages in the order they were sent
    candidates.sort(key=lambda c: by_id[c[0].message_id].sent_at)
    # (user, day) -> project -> rows from one message (several rows of one message add up,
    # e.g. "Protecta 7h + 1h Protecta support"; a newer message for the project replaces them)
    state: Dict[Tuple[int, date], Dict[int, List[Tuple[RawAllocation, Match, Match]]]] = {}
    for raw, pm, prj, d in candidates:
        day = state.setdefault((pm.id, d), {})
        if prj is not None and raw.hours_per_day == 0:
            day.pop(prj.id, None)         # cancellation of one project; the person's other work stays
            continue
        if raw.replaces_earlier:
            for pid in [pid for pid, rows in day.items() if rows[0][0].message_id != raw.message_id]:
                del day[pid]
        if prj is None:
            continue                      # cancellation of the whole day
        rows = day.get(prj.id)
        if rows and rows[0][0].message_id == raw.message_id:
            rows.append((raw, pm, prj))
        else:
            day[prj.id] = [(raw, pm, prj)]

    def stated_seconds(rows) -> Optional[int]:
        hours = [r.hours_per_day for r, _, _ in rows if r.hours_per_day is not None]
        return int(sum(hours) * 3600) if hours else None

    # 4. Hours not stated are recorded as 0 h
    planned: List[PlannedDay] = []
    for (uid, d), day in state.items():
        stated = {pid: stated_seconds(rows) for pid, rows in day.items()}
        for pid, rows in day.items():
            raw, pm, prj = rows[0]
            msg = by_id[raw.message_id]
            seconds = stated[pid] if stated[pid] is not None else 0
            notes = []
            for r, _, _ in rows:
                notes += [n for n in notes_by_row.get(id(r), []) if n not in notes]
            if stated[pid] is None:
                notes.append("hours not stated (recorded as 0 h)")
            confidence = min((r.confidence for r, _, _ in rows), key=["low", "medium", "high"].index)  # weakest wins
            if confidence == "high" and any(n.endswith(" by AI") for n in notes):
                confidence = "medium"   # name matched only by the AI's judgement, not by the written words
            planned.append(PlannedDay(uid, pm.name, pid, prj.name, d, seconds, stated[pid] is not None,
                                      confidence, raw.message_id, msg.sent_at, msg.sender,
                                      " + ".join(dict.fromkeys(r.evidence for r, _, _ in rows if r.evidence)),
                                      "; ".join(notes),
                                      task="; ".join(dict.fromkeys(_task_label(r) for r, _, _ in rows if r.task))))

    # A review item can be added once per day for the same row; keep one
    seen, unique_review = set(), []
    for item in review:
        key = (item.message_id, item.person, item.project, item.reason)
        if key not in seen:
            seen.add(key)
            unique_review.append(item)

    planned.sort(key=lambda p: (p.date, p.user_name, p.project_name))
    return Resolution(planned, unique_review)


def person_day_totals(planned: Sequence[PlannedDay]) -> Dict[Tuple[int, date], int]:
    totals: Dict[Tuple[int, date], int] = {}
    for p in planned:
        totals[(p.user_id, p.date)] = totals.get((p.user_id, p.date), 0) + p.seconds
    return totals
