"""
Timesheet Repository implementation.
"""

from typing import List, Optional
from datetime import date, datetime
from sqlalchemy import text
from database.repositories.base import BaseRepository
from models.schemas import Timesheet, TimesheetStatus, RetractStatus

class TimesheetRepository(BaseRepository[Timesheet]):
    """
    Handles database operations for timesheets using raw SQL.
    """
    
    def _map_row(self, row) -> Timesheet:
        """Maps a database row to a Timesheet object."""
        # Helper to safely map row attributes
        return Timesheet(
            globalId=row.globalId,
            id=row.id,
            date=row.date,
            duration=row.duration,
            description=row.description,
            taskId=row.taskId,
            projectId=row.projectId,
            createdById=row.createdById,
            approved=bool(row.approved),
            approvedById=row.approvedById,
            approvedAt=row.approvedAt,
            rejected=bool(row.rejected),
            rejectedById=row.rejectedById,
            rejectedAt=row.rejectedAt,
            rejectionNote=row.rejectionNote,
            submittedForApproval=bool(row.submittedForApproval),
            status=TimesheetStatus(row.status) if row.status else TimesheetStatus.DRAFT,
            createdAt=row.createdAt,
            updatedAt=row.updatedAt,
            workspaceId=row.workspaceId,
            ticket=row.ticket,
            resourceAllocationId=row.resourceAllocationId,
            retractApproval=bool(row.retractApproval),
            retractReason=row.retractReason,
            retractStatus=RetractStatus(row.retractStatus) if row.retractStatus else RetractStatus.NOT_SUBMITTED,
            retractrejectionNote=row.retractrejectionNote
        )

    def get_by_id(self, id: int) -> Optional[Timesheet]:
        query = text("SELECT * FROM Timesheet WHERE id = :id")
        result = self.session.execute(query, {"id": id}).fetchone()
        if result:
            return self._map_row(result)
        return None

    def get_all(self) -> List[Timesheet]:
        query = text("SELECT * FROM Timesheet")
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]

    def get_by_user_and_date(self, user_id: int, date_val: date) -> List[Timesheet]:
        """Fetch timesheets for a specific user and date."""
        query = text("SELECT * FROM Timesheet WHERE createdById = :user_id AND date = :date")
        result = self.session.execute(query, {"user_id": user_id, "date": date_val}).fetchall()
        return [self._map_row(row) for row in result]

    def get_by_user_date_range(self, user_id: int, start_date: date, end_date: date) -> List[Timesheet]:
        """Fetch timesheets for a specific user within a date range."""
        query = text("SELECT * FROM Timesheet WHERE createdById = :user_id AND date >= :start_date AND date <= :end_date")
        result = self.session.execute(query, {"user_id": user_id, "start_date": start_date, "end_date": end_date}).fetchall()
        return [self._map_row(row) for row in result]

    def get_by_date_range(self, start_date: date, end_date: date) -> List[Timesheet]:
        """Fetch timesheets within a date range."""
        query = text("SELECT * FROM Timesheet WHERE date >= :start_date AND date <= :end_date")
        result = self.session.execute(query, {"start_date": start_date, "end_date": end_date}).fetchall()
        return [self._map_row(row) for row in result]

    def get_pending_approvals(self, days_pending: int) -> List[Timesheet]:
        """Fetch timesheets pending approval for more than X days."""
        query = text("""
            SELECT * FROM Timesheet 
            WHERE status = 'Pending' 
            AND approved = 0 
            AND updatedAt < DATE_SUB(NOW(), INTERVAL :days DAY)
        """)
        result = self.session.execute(query, {"days": days_pending}).fetchall()
        return [self._map_row(row) for row in result]

    def get_rejected_no_update(self, days_since_rejection: int) -> List[Timesheet]:
        """Fetch rejected timesheets not updated for X days."""
        query = text("""
            SELECT * FROM Timesheet 
            WHERE status = 'Rejected' 
            AND updatedAt < DATE_SUB(NOW(), INTERVAL :days DAY)
        """)
        result = self.session.execute(query, {"days": days_since_rejection}).fetchall()
        return [self._map_row(row) for row in result]

    def get_bad_descriptions(self, min_length: int) -> List[Timesheet]:
        """Fetch timesheets with missing or short descriptions."""
        query = text("""
            SELECT * FROM Timesheet 
            WHERE description IS NULL 
            OR CHAR_LENGTH(description) < :min_length
        """)
        result = self.session.execute(query, {"min_length": min_length}).fetchall()
        return [self._map_row(row) for row in result]

    def get_missing_tickets(self) -> List[Timesheet]:
        """
        Fetch timesheets that are billable (via Project or Task) but have no ticket.
        """
        # Note: This query joins across tables, adhering to SQL isolation.
        query = text("""
            SELECT T.* 
            FROM Timesheet T
            JOIN Project P ON T.projectId = P.id
            LEFT JOIN Task K ON T.taskId = K.id
            WHERE (P.billable = 1 OR K.billable = 1)
            AND (T.ticket IS NULL OR T.ticket = '')
        """)
        result = self.session.execute(query).fetchall()
        return [self._map_row(row) for row in result]

    def get_missing_timesheets(self, start_date: date, end_date: date) -> List[int]:
        """
        Fetch user IDs who haven't logged any timesheets within the date range.
        Only includes active users (workspaceId = 3 and active = 1).
        Returns a list of user IDs (createdById).
        """
        query = text("""
            SELECT DISTINCT u.id
            FROM User u
            LEFT JOIN Timesheet t ON u.id = t.createdById 
                AND t.date >= :start_date 
                AND t.date <= :end_date
            WHERE t.id IS NULL
            AND u.workspaceId = 3
            AND u.active = 1
        """)
        result = self.session.execute(query, {"start_date": start_date, "end_date": end_date}).fetchall()
        return [row.id for row in result]

    def get_weekly_timesheets_for_report(self, start_date: date, end_date: date) -> List[dict]:
        """
        Fetch timesheets for weekly report with specific columns.
        Returns raw data as dictionaries for Excel export.
        """
        query = text("""
            SELECT 
                t.date,
                t.duration,
                t.description,
                t.taskId,
                task.name AS taskName,
                t.projectId,
                project.name AS projectName,
                t.approved,
                t.approvedById,
                approver.name AS approvedByName,
                t.rejected,
                t.rejectedById,
                rejecter.name AS rejectedByName,
                t.rejectedAt,
                t.rejectionNote,
                t.status,
                t.createdById,
                creator.name AS userName
            FROM Timesheet t
            LEFT JOIN Task task ON t.taskId = task.id AND task.workspaceId = 3
            LEFT JOIN Project project ON t.projectId = project.id AND project.workspaceId = 3
            LEFT JOIN User creator ON t.createdById = creator.id
            LEFT JOIN User approver ON t.approvedById = approver.id
            LEFT JOIN User rejecter ON t.rejectedById = rejecter.id
            WHERE t.date >= :start_date
            AND t.date < :end_date
            AND creator.workspaceId = 3
            ORDER BY t.date
        """)
        result = self.session.execute(query, {"start_date": start_date, "end_date": end_date}).fetchall()
        
        def format_duration(seconds):
            """Convert seconds to human-readable format (e.g., '8 hrs', '30 mins', '2 hrs 15 mins')"""
            hours = seconds / 3600
            
            if hours >= 1:
                full_hours = int(hours)
                remaining_minutes = int((hours - full_hours) * 60)
                
                if remaining_minutes > 0:
                    return f"{full_hours} hrs {remaining_minutes} mins"
                else:
                    return f"{full_hours} hrs"
            else:
                minutes = int(seconds / 60)
                return f"{minutes} mins"
            # Convert to list of dictionaries

        return [
            {
                "date": row.date,
                "userName": row.userName,
                "duration": format_duration(row.duration),
                "description": row.description,
                #"taskId": row.taskId,
                "taskName": row.taskName,
                #"projectId": row.projectId,
                "projectName": row.projectName,
                "approved": row.approved,
                #"approvedById": row.approvedById,
                "approvedByName": row.approvedByName,
                "rejected": row.rejected,
                #"rejectedById": row.rejectedById,
                "rejectedByName": row.rejectedByName,
                "rejectedAt": row.rejectedAt,
                "rejectionNote": row.rejectionNote,
                "status": row.status,
                #"userId": row.createdById,
                
            }
            for row in result
        ]