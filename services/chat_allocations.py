"""
Day plans posted in a person's 1:1 chat with the agent, turned into TaskSprint allocations.

Plain, testable rules (the AI only turns the message into rows; see allocation_extraction):
- Only the sender's own plan for today is saved. Other people, other dates, cancellations
  and rows without hours are answered with what to fix, never guessed.
- The project must be active and the person a member of it (ProjectUser); otherwise the
  agent asks the project's PM to add them (a PmRequest) and the person sends it again once added.
- TaskSprint needs a task for every allocation. The written task is matched to the project's
  active tasks the person is on (TaskUser): by name, then by "the text starts with a task name"
  ("Data Validation Testing (AP and AR ...)" -> Data Validation). With no task written, a person
  on exactly one task gets that task. A task they are not on, a task that does not exist, or no
  task at all on the project becomes a request to the PM; with several tasks and none written,
  the person is asked which one.
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
MIN_PREFIX_CHARS = 4      # "the text starts with a task name" needs a task name at least this long

# PmRequest kinds
ADD_TO_PROJECT = "project"   # person is not a member of the project
ASSIGN_TASK = "assign"       # the task exists, the person is not on it
CREATE_TASK = "create"       # no task with that name in the project
ANY_TASK = "any"             # the person is on no task of the project


@dataclass
class AllocationRow:
    project_id: int
    project_name: str
    task_id: int
    task_name: str
    seconds: int
    note: str = ""


@dataclass
class PmRequest:
    """Something only the project's PM can fix in TaskSprint before the plan can be saved."""
    kind: str                     # ADD_TO_PROJECT, ASSIGN_TASK, CREATE_TASK, ANY_TASK
    project_id: int
    project_name: str
    task: str = ""                # existing task name (ASSIGN_TASK) or the written text (CREATE_TASK)
    posted: List[str] = field(default_factory=list)   # what the person wrote, e.g. "Development, 2 h"
    my_tasks: List[str] = field(default_factory=list)  # the person's tasks on the project

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.project_id}:{normalize(self.task)}"[:200]

    def describe(self) -> str:
        """What the PM is asked to do, in the person's words: 'add you to project X'."""
        return {
            ADD_TO_PROJECT: f"add you to project {self.project_name}",
            ASSIGN_TASK: f"put you on task '{self.task}' in {self.project_name}",
            CREATE_TASK: f"add task '{self.task}' in {self.project_name} for you",
            ANY_TASK: f"put you on a task in {self.project_name}",
        }[self.kind]

    def problem(self) -> str:
        """Why the row could not be saved, for the person."""
        mine = f" Your tasks: {_task_list(self.my_tasks)}." if self.my_tasks else ""
        return {
            ADD_TO_PROJECT: f"{self.project_name}: you are not a member of this project in TaskSprint.",
            ASSIGN_TASK: f"{self.project_name}: you are not on task '{self.task}' in TaskSprint.{mine}",
            CREATE_TASK: f"{self.project_name}: there is no task '{self.task}'.{mine}",
            ANY_TASK: f"{self.project_name}: you are not on any task of this project in TaskSprint.",
        }[self.kind]


@dataclass
class ChatPlan:
    rows: List[AllocationRow] = field(default_factory=list)       # to write to TaskSprint
    skipped: List[str] = field(default_factory=list)              # already in TaskSprint
    problems: List[str] = field(default_factory=list)             # the person has to fix these
    requests: List[PmRequest] = field(default_factory=list)       # the PM has to fix these

    @property
    def total_seconds(self) -> int:
        return sum(r.seconds for r in self.rows)


def _esc(text: str) -> str:
    """HTML-escape chat text (it is never put inside an attribute, so quotes stay readable)."""
    return html.escape(text or "", quote=False)


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:g} h"


def _task_list(names: Sequence[str]) -> str:
    names = sorted(names)
    more = f" and {len(names) - MAX_TASKS_LISTED} more" if len(names) > MAX_TASKS_LISTED else ""
    return ", ".join(names[:MAX_TASKS_LISTED]) + more


def _starts_with_task(written: str, tasks: Sequence[Dict]) -> Optional[Dict]:
    """The task whose full name begins the written text ('Data Validation Testing (...)' -> 'Data Validation').
    The longest such name wins; None when there is none or two of the same length."""
    text = normalize(written)
    hits = [t for t in tasks if len(normalize(t["name"])) >= MIN_PREFIX_CHARS
            and (text == normalize(t["name"]) or text.startswith(normalize(t["name"]) + " "))]
    if not hits:
        return None
    hits.sort(key=lambda t: len(normalize(t["name"])), reverse=True)
    if len(hits) > 1 and len(normalize(hits[0]["name"])) == len(normalize(hits[1]["name"])):
        return None
    return hits[0]


def _find(written: str, tasks: Sequence[Dict]) -> Tuple[Optional[Dict], str, List[str]]:
    """(task, note, ambiguous candidates) for written text among tasks."""
    m = NameMatcher({t["id"]: t["name"] for t in tasks}).match(written)
    if m.id is not None:
        note = f"'{written}' read as task {m.name}" if m.method == "fuzzy" else ""
        return next(t for t in tasks if t["id"] == m.id), note, []
    hit = _starts_with_task(written, tasks)
    if hit is not None:
        return hit, f"'{written}' read as task {hit['name']}", []
    return None, "", m.candidates if m.method == "ambiguous" else []


def pick_task(written: str, project_id: int, project_name: str,
              tasks: Sequence[Dict]) -> Tuple[Optional[Dict], str, str, Optional[PmRequest]]:
    """
    Task for one row. tasks: active tasks of the project with an `assigned` flag.
    Returns (task or None, problem for the person, note, request for the PM).
    """
    mine = [t for t in tasks if t["assigned"]]
    my_names = [t["name"] for t in mine]
    if written:
        task, note, candidates = _find(written, mine)
        if task is not None:
            return task, "", note, None
        if candidates:
            return None, f"{project_name}: '{written}' could be {', '.join(candidates)}. Which one?", "", None
        other, _, _ = _find(written, [t for t in tasks if not t["assigned"]])
        if other is not None:
            return None, "", "", PmRequest(ASSIGN_TASK, project_id, project_name, other["name"], my_tasks=my_names)
        if mine:
            return None, "", "", PmRequest(CREATE_TASK, project_id, project_name, written, my_tasks=my_names)
        return None, "", "", PmRequest(ANY_TASK, project_id, project_name)
    if len(mine) == 1:
        return mine[0], "", f"no task written, used your only task {mine[0]['name']}", None
    if not mine:
        return None, "", "", PmRequest(ANY_TASK, project_id, project_name)
    return None, f"{project_name}: which task? Your tasks: {_task_list(my_names)}.", "", None


def _add_request(plan: ChatPlan, request: PmRequest, posted: str) -> None:
    same = next((r for r in plan.requests if r.key == request.key), None)
    if same is None:
        plan.requests.append(request)
        same = request
    if posted:
        same.posted.append(posted)


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
        posted = ", ".join(x for x in (raw.task, _hours(raw.hours_per_day * 3600)) if x)
        if prj.id not in member_project_ids:
            _add_request(plan, PmRequest(ADD_TO_PROJECT, prj.id, prj.name), posted)
            continue

        task, problem, note, request = pick_task(raw.task, prj.id, prj.name, tasks_by_project.get(prj.id, []))
        if request is not None:
            _add_request(plan, request, posted)
            continue
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
    return f"<p>Hi {_esc(first)},</p>"


def reply_html(today: date, plan: ChatPlan, pm_notes: Dict[str, str] = None, state: str = "pending") -> str:
    """
    One part of the agent's answer in the 1:1 chat.
    state: "pending" (rows read, waiting for yes), "saved" (rows saved after yes) or "dry" (dry run).
    pm_notes: request key -> what happened with the PM, e.g. "I have asked the PM, Asha K, to add you".
    """
    e = _esc
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
    if plan.requests:
        notes = pm_notes or {}
        parts.append("<p>Not saved yet, the PM has to change TaskSprint first:</p><ul>" + "".join(
            f"<li>{e(r.problem())} {e(notes.get(r.key) or 'Ask the PM to fix it.')}</li>" for r in plan.requests)
            + "</ul><p>Once that is done, send that part again (or use one of your tasks now).</p>")
    if plan.problems:
        parts.append("<p>Please fix and send again:</p><ul>" + "".join(f"<li>{e(p)}</li>" for p in plan.problems)
                     + "</ul>")
    return "".join(parts)


NOTHING_WAITING_HTML = "<p>There is no plan waiting to be saved. Send today's plan first, e.g. Project - Task - 3 h.</p>"
CANCELLED_HTML = "<p>OK, that plan was not saved. Send the corrected plan when you are ready.</p>"
HELP_HTML = ("<p>I save your work plan for today in TaskSprint. Post it like:<br>Project - Task - 3 h<br>"
             "(one line per task). I answer with what I read: reply <b>yes</b> to save it, <b>no</b> to drop it, "
             "or send the corrected plan. If you are not on a project or task, I ask your PM to add you.</p>")
GREETING_HTML = "<p>Hello! Post today's plan whenever you are ready, e.g. Project - Task - 3 h.</p>"
THANKS_HTML = "<p>You're welcome!</p>"


def has_content(plan: ChatPlan) -> bool:
    """False when the messages held no plan at all (greetings, questions)."""
    return bool(plan.rows or plan.skipped or plan.requests or plan.problems)


def pm_message_html(pm_name: str, person_name: str, day: date, request: PmRequest, intended_for: str = "") -> str:
    """Teams message asking a project's PM to change TaskSprint so the person's plan can be saved."""
    e = _esc
    test = f"<b>[TEST, meant for {e(intended_for)}]</b><br>" if intended_for else ""
    first = (pm_name or "").split()[0] if pm_name else ""
    posted = "; ".join(request.posted)
    plan = (f"{e(person_name)} posted a work plan for today ({day:%a %d-%m-%Y}) on <b>{e(request.project_name)}</b>"
            + (f" ({e(posted)})" if posted else ""))
    ask = {
        ADD_TO_PROJECT: f"but is not a member of this project in TaskSprint. If {e(person_name)} should work on it, "
                        "please add them to the project and the task",
        ASSIGN_TASK: f"but is not on the task <b>{e(request.task)}</b>. If that is right, please add them to that task",
        CREATE_TASK: f"but the project has no task <b>{e(request.task)}</b>. Please create it and add them to it, "
                     "or tell them which existing task to use",
        ANY_TASK: "but is not on any task of this project. Please add them to the right task",
    }[request.kind]
    return (f"{test}<i>Automated message:</i> Hi {e(first)}, {plan}, {ask} in TaskSprint; "
            "they will then post it again. Thanks!")


def requested_html(asked: Sequence[Tuple[str, str]]) -> str:
    """Answer to "add this task to me": what the PMs were already asked today. asked: [(pm name, describe())]."""
    e = _esc
    if not asked:
        return ("<p>Tell me the project and the task, e.g. <i>add me to Archival - Data Validation Testing</i>, "
                "and I will ask the PM. Or post your plan; if you are not on a project or task, I ask the PM myself.</p>")
    return ("<p>I have already asked today:</p><ul>" + "".join(f"<li>{e(pm)}: {e(what)}</li>" for pm, what in asked)
            + "</ul><p>Once it is done in TaskSprint, send that part of your plan again.</p>")
