"""
User Repository implementation.
"""

from typing import List, Optional
from sqlalchemy import text
from database.repositories.base import BaseRepository
from models.schemas import User, UserType

class UserRepository(BaseRepository[User]):
    """
    Handles database operations for users.
    """

    def _map_row(self, row) -> User:
        return User(
            globalId=row.globalId,
            id=row.id,
            name=row.name,
            email=row.email,
            firstName=row.firstName,
            lastName=row.lastName,
            designation=row.designation,
            supervisorId=row.supervisorId,
            type=UserType(row.type) if row.type else None,
            roleId=row.roleId,
            canBePM=bool(row.canBePM),
            canBeSupervisor=bool(row.canBeSupervisor),
            departmentId=row.departmentId,
            workspaceId=row.workspaceId,
            active=bool(row.active),
            deleted=bool(row.deleted),
            billable=bool(row.billable),
            billableCost=row.billableCost,
            billableRate=row.billableRate,
            hiringDate=row.hiringDate,
            timeZone=row.timeZone
        )

    def get_by_id(self, id: int) -> Optional[User]:
        query = text("SELECT * FROM User WHERE id = :id")
        result = self.session.execute(query, {"id": id}).fetchone()
        if result:
            return self._map_row(result)
        return None

    def get_all(self) -> List[User]:
        query = text("SELECT * FROM User")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]

    def get_active_users(self) -> List[User]:
        """Fetch all active users with workspaceId = 3 and active = 1."""
        query = text("""
            SELECT * FROM User 
            WHERE workspaceId = 3 
            AND active = 1
            AND User.id= 60
        """)
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]
