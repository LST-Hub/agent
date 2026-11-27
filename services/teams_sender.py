"""
Sends Teams 1:1 messages from one signed-in person's account (for example the PM
who already chases timesheets), using delegated Microsoft Graph permissions.

The same login also reads the agent's 1:1 chats, where people post their day plans
(chat-allocations). Reading needs Chat.Read; sending keeps working without it.

Setup (once, in the same Azure app as TENANT_ID / CLIENT_ID):
  - API permissions, delegated: Chat.Create, ChatMessage.Send, Chat.Read, User.Read, offline_access
  - Authentication: "Allow public client flows" = Yes
  - TEAMS_TOKEN_KEY in .env: a Fernet key that encrypts the stored login

Sign-in is the device code flow: `teams-signin` prints a code, the person opens
https://microsoft.com/devicelogin on any device and signs in. Only the refresh
token is kept (encrypted, in the agent store); the password never reaches the agent.
Signing in again (by anyone) replaces the stored login.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional
import requests
from config.settings import settings

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Chat.Create ChatMessage.Send"   # sending reminders
CHAT_READ_SCOPES = SCOPES + " Chat.Read"                            # also reading the agent's 1:1 chats
LOGIN_ID = 1  # a single stored login; signing in again replaces it


class TeamsSignInError(Exception):
    """No usable login: nobody signed in, the key is missing, or the login expired or was revoked."""


def _authority() -> str:
    if not settings.tenant_id or not settings.client_id:
        raise TeamsSignInError("TENANT_ID and CLIENT_ID must be set in .env")
    return f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0"


def _fernet():
    from cryptography.fernet import Fernet
    key = (settings.teams_token_key or "").strip()
    if not key:
        raise TeamsSignInError(
            "TEAMS_TOKEN_KEY is not set. Generate one and add it to .env:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    return Fernet(key.encode())


# ------------------------------------------------------------ stored login

def _load_login(session):
    from database.agent_store import TeamsSenderLogin
    return session.get(TeamsSenderLogin, LOGIN_ID)


def _store_refresh_token(session, refresh_token: str, me: dict = None):
    from database.agent_store import TeamsSenderLogin, _utcnow
    login = _load_login(session)
    if login is None:
        login = TeamsSenderLogin(id=LOGIN_ID)
        session.add(login)
    login.refresh_token = _fernet().encrypt(refresh_token.encode()).decode()
    if me is not None:
        login.graph_user_id = me["id"]
        login.display_name = me.get("displayName") or ""
        login.upn = me.get("userPrincipalName") or me.get("mail") or ""
        login.signed_in_at = _utcnow()
    login.updated_at = _utcnow()
    session.commit()
    return login


def login_status(session) -> Optional[str]:
    login = _load_login(session)
    if login is None:
        return None
    return f"{login.display_name} ({login.upn}), signed in {login.signed_in_at:%d-%m-%Y %H:%M} UTC"


def sign_out(session) -> bool:
    login = _load_login(session)
    if login is None:
        return False
    session.delete(login)
    session.commit()
    return True


def sign_in(session, show: Callable[[str], None] = print, scopes: str = CHAT_READ_SCOPES) -> str:
    """Device code sign-in. Blocks until the person approves (or the code expires).
    Asks for Chat.Read too (pass scopes=SCOPES for sending only)."""
    _fernet()  # fail before showing a code if the key is missing
    r = requests.post(f"{_authority()}/devicecode", data={"client_id": settings.client_id, "scope": scopes}, timeout=30)
    if r.status_code != 200:
        raise TeamsSignInError(f"Could not start sign-in: {r.text[:500]}")
    flow = r.json()
    show(flow["message"])
    interval, deadline = int(flow.get("interval", 5)), time.time() + int(flow.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        t = requests.post(f"{_authority()}/token", timeout=30, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": settings.client_id, "device_code": flow["device_code"]})
        body = t.json()
        if t.status_code == 200:
            me = requests.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {body['access_token']}"}, timeout=30)
            me.raise_for_status()
            login = _store_refresh_token(session, body["refresh_token"], me.json())
            return f"{login.display_name} ({login.upn})"
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise TeamsSignInError(f"Sign-in failed: {body.get('error_description') or error}")
    raise TeamsSignInError("The sign-in code expired. Run teams-signin again.")


# ------------------------------------------------------------ sending

@dataclass
class SendResult:
    ok: bool
    error: str = ""


class TeamsSender:
    """Sends 1:1 chat messages as the signed-in person; with scopes=CHAT_READ_SCOPES it can read them too."""

    def __init__(self, session, scopes: str = SCOPES):
        self.session = session
        login = _load_login(session)
        if login is None:
            raise TeamsSignInError("Nobody is signed in for Teams messages. Run teams-signin.")
        self.sender_id = login.graph_user_id
        self.sender_name = login.display_name
        self.sender_upn = (login.upn or "").lower()
        self.scopes = scopes
        self._token = self._refresh(_fernet().decrypt(login.refresh_token.encode()).decode())

    def _refresh(self, refresh_token: str) -> str:
        r = requests.post(f"{_authority()}/token", timeout=30, data={
            "grant_type": "refresh_token", "client_id": settings.client_id,
            "refresh_token": refresh_token, "scope": self.scopes})
        body = r.json()
        if r.status_code != 200:
            error = body.get("error_description") or body.get("error") or ""
            if "Chat.Read" in self.scopes and ("consent" in error.lower() or "AADSTS65001" in error):
                raise TeamsSignInError(f"The Teams login of {self.sender_name} cannot read chats yet: Chat.Read is not "
                                       "granted. Add the delegated Chat.Read permission to the Azure app, then run "
                                       "teams-signin again (reminders keep working meanwhile).")
            raise TeamsSignInError(f"The Teams login of {self.sender_name} no longer works ({error}). "
                                   "Run teams-signin again.")
        if body.get("refresh_token"):  # refresh tokens rotate; keep the newest
            _store_refresh_token(self.session, body["refresh_token"])
        return body["access_token"]

    def _post(self, path: str, payload: dict) -> requests.Response:
        return requests.post(f"{GRAPH}{path}", json=payload, timeout=30,
                             headers={"Authorization": f"Bearer {self._token}"})

    def open_chat(self, email: str) -> str:
        """Id of the 1:1 chat between the signed-in account and `email` (created when there is none yet)."""
        recipient = (email or "").strip()
        if not recipient:
            raise ValueError("no email address")
        if recipient.lower() == self.sender_upn:
            raise ValueError("Teams cannot send a 1:1 message to the signed-in account itself")
        member = lambda who: {"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"],  # noqa: E731
                              "user@odata.bind": f"{GRAPH}/users('{who}')"}
        # Returns the existing 1:1 chat between the two people when there is one
        chat = self._post("/chats", {"chatType": "oneOnOne", "members": [member(self.sender_id), member(recipient)]})
        if chat.status_code not in (200, 201):
            raise ValueError(f"could not open a chat with {recipient}: {chat.status_code} {chat.text[:300]}")
        return chat.json()["id"]

    def read_chat(self, chat_id: str, since):
        """Messages in a chat since `since` (local office time), oldest first. Needs CHAT_READ_SCOPES."""
        from datetime import timedelta, timezone
        from services.teams_chat_service import fetch_chat_messages
        return fetch_chat_messages(chat_id, self._token, since,
                                   timezone(timedelta(minutes=settings.teams_utc_offset_minutes)))

    def send(self, recipient_email: str, html_text: str) -> SendResult:
        try:
            chat_id = self.open_chat(recipient_email)
        except ValueError as exc:
            return SendResult(False, str(exc))
        msg = self._post(f"/chats/{chat_id}/messages", {"body": {"contentType": "html", "content": html_text}})
        if msg.status_code not in (200, 201):
            return SendResult(False, f"message not sent: {msg.status_code} {msg.text[:300]}")
        return SendResult(True)
