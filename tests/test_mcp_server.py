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
                          "resource_forecast", "teams_allocations", "whoami"])

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


if __name__ == "__main__":
    unittest.main()
