"""
Agent-owned operational store (PostgreSQL in production).

Holds state the agent needs between runs: follow-ups sent to PMs, a log of
every notification, the day plans read from the Teams allocation chat, and the
encrypted login of the account Teams reminders are sent from, and which 1:1 chat
messages have been turned into TaskSprint allocations.
The PM tool's MySQL database stays read-only.

Tables are created on first use (create_all is idempotent). Move to Alembic
migrations once the schema starts changing.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Date, DateTime, Integer, String, Text, Boolean, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from config.settings import settings

Base = declarative_base()

# Follow-up lifecycle
STATUS_OPEN = "open"            # PM emailed, waiting for the issue to be fixed
STATUS_ESCALATED = "escalated"  # No action after the allowed emails; managers notified
STATUS_RESOLVED = "resolved"    # The issue no longer appears in the checks
STATUS_CLOSED = "closed"        # Closed manually
ACTIVE_STATUSES = (STATUS_OPEN, STATUS_ESCALATED)


def _utcnow() -> datetime:
    """Naive UTC timestamp (portable across PostgreSQL and SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FollowUp(Base):
    __tablename__ = "followup_records"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, nullable=False, index=True)
    project_name = Column(String(255), nullable=False)
    pm_id = Column(Integer, index=True)
    pm_name = Column(String(255))
    pm_email = Column(String(255))
    finding_code = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    evidence = Column(Text)
    severity = Column(String(16))
    status = Column(String(16), nullable=False, default=STATUS_OPEN, index=True)
    emails_sent = Column(Integer, nullable=False, default=0)
    first_sent_on = Column(Date)
    last_sent_on = Column(Date)
    escalated_on = Column(Date)
    resolved_on = Column(Date)
    resolution_note = Column(Text)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id = Column(Integer, primary_key=True)
    kind = Column(String(32), nullable=False)       # followup, escalation
    recipients = Column(Text, nullable=False)
    subject = Column(String(255), nullable=False)
    followup_ids = Column(Text)                     # Comma-separated FollowUp ids covered by this email
    success = Column(Boolean, nullable=False)
    sent_at = Column(DateTime, nullable=False, default=_utcnow)


class TeamsPlan(Base):
    """One person, project and day from a plan posted in the Teams allocation chat."""
    __tablename__ = "teams_plans"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_name = Column(String(255), nullable=False)
    project_id = Column(Integer, nullable=False)
    project_name = Column(String(255), nullable=False)
    work_date = Column(Date, nullable=False, index=True)
    seconds = Column(Integer, nullable=False)
    hours_stated = Column(Boolean, nullable=False, default=True)  # False: not stated in the message, saved as 0 h
    confidence = Column(String(16), nullable=False)
    task = Column(Text)
    message_id = Column(String(64), nullable=False, index=True)   # Teams message the plan came from
    sent_at = Column(DateTime, nullable=False)
    sender = Column(String(255))
    evidence = Column(Text)
    match_note = Column(Text)
    saved_at = Column(DateTime, nullable=False, default=_utcnow)


class TeamsSenderLogin(Base):
    """The account Teams reminders are sent from (one row). Signing in again replaces it."""
    __tablename__ = "teams_sender_login"

    id = Column(Integer, primary_key=True)
    graph_user_id = Column(String(64), nullable=False)
    display_name = Column(String(255))
    upn = Column(String(255))
    refresh_token = Column(Text, nullable=False)   # Fernet-encrypted with TEAMS_TOKEN_KEY
    signed_in_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow)


class ChatAllocationMessage(Base):
    """A message from someone's 1:1 chat with the agent that chat-allocations has handled (each only once)."""
    __tablename__ = "chat_allocation_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    status = Column(String(16), nullable=False)   # pending, needs_fix, no_plan, confirmed, partly, failed, cancelled, nothing
    written = Column(Text)                        # rows sent to TaskSprint: project/task/seconds
    reply = Column(Text)                          # what the agent answered
    processed_at = Column(DateTime, nullable=False, default=_utcnow)


class ChatPendingPlan(Base):
    """A plan read from someone's 1:1 chat, waiting for them to reply "yes" before it goes to TaskSprint."""
    __tablename__ = "chat_pending_plans"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    rows = Column(Text, nullable=False)            # JSON: project/task ids and names, seconds, note
    status = Column(String(16), nullable=False)    # pending, confirmed, partly, failed, cancelled, replaced
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    decided_at = Column(DateTime)


_session_factory = None


def get_agent_session(url: str = None):
    """Return a session on the agent store, creating tables on first use."""
    global _session_factory
    if _session_factory is None or url:
        url = url or settings.agent_database_url
        if not url:
            raise ValueError("AGENT_DATABASE_URL environment variable is not set")
        engine = create_engine(url, pool_pre_ping=True)
        Base.metadata.create_all(engine)
        _session_factory = sessionmaker(bind=engine)
    return _session_factory()
