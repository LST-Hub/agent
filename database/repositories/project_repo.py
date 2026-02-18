"""
Project Repository implementation.
"""

from typing import List, Optional
from sqlalchemy import text
from database.repositories.base import BaseRepository
from models.schemas import Project

class ProjectRepository(BaseRepository[Project]):
    """
    Handles database operations for projects.
    """

    def _map_row(self, row) -> Project:
        return Project(
            globalId=row.globalId,
            id=row.id,
            name=row.name,
            description=row.description,
            clientId=row.clientId,
            pmId=row.pmId,
            statusId=row.statusId,
            priorityId=row.priorityId,
            startDate=row.startDate,
            estimatedTime=row.estimatedTime,
            estimatedEndDate=row.estimatedEndDate,
            actualEndDate=row.actualEndDate,
            canSupervisorApproveTime=bool(row.canSupervisorApproveTime),
            active=bool(row.active),
            createdById=row.createdById,
            createdAt=row.createdAt,
            updatedAt=row.updatedAt,
            workspaceId=row.workspaceId,
            ticket=bool(row.ticket),
            billable=bool(row.billable),
            projectTypeId=row.projectTypeId
        )

    def get_by_id(self, id: int) -> Optional[Project]:
        query = text("SELECT * FROM Project WHERE id = :id AND workspaceId = 3")
        result = self.session.execute(query, {"id": id}).fetchone()
        if result:
            return self._map_row(result)
        return None

    def get_all(self) -> List[Project]:
        query = text("SELECT * FROM Project WHERE workspaceId = 3")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]

    def get_active_projects(self) -> List[Project]:
        """Fetch all active projects."""
        query = text("SELECT * FROM Project WHERE active = 1 AND workspaceId = 3")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]
