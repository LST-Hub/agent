"""
Who may use the connector.

Everyone signs in with their LST Microsoft account (Entra). On top of that, MCP_ALLOWED_USERS says
who gets in:
    MCP_ALLOWED_USERS=pm1@lstconsultancy.com,boss@lstconsultancy.com   # these people
    MCP_ALLOWED_USERS=@lstconsultancy.com                               # anyone with that domain
    MCP_ALLOWED_USERS=*                                                 # anyone who can sign in
                                                                         # (use with Entra "assignment required")
Empty means nobody, so a forgotten setting fails closed.
"""

import os
from typing import Dict, Optional


def allowed_rules(raw: Optional[str] = None) -> list:
    raw = os.getenv("MCP_ALLOWED_USERS", "") if raw is None else raw
    return [r.strip().lower() for r in raw.split(",") if r.strip()]


def user_email(claims: Dict) -> str:
    """The signed-in person's email from Entra token claims."""
    for key in ("preferred_username", "upn", "email", "unique_name"):
        value = (claims or {}).get(key)
        if value and "@" in str(value):
            return str(value).strip().lower()
    return ""


def is_allowed(email: str, rules: list) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    for rule in rules:
        if rule == "*":
            return True
        if rule.startswith("@") and email.endswith(rule):
            return True
        if rule == email:
            return True
    return False


def sender_rules(raw: Optional[str] = None) -> list:
    """Who may send Teams reminders through the connector (MCP_SENDERS, same format; empty = nobody)."""
    return allowed_rules(os.getenv("MCP_SENDERS", "") if raw is None else raw)
