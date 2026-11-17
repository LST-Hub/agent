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

    def get_active_tasks_with_effort(self) -> List[dict]:
        """
        Fetch active tasks of active projects with logged effort
        (seconds, excluding rejected timesheets).
        """
        query = text("""
            SELECT
                k.id, k.name, k.projectId, k.estimatedTime,
                k.estimatedEndDate, k.actualEndDate,
                COALESCE(ts.workedSeconds, 0) AS workedSeconds
            FROM Task k
            INNER JOIN Project p ON p.id = k.projectId AND p.active = 1 AND p.workspaceId = 3
            LEFT JOIN (
                SELECT taskId, SUM(duration) AS workedSeconds
                FROM Timesheet
                WHERE rejected = 0
                GROUP BY taskId
            ) ts ON ts.taskId = k.id
            WHERE k.active = 1
        """)
        result = self.session.execute(query).fetchall()
        return [dict(row._mapping) for row in result]
