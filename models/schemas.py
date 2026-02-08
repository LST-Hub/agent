"""
Core Data Models corresponding to the Database Schema.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from enum import Enum

class TimesheetStatus(Enum):
    DRAFT = 'Draft'
    PENDING = 'Pending'
    APPROVED = 'Approved'
    REJECTED = 'Rejected'

class RetractStatus(Enum):
    NOT_SUBMITTED = 'NotSubmitted'
    PENDING = 'Pending'
    APPROVED = 'Approved'
    REJECTED = 'Rejected'

class UserType(Enum):
    CONTRACTOR = 'Contractor'
    EMPLOYEE = 'Employee'

class RepetationType(Enum):
    DONT_REPEAT = 'DontRepeat'
    DAILY = 'Daily'
    WEEKLY = 'Weekly'
    MONTHLY = 'Monthly'

@dataclass
class Timesheet:
    globalId: int
    id: int
    date: date
    duration: int
    description: Optional[str]
    taskId: int
    projectId: int
    createdById: int
    approved: bool
    approvedById: Optional[int]
    approvedAt: Optional[datetime]
    rejected: bool
    rejectedById: Optional[int]
    rejectedAt: Optional[datetime]
    rejectionNote: Optional[str]
    submittedForApproval: bool
    status: TimesheetStatus
    createdAt: datetime
    updatedAt: datetime
    workspaceId: int
    ticket: Optional[str]
    resourceAllocationId: Optional[int]
    retractApproval: bool
    retractReason: Optional[str]
    retractStatus: RetractStatus
    retractrejectionNote: Optional[str]

@dataclass
class Project:
    globalId: int
    id: int
    name: str
    description: Optional[str]
    clientId: int
    pmId: int
    statusId: int
    priorityId: int
    startDate: date
    estimatedTime: int
    estimatedEndDate: Optional[date]
    actualEndDate: Optional[date]
    canSupervisorApproveTime: bool
    active: bool
    createdById: int
    createdAt: datetime
    updatedAt: datetime
    workspaceId: int
    ticket: bool
    billable: bool
    projectTypeId: int

@dataclass
class Task:
    globalId: int
    id: int
    name: str
    description: Optional[str]
    projectId: int
    statusId: int
    priorityId: int
    startDate: date
    estimatedTime: int
    estimatedEndDate: Optional[date]
    actualEndDate: Optional[date]
    active: bool
    billable: bool
    createdById: int
    createdAt: datetime
    updatedAt: datetime
    workspaceId: int
    taskTypeId: int

@dataclass
class User:
    globalId: str
    id: int
    name: str
    email: str
    firstName: Optional[str]
    lastName: Optional[str]
    designation: Optional[str]
    supervisorId: Optional[int]
    type: UserType
    roleId: int
    canBePM: bool
    canBeSupervisor: bool
    departmentId: int
    workspaceId: int
    active: bool
    deleted: bool
    billable: bool
    billableCost: float
    billableRate: float
    hiringDate: Optional[date]
    timeZone: Optional[int]

@dataclass
class ResourceAllocation:
    globalId: int
    id: int
    repeatId: Optional[str]
    projectId: int
    taskId: Optional[int]
    allocatedUserId: int
    date: date
    duration: int
    startDate: date
    endDate: date
    repetationType: RepetationType
    createdById: int
    createdAt: datetime
    updatedAt: datetime
    workspaceId: int
    userGlobalId: str
    idleHours: int
