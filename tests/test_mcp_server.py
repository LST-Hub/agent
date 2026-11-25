"""
Unit tests for the LST Ops connector: access rules, the summaries returned to Claude, and the MCP tool
wiring (in-memory client, no sign-in). Run with: python -m unittest discover tests
"""

import asyncio
import unittest
from datetime import date
from unittest import mock

from mcp_server import tools
from mcp_server.access import allowed_rules, is_allowed, user_email
from services.project_health_service import HealthFlag, ProjectHealth


class AccessTests(unittest.TestCase):
    def test_email_from_claims(self):
        self.assertEqual(user_email({"preferred_username": "PM@LST.com"}), "pm@lst.com")
        self.assertEqual(user_email({"name": "X", "upn": "a@lst.com"}), "a@lst.com")
        self.assertEqual(user_email({"name": "no email"}), "")

    def test_rules(self):
        rules = allowed_rules(" pm@lst.com , @boss.com ")
        self.assertTrue(is_allowed("PM@lst.com", rules))
        self.assertTrue(is_allowed("ceo@boss.com", rules))
        self.assertFalse(is_allowed("other@lst.com", rules))
        self.assertFalse(is_allowed("evil@notboss.com.attacker", rules))
        self.assertTrue(is_allowed("anyone@x.com", allowed_rules("*")))

    def test_empty_rules_let_nobody_in(self):
        self.assertFalse(is_allowed("pm@lst.com", allowed_rules("")))
        self.assertFalse(is_allowed("", allowed_rules("*")))


class SummaryTests(unittest.TestCase):
    def test_health_filters_by_project_and_pm(self):
        results = [
            ProjectHealth(1, "PointsBet", 7, "Varad", "In Progress",
                          flags=[HealthFlag("effort", "high", "Over estimate", "120 h of 40 h"),
                                 HealthFlag("data-quality", "low", "No end date", "")]),
            ProjectHealth(2, "LST Website", 8, "Poonam", "In Progress",
                          flags=[HealthFlag("data-quality", "low", "No estimate", "")]),
        ]
        out = tools.summarise_health(results, pm="varad")
        self.assertEqual(out["projects_with_issues"], 1)
        self.assertEqual(out["projects"][0]["worst_risk"], "high")
        self.assertEqual(len(out["projects"][0]["flags"]), 2)
        risks_only = tools.summarise_health(results, include_data_quality=False)
        self.assertEqual([p["project"] for p in risks_only["projects"]], ["PointsBet"])
        self.assertEqual(risks_only["with_data_quality_issues"], 2)

    def test_plans_in_hours_and_unstated_hours(self):
        rows = [{"userName": "Poonam K", "projectName": "NetSuite X", "seconds": 5400, "task": "UAT", "hours_stated": True},
                {"userName": "Amit", "projectName": "LST Website", "seconds": 0, "task": "", "hours_stated": False}]
        out = tools.summarise_plans(rows)
        self.assertEqual(out["people"], 2)
        self.assertEqual(out["plans"][0], {"person": "Amit", "project": "LST Website", "hours": None, "task": ""})
        self.assertEqual(out["plans"][1]["hours"], 1.5)
        self.assertEqual(tools.summarise_plans(rows, project="netsuite")["people"], 1)

    def test_hours_grouping_and_ignore_list(self):
        rows = [
            {"userId": 5, "userName": "Poonam", "projectName": "A", "date": date(2026, 11, 2), "seconds": 3600 * 6},
            {"userId": 5, "userName": "Poonam", "projectName": "B", "date": date(2026, 11, 3), "seconds": 3600 * 2},
            {"userId": 9, "userName": "Amit", "projectName": "A", "date": date(2026, 11, 2), "seconds": 3600 * 3},
            {"userId": 1, "userName": "Admin", "projectName": "A", "date": date(2026, 11, 2), "seconds": 3600 * 9},
        ]
        with mock.patch.object(tools.settings, "get_ignore_id_list", return_value=[1]):
            by_person = tools.summarise_hours(rows)
            by_day = tools.summarise_hours(rows, person="poonam", group_by="day")
            by_project = tools.summarise_hours(rows, group_by="project")
        self.assertEqual(by_person["total_hours"], 11)
        self.assertEqual(by_person["groups"][0], {"person": "Poonam", "hours": 8, "by_project": {"A": 6, "B": 2}})
        self.assertEqual([g["day"] for g in by_day["groups"]], ["2026-11-02", "2026-11-03"])
        self.assertEqual(by_project["groups"][0]["by_person"], {"Poonam": 6, "Amit": 3})
        with self.assertRaises(ValueError):
            tools.summarise_hours(rows, group_by="month")

    def test_clean_converts_seconds_and_dates(self):
        row = {"userName": "X", "date": date(2026, 11, 3), "seconds": 36000, "over": 7200,
               "projects": {"A": 36000}}
        self.assertEqual(tools.clean(row), {"userName": "X", "date": "2026-11-03", "hours": 10, "over_hours": 2,
                                            "projects_hours": {"A": 10}})

    def test_parse_day(self):
        with mock.patch.object(tools, "get_current_date", return_value=date(2026, 11, 24)):
            self.assertEqual(tools.parse_day("yesterday", None), date(2026, 11, 23))
            self.assertEqual(tools.parse_day("24-11-2026", None), date(2026, 11, 24))
            self.assertEqual(tools.parse_day(None, date(2026, 1, 1)), date(2026, 1, 1))
        with self.assertRaises(ValueError):
            tools.parse_day("next tuesday", None)

    def test_weekdays_between(self):
        days = tools.weekdays_between(date(2026, 11, 20), date(2026, 11, 24))   # Fri .. Tue
        self.assertEqual([d.day for d in days], [20, 23, 24])


class ToolWiringTests(unittest.TestCase):
    """The MCP tools through an in-memory client, with sign-in replaced by a fixed user."""

    def call(self, name, args=None, user="pm@lst.com"):
        from fastmcp import Client
        from mcp_server import server

        async def go():
            async with Client(server.build(auth=False)) as client:
                if name == "__list__":
                    return [t.name for t in await client.list_tools()]
                return await client.call_tool(name, args or {}, raise_on_error=False)

        def fake_user():
            if user is None:
                from fastmcp.exceptions import ToolError
                raise ToolError("not allowed")
            return user

        with mock.patch.object(server, "current_user", side_effect=fake_user):
            return asyncio.run(go())

    def test_tools_are_listed(self):
        self.assertEqual(sorted(self.call("__list__")),
                         ["daily_operations", "email_me_report", "logged_hours", "project_health",
                          "remind_to_post_plan", "resource_forecast", "teams_allocations", "whoami"])

    def test_whoami_returns_the_signed_in_person(self):
        result = self.call("whoami")
        self.assertFalse(result.is_error)
        self.assertEqual(result.data, {"email": "pm@lst.com"})

    def test_refused_user_gets_an_error_and_no_data(self):
        with mock.patch.object(tools, "logged_hours") as lookup:
            result = self.call("logged_hours", {"start": "2026-11-01"}, user=None)
        self.assertTrue(result.is_error)
        lookup.assert_not_called()

    def test_bad_input_is_a_readable_error(self):
        result = self.call("logged_hours", {"start": "someday"})
        self.assertTrue(result.is_error)
        self.assertIn("Could not read the date", result.content[0].text)

    def test_email_goes_to_the_asker(self):
        with mock.patch.object(tools, "email_report", return_value="Sent") as send:
            result = self.call("email_me_report", {"report": "forecast"}, user="boss@lst.com")
        self.assertFalse(result.is_error)
        send.assert_called_once_with("forecast", "boss@lst.com")

    def test_reminder_needs_mcp_senders(self):
        with mock.patch.object(tools, "remind_to_post_plan", return_value={}) as send,                 mock.patch.dict("os.environ", {"MCP_SENDERS": "boss@lst.com"}):
            refused = self.call("remind_to_post_plan", {"people": ["sachin"]}, user="pm@lst.com")
            allowed = self.call("remind_to_post_plan", {"people": ["sachin"]}, user="boss@lst.com")
        self.assertTrue(refused.is_error)
        self.assertIn("not send reminders", refused.content[0].text)
        self.assertFalse(allowed.is_error)
        send.assert_called_once_with(["sachin"], "boss@lst.com", even_if_posted=False)


class PlanReminderTests(unittest.TestCase):
    """remind_to_post_plan with fake users, Teams sender and agent store (from the timesheet reminder tests)."""

    def setUp(self):
        from types import SimpleNamespace as U
        self.users = [U(id=1, name="Sachin Ubale", firstName="Sachin", email="sachin@lst.com"),
                      U(id=2, name="Sachin Patil", firstName="Sachin", email="sp@lst.com"),
                      U(id=3, name="Poonam Kale", firstName="Poonam", email="poonam@lst.com"),
                      U(id=4, name="Varad M", firstName="Varad", email="varad@lst.com"),
                      U(id=106, name="Arpita Bhalerao", firstName="Arpita", email="arpita@lst.com")]

    def run_remind(self, people, test_to="", posted=(), store=None, ignore="106", **kw):
        from tests.test_timesheet_reminders import FakeSender, FakeStore
        sender, store = FakeSender(), store or FakeStore()
        with mock.patch.object(tools, "get_current_date", return_value=date(2026, 11, 25)),                 mock.patch("utils.date_utils.is_holiday", return_value=False),                 mock.patch("agents.teams_plan_check_agent._office_emails", return_value={}),                 mock.patch("services.teams_plans.load_plans_safe", return_value=[{"userId": i} for i in posted]),                 mock.patch.object(tools.settings, "teams_dm_test_recipient", test_to),                 mock.patch.object(tools.settings, "ignore_ids", ignore):
            out = tools.remind_to_post_plan(people, "varad@lst.com", users=self.users, store_factory=lambda: store,
                                            sender_factory=lambda _: sender, **kw)
        return out, sender, store

    def test_resolve_person(self):
        self.assertEqual(tools.resolve_person("poonam", self.users).id, 3)
        self.assertEqual(tools.resolve_person("SACHIN UBALE", self.users).id, 1)
        self.assertEqual(tools.resolve_person("sp@lst.com", self.users).id, 2)
        with self.assertRaisesRegex(ValueError, "several people"):
            tools.resolve_person("sachin", self.users)
        with self.assertRaisesRegex(ValueError, "No active"):
            tools.resolve_person("nobody", self.users)

    def test_sends_the_fixed_text_without_who_asked(self):
        out, sender, _ = self.run_remind(["poonam"])
        self.assertEqual(sender.sent[0][0], "poonam@lst.com")
        self.assertIn("Hi Poonam, your work plan for today (Wed 25-11-2026) is not posted", sender.sent[0][1])
        self.assertNotIn("Varad", sender.sent[0][1])
        self.assertEqual(out["results"], [{"person": "Poonam Kale", "status": "sent"}])

    def test_test_mode_goes_to_the_tester(self):
        out, sender, _ = self.run_remind(["poonam", "sachin ubale"], test_to="me@lst.com")
        self.assertEqual([s[0] for s in sender.sent], ["me@lst.com", "me@lst.com"])
        self.assertIn("[TEST, meant for Sachin Ubale]", sender.sent[1][1])
        self.assertTrue(out["test_mode"])

    def test_once_per_day_and_skips_posted(self):
        out, sender, store = self.run_remind(["poonam", "sachin ubale"], posted=[1])
        self.assertEqual(len(sender.sent), 1)
        self.assertIn("already saved", out["results"][1]["status"])
        again, sender2, _ = self.run_remind(["poonam"], store=store)
        self.assertEqual(sender2.sent, [])
        self.assertIn("already reminded", again["results"][0]["status"])
        forced, sender3, _ = self.run_remind(["sachin ubale"], posted=[1], even_if_posted=True)
        self.assertEqual(len(sender3.sent), 1)

    def test_nothing_sent_when_a_name_is_wrong_or_ignored(self):
        for people, error in ((["poonam", "nobody"], "No active"), (["poonam", "arpita"], "ignore list")):
            with self.assertRaisesRegex(ValueError, error):
                self.run_remind(people)

    def test_limits(self):
        with self.assertRaisesRegex(ValueError, "At most"):
            tools.remind_to_post_plan(["a"] * 11, "x@lst.com", users=self.users)


if __name__ == "__main__":
    unittest.main()
