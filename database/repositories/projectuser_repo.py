"""
ProjectUser Repository implementation.
"""

from typing import List, Optional
from sqlalchemy import text
from database.repositories.base import BaseRepository

class ProjectUserRepository:
    """
    Handles database operations for ProjectUser table.
    """
    
    def __init__(self, session):
        self.session = session

    def get_projects_by_user(self, user_id: int) -> List[int]:
        """Get all project IDs for a given user."""
        query = text("""
            SELECT DISTINCT projectId 
            FROM ProjectUser 
            WHERE userId = :user_id
        """)
        result = self.session.execute(query, {"user_id": user_id}).fetchall()
        return [row.projectId for row in result]

    def get_users_by_project(self, project_id: int) -> List[int]:
        """Get all user IDs for a given project."""
        query = text("""
            SELECT DISTINCT userId 
            FROM ProjectUser 
            WHERE projectId = :project_id
        """)
        result = self.session.execute(query, {"project_id": project_id}).fetchall()
        return [row.userId for row in result]