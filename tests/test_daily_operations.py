"""
Unit tests for daily operations checks. Run with: python -m unittest discover tests
"""

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from services.daily_operations_service import (
    find_overtime, find_over_allocations, find_allocation_variances, working_days_back,
)
from services.timesheet_service import TimesheetService

H = 3600
MON, TUE = date(2026, 10, 26), date(2026, 10, 27)


def logged(user=1, project=10, d=MON, hours=4, name="Asha", project_name="Client A"):
    return {"userId": user, "userName": name, "projectId": project, "projectName": project_name,
            "date": d, "seconds": hours * H}


def alloc(user=1, project=10, d=MON, hours=4, name="Asha", project_name="Client A"):
    return dict(logged(user, project, d, hours, name, project_name), pmName="PM One")


class OvertimeTests(unittest.TestCase):
    def test_exactly_eight_hours_is_not_overtime(self):
        self.assertEqual(find_overtime([logged(hours=4), logged(project=11, hours=4)], [MON]), [])

    def test_split_day_above_capacity(self):
        rows = [logged(hours=6), logged(project=11, hours=3.5, project_name="Client B")]
        [r] = find_overtime(rows, [MON])
        self.assertEqual(r["seconds"], 9.5 * H)
        self.assertEqual(r["over"], 1.5 * H)
        self.assertEqual(r["projects"], {"Client A": 6 * H, "Client B": 3.5 * H})

    def test_days_outside_window_ignored(self):
        self.assertEqual(find_overtime([logged(d=TUE, hours=10)], [MON]), [])

    def test_datetime_dates_are_handled(self):
        self.assertEqual(len(find_overtime([logged(d=datetime(2026, 10, 26), hours=9)], [MON])), 1)

    def test_leave_does_not_count_towards_overtime(self):
        rows = [logged(hours=8.5, project=99, project_name="Leave")]
        self.assertEqual(find_overtime(rows, [MON]), [])

    def test_half_day_leave_plus_work_is_not_overtime(self):
        rows = [logged(hours=4.5, project=99, project_name="Half Day Leave"), logged(hours=4)]
        self.assertEqual(find_overtime(rows, [MON]), [])

    def test_users_are_separate(self):
        rows = [logged(user=1, hours=5), logged(user=2, hours=5, name="Ravi")]
        self.assertEqual(find_overtime(rows, [MON]), [])


class OverAllocationTests(unittest.TestCase):
    def test_over_allocated_day(self):
        [r] = find_over_allocations([alloc(hours=7), alloc(project=11, hours=2)], [MON])
        self.assertEqual(r["over"], 1 * H)

    def test_full_allocation_is_fine(self):
        self.assertEqual(find_over_allocations([alloc(hours=8)], [MON]), [])


class AllocationVarianceTests(unittest.TestCase):
    def test_allocated_project_not_logged_while_other_work_logged(self):
        [v] = find_allocation_variances([alloc(hours=4)], [logged(project=11, hours=8)], [MON])
        self.assertEqual((v["allocated"], v["logged"], v["diff"]), (4 * H, 0, -4 * H))

    def test_no_timesheet_at_all_is_missing_data_not_variance(self):
        self.assertEqual(find_allocation_variances([alloc(hours=4)], [], [MON]), [])

    def test_logged_more_than_allocated(self):
        [v] = find_allocation_variances([alloc(hours=4)], [logged(hours=6)], [MON])
        self.assertEqual(v["diff"], 2 * H)

    def test_small_difference_ignored(self):
        self.assertEqual(find_allocation_variances([alloc(hours=4)], [logged(hours=3.5)], [MON]), [])

    def test_unallocated_work_is_not_a_variance(self):
        self.assertEqual(find_allocation_variances([], [logged(hours=8)], [MON]), [])


class WorkingDaysTests(unittest.TestCase):
    def test_skips_weekend(self):
        # Monday 26-10-2026 looking back 2 working days -> previous Friday and Monday
        self.assertEqual(working_days_back(MON, 2), [date(2026, 10, 23), MON])

    def test_skips_company_holiday(self):
        # 26-01-2026 (Republic Day, a Monday) is in data/holidays.json
        self.assertNotIn(date(2026, 1, 26), working_days_back(date(2026, 1, 27), 3))


class OvertimeBugRegressionTests(unittest.TestCase):
    """get_overtime_users used to compare seconds with hours, flagging anyone with > 8 seconds."""

    def _service(self, durations, rejected=None):
        service = TimesheetService.__new__(TimesheetService)
        service.ignore_ids = []
        user = SimpleNamespace(id=1)
        service.user_repo = MagicMock(get_active_users=MagicMock(return_value=[user]))
        rejected = rejected or [False] * len(durations)
        service.timesheet_repo = MagicMock(get_by_user_and_date=MagicMock(return_value=[
            SimpleNamespace(duration=d, rejected=r) for d, r in zip(durations, rejected)
        ]))
        return service

    def test_normal_day_is_not_overtime(self):
        self.assertEqual(self._service([4 * H, 4 * H]).get_overtime_users(MON), [])

    def test_long_day_is_overtime_in_seconds(self):
        [r] = self._service([5 * H, 4 * H]).get_overtime_users(MON)
        self.assertEqual(r["overtime_hours"], 1 * H)

    def test_rejected_time_not_counted(self):
        self.assertEqual(self._service([5 * H, 4 * H], rejected=[False, True]).get_overtime_users(MON), [])


if __name__ == "__main__":
    unittest.main()
