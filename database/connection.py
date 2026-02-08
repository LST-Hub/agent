"""
Database connection management.
Encapsulates the SQLAlchemy engine and session creation.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from config.settings import settings

# Construct connection string for MySQL
# Example: mysql+pymysql://user:pass@localhost:3306/dbname
DATABASE_URL = f"mysql+pymysql://{settings.db.user}:{settings.db.password}@{settings.db.host}:{settings.db.port}/{settings.db.name}"

class DatabaseManager:
    """
    Singleton-like wrapper for database engine and session factory.
    """
    
    def __init__(self, connection_string: str):
        self._engine = create_engine(connection_string, echo=False, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=self._engine)
        self._ScopedSession = scoped_session(self._session_factory)

    def get_session(self):
        """Returns a thread-safe database session."""
        return self._ScopedSession()

    def close(self):
        """Dispose of the engine."""
        self._engine.dispose()

# Global database instance
db_manager = DatabaseManager(DATABASE_URL)

def get_db_session():
    """Dependency injection helper for obtaining a session."""
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()
