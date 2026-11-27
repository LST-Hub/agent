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

    def get_projects_for_matching(self) -> List[dict]:
        """All workspace projects (active and inactive) with their status name, for matching free-text names."""
        query = text("""
            SELECT p.id, p.name, p.active, p.pmId, pm.name AS pmName, pm.email AS pmEmail,
                   (SELECT s.name FROM Status s WHERE s.id = p.statusId AND s.workspaceId = 3 LIMIT 1) AS statusName
            FROM Project p
            LEFT JOIN User pm ON pm.id = p.pmId AND pm.workspaceId = 3
            WHERE p.workspaceId = 3
        """)
        return [dict(row._mapping) for row in self.session.execute(query).fetchall()]

    def get_active_projects_with_effort(self) -> List[dict]:
        """
        Fetch active projects with PM, status name, logged effort (seconds,
        excluding rejected timesheets) and the last date time was logged.
        """
        query = text("""
            SELECT
                p.id, p.name, p.pmId, p.startDate, p.estimatedTime,
                p.estimatedEndDate, p.actualEndDate, p.billable,
                u.name AS pmName, u.email AS pmEmail,
                (SELECT s.name FROM Status s WHERE s.id = p.statusId AND s.workspaceId = 3 LIMIT 1) AS statusName,
                (SELECT pt.name FROM ProjectType pt WHERE pt.id = p.projectTypeId AND pt.workspaceId = 3 LIMIT 1) AS projectTypeName,
                COALESCE(ts.workedSeconds, 0) AS workedSeconds,
                ts.lastLoggedDate
            FROM Project p
            LEFT JOIN User u ON u.id = p.pmId AND u.workspaceId = 3
            LEFT JOIN (
                SELECT projectId, SUM(duration) AS workedSeconds, MAX(date) AS lastLoggedDate
                FROM Timesheet
                WHERE rejected = 0
                GROUP BY projectId
            ) ts ON ts.projectId = p.id
            WHERE p.active = 1 AND p.workspaceId = 3
        """)
        result = self.session.execute(query).fetchall()
        return [dict(row._mapping) for row in result]
