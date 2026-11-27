"""
Day plans posted in a person's 1:1 chat with the agent, turned into TaskSprint allocations.

Plain, testable rules (the AI only turns the message into rows; see allocation_extraction):
- Only the sender's own plan for today is saved. Other people, other dates, cancellations
  and rows without hours are answered with what to fix, never guessed.
- The project must be active and the person a member of it (ProjectUser); otherwise the
  agent tells the project's PM (see pm_message_html) and the person sends it again once added.
- TaskSprint needs a task for every allocation. The written task is matched to the
  project's active tasks the person is on (TaskUser). With no task written, or no match,
  a person on exactly one task of the project gets that task; otherwise they are asked.
- Rows that are already allocated in TaskSprint (same project and task that day) are
  skipped: adding is supported, changing hours is done in TaskSprint for now.
"""

import html
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.allocation_extraction import RawAllocation
from services.allocation_matching import NameMatcher, check_name, normalize

MAX_TASKS_LISTED = 8
LONG_DAY_SECONDS = 8 * 3600


@dataclass
class AllocationRow:
    project_id: int
    project_name: str
    task_id: int
    task_name: str
    seconds: int
    note: str = ""


@dataclass
class NotMember:
    project_id: int
    project_name: str
    posted: List[str] = field(default_factory=list)   # what the person wrote for it, e.g. "Development, 2 h"


@dataclass
class ChatPlan:
    rows: List[AllocationRow] = field(default_factory=list)       # to write to TaskSprint
    skipped: List[str] = field(default_factory=list)              # already in TaskSprint
    problems: List[str] = field(default_factory=list)             # the person has to fix these
    not_member: List["NotMember"] = field(default_factory=list)  # PM must add the person first

    @property
    def total_seconds(self) -> int:
        return sum(r.seconds for r in self.rows)


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:g} h"


def _task_list(tasks: Sequence[Dict]) -> str:
    names = sorted(t["name"] for t in tasks)
    more = f" and {len(names) - MAX_TASKS_LISTED} more" if len(names) > MAX_TASKS_LISTED else ""
    return ", ".join(names[:MAX_TASKS_LISTED]) + more


def pick_task(written: str, project_name: str, tasks: Sequence[Dict]) -> Tuple[Optional[Dict], str, str]:
    """
    Task for one row. tasks: active tasks of the project with an `assigned` flag.
    Returns (task or None, problem, note).
    """
    mine = [t for t in tasks if t["assigned"]]
    if written:
        m = NameMatcher({t["id"]: t["name"] for t in mine}).match(written)
        if m.id is not None:
            note = f"'{written}' read as task {m.name}" if m.method == "fuzzy" else ""
            return next(t for t in mine if t["id"] == m.id), "", note
        other = NameMatcher({t["id"]: t["name"] for t in tasks if not t["assigned"]}).match(written)
        if other.id is not None:
            return None, (f"{project_name}: you are not on task '{other.name}' in TaskSprint. "
                          "Ask your PM to add you, or name one of your tasks."), ""
        if m.method == "ambiguous":
            return None, f"{project_name}: '{written}' could be {', '.join(m.candidates)}. Which one?", ""
    if len(mine) == 1:
        note = (f"'{written}' not found, used your only task {mine[0]['name']}" if written
                else f"no task written, used your only task {mine[0]['name']}")
        return mine[0], "", note
    if not mine:
        return None, f"{project_name}: you are not on any task of this project in TaskSprint. Ask your PM to add you.", ""
    asked = f"task '{written}' not found." if written else "which task?"
    return None, f"{project_name}: {asked} Your tasks: {_task_list(mine)}.", ""


def build_plan(raw_rows: Iterable[RawAllocation], sender_names: Iterable[str], today: date,
               projects: NameMatcher, active_project_ids: Set[int], member_project_ids: Set[int],
               tasks_by_project: Dict[int, List[Dict]], existing: Sequence[Dict]) -> ChatPlan:
    """
    raw_rows: rows extracted from the person's new messages. sender_names: their TaskSprint and
    Teams names. existing: their allocation rows in TaskSprint for today ({projectId, taskId}).
    """
    plan = ChatPlan()
    already = {(e["projectId"], e["taskId"]) for e in existing}
    merged: Dict[Tuple[int, int], AllocationRow] = {}
    me = {normalize(n) for n in sender_names if n}

    for raw in raw_rows:
        label = raw.project_text or raw.project or "plan"
        if normalize(raw.person) not in me and normalize(raw.person_text) not in me:
            plan.problems.append(f"'{raw.evidence}': only your own plan can be saved from this chat.")
            continue
        if not (raw.start_date == raw.end_date == today):
            when = (f"{raw.start_date:%d-%m-%Y}" if raw.start_date == raw.end_date
                    else f"{raw.start_date:%d-%m-%Y} to {raw.end_date:%d-%m-%Y}")
            plan.problems.append(f"{label}: only today's plan is saved for now (you wrote {when}).")
            continue
        if raw.project is None or raw.hours_per_day == 0:
            plan.problems.append(f"{label}: removing or cancelling work is not supported yet; change it in TaskSprint.")
            continue
        if raw.hours_per_day is None:
            plan.problems.append(f"{label}: how many hours? Send it again with the hours.")
            continue

        prj, reason, _ = check_name("Project", raw.project, raw.project_text, projects)
        if prj is None:
            plan.problems.append(reason + ".")
            continue
        if prj.id not in active_project_ids:
            plan.problems.append(f"{prj.name} is not active in TaskSprint, so nothing can be allocated to it.")
            continue
        if prj.id not in member_project_ids:
            entry = next((n for n in plan.not_member if n.project_id == prj.id), None)
            if entry is None:
                entry = NotMember(prj.id, prj.name)
                plan.not_member.append(entry)
            entry.posted.append(", ".join(x for x in (raw.task, _hours(raw.hours_per_day * 3600)) if x))
            continue

        task, problem, note = pick_task(raw.task, prj.name, tasks_by_project.get(prj.id, []))
        if task is None:
            plan.problems.append(problem)
            continue
        if (prj.id, task["id"]) in already:
            line = f"{prj.name} / {task['name']}: already allocated today; change the hours in TaskSprint."
            if line not in plan.skipped:
                plan.skipped.append(line)
            continue
        seconds = int(round(raw.hours_per_day * 3600))
        key = (prj.id, task["id"])
        if key in merged:
            merged[key].seconds += seconds
        else:
            merged[key] = AllocationRow(prj.id, prj.name, task["id"], task["name"], seconds, note)

    plan.rows = sorted(merged.values(), key=lambda r: (r.project_name, r.task_name))
    return plan


YES_WORDS = {"yes", "y", "yes save", "yes please", "ok", "okay", "ok save", "confirm", "confirmed", "correct",
             "save", "save it", "haan", "ha", "han", "ho", "done"}
NO_WORDS = {"no", "n", "cancel", "dont save", "don t save", "do not save", "wrong", "nahi", "nako"}
YES_EMOJI = "\U0001F44D✅✔️ "   # thumbs up, check marks (and the emoji variation selector)


def classify_reply(text: str) -> str:
    """'yes' (save the waiting plan), 'no' (drop it) or 'plan' (anything else, read by the AI)."""
    raw = (text or "").strip()
    if raw and all(ch in YES_EMOJI for ch in raw):
        return "yes"
    key = normalize(raw)
    if key in YES_WORDS:
        return "yes"
    if key in NO_WORDS:
        return "no"
    return "plan"


def rows_to_json(rows: Sequence[AllocationRow]) -> str:
    return json.dumps([r.__dict__ for r in rows])


def rows_from_json(text: str) -> List[AllocationRow]:
    return [AllocationRow(**r) for r in json.loads(text or "[]")]


def greeting_html(name: str) -> str:
    first = (name or "").split()[0] if name else ""
    return f"<p>Hi {html.escape(first)},</p>"


def reply_html(today: date, plan: ChatPlan, pm_notes: Dict[int, str] = None, state: str = "pending") -> str:
    """
    One part of the agent's answer in the 1:1 chat.
    state: "pending" (rows read, waiting for yes), "saved" (rows saved after yes) or "dry" (dry run).
    pm_notes: project id -> what happened with its PM, e.g. "I have asked the PM, Asha K, to add you".
    """
    e = html.escape
    parts = []
    if plan.rows:
        lead = {"saved": "Saved your plan", "dry": "Read your plan (dry run)"}.get(state, "I read your plan")
        parts.append(f"<p>{lead} for {today:%d-%m-%Y} ({_hours(plan.total_seconds)}):</p><ul>"
                     + "".join(f"<li>{e(r.project_name)} / {e(r.task_name)}: {_hours(r.seconds)}"
                               + (f" ({e(r.note)})" if r.note else "") + "</li>" for r in plan.rows) + "</ul>")
        if plan.total_seconds > LONG_DAY_SECONDS:
            parts.append("<p>That is more than 8 h in total.</p>")
        if state != "saved":
            parts.append("<p><b>Reply yes to save it in TaskSprint</b>, or send the whole corrected plan to replace it "
                         "(no to drop it).</p>")
    if plan.skipped:
        parts.append("<p>Already in TaskSprint:</p><ul>" + "".join(f"<li>{e(s)}</li>" for s in plan.skipped) + "</ul>")
    if plan.not_member:
        notes = pm_notes or {}
        parts.append("<p>You are not on these projects in TaskSprint, so they cannot be saved:</p><ul>" + "".join(
            f"<li>{e(n.project_name)}: {e(notes.get(n.project_id) or 'ask the PM to add you')}</li>"
            for n in plan.not_member) + "</ul><p>Once you are added to the project and its task, send that part again.</p>")
    if plan.problems:
        parts.append("<p>Please fix and send again:</p><ul>" + "".join(f"<li>{e(p)}</li>" for p in plan.problems)
                     + "</ul>")
    return "".join(parts)


NOTHING_WAITING_HTML = "<p>There is no plan waiting to be saved. Send today's plan first, e.g. Project - Task - 3 h.</p>"
CANCELLED_HTML = "<p>OK, that plan was not saved. Send the corrected plan when you are ready.</p>"


def has_content(plan: ChatPlan) -> bool:
    """False when the messages held no plan at all (greetings, questions): the agent stays quiet."""
    return bool(plan.rows or plan.skipped or plan.not_member or plan.problems)


def pm_message_html(pm_name: str, person_name: str, day: date, entry: NotMember, intended_for: str = "") -> str:
    """Teams message to a project's PM when someone posted a plan for a project they are not a member of."""
    e = html.escape
    test = f"<b>[TEST, meant for {e(intended_for)}]</b><br>" if intended_for else ""
    first = (pm_name or "").split()[0] if pm_name else ""
    posted = "; ".join(entry.posted)
    return (f"{test}<i>Automated message:</i> Hi {e(first)}, {e(person_name)} posted a work plan for today "
            f"({day:%a %d-%m-%Y}) on <b>{e(entry.project_name)}</b>" + (f" ({e(posted)})" if posted else "") +
            f", but is not a member of this project in TaskSprint, so it was not saved. If {e(person_name)} should "
            "work on it, please add them to the project and the task in TaskSprint; they will then post it again. Thanks!")
