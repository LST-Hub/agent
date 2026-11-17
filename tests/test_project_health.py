"""
Unit tests for project health rules. Run with: python -m unittest discover tests
"""

import unittest
from datetime import date, datetime
from services.project_health_service import evaluate_project, is_leave_project, ProjectHealth, HealthFlag

TODAY = date(2026, 11, 3)
H = 3600


def project(**overrides):
    base = {
        "id": 1, "name": "Client A - NetSuite", "pmId": 7,
        "startDate": date(2026, 9, 1), "estimatedTime": 100 * H,
        "estimatedEndDate": date(2026, 12, 31), "actualEndDate": None,
        "workedSeconds": 10 * H, "lastLoggedDate": date(2026, 11, 2),
    }
    base.update(overrides)
    return base


def task(**overrides):
    base = {"id": 1, "name": "Data Migration", "projectId": 1, "estimatedTime": 10 * H,
            "estimatedEndDate": date(2026, 12, 1), "actualEndDate": None, "workedSeconds": 2 * H}
    base.update(overrides)
    return base


def titles(flags):
    return [f.title for f in flags]


class EvaluateProjectTests(unittest.TestCase):
    def test_healthy_project_has_no_flags(self):
        self.assertEqual(evaluate_project(project(), [task()], TODAY), [])

    def test_effort_over_estimate_is_high(self):
        flags = evaluate_project(project(workedSeconds=120 * H), [], TODAY)
        self.assertEqual(flags[0].title, "Effort over estimate")
        self.assertEqual(flags[0].severity, "high")
        self.assertIn("120%", flags[0].evidence)

    def test_effort_nearing_estimate_is_medium(self):
        flags = evaluate_project(project(workedSeconds=95 * H), [], TODAY)
        self.assertEqual([(f.title, f.severity) for f in flags], [("Effort nearing estimate", "medium")])

    def test_missing_estimate_is_data_quality(self):
        flags = evaluate_project(project(estimatedTime=0), [], TODAY)
        self.assertEqual(flags[0].category, "data-quality")
        self.assertEqual(flags[0].title, "No estimated hours")

    def test_estimate_unit_conversion(self):
        # 100 hours expressed in hours: 95 h worked is 95%, not an overrun
        flags = evaluate_project(project(estimatedTime=100, workedSeconds=95 * H), [], TODAY, estimate_unit_seconds=H)
        self.assertEqual(titles(flags), ["Effort nearing estimate"])

    def test_fixed_billing_past_end_date_is_high(self):
        flags = evaluate_project(project(projectTypeName="Fixed billing", estimatedEndDate=date(2025, 3, 31)), [], TODAY)
        self.assertEqual([(f.title, f.severity) for f in flags], [("Past estimated end date", "high")])

    def test_other_type_recently_past_end_date_is_medium(self):
        flags = evaluate_project(project(projectTypeName="Time and Material", estimatedEndDate=date(2026, 10, 31)), [], TODAY)
        self.assertEqual([(f.title, f.severity) for f in flags], [("Past estimated end date", "medium")])
        self.assertIn("3 day(s) ago", flags[0].evidence)

    def test_other_type_long_past_end_date_is_data_quality(self):
        flags = evaluate_project(project(projectTypeName=None, estimatedEndDate=date(2025, 3, 31)), [], TODAY)
        self.assertEqual([(f.title, f.category) for f in flags], [("End date not updated", "data-quality")])

    def test_end_date_as_datetime_is_handled(self):
        flags = evaluate_project(project(estimatedEndDate=datetime(2026, 10, 31, 0, 0)), [], TODAY)
        self.assertEqual(titles(flags), ["Past estimated end date"])

    def test_missing_end_date_is_data_quality(self):
        flags = evaluate_project(project(estimatedEndDate=None), [], TODAY)
        self.assertEqual(titles(flags), ["No estimated end date"])

    def test_overdue_tasks_ignore_finished_ones(self):
        tasks = [
            task(id=1, name="Late", estimatedEndDate=date(2026, 10, 1)),
            task(id=2, name="Done", estimatedEndDate=date(2026, 10, 1), actualEndDate=date(2026, 10, 2)),
            task(id=3, name="Future", estimatedEndDate=date(2026, 12, 1)),
        ]
        flags = evaluate_project(project(), tasks, TODAY)
        self.assertEqual(titles(flags), ["1 overdue task(s)"])
        self.assertIn("Late", flags[0].evidence)
        self.assertNotIn("Done", flags[0].evidence)

    def test_overdue_task_list_is_truncated(self):
        tasks = [task(id=i, name=f"T{i}", estimatedEndDate=date(2026, 10, 1)) for i in range(7)]
        flags = evaluate_project(project(), tasks, TODAY)
        self.assertIn("and 2 more", flags[0].evidence)

    def test_task_over_estimate(self):
        flags = evaluate_project(project(), [task(workedSeconds=15 * H)], TODAY)
        self.assertEqual(titles(flags), ["1 task(s) over estimate"])
        self.assertIn("15.0 h vs 10.0 h", flags[0].evidence)

    def test_task_without_estimate_is_not_overrun(self):
        flags = evaluate_project(project(), [task(estimatedTime=0, workedSeconds=15 * H)], TODAY)
        self.assertEqual(flags, [])

    def test_inactive_project(self):
        flags = evaluate_project(project(lastLoggedDate=date(2026, 10, 1)), [], TODAY)
        self.assertEqual(titles(flags), ["No time logged in 14 days"])

    def test_never_logged_project(self):
        flags = evaluate_project(project(lastLoggedDate=None, workedSeconds=0), [], TODAY)
        self.assertIn("never", flags[0].evidence)

    def test_halted_project_skips_schedule_and_activity(self):
        flags = evaluate_project(
            project(statusName="Halted", estimatedEndDate=date(2026, 10, 1), lastLoggedDate=date(2026, 9, 1)),
            [task(estimatedEndDate=date(2026, 10, 1))], TODAY)
        self.assertEqual(flags, [])

    def test_halted_project_still_reports_effort_overrun(self):
        flags = evaluate_project(project(statusName="Halted", workedSeconds=120 * H), [], TODAY)
        self.assertEqual(titles(flags), ["Effort over estimate"])

    def test_recently_started_project_is_not_inactive(self):
        flags = evaluate_project(project(startDate=date(2026, 10, 30), lastLoggedDate=None, workedSeconds=0), [], TODAY)
        self.assertEqual(flags, [])


class HelperTests(unittest.TestCase):
    def test_leave_projects_detected(self):
        self.assertTrue(is_leave_project("Sick Leave"))
        self.assertTrue(is_leave_project(" Public Holiday "))
        self.assertFalse(is_leave_project("Client A - NetSuite"))

    def test_overall_severity_is_worst_flag(self):
        health = ProjectHealth(1, "P", 7, "PM", "Active", flags=[
            HealthFlag("activity", "low", "a", ""), HealthFlag("effort", "high", "b", ""),
        ])
        self.assertEqual(health.severity, "high")

    def test_data_quality_flags_do_not_set_severity(self):
        health = ProjectHealth(1, "P", 7, "PM", "Active", flags=[HealthFlag("data-quality", "low", "a", "")])
        self.assertIsNone(health.severity)
        self.assertEqual(len(health.data_quality_flags), 1)


if __name__ == "__main__":
    unittest.main()
