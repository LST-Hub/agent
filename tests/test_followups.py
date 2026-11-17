"""
Unit tests for PM follow-ups and escalation. Run with: python -m unittest discover tests
"""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import database.agent_store as agent_store
from database.agent_store import FollowUp, NotificationLog, get_agent_session
from services.followup_service import (
    plan_followups, findings_from_health, working_days_between, escalation_due_on,
)
from services.project_health_service import ProjectHealth, HealthFlag

# 26-10-2026 is a Monday with no company holidays that week
MON, TUE, WED, THU, FRI = (date(2026, 10, d) for d in range(26, 31))
NEXT_MON = date(2026, 11, 2)


def finding(project_id=1, code="past_end", **overrides):
    f = {"project_id": project_id, "project_name": f"Project {project_id}", "pm_id": 7, "pm_name": "Rutuja L",
         "pm_email": "pm@example.com", "code": code, "title": "Past estimated end date",
         "evidence": "Estimated end 01-10-2026.", "severity": "high"}
    f.update(overrides)
    return f


def followup(project_id=1, code="past_end", status="open", emails_sent=1, last_sent_on=MON):
    return SimpleNamespace(project_id=project_id, finding_code=code, status=status,
                           emails_sent=emails_sent, last_sent_on=last_sent_on)


class PlanTests(unittest.TestCase):
    def test_new_finding_opens_followup(self):
        plan = plan_followups([], [finding()], MON, reminder_days=2)
        self.assertEqual([f["project_id"] for f in plan.new], [1])

    def test_nothing_due_before_interval(self):
        plan = plan_followups([followup()], [finding()], TUE, reminder_days=2)
        self.assertEqual((plan.new, plan.remind, plan.escalate, plan.resolve), ([], [], [], []))

    def test_reminder_after_interval(self):
        fu = followup(emails_sent=1)
        self.assertEqual(plan_followups([fu], [finding()], WED, reminder_days=2).remind, [fu])

    def test_escalate_after_second_email(self):
        fu = followup(emails_sent=2, last_sent_on=WED)
        self.assertEqual(plan_followups([fu], [finding()], FRI, reminder_days=2).escalate, [fu])

    def test_escalated_followup_gets_no_more_emails(self):
        fu = followup(status="escalated", emails_sent=2, last_sent_on=MON)
        plan = plan_followups([fu], [finding()], NEXT_MON, reminder_days=2)
        self.assertEqual((plan.new, plan.remind, plan.escalate), ([], [], []))

    def test_fixed_finding_resolves_at_any_stage(self):
        items = [followup(1), followup(2, status="escalated"), followup(3, status="closed")]
        self.assertEqual(plan_followups(items, [], WED, reminder_days=2).resolve, items)

    def test_closed_followup_suppresses_new_one(self):
        plan = plan_followups([followup(status="closed")], [finding()], NEXT_MON, reminder_days=2)
        self.assertEqual((plan.new, plan.remind, plan.escalate, plan.resolve), ([], [], [], []))

    def test_same_project_different_rule_is_separate(self):
        plan = plan_followups([followup(code="past_end")], [finding(code="past_end"), finding(code="no_estimate")],
                              TUE, reminder_days=2)
        self.assertEqual([f["code"] for f in plan.new], ["no_estimate"])


class WorkingDayTests(unittest.TestCase):
    def test_weekend_not_counted(self):
        self.assertEqual(working_days_between(FRI, NEXT_MON), 1)

    def test_holiday_not_counted(self):
        # 10-11 and 11-11-2026 are company holidays
        self.assertEqual(working_days_between(date(2026, 11, 9), date(2026, 11, 12)), 1)

    def test_escalation_date_skips_weekend(self):
        self.assertEqual(escalation_due_on(THU, 2), NEXT_MON)


class FindingsFromHealthTests(unittest.TestCase):
    def _health(self, pm_id=7, flags=None):
        return ProjectHealth(1, "P", pm_id, "PM", "Inprogress", flags=flags or [], pm_email="pm@example.com")

    def test_only_followup_rules_are_used(self):
        flags = [HealthFlag("schedule", "high", "Past", "e", "past_end"),
                 HealthFlag("activity", "low", "Inactive", "e", "inactive")]
        self.assertEqual([f["code"] for f in findings_from_health([self._health(flags=flags)])], ["past_end"])

    def test_projects_without_pm_or_ignored_pm_skipped(self):
        flags = [HealthFlag("schedule", "high", "Past", "e", "past_end")]
        results = [self._health(pm_id=None, flags=flags), self._health(pm_id=9, flags=flags)]
        self.assertEqual(findings_from_health(results, ignore_pm_ids={9}), [])


class LifecycleTests(unittest.TestCase):
    """End-to-end runs of FollowUpAgent against an in-memory SQLite store."""

    def setUp(self):
        agent_store._session_factory = None
        get_agent_session("sqlite://")
        self.findings = [finding()]
        self.send = MagicMock(return_value=True)

        patches = [
            patch("agents.followup_agent.get_db_session", lambda: iter([MagicMock()])),
            patch("agents.followup_agent.ProjectHealthService"),
            patch("agents.followup_agent.findings_from_health", lambda results, ignore_pm_ids: list(self.findings)),
            patch("agents.followup_agent.EmailService", return_value=MagicMock(send_alert=self.send)),
            patch("agents.followup_agent.EmailResolver", return_value=MagicMock(get_office_email=lambda _id: None)),
            patch("agents.followup_agent.settings.followup_reminder_days", 2),
            patch("agents.followup_agent.settings.followup_test_recipient", ""),
            patch("agents.followup_agent.settings.pm_email", "boss@example.com"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        agent_store._session_factory = None

    def _run(self, day):
        from agents.followup_agent import FollowUpAgent
        with patch("agents.followup_agent.get_current_date", return_value=day):
            FollowUpAgent().run()

    def _followups(self):
        store = get_agent_session()
        try:
            return store.query(FollowUp).order_by(FollowUp.id).all()
        finally:
            store.close()

    def _recipients(self):
        return [c.kwargs["to_email"] for c in self.send.call_args_list]

    def test_full_lifecycle_two_emails_then_escalation_then_resolution(self):
        self._run(MON)
        [fu] = self._followups()
        self.assertEqual((fu.status, fu.emails_sent, fu.first_sent_on), ("open", 1, MON))
        self.assertEqual(self._recipients(), [["pm@example.com"]])

        self._run(TUE)                       # 1 working day: nothing due
        self.assertEqual(self.send.call_count, 1)

        self._run(WED)                       # 2 working days: reminder
        [fu] = self._followups()
        self.assertEqual((fu.emails_sent, fu.last_sent_on), (2, WED))
        self.assertIn("Reminder", self.send.call_args.kwargs["subject"])

        self._run(FRI)                       # 2 more working days: escalate to management
        [fu] = self._followups()
        self.assertEqual((fu.status, fu.escalated_on), ("escalated", FRI))
        self.assertEqual(self._recipients()[-1], ["boss@example.com"])

        self._run(NEXT_MON)                  # escalated: no more emails
        self.assertEqual(self.send.call_count, 3)

        self.findings = []                   # PM fixed the data
        self._run(date(2026, 11, 3))
        [fu] = self._followups()
        self.assertEqual(fu.status, "resolved")

    def test_one_email_per_pm_for_many_items(self):
        self.findings = [finding(1), finding(2), finding(3, code="no_estimate")]
        self._run(MON)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(len(self._followups()), 3)

    def test_failed_email_is_retried_next_run(self):
        self.send.return_value = False
        self._run(MON)
        self.assertEqual(self._followups(), [])
        store = get_agent_session()
        self.assertEqual(store.query(NotificationLog).one().success, False)
        store.close()

        self.send.return_value = True
        self._run(TUE)
        [fu] = self._followups()
        self.assertEqual((fu.emails_sent, fu.first_sent_on), (1, TUE))

    def test_manual_close_stops_followup_until_issue_clears(self):
        from agents.followup_agent import close_followup
        self._run(MON)
        [fu] = self._followups()
        close_followup(fu.id, "Ongoing retainer, end date intentionally open")

        self._run(WED)
        self._run(FRI)
        self.assertEqual(self.send.call_count, 1)
        [fu] = self._followups()
        self.assertEqual(fu.status, "closed")

    def test_pilot_mode_redirects_every_email(self):
        with patch("agents.followup_agent.settings.followup_test_recipient", "pilot@example.com"):
            self._run(MON)
        self.assertEqual(self._recipients(), [["pilot@example.com"]])
        self.assertIn("pm@example.com", self.send.call_args.kwargs["body"])


if __name__ == "__main__":
    unittest.main()
