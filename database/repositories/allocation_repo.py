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
        query = text("SELECT * FROM ResourceAllocation WHERE id = :id")
        result = self.session.execute(query, {"id": id}).fetchone()
        if result:
            return self._map_row(result)
        return None

    def get_all(self) -> List[ResourceAllocation]:
        query = text("SELECT * FROM ResourceAllocation")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]

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
        query = text("SELECT * FROM ResourceAllocation WHERE date = :target_date")
        result = self.session.execute(query, {"target_date": target_date}).fetchall()
        return [self._map_row(row) for row in result]
