"""
Configuration management for the AI Operations Agent.
Reads environment variables and provides a centralized settings object.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
from urllib.parse import urlparse
from typing import List

# Load environment variables from .env file
load_dotenv()

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
    service: str = field(default_factory=lambda: os.getenv("EMAIL_SERVICE", "smtp"))
    host: str = field(default_factory=lambda: os.getenv("EMAIL_SERVER_HOST", "smtp.example.com"))
    port: int = field(default_factory=lambda: int(os.getenv("EMAIL_SERVER_PORT", "587")))
    secure: bool = field(default_factory=lambda: os.getenv("EMAIL_SERVER_SECURE", "true").lower() == "true")
    user: str = field(default_factory=lambda: os.getenv("EMAIL_SERVER_USER", ""))
    password: str = field(default_factory=lambda: os.getenv("EMAIL_SERVER_PASSWORD", ""))
    from_email: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", os.getenv("EMAIL_SERVER_USER", "")))

@dataclass
class AIConfig:
    """AI/LLM configuration."""
    api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))

@dataclass
class AppSettings:
    """Global application settings."""
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    ignore_ids: str = field(default_factory=lambda: os.getenv("IGNORE_ID", ""))
    
    def get_ignore_id_list(self) -> List[int]:
        """Parse comma-separated ignore IDs into a list of integers."""
        if not self.ignore_ids:
            return []
        return [int(id.strip()) for id in self.ignore_ids.split(",") if id.strip().isdigit()]

# Singleton instance
settings = AppSettings()
