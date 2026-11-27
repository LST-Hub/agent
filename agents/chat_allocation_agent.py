"""
Chat Allocation Agent: reads day plans people post in their 1:1 Teams chat with the agent
account and, once the person confirms, writes them to TaskSprint as allocations (through
the TaskSprint API, as the agent's TaskSprint account).

Conversation: the person posts a plan -> the agent answers "I read: Project / Task / hours, reply
yes to save" (and what to fix) -> "yes" saves the waiting plan, "no" drops it, a new plan replaces it.
Every other message gets an answer too (help, greetings, "add this task to me").

- Only people in CHAT_ALLOCATION_USERS are read (empty = nobody); IGNORE_ID people are skipped.
- Only today's messages; each message is handled once (agent-db chat_allocation_messages).
- TaskSprint is read from MySQL (read-only) and written only through its API.
- When the person is not on the project or the task, or the task does not exist, the project's PM
  gets a Teams message from the agent (once per person, request and day; TEAMS_DM_TEST_RECIPIENT test
  mode applies). PMs on IGNORE_ID get it only when they are in CHAT_PM_IDS.
- --dry-run: reads and matches, prints the reply; saves, writes and sends nothing.
- --watch: keeps running and checks the chats every CHAT_POLL_SECONDS (default 20) in office hours.
"""

import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from config.settings import settings
from database.connection import get_db_session
from database.repositories.allocation_repo import ResourceAllocationRepository
from database.repositories.project_repo import ProjectRepository
from database.repositories.projectuser_repo import ProjectUserRepository
from database.repositories.task_repo import TaskRepository
from database.repositories.user_repo import UserRepository
from services.allocation_extraction import OpenAIAllocationExtractor
from services.allocation_matching import NameMatcher, load_aliases
from services.chat_allocations import (
    ADD_TO_PROJECT, CANCELLED_HTML, GREETING_HTML, HELP_HTML, NOTHING_WAITING_HTML, THANKS_HTML, AllocationRow,
    ChatPlan, PmRequest, build_plan, classify_reply, greeting_html, has_content, merge_rows, pick_task,
    pm_message_html, reply_html, requested_html, rows_from_json, rows_to_json,
)
from services.chat_intent import Intent

NOT_READ_HTML = "<p>I could not read a plan from that. Send it like: Project - Task - 3 h (one line per task).</p>"
from services.project_health_service import CLOSED_STATUSES
from services.tasksprint_api import TaskSprintClient, TaskSprintError
from utils.date_utils import is_holiday, is_weekend

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOCK_KEY = 7310_0001          # Postgres advisory lock: one chat-allocations run at a time
OFFICE_START = (8, 30)        # --watch checks chats between these office times (Mon-Fri)
OFFICE_END = (20, 30)
LOGIN_REFRESH_SECONDS = 30 * 60   # --watch renews the Teams and TaskSprint logins this often


def office_now() -> datetime:
    """Office time now (the VPS clock is UTC)."""
    return (datetime.now(timezone.utc) + timedelta(minutes=settings.teams_utc_offset_minutes)).replace(tzinfo=None)


def office_today() -> date:
    """Today in office time. AGENT_TODAY=YYYY-MM-DD overrides it for tests."""
    override = (os.getenv("AGENT_TODAY") or "").strip()
    if override:
        return date.fromisoformat(override)
    return office_now().date()


def _load_email_files() -> List[Dict]:
    found = []
    for name in ("active_emp.json", "office_emails.json"):
        try:
            with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
                found.append({int(uid): email.strip() for uid, email in json.load(f).items() if email})
        except (OSError, ValueError):
            pass
    return found


def _id_to_email() -> Dict[int, str]:
    """TaskSprint user id -> office email (active_emp.json first, then office_emails.json)."""
    found: Dict[int, str] = {}
    for mapping in _load_email_files():
        for uid, email in mapping.items():
            found.setdefault(uid, email)
    return found


def _email_maps() -> Dict[str, int]:
    """email (lower case) -> TaskSprint user id."""
    found: Dict[str, int] = {}
    for mapping in _load_email_files():
        for uid, email in mapping.items():
            found.setdefault(email.lower(), uid)
    return found


def tasksprint_client() -> TaskSprintClient:
    return TaskSprintClient(settings.tasksprint_url, settings.tasksprint_agent_email, settings.tasksprint_agent_password)


def write_plan(client, today: date, user_id: int, plan: ChatPlan) -> ChatPlan:
    """Write plan.rows to TaskSprint, one call per project. Returns the plan with only the saved rows;
    rows TaskSprint refused become problems."""
    by_project: Dict[int, List[AllocationRow]] = defaultdict(list)
    for r in plan.rows:
        by_project[r.project_id].append(r)
    saved: List[AllocationRow] = []
    for project_id, rows in by_project.items():
        try:
            client.create_allocation(project_id, today, [{"employeeId": user_id, "taskId": r.task_id, "seconds": r.seconds}
                                                         for r in rows])
            saved.extend(rows)
        except (TaskSprintError, OSError) as exc:
            logger.error(f"TaskSprint refused the allocation for user {user_id}, project {project_id}: {exc}")
            plan.problems.extend(f"{r.project_name} / {r.task_name}: TaskSprint did not save it ({exc}). "
                                 "Add it in TaskSprint or send it again later." for r in rows)
    plan.rows = saved
    return plan


class ChatAllocationAgent:
    def __init__(self, dry_run: bool = False, today: Optional[date] = None,
                 teams=None, client=None, extractor=None, store=None, intents=None):
        self.dry_run = dry_run
        self.today = today or office_today()
        self._teams, self._client, self._extractor, self._store = teams, client, extractor, store
        self._intents = intents

    # ------------------------------------------------------------ setup

    def _open_store(self):
        if self._store is not None:
            return self._store
        from database.agent_store import get_agent_session
        return get_agent_session()

    @staticmethod
    def _lock(store, take: bool = True) -> bool:
        """Postgres advisory lock so two runs never handle the same message twice (take=False releases it)."""
        try:
            if store.bind.dialect.name != "postgresql":
                return True
            from sqlalchemy import text
            sql = "SELECT pg_try_advisory_lock(:k)" if take else "SELECT pg_advisory_unlock(:k)"
            return bool(store.execute(text(sql), {"k": LOCK_KEY}).scalar())
        except AttributeError:
            return True   # test store

    def _load_people(self, emails: List[str]):
        """[(email, user)] for the listed emails that are active TaskSprint people."""
        session = next(get_db_session())
        try:
            users = {u.id: u for u in UserRepository(session).get_active_users()}
        finally:
            session.close()
        by_email = _email_maps()
        for u in users.values():
            if u.email:
                by_email.setdefault(u.email.strip().lower(), u.id)
        ignored = set(settings.get_ignore_id_list())
        people = []
        for email in emails:
            uid = by_email.get(email)
            if uid is None or uid not in users:
                logger.warning(f"CHAT_ALLOCATION_USERS: {email} is not an active TaskSprint person; skipped.")
            elif uid in ignored:
                logger.warning(f"CHAT_ALLOCATION_USERS: {email} (user {uid}) is on IGNORE_ID; skipped.")
            else:
                people.append((email, users[uid]))
        return people

    # ------------------------------------------------------------ run

    def run(self) -> List[Dict]:
        if is_weekend(self.today) or is_holiday(self.today):
            logger.info(f"{self.today}: weekend or company holiday, chat plans not read.")
            return []
        emails = settings.get_chat_allocation_users()
        if not emails:
            logger.info("CHAT_ALLOCATION_USERS is empty: nobody's chat is read.")
            return []
        people = self._load_people(emails)
        if not people:
            return []

        store = self._open_store()
        locked = False
        try:
            if not self.dry_run:
                locked = self._lock(store)
                if not locked:
                    logger.info("Another chat-allocations run is still busy; this one stops.")
                    return []
            teams = self._teams or self._make_teams(store)
            results = []
            for email, user in people:
                try:
                    results.append(self._handle_person(store, teams, email, user))
                except Exception:    # one person's problem must not stop the others
                    logger.exception(f"chat-allocations failed for {email}")
                    if hasattr(store, "rollback"):
                        store.rollback()
            return [r for r in results if r]
        finally:
            if locked:
                self._lock(store, take=False)
            if self._store is None:
                store.close()

    @staticmethod
    def _make_teams(store):
        from services.teams_sender import CHAT_READ_SCOPES, TeamsSender
        return TeamsSender(store, scopes=CHAT_READ_SCOPES)

    def _handled_ids(self, store, message_ids: List[str]) -> set:
        from database.agent_store import ChatAllocationMessage
        if not message_ids:
            return set()
        rows = store.query(ChatAllocationMessage.message_id).filter(
            ChatAllocationMessage.message_id.in_(message_ids)).all()
        return {r[0] for r in rows}

    def _handle_person(self, store, teams, email: str, user) -> Optional[Dict]:
        chat_id = teams.open_chat(email)
        since = datetime.combine(self.today, datetime.min.time())
        today = [m for m in teams.read_chat(chat_id, since) if m.sent_at.date() == self.today]   # both sides
        mine = [m for m in today if m.sender_id != teams.sender_id]
        handled = self._handled_ids(store, [m.id for m in mine])
        new = [m for m in mine if m.id not in handled]
        if not new:
            return None

        ctx = self._context(user)

        def before(m):
            """Earlier messages of today, the agent's own included (context for the AI)."""
            return today[:today.index(m)]

        # In the order sent: plan messages are collected and read together; anything else first acts on them
        parts, buffer = [], []

        def flush():
            if buffer:
                parts.append(self._read_plan(store, teams, user, list(buffer), ctx, before(buffer[0])))
                buffer.clear()

        for m in new:
            kind = classify_reply(m.text)
            if kind in ("yes", "no"):
                flush()
                parts.append(self._answer(store, user, m, kind))
                continue
            intent = self._classifier().classify(m.text, before(m))
            if intent.intent == "plan":
                buffer.append(m)
                continue
            flush()
            parts.append(self._other_message(store, teams, user, m, ctx, intent))
        flush()

        result = {"user": user.name, "messages": len(new), "reply": ""}
        body = "".join(p for p in parts if p)
        if not body:
            return result
        html_text = greeting_html(user.name) + body
        result["reply"] = html_text
        if self.dry_run:
            print(f"\n--- {user.name} ({email}): {len(new)} new message(s) ---\n  reply: {html_text}")
            return result
        sent = teams.send(email, html_text)
        if not sent.ok:
            logger.error(f"Reply to {email} not sent: {sent.error}")
        return result

    def _context(self, user) -> Dict:
        """TaskSprint data for matching one person's plan (read-only)."""
        session = next(get_db_session())
        try:
            projects = [p for p in ProjectRepository(session).get_projects_for_matching()
                        if (p.get("statusName") or "").strip().lower() not in CLOSED_STATUSES]
            member_ids = set(ProjectUserRepository(session).get_member_project_ids(user.id))
            tasks = TaskRepository(session).get_active_tasks_in_projects(sorted(member_ids), user.id)
            existing = ResourceAllocationRepository(session).get_user_day_allocations(user.id, self.today)
        finally:
            session.close()
        tasks_by_project: Dict[int, List[Dict]] = defaultdict(list)
        for t in tasks:
            tasks_by_project[t["projectId"]].append(t)
        names = {p["id"]: p["name"] for p in projects}
        active = {p["id"] for p in projects if p.get("active")}
        _, project_aliases = load_aliases()
        return {"names": names, "active": active, "projects": {p["id"]: p for p in projects},
                "member_ids": member_ids, "tasks": tasks_by_project, "existing": existing,
                "matcher": NameMatcher(names, project_aliases, preferred=active)}

    def _existing_now(self, user_id: int) -> List[Dict]:
        session = next(get_db_session())
        try:
            return ResourceAllocationRepository(session).get_user_day_allocations(user_id, self.today)
        finally:
            session.close()

    def _pending(self, store, user_id: int):
        from database.agent_store import ChatPendingPlan
        return (store.query(ChatPendingPlan)
                .filter_by(user_id=user_id, work_date=self.today, status="pending")
                .order_by(ChatPendingPlan.id.desc()).first())

    def _ask_pms(self, store, teams, user, requests: List[PmRequest], ctx) -> Dict[str, str]:
        """Send (or, in a dry run, describe) the PM requests. Returns request key -> line for the person."""
        if self.dry_run:
            return {r.key: f"(dry run) the PM, {ctx['projects'][r.project_id].get('pmName') or 'not set'}, would be "
                           f"asked to {r.describe().replace(' you', ' the person', 1)}." for r in requests}
        return {r.key: self._tell_pm(store, teams, user, r, ctx["projects"][r.project_id]) for r in requests}

    def _classifier(self):
        if self._intents is None:
            from services.chat_intent import OpenAIIntentClassifier
            self._intents = OpenAIIntentClassifier(settings.ai.api_key, settings.ai.extraction_model)
        return self._intents

    def _read_plan(self, store, teams, user, messages, ctx, context=()) -> str:
        """Plan messages -> waiting plan + the reply part asking for yes (and what to fix).
        context: earlier messages of today, e.g. the agent's "how many hours?" that "2hr" answers.
        A follow-up adds to the plan still waiting; it replaces it only when the message says so."""
        from database.agent_store import ChatPendingPlan, _utcnow
        extractor = self._extractor or OpenAIAllocationExtractor(settings.ai.api_key, settings.ai.extraction_model)
        raw = extractor.extract(messages, [user.name], [ctx["names"][i] for i in ctx["active"]], context=context)
        plan = build_plan(raw, {user.name, *(m.sender for m in messages)}, self.today, ctx["matcher"], ctx["active"],
                          ctx["member_ids"], ctx["tasks"], ctx["existing"])
        if not has_content(plan):
            self._mark(store, messages, user.id, "no_plan", NOT_READ_HTML)
            return NOT_READ_HTML
        pm_notes = self._ask_pms(store, teams, user, plan.requests, ctx)
        earlier = self._pending(store, user.id)
        if plan.rows and earlier is not None and not plan.replaces:
            plan.rows = merge_rows(rows_from_json(earlier.rows), plan.rows)
        if self.dry_run:
            return reply_html(self.today, plan, pm_notes, state="dry")
        if plan.rows:
            if earlier is not None:          # the new (or merged) plan takes the place of the waiting one
                earlier.status, earlier.decided_at = "replaced", _utcnow()
            store.add(ChatPendingPlan(user_id=user.id, work_date=self.today, rows=rows_to_json(plan.rows),
                                      status="pending"))
        part = reply_html(self.today, plan, pm_notes, state="pending")
        self._mark(store, messages, user.id, "pending" if plan.rows else "needs_fix", part)
        logger.info(f"{user.name}: plan read ({len(plan.rows)} row(s) waiting for yes, {len(plan.requests)} PM request(s)).")
        return part

    def _other_message(self, store, teams, user, message, ctx, intent: Intent) -> str:
        """A message that is not a plan: answer requests ("add this task to me"), questions and small talk."""
        if intent.intent == "request":
            part = self._request(store, teams, user, intent, ctx)
        else:
            part = {"greeting": GREETING_HTML, "thanks": THANKS_HTML}.get(intent.intent, HELP_HTML)
        self._mark(store, [message], user.id, intent.intent, part)
        return part

    def _request(self, store, teams, user, intent, ctx) -> str:
        """'add me to X - Y': ask the PM. Without a project: say what the PMs were already asked today."""
        if not intent.project:
            return requested_html(self._asked_today(store, user.id))
        prj = ctx["matcher"].match(intent.project)
        if prj.id is None or prj.id not in ctx["active"]:
            return f"<p>I could not find an active project '{intent.project}' in TaskSprint. Which project do you mean?</p>"
        if prj.id not in ctx["member_ids"]:
            request = PmRequest(ADD_TO_PROJECT, prj.id, prj.name, posted=[intent.task] if intent.task else [])
        else:
            task, problem, _, request = pick_task(intent.task or "", prj.id, prj.name, ctx["tasks"].get(prj.id, []),
                                                  strict=True)
            if request is None:
                if task is not None:
                    return (f"<p>You are already on task {task['name']} in {prj.name}. Post your plan like: "
                            f"{prj.name} - {task['name']} - 3 h</p>")
                return f"<p>{problem} Which task should the PM add you to?</p>"
        note = self._ask_pms(store, teams, user, [request], ctx)[request.key]
        return f"<p>{request.problem()} {note}</p>"

    def _asked_today(self, store, user_id: int) -> List[tuple]:
        from database.agent_store import ChatPmRequest
        rows = store.query(ChatPmRequest).filter_by(user_id=user_id, work_date=self.today, success=True).all()
        return [(r.pm_name, r.what) for r in rows]

    def _answer(self, store, user, message, kind: str) -> str:
        """A "yes" saves the waiting plan in TaskSprint; a "no" drops it."""
        from database.agent_store import _utcnow
        pending = self._pending(store, user.id)
        if pending is None:
            self._mark(store, [message], user.id, "nothing", NOTHING_WAITING_HTML)
            return NOTHING_WAITING_HTML
        if kind == "no":
            if not self.dry_run:
                pending.status, pending.decided_at = "cancelled", _utcnow()
            self._mark(store, [message], user.id, "cancelled", CANCELLED_HTML)
            return CANCELLED_HTML

        rows = rows_from_json(pending.rows)
        already = {(e["projectId"], e["taskId"]) for e in self._existing_now(user.id)}
        plan = ChatPlan(rows=[r for r in rows if (r.project_id, r.task_id) not in already],
                        skipped=[f"{r.project_name} / {r.task_name}: already allocated today."
                                 for r in rows if (r.project_id, r.task_id) in already])
        if self.dry_run:
            return "<p>(dry run) yes would save:</p>" + reply_html(self.today, plan, state="saved")
        wanted = len(plan.rows)
        plan = write_plan(self._client or self._get_client(), self.today, user.id, plan)
        status = "failed" if wanted and not plan.rows else "partly" if plan.problems else "confirmed"
        pending.status, pending.decided_at = status, _utcnow()
        part = reply_html(self.today, plan, state="saved")
        self._mark(store, [message], user.id, status, part)
        logger.info(f"{user.name}: confirmed, {len(plan.rows)} allocation row(s) saved in TaskSprint ({status}).")
        return part

    def _tell_pm(self, store, teams, user, request: PmRequest, project: Dict) -> str:
        """Ask the project's PM to fix TaskSprint for the person. Returns the line for the person's reply."""
        from database.agent_store import ChatPmRequest
        pm_id, pm_name = project.get("pmId"), project.get("pmName") or ""
        if not pm_id or not pm_name:
            return "The project has no PM in TaskSprint; ask the operations team."
        if pm_id == user.id:
            return "You are its PM; change it in TaskSprint yourself."
        if pm_id in set(settings.get_ignore_id_list()) and pm_id not in set(settings.get_chat_pm_ids()):
            return f"Ask the PM, {pm_name}."     # IGNORE_ID people get no Teams messages
        pm_email = _id_to_email().get(pm_id) or (project.get("pmEmail") or "").strip()
        if not pm_email:
            return f"Ask the PM, {pm_name} (no email found for them)."
        done = store.query(ChatPmRequest).filter_by(user_id=user.id, work_date=self.today,
                                                   request_key=request.key, success=True).first()
        if done is not None:
            return f"I have already asked the PM, {pm_name}, today."
        test_to = settings.teams_dm_test_recipient
        to = test_to or pm_email
        outcome = teams.send(to, pm_message_html(pm_name, user.name, self.today, request,
                                                 intended_for=pm_name if test_to else ""))
        store.add(ChatPmRequest(user_id=user.id, work_date=self.today, request_key=request.key,
                                project_id=request.project_id, what=request.describe(), pm_id=pm_id,
                                pm_name=pm_name, recipients=to, success=outcome.ok))
        store.commit()
        if not outcome.ok:
            logger.error(f"Message to PM {pm_name} ({to}) not sent: {outcome.error}")
            return f"Ask the PM, {pm_name} (my message to them could not be sent)."
        logger.info(f"Asked PM {pm_name} ({to}) to {request.describe()} ({user.name}).")
        return f"I have asked the PM, {pm_name}, to {request.describe()}."

    def _get_client(self):
        if self._client is None:
            self._client = tasksprint_client()
        return self._client

    def _mark(self, store, messages, user_id: int, status: str, reply: str):
        """Remember that these messages are handled (never in a dry run)."""
        from database.agent_store import ChatAllocationMessage
        if self.dry_run:
            return
        for m in messages:
            store.add(ChatAllocationMessage(message_id=m.id, user_id=user_id, work_date=self.today,
                                            status=status, reply=reply))
        store.commit()


def in_office_hours(now: datetime) -> bool:
    return now.weekday() < 5 and OFFICE_START <= (now.hour, now.minute) < OFFICE_END


def watch(interval: int = None, sleep=time.sleep, now=office_now, rounds: Optional[int] = None) -> None:
    """Keep checking the chats every `interval` seconds in office hours (the chat service in docker).
    The Teams and TaskSprint logins are renewed every LOGIN_REFRESH_SECONDS."""
    interval = interval or int(os.getenv("CHAT_POLL_SECONDS") or 20)
    logger.info(f"Watching the agent's chats every {interval} s, {OFFICE_START[0]:02d}:{OFFICE_START[1]:02d}-"
                f"{OFFICE_END[0]:02d}:{OFFICE_END[1]:02d} office time, Mon-Fri.")
    teams = client = None
    logins_at = 0.0
    done = 0
    while rounds is None or done < rounds:
        done += 1
        if not in_office_hours(now()):
            teams = client = None
            sleep(300)
            continue
        try:
            if teams is None or time.monotonic() - logins_at > LOGIN_REFRESH_SECONDS:
                from database.agent_store import get_agent_session
                login_store = get_agent_session()
                try:
                    teams = ChatAllocationAgent._make_teams(login_store)
                finally:
                    login_store.close()
                client, logins_at = None, time.monotonic()
            agent = ChatAllocationAgent(teams=teams, client=client)
            agent.run()
            client = agent._client          # keep the TaskSprint login between rounds
        except Exception:
            logger.exception("chat-allocations round failed; trying again next round")
            teams = client = None
        sleep(interval)


def tasksprint_check(user_id: Optional[int] = None) -> None:
    """Log in to TaskSprint as the agent account and show what it can see. Writes nothing."""
    client = tasksprint_client()
    client.login()
    session = client.session_info()
    user = session.get("user") or {}
    print(f"Logged in to {settings.tasksprint_url} as {user.get('name') or '?'} ({user.get('email') or client.email}).")
    extra = {k: v for k, v in user.items() if k not in ("name", "email", "image")}
    if extra:
        print("Session details: " + ", ".join(f"{k}={v}" for k, v in sorted(extra.items())))
    today = office_today()
    rows = client.allocations(today, today, user_id)
    who = f"user {user_id}" if user_id is not None else "everyone it can see"
    print(f"Allocations on {today} for {who}: {len(rows)} row(s).")
    for r in rows[:20]:
        project = (r.get("project") or {}).get("name") or r.get("projectId")
        task = (r.get("task") or {}).get("name") or r.get("taskId")
        person = (r.get("allocatedUser") or {}).get("name") or r.get("allocatedUserId")
        print(f"  {person}: {project} / {task}  {int(r.get('duration') or 0) / 3600:g} h")
