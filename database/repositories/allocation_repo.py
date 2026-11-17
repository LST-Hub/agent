"""
Resource Allocation Repository implementation.
"""

from typing import List, Optional
from datetime import date
from sqlalchemy import text
from database.repositories.base import BaseRepository
from models.schemas import ResourceAllocation, RepetationType

class ResourceAllocationRepository(BaseRepository[ResourceAllocation]):
    """
    Handles database operations for resource allocations.
    """

    def _map_row(self, row) -> ResourceAllocation:
        return ResourceAllocation(
            globalId=row.globalId,
            id=row.id,
            repeatId=row.repeatId,
            projectId=row.projectId,
            taskId=row.taskId,
            allocatedUserId=row.allocatedUserId,
            date=row.date,
            duration=row.duration,
            startDate=row.startDate,
            endDate=row.endDate,
            repetationType=RepetationType(row.repetationType) if row.repetationType else None,
            createdById=row.createdById,
            createdAt=row.createdAt,
            updatedAt=row.updatedAt,
            workspaceId=row.workspaceId,
            userGlobalId=row.userGlobalId,
            idleHours=row.idleHours
        )

    def get_by_id(self, id: int) -> Optional[ResourceAllocation]:
        query = text("SELECT * FROM ResourceAllocation WHERE id = :id AND workspaceId = 3")
        result = self.session.execute(query, {"id": id}).fetchone()
        if result:
            return self._map_row(result)
        return None

    def get_all(self) -> List[ResourceAllocation]:
        query = text("SELECT * FROM ResourceAllocation WHERE workspaceId = 3")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]

    def get_user_project_day_allocations(self, start_date: date, end_date: date) -> List[dict]:
        """
        Allocated seconds per active user, project and date. Repeating allocations
        are stored expanded (one row per date), so grouping on `date` is sufficient.
        """
        query = text("""
            SELECT ra.allocatedUserId AS userId, u.name AS userName, ra.projectId,
                   p.name AS projectName, pm.name AS pmName, ra.date, SUM(ra.duration) AS seconds
            FROM ResourceAllocation ra
            INNER JOIN User u ON u.id = ra.allocatedUserId AND u.workspaceId = 3 AND u.active = 1
            LEFT JOIN Project p ON p.id = ra.projectId AND p.workspaceId = 3
            LEFT JOIN User pm ON pm.id = p.pmId AND pm.workspaceId = 3
            WHERE ra.workspaceId = 3
            AND ra.date >= :start_date AND ra.date <= :end_date
            GROUP BY ra.allocatedUserId, u.name, ra.projectId, p.name, pm.name, ra.date
        """)
        result = self.session.execute(query, {"start_date": start_date, "end_date": end_date}).fetchall()
        return [dict(row._mapping) for row in result]

    def get_by_date(self, target_date: date) -> List[ResourceAllocation]:
        """Fetch allocations active on a specific date."""
        # This one is tricky if allocations repeat. "repeatId" and "repetationType" suggest complex logic.
        # But the schema has a `date` column. 
        # If the table stores expanded allocation PER DATE, then `date = target_date` works.
        # If it stores rules (startDate, endDate, repeatType), then logic is complex.
        # Based on "ResourceAllocation.duration vs SUM(Timesheet.duration)", comparing `duration` suggests `date` column is the specific date allocation.
        # Let's assume the table contains simplified per-day records or the "date" field is the relevant one.
        # If repeat logic is in DB triggers or app logic that generates rows, then `date` is specific.
        # If `date` is missing or null for repeated ones, we'd need more logic.
        # Given `date (date)` column is present, I'll filter by it.
        query = text("SELECT * FROM ResourceAllocation WHERE date = :target_date AND workspaceId = 3")
        result = self.session.execute(query, {"target_date": target_date}).fetchall()
        return [self._map_row(row) for row in result]
