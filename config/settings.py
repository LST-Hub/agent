"""
Configuration management for the AI Operations Agent.
Reads environment variables and provides a centralized settings object.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables from .env file
load_dotenv()

@dataclass
class DatabaseConfig:
    """Database connection parameters."""
    url: str = os.getenv("DATABASE_URL", "postgresql://user:@localhost:5432/project_db")
    
    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or "localhost"
    
    @property
    def port(self) -> int:
        return urlparse(self.url).port or 5432
    
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
    service: str = os.getenv("EMAIL_SERVICE", "smtp")
    host: str = os.getenv("EMAIL_SERVER_HOST", "smtp.example.com")
    port: int = int(os.getenv("EMAIL_SERVER_PORT", "587"))
    secure: bool = os.getenv("EMAIL_SERVER_SECURE", "true").lower() == "true"
    user: str = os.getenv("EMAIL_SERVER_USER", "")
    password: str = os.getenv("EMAIL_SERVER_PASSWORD", "")
    from_email: str = os.getenv("EMAIL_FROM", os.getenv("EMAIL_SERVER_USER", ""))  # Defaults to user if not set

@dataclass
class AIConfig:
    """AI/LLM configuration."""
    api_key: str | None = os.getenv("OPENAI_API_KEY")

@dataclass
class AppSettings:
    """Global application settings."""
    db: DatabaseConfig = DatabaseConfig()
    smtp: SMTPConfig = SMTPConfig()
    ai: AIConfig = AIConfig()
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

# Singleton instance
settings = AppSettings()
