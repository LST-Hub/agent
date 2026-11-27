"""
TaskSprint web API, used as the agent's own TaskSprint account (TASKSPRINT_AGENT_EMAIL).

TaskSprint has no API keys: it uses NextAuth sessions. The client logs in like the login
page does (GET /api/auth/csrf, then POST /api/auth/callback/credentials) and keeps the
session cookie for the rest of the run. Every write is done as the agent account, so
TaskSprint records it as createdBy = the agent.

Responses look like {"success": bool, "data": ..., "message": str}. A missing or expired
session comes back as HTTP 500 ("Some Internal Error Occured"), not 401.

Settings (.env): TASKSPRINT_URL (e.g. https://app.tasksprint.app), TASKSPRINT_AGENT_EMAIL,
TASKSPRINT_AGENT_PASSWORD. The password is only sent to TaskSprint's own login endpoint.
"""

import logging
from datetime import date
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SESSION_COOKIES = ("__Secure-next-auth.session-token", "next-auth.session-token")
DONT_REPEAT = "DontRepeat"


class TaskSprintError(Exception):
    """Login failed or TaskSprint refused a request."""


class TaskSprintClient:
    def __init__(self, base_url: str, email: str, password: str, http: Optional[requests.Session] = None,
                 timeout: int = 30):
        if not base_url or not email or not password:
            raise TaskSprintError("TASKSPRINT_URL, TASKSPRINT_AGENT_EMAIL and TASKSPRINT_AGENT_PASSWORD must be set in .env")
        self.base_url = base_url.rstrip("/")
        self.email = email
        self._password = password
        self.http = http or requests.Session()
        self.timeout = timeout
        self.logged_in = False

    # ------------------------------------------------------------ login

    def login(self) -> None:
        r = self.http.get(f"{self.base_url}/api/auth/csrf", timeout=self.timeout)
        try:
            csrf = r.json().get("csrfToken") if r.status_code == 200 else None
        except ValueError:
            csrf = None
        if not csrf:
            raise TaskSprintError(f"TaskSprint login page did not answer ({r.status_code}). Check TASKSPRINT_URL.")
        r = self.http.post(f"{self.base_url}/api/auth/callback/credentials", timeout=self.timeout,
                           allow_redirects=False,
                           data={"csrfToken": csrf, "email": self.email, "password": self._password, "json": "true"})
        if not any(name in self.http.cookies for name in SESSION_COOKIES):
            reason = ""
            try:
                reason = (r.json() or {}).get("url", "")
            except ValueError:
                pass
            raise TaskSprintError(f"TaskSprint login failed for {self.email} ({r.status_code} {reason[-120:]}). "
                                  "Check TASKSPRINT_AGENT_EMAIL / TASKSPRINT_AGENT_PASSWORD; the account needs a "
                                  "password login, not Google sign-in.")
        self.logged_in = True
        logger.info(f"Logged in to TaskSprint as {self.email}.")

    def _ensure_login(self):
        if not self.logged_in:
            self.login()

    def session_info(self) -> Dict:
        """The NextAuth session of the agent account (user name, email, role ...)."""
        self._ensure_login()
        r = self.http.get(f"{self.base_url}/api/auth/session", timeout=self.timeout)
        r.raise_for_status()
        return r.json() or {}

    # ------------------------------------------------------------ API calls

    def _call(self, method: str, path: str, **kw):
        self._ensure_login()
        r = self.http.request(method, f"{self.base_url}/api/v1{path}", timeout=self.timeout, **kw)
        try:
            body = r.json()
        except ValueError:
            raise TaskSprintError(f"{method} {path}: TaskSprint answered {r.status_code} without JSON")
        if r.status_code != 200 or not body.get("success"):
            raise TaskSprintError(f"{method} {path}: {r.status_code} {body.get('message') or ''}".strip())
        return body.get("data")

    def allocations(self, start: date, end: date, user_id: Optional[int] = None) -> List[Dict]:
        """Resource allocations the agent account can see (an admin-like role sees everyone's)."""
        params = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        if user_id is not None:
            params["allocatedUserId"] = user_id
        return self._call("GET", "/resource-allocation", params=params) or []

    def create_allocation(self, project_id: int, day: date, tasks: List[Dict]) -> None:
        """
        One-day allocation ("Don't Repeat") on one project. tasks: [{"employeeId", "taskId", "seconds"}].
        TaskSprint emails each employee about the allocation. Never retried automatically: a failure
        after TaskSprint saved the rows would otherwise create them twice.
        """
        if not tasks:
            return
        body = {
            "projectId": int(project_id),
            "date": day.isoformat(),
            "repetationType": DONT_REPEAT,
            "tasksData": [{"employeeId": int(t["employeeId"]), "taskId": int(t["taskId"]),
                           "duration": int(t["seconds"])} for t in tasks],
        }
        self._call("POST", "/resource-allocation", json=body)
