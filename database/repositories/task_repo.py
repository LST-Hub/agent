"""
Task Repository implementation.
"""

from typing import List, Optional
from sqlalchemy import text
from database.repositories.base import BaseRepository
from models.schemas import Task

class TaskRepository(BaseRepository[Task]):
    """
    Handles database operations for tasks.
    """

    def _map_row(self, row) -> Task:
        return Task(
            globalId=row.globalId,
            id=row.id,
            name=row.name,
            description=row.description,
            projectId=row.projectId,
            statusId=row.statusId,
            priorityId=row.priorityId,
            startDate=row.startDate,
            estimatedTime=row.estimatedTime,
            estimatedEndDate=row.estimatedEndDate,
            actualEndDate=row.actualEndDate,
            active=bool(row.active),
            billable=bool(row.billable),
            createdById=row.createdById,
            createdAt=row.createdAt,
            updatedAt=row.updatedAt,
            workspaceId=row.workspaceId,
            taskTypeId=row.taskTypeId
        )

    def get_by_id(self, id: int) -> Optional[Task]:
        query = text("SELECT * FROM Task WHERE id = :id")
        result = self.session.execute(query, {"id": id}).fetchone()
        if result:
            return self._map_row(result)
        return None

    def get_all(self) -> List[Task]:
        query = text("SELECT * FROM Task")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]
