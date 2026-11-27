"""
Configuration management for the AI Operations Agent.
Reads environment variables and provides a centralized settings object.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
from urllib.parse import urlparse
from typing import List
#from pydantic import BaseSettings
from pydantic_settings import BaseSettings

# Load environment variables from .env file
load_dotenv()


def _env_int(name: str, default: int) -> int:
    """Integer from the environment; a missing or blank value falls back to the default."""
    value = (os.getenv(name) or "").strip()
    return int(value) if value else default


@dataclass
class DatabaseConfig:
    """Database connection parameters."""
    url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    
    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or "localhost"
    
    @property
    def port(self) -> int:
        return urlparse(self.url).port or 3306
    
    @property
    def name(self) -> str:
        return urlparse(self.url).path.lstrip("/") or "project_db"
    
    @property
    def user(self) -> str:
        return urlparse(self.url).username or "user"
    
    @property
    def password(self) -> str:
        return urlparse(self.url).password or ""

@dataclass
class SMTPConfig:
    """Email server configuration."""
    service: str = field(default_factory=lambda: os.getenv("EMAIL_SERVICE"))
    host: str = field(default_factory=lambda: os.getenv("EMAIL_SERVER_HOST"))
    port: int = field(default_factory=lambda: _env_int("EMAIL_SERVER_PORT", 465))
    # true/1/yes = STARTTLS on ports other than 465 (465 always uses SSL); "false" is really false now
    secure: bool = field(default_factory=lambda: (os.getenv("EMAIL_SERVER_SECURE") or "").strip().lower() in ("1", "true", "yes"))
    user: str = field(default_factory=lambda: os.getenv("EMAIL_SERVER_USER"))
    password: str = field(default_factory=lambda: os.getenv("EMAIL_SERVER_PASSWORD"))
    from_email: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", os.getenv("EMAIL_SERVER_USER")))

@dataclass
class AIConfig:
    """AI/LLM configuration."""
    api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    # Model used to extract allocations from Teams messages
    extraction_model: str = field(default_factory=lambda: (os.getenv("OPENAI_EXTRACTION_MODEL") or "gpt-4.1-mini").strip())

@dataclass
class AppSettings:
    """Global application settings."""
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    # How email is sent: smtp (EMAIL_SERVER_* above) or graph (Microsoft 365, Azure app above, Mail.Send)
    email_transport: str = field(default_factory=lambda: (os.getenv("EMAIL_TRANSPORT") or "smtp").strip().lower())
    # Mailbox the agent sends from with EMAIL_TRANSPORT=graph, e.g. ops-agent@lstconsultancy.com
    graph_sender_mailbox: str = field(default_factory=lambda: (os.getenv("GRAPH_SENDER_MAILBOX") or "").strip())
    # Gentle sending (shared hosting blocks bursts): pause between emails, cap per run, retries
    email_send_delay_seconds: float = field(default_factory=lambda: float((os.getenv("EMAIL_SEND_DELAY_SECONDS") or "10").strip()))
    email_max_per_run: int = field(default_factory=lambda: _env_int("EMAIL_MAX_PER_RUN", 60))
    email_retries: int = field(default_factory=lambda: _env_int("EMAIL_RETRIES", 2))
    email_retry_wait_seconds: int = field(default_factory=lambda: _env_int("EMAIL_RETRY_WAIT_SECONDS", 60))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    ignore_ids: str = field(default_factory=lambda: os.getenv("IGNORE_ID", ""))
    pm_email: str = field(default_factory=lambda: os.getenv("PM_EMAIL", ""))
    tenant_id: str = field(default_factory=lambda: os.getenv("TENANT_ID", ""))
    client_id: str = field(default_factory=lambda: os.getenv("CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("CLIENT_SECRET", ""))
    site_id: str = field(default_factory=lambda: os.getenv("SITE_ID", ""))
    drive_id: str = field(default_factory=lambda: os.getenv("DRIVE_ID", ""))
    file_name: str = field(default_factory=lambda: os.getenv("FILE_NAME", ""))
    table_name: str = field(default_factory=lambda: os.getenv("TABLE_NAME", ""))
    # Unit of Project.estimatedTime / Task.estimatedTime in the PM database: seconds, minutes or hours
    estimated_time_unit: str = field(default_factory=lambda: (os.getenv("ESTIMATED_TIME_UNIT") or "seconds").strip())
    health_inactive_days: int = field(default_factory=lambda: _env_int("HEALTH_INACTIVE_DAYS", 14))
    # Number of past working days (ending yesterday) covered by the daily operations report
    daily_ops_lookback_days: int = field(default_factory=lambda: _env_int("DAILY_OPS_LOOKBACK_DAYS", 5))
    # Agent's own PostgreSQL store (follow-ups, notification log), e.g. postgresql+psycopg2://user:pass@agent-db:5432/ops_agent
    agent_database_url: str = field(default_factory=lambda: os.getenv("AGENT_DATABASE_URL", ""))
    # Working days to wait for a PM to act before the next email (reminder, then escalation)
    followup_reminder_days: int = field(default_factory=lambda: _env_int("FOLLOWUP_REMINDER_DAYS", 2))
    # Pilot mode: when set, every follow-up/reminder/escalation email goes to this address instead
    followup_test_recipient: str = field(default_factory=lambda: os.getenv("FOLLOWUP_TEST_RECIPIENT", ""))
    # Test mode: when set, EVERY email from every command goes only to this address (intended recipients shown in a banner)
    email_test_recipient: str = field(default_factory=lambda: os.getenv("EMAIL_TEST_RECIPIENT", ""))
    # PM user IDs who never receive follow-ups (separate from IGNORE_ID, which is for timesheet reminders)
    followup_ignore_pm_ids: str = field(default_factory=lambda: os.getenv("FOLLOWUP_IGNORE_PM_IDS", ""))
    # Teams group chat where allocations are posted (19:...@thread.v2), read with the Azure app above
    teams_chat_id: str = field(default_factory=lambda: (os.getenv("TEAMS_CHAT_ID") or "").strip())
    # Days of chat history read on each run
    teams_lookback_days: int = field(default_factory=lambda: _env_int("TEAMS_LOOKBACK_DAYS", 7))
    # Office time zone offset from UTC in minutes (India: 330)
    teams_utc_offset_minutes: int = field(default_factory=lambda: _env_int("TEAMS_UTC_OFFSET_MINUTES", 330))
    # Fernet key that encrypts the stored Teams login used to send reminders (teams-signin)
    teams_token_key: str = field(default_factory=lambda: os.getenv("TEAMS_TOKEN_KEY", ""))
    # Test mode for Teams reminders: when set, every reminder goes only to this person (email / UPN)
    teams_dm_test_recipient: str = field(default_factory=lambda: (os.getenv("TEAMS_DM_TEST_RECIPIENT") or "").strip())
    # Missing-timesheet reminders (daily): email, teams (email only if Teams fails) or both
    timesheet_reminder_channel: str = field(
        default_factory=lambda: (os.getenv("TIMESHEET_REMINDER_CHANNEL") or "email").strip().lower())
    # User IDs who do not need to post a daily plan (e.g. PMs), on top of IGNORE_ID
    teams_plan_ignore_ids: str = field(default_factory=lambda: os.getenv("TEAMS_PLAN_IGNORE_IDS", ""))
    # TaskSprint web API, used as the agent's own TaskSprint account (writes allocations)
    tasksprint_url: str = field(default_factory=lambda: (os.getenv("TASKSPRINT_URL") or "").strip())
    tasksprint_agent_email: str = field(default_factory=lambda: (os.getenv("TASKSPRINT_AGENT_EMAIL") or "").strip())
    tasksprint_agent_password: str = field(default_factory=lambda: os.getenv("TASKSPRINT_AGENT_PASSWORD") or "")
    # People whose 1:1 chat with the agent is read for day plans (emails, comma-separated; empty = nobody)
    chat_allocation_users: str = field(default_factory=lambda: os.getenv("CHAT_ALLOCATION_USERS", ""))

    def get_chat_allocation_users(self) -> List[str]:
        return [e.strip().lower() for e in self.chat_allocation_users.split(",") if e.strip()]

    @staticmethod
    def _parse_id_list(value: str) -> List[int]:
        """Parse comma-separated IDs into a list of integers, skipping invalid entries."""
        if not value:
            return []

        result = []
        for x in value.split(","):
            try:
                result.append(int(x.strip()))
            except ValueError:
                pass
        return result

    def get_ignore_id_list(self) -> List[int]:
        """Parse comma-separated ignore IDs into a list of integers."""
        return self._parse_id_list(self.ignore_ids)

    def get_teams_plan_ignore_ids(self) -> List[int]:
        """User IDs who are not expected to post a daily plan in Teams."""
        return self._parse_id_list(self.teams_plan_ignore_ids)

    def get_followup_ignore_pm_ids(self) -> List[int]:
        """PM user IDs excluded from follow-ups."""
        return self._parse_id_list(self.followup_ignore_pm_ids)
    
    def get_pm_email(self) -> List[str]:
        """Parse comma-separated PM emails into a list of strings."""
        if not self.pm_email:
            return []
        
        return [email.strip() for email in self.pm_email.split(",") if email.strip()]


# Singleton instance
settings = AppSettings()