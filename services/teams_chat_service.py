"""
Reads messages from the Teams group chat where allocations are posted.

Uses Microsoft Graph with the same Azure app as the SharePoint upload
(TENANT_ID / CLIENT_ID / CLIENT_SECRET). The app needs read access to the chat,
granted by the Microsoft 365 admin: either the Chat.Read.All application
permission, or ChatMessage.Read.Chat resource-specific consent on this one chat.

For testing without Teams access, load_sample_messages() reads the same message
shape from a local JSON file.
"""

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import requests
import logging

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
PAGE_SIZE = 50  # Graph maximum for chat messages


@dataclass
class ChatMessage:
    id: str
    sent_at: datetime   # Local office time, naive
    sender: str
    text: str
    sender_id: str = ""  # Microsoft account id of the sender (empty in sample files)


def html_to_text(content: str) -> str:
    """Teams message HTML to plain text, keeping line breaks and @mention names."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", content or "")
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def clean_sender(name: str) -> str:
    """Teams display names can carry a company suffix: 'Prakash Bagul | LST Consultancy' -> 'Prakash Bagul'."""
    return (name or "").split("|")[0].strip() or "Unknown"


def load_sample_messages(path: str) -> List[ChatMessage]:
    """
    Messages from a JSON file: a list of {"sent_at": "YYYY-MM-DD HH:MM", "sender": "...", "text": "..."}
    with an optional "id". Times are local office time.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("messages", [])
    messages = []
    for i, m in enumerate(data, start=1):
        messages.append(ChatMessage(
            id=str(m.get("id") or i),
            sent_at=datetime.fromisoformat(m["sent_at"].replace("T", " ")),
            sender=clean_sender(m.get("sender")),
            text=(m.get("text") or "").strip(),
        ))
    return sorted(messages, key=lambda m: m.sent_at)


class TeamsChatService:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, chat_id: str,
                 utc_offset_minutes: int = 330):
        if not all([tenant_id, client_id, client_secret]):
            raise ValueError("TENANT_ID, CLIENT_ID and CLIENT_SECRET must be set to read Teams")
        if not chat_id:
            raise ValueError("TEAMS_CHAT_ID must be set to read Teams")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.chat_id = chat_id
        self.local_tz = timezone(timedelta(minutes=utc_offset_minutes))
        self._token: Optional[str] = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        response = requests.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
            data={"client_id": self.client_id, "client_secret": self.client_secret,
                  "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
            timeout=30,
        )
        response.raise_for_status()
        self._token = response.json()["access_token"]
        return self._token

    def fetch_messages(self, since: datetime) -> List[ChatMessage]:
        """User messages created or edited after `since` (local time), oldest first."""
        return fetch_chat_messages(self.chat_id, self._get_token(), since, self.local_tz)


def fetch_chat_messages(chat_id: str, token: str, since: datetime, local_tz: timezone) -> List[ChatMessage]:
    """User messages of one chat created or edited after `since` (local time), oldest first.
    Works with an app token (Chat.Read.All) or a signed-in member's token (Chat.Read)."""
    to_local = lambda iso: datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(local_tz).replace(tzinfo=None)  # noqa: E731
    since_utc = since.replace(tzinfo=local_tz).astimezone(timezone.utc)
    url = f"{GRAPH}/chats/{chat_id}/messages"
    params = {
        "$top": PAGE_SIZE,
        "$orderby": "lastModifiedDateTime desc",
        "$filter": f"lastModifiedDateTime gt {since_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')}",
    }
    headers = {"Authorization": f"Bearer {token}"}
    messages: List[ChatMessage] = []
    while url:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        if response.status_code == 403:
            logger.error(f"Graph refused access to the chat (403): {response.text[:500]}")
        response.raise_for_status()
        payload = response.json()
        for m in payload.get("value", []):
            if m.get("messageType") != "message" or m.get("deletedDateTime"):
                continue
            body = m.get("body") or {}
            content = body.get("content") or ""
            text = html_to_text(content) if body.get("contentType") == "html" else content.strip()
            if not text:
                continue
            user = (m.get("from") or {}).get("user") or {}
            messages.append(ChatMessage(
                id=m["id"],
                sent_at=to_local(m["createdDateTime"]),
                sender=clean_sender(user.get("displayName")),
                text=text,
                sender_id=user.get("id") or "",
            ))
        url, params = payload.get("@odata.nextLink"), None  # nextLink already carries the query
    logger.info(f"Read {len(messages)} Teams messages since {since:%d-%m-%Y %H:%M}.")
    return sorted(messages, key=lambda m: m.sent_at)
