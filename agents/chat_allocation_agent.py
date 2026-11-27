"""
Chat Allocation Agent: reads day plans people post in their 1:1 Teams chat with the agent
account, writes them to TaskSprint as allocations (through the TaskSprint API, as the
agent's TaskSprint account) and answers in the same chat with what was saved or what to fix.

- Only people in CHAT_ALLOCATION_USERS are read (empty = nobody); IGNORE_ID people are skipped.
- Only today's messages; each message is handled once (agent-db chat_allocation_messages).
- TaskSprint is read from MySQL (read-only) and written only through its API.
- A plan for a project the person is not a member of is not saved; the project's PM gets a Teams
  message from the agent (once per person, project and day; TEAMS_DM_TEST_RECIPIENT test mode applies).
- --dry-run: reads and matches, prints what would be saved and the reply; writes and sends nothing.

Run it every few minutes during office hours from cron.
"""

import json
import logging
import os
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
    AllocationRow, ChatPlan, NotMember, build_plan, has_content, pm_message_html, reply_html,
)
from services.project_health_service import CLOSED_STATUSES
from services.tasksprint_api import TaskSprintClient, TaskSprintError
from utils.date_utils import is_holiday, is_weekend

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOCK_KEY = 7310_0001   # Postgres advisory lock: one chat-allocations run at a time
PM_LOG_KIND = "chat_alloc_pm"   # NotificationLog kind for messages to PMs


def office_today() -> date:
    """Today in office time (the VPS clock is UTC). AGENT_TODAY=YYYY-MM-DD overrides it for tests."""
    override = (os.getenv("AGENT_TODAY") or "").strip()
    if override:
        return date.fromisoformat(override)
    return (datetime.now(timezone.utc) + timedelta(minutes=settings.teams_utc_offset_minutes)).date()


def _id_to_email() -> Dict[int, str]:
    """TaskSprint user id -> office email (active_emp.json first, then office_emails.json)."""
    found: Dict[int, str] = {}
    for name in ("active_emp.json", "office_emails.json"):
        try:
            with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
                for uid, email in json.load(f).items():
                    if email:
                        found.setdefault(int(uid), email.strip())
        except (OSError, ValueError):
            pass
    return found


def _email_maps() -> Dict[str, int]:
    """email (lower case) -> TaskSprint user id, from data/active_emp.json and data/office_emails.json."""
    found: Dict[str, int] = {}
    for name in ("active_emp.json", "office_emails.json"):
        try:
            with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
                for uid, email in json.load(f).items():
                    if email:
                        found.setdefault(email.strip().lower(), int(uid))
        except (OSError, ValueError):
            pass
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
                 teams=None, client=None, extractor=None, store=None):
        self.dry_run = dry_run
        self.today = today or office_today()
        self._teams, self._client, self._extractor, self._store = teams, client, extractor, store

    # ------------------------------------------------------------ setup

    def _open_store(self):
        if self._store is not None:
            return self._store
        from database.agent_store import get_agent_session
        return get_agent_session()

    @staticmethod
    def _lock(store) -> bool:
        """Postgres advisory lock so two cron runs never handle the same message twice."""
        try:
            if store.bind.dialect.name != "postgresql":
                return True
            from sqlalchemy import text
            return bool(store.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar())
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
        try:
            if not self.dry_run and not self._lock(store):
                logger.info("Another chat-allocations run is still busy; this one stops.")
                return []
            teams = self._teams or self._make_teams(store)
            results = [self._handle_person(store, teams, email, user) for email, user in people]
            return [r for r in results if r]
        finally:
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
        messages = [m for m in teams.read_chat(chat_id, since)
                    if m.sender_id != teams.sender_id and m.sent_at.date() == self.today]
        handled = self._handled_ids(store, [m.id for m in messages])
        new = [m for m in messages if m.id not in handled]
        if not new:
            logger.info(f"{user.name}: no new messages today.")
            return None

        session = next(get_db_session())
        try:
            projects = [p for p in ProjectRepository(session).get_projects_for_matching()
                        if (p.get("statusName") or "").strip().lower() not in CLOSED_STATUSES]
            member_ids = set(ProjectUserRepository(session).get_member_project_ids(user.id))
            tasks = TaskRepository(session).get_active_tasks_in_projects(sorted(member_ids), user.id)
            existing = ResourceAllocationRepository(session).get_user_day_allocations(user.id, self.today)
        finally:
            session.close()
        names = {p["id"]: p["name"] for p in projects}
        active = {p["id"] for p in projects if p.get("active")}
        project_info = {p["id"]: p for p in projects}
        tasks_by_project: Dict[int, List[Dict]] = defaultdict(list)
        for t in tasks:
            tasks_by_project[t["projectId"]].append(t)

        extractor = self._extractor or OpenAIAllocationExtractor(settings.ai.api_key, settings.ai.extraction_model)
        raw = extractor.extract(new, [user.name], [names[i] for i in active])
        _, project_aliases = load_aliases()
        plan = build_plan(raw, {user.name, *(m.sender for m in new)}, self.today,
                          NameMatcher(names, project_aliases, preferred=active), active, member_ids,
                          tasks_by_project, existing)

        result = {"user": user.name, "messages": len(new), "plan": plan}
        if not has_content(plan):
            logger.info(f"{user.name}: {len(new)} new message(s) without a plan; no reply.")
            self._mark(store, new, user.id, "no_plan", plan, "")
            return result

        if self.dry_run:
            pm_notes = {n.project_id: f"the PM, {project_info[n.project_id].get('pmName') or 'not set'}, would be asked to add you"
                        for n in plan.not_member}
            html_text = reply_html(user.name, self.today, plan, pm_notes, saved=False)
            print(f"\n--- {user.name} ({email}): {len(new)} new message(s) ---")
            for r in plan.rows:
                print(f"  would save  {r.project_name} / {r.task_name}: {r.seconds / 3600:g} h  {r.note}")
            for line in plan.skipped + [f"not on project {n.project_name}" for n in plan.not_member] + plan.problems:
                print(f"  not saved   {line}")
            print(f"  reply: {html_text}")
            result["reply"] = html_text
            return result

        wanted = len(plan.rows)
        plan = write_plan(self._client or self._get_client(), self.today, user.id, plan)
        pm_notes = {n.project_id: self._tell_pm(store, teams, user, n, project_info[n.project_id])
                    for n in plan.not_member}
        html_text = reply_html(user.name, self.today, plan, pm_notes)
        sent = teams.send(email, html_text)
        if not sent.ok:
            logger.error(f"Reply to {email} not sent: {sent.error}")
        status = ("failed" if wanted and not plan.rows else "saved" if plan.rows and not
                  (plan.problems or plan.not_member) else "partly" if plan.rows else "needs_fix")
        self._mark(store, new, user.id, status, plan, html_text)
        logger.info(f"{user.name}: {len(plan.rows)} allocation row(s) saved in TaskSprint ({status}).")
        result.update(reply=html_text, status=status)
        return result

    def _tell_pm(self, store, teams, user, entry: NotMember, project: Dict) -> str:
        """Ask the project's PM to add the person. Returns the line for the person's reply."""
        from database.agent_store import NotificationLog
        pm_id, pm_name = project.get("pmId"), project.get("pmName") or ""
        if not pm_id or not pm_name:
            return "the project has no PM in TaskSprint; ask the operations team to add you"
        if pm_id == user.id:
            return "you are its PM; add yourself to the project and the task in TaskSprint"
        if pm_id in set(settings.get_ignore_id_list()):
            return f"ask the PM, {pm_name}, to add you"
        pm_email = _id_to_email().get(pm_id) or (project.get("pmEmail") or "").strip()
        if not pm_email:
            return f"ask the PM, {pm_name}, to add you (no email found for them)"
        subject = f"{self.today} user {user.id} project {entry.project_id}"
        done = store.query(NotificationLog).filter_by(kind=PM_LOG_KIND, subject=subject, success=True).first()
        if done is not None:
            return f"I have already asked the PM, {pm_name}, to add you today"
        test_to = settings.teams_dm_test_recipient
        to = test_to or pm_email
        outcome = teams.send(to, pm_message_html(pm_name, user.name, self.today, entry,
                                                 intended_for=pm_name if test_to else ""))
        store.add(NotificationLog(kind=PM_LOG_KIND, recipients=to, subject=subject, success=outcome.ok))
        store.commit()
        if not outcome.ok:
            logger.error(f"Message to PM {pm_name} ({to}) not sent: {outcome.error}")
            return f"ask the PM, {pm_name}, to add you (my message to them could not be sent)"
        logger.info(f"Asked PM {pm_name} ({to}) to add {user.name} to {entry.project_name}.")
        return f"I have asked the PM, {pm_name}, to add you"

    def _get_client(self):
        if self._client is None:
            self._client = tasksprint_client()
        return self._client

    def _mark(self, store, messages, user_id: int, status: str, plan: ChatPlan, reply: str):
        from database.agent_store import ChatAllocationMessage
        if self.dry_run:
            return
        written = json.dumps([{"project_id": r.project_id, "task_id": r.task_id, "seconds": r.seconds}
                              for r in plan.rows])
        for m in messages:
            store.add(ChatAllocationMessage(message_id=m.id, user_id=user_id, work_date=self.today,
                                            status=status, written=written, reply=reply))
        store.commit()


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
