"""
Unit tests for the 15-working-day forecast. Run with: python -m unittest discover tests
"""

import json
import os
import tempfile
import unittest
from datetime import date
from services.forecast_service import (
    build_run_rates, build_forecast, working_days_forward, is_forecastable_project,
    load_skills, load_roles, update_skills_template, parse_skill_cell, import_skills_csv, UNMAPPED_SKILL,
)

H = 3600
# History: Mon 19-10 .. Fri 23-10-2026 has a company holiday on Tue 20-10, so 4 working days
HISTORY = [date(2026, 10, 19), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
# Forecast: Mon 26-10 .. Fri 30-10-2026, 5 working days
DAYS = [date(2026, 10, d) for d in range(26, 31)]
PROJECTS = {
    10: {"id": 10, "name": "Client A", "statusName": "Inprogress", "actualEndDate": None, "billable": 1},
    11: {"id": 11, "name": "Client B", "statusName": "Inprogress", "actualEndDate": None, "billable": 1},
    12: {"id": 12, "name": "Old Project", "statusName": "Completed", "actualEndDate": None, "billable": 1},
}
NO_FLOATERS = lambda d, uid: False  # noqa: E731


def logged(user=1, project=10, d=HISTORY[0], hours=8, project_name=None, name="Asha"):
    return {"userId": user, "userName": name, "projectId": project,
            "projectName": project_name or PROJECTS.get(project, {}).get("name", "Idle"), "date": d, "seconds": hours * H}


def full_history(user=1, project=10, hours=8, **kw):
    return [logged(user, project, d, hours, **kw) for d in HISTORY]


class WindowTests(unittest.TestCase):
    def test_fifteen_working_days_skip_weekends_and_holidays(self):
        days = working_days_forward(date(2026, 11, 2), 15)
        self.assertEqual(len(days), 15)
        self.assertTrue(all(d.weekday() < 5 for d in days))
        self.assertNotIn(date(2026, 11, 10), days)  # company holiday
        self.assertEqual(days[-1], date(2026, 11, 24))


class RunRateTests(unittest.TestCase):
    def test_average_over_available_days(self):
        rows = full_history(hours=6) + full_history(project=11, hours=2)
        rates, names = build_run_rates(rows, HISTORY, PROJECTS, NO_FLOATERS)
        self.assertEqual(rates[1], {10: 6 * H, 11: 2 * H})
        self.assertEqual(names, {1: "Asha"})

    def test_full_leave_days_excluded_from_average(self):
        rows = [logged(d=d, hours=8) for d in HISTORY[:2]] + \
               [logged(project=99, project_name="Leave", d=d, hours=8) for d in HISTORY[2:]]
        rates, _ = build_run_rates(rows, HISTORY, PROJECTS, NO_FLOATERS)
        self.assertEqual(rates[1][10], 8 * H)  # 16 h over 2 available days, not 4

    def test_idle_and_leave_are_not_demand(self):
        rows = full_history(hours=4) + full_history(project=None, hours=4, project_name="Idle")
        rates, _ = build_run_rates(rows, HISTORY, PROJECTS, NO_FLOATERS)
        self.assertEqual(rates[1], {10: 4 * H})

    def test_completed_projects_dropped_but_person_kept(self):
        rates, names = build_run_rates(full_history(project=12), HISTORY, PROJECTS, NO_FLOATERS)
        self.assertEqual(rates[1], {})
        self.assertIn(1, names)

    def test_too_little_project_work_is_not_delivery_staff(self):
        rates, names = build_run_rates([logged(hours=8)], HISTORY, PROJECTS, NO_FLOATERS)
        self.assertEqual((rates, names), ({}, {}))

    def test_inactive_project_is_not_forecastable(self):
        self.assertFalse(is_forecastable_project(None))
        self.assertFalse(is_forecastable_project({"statusName": "Halted", "actualEndDate": None}))
        self.assertTrue(is_forecastable_project(PROJECTS[10]))


class ForecastTests(unittest.TestCase):
    def _forecast(self, rates, allocations=(), skills=None, floater=NO_FLOATERS, names=None):
        names = {1: "Asha"} if names is None else names
        return build_forecast(DAYS, HISTORY, rates, dict(names), list(allocations),
                              PROJECTS, skills or {}, floater)

    def test_projection_fills_window(self):
        f = self._forecast({1: {10: 6 * H}})
        [p] = f.people
        self.assertEqual((p.capacity, p.confirmed, p.projected, p.free), (40 * H, 0, 30 * H, 10 * H))
        self.assertEqual(p.utilisation, 0.75)

    def test_confirmed_allocation_replaces_projection_same_project_day(self):
        alloc = {"userId": 1, "userName": "Asha", "projectId": 10, "projectName": "Client A", "date": DAYS[0], "seconds": 8 * H}
        [p] = self._forecast({1: {10: 6 * H}}, [alloc]).people
        self.assertEqual((p.confirmed, p.projected), (8 * H, 24 * H))

    def test_allocation_on_new_project_adds_to_projection(self):
        alloc = {"userId": 1, "userName": "Asha", "projectId": 77, "projectName": "New Client", "date": DAYS[0], "seconds": 4 * H}
        f = self._forecast({1: {10: 6 * H}}, [alloc])
        [p] = f.people
        self.assertEqual((p.confirmed, p.projected), (4 * H, 30 * H))
        self.assertIn("New Client", p.projects)

    def test_allocated_person_without_history_is_included(self):
        alloc = {"userId": 2, "userName": "Ravi", "projectId": 10, "projectName": "Client A", "date": DAYS[0], "seconds": 8 * H}
        f = self._forecast({}, [alloc], names={})
        [p] = f.people
        self.assertEqual((p.name, p.capacity, p.confirmed), ("Ravi", 40 * H, 8 * H))

    def test_floater_removes_capacity_and_projection(self):
        floater = lambda d, uid: d == DAYS[0]  # noqa: E731
        [p] = self._forecast({1: {10: 8 * H}}, floater=floater).people
        self.assertEqual((p.capacity, p.projected), (32 * H, 32 * H))

    def test_overload_shows_negative_free(self):
        [p] = self._forecast({1: {10: 6 * H, 11: 4 * H}}).people
        self.assertEqual(p.free, -10 * H)
        self.assertEqual(p.utilisation, 1.25)

    def test_daily_totals(self):
        f = self._forecast({1: {10: 6 * H}})
        self.assertEqual(len(f.daily), 5)
        self.assertEqual((f.daily[0]["capacity"], f.daily[0]["projected"]), (8 * H, 6 * H))

    def test_skill_rollup_uses_primary_skill_once(self):
        rates = {1: {10: 8 * H}, 2: {11: 4 * H}, 3: {11: 2 * H}}
        names = {1: "Asha", 2: "Ravi", 3: "Meera"}
        f = self._forecast(rates, skills={1: ["NetSuite", "QA"], 2: ["NetSuite"]}, names=names)
        by_skill = {s["skill"]: s for s in f.skills}
        self.assertEqual(set(by_skill), {"NetSuite", UNMAPPED_SKILL})
        self.assertEqual((by_skill["NetSuite"]["people"], by_skill["NetSuite"]["capacity"]), (2, 80 * H))
        self.assertEqual(f.skills_mapped, 2)

    def test_role_rollup(self):
        rates = {1: {10: 8 * H}, 2: {11: 4 * H}, 3: {11: 2 * H}}
        names = {1: "Asha", 2: "Ravi", 3: "Meera"}
        f = build_forecast(DAYS, HISTORY, rates, names, [], PROJECTS, {}, NO_FLOATERS,
                           roles={1: "NetSuite Developer", 2: "NetSuite Developer"})
        by_role = {r["role"]: r for r in f.roles}
        self.assertEqual((by_role["NetSuite Developer"]["people"], by_role["NetSuite Developer"]["projected"]),
                         (2, 60 * H))
        self.assertEqual(by_role[UNMAPPED_SKILL]["people"], 1)
        self.assertEqual(f.roles_mapped, 2)

    def test_project_totals(self):
        f = self._forecast({1: {10: 6 * H}, 2: {10: 2 * H}}, names={1: "Asha", 2: "Ravi"})
        [proj] = f.projects
        self.assertEqual((proj["name"], proj["projected"], len(proj["people"])), ("Client A", 40 * H, 2))


class SkillsFileTests(unittest.TestCase):
    def test_template_adds_people_and_keeps_existing_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "skills.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"employees": {"1": {"name": "Asha", "skills": ["NetSuite"]}}}, f)
            self.assertEqual(update_skills_template({1: "Asha", 2: "Ravi"}, path), 1)
            self.assertEqual(load_skills(path), {1: ["NetSuite"], 2: []})

    def test_ignored_users_are_dropped(self):
        from services.forecast_service import exclude_users
        rows = [logged(user=1), logged(user=2, name="Ravi"), logged(user=3, name="Meera")]
        self.assertEqual([r["userId"] for r in exclude_users(rows, [2, 99])], [1, 3])

    def test_missing_file_means_nothing_mapped(self):
        self.assertEqual(load_skills(os.path.join(tempfile.gettempdir(), "does-not-exist.json")), {})


class SkillSheetImportTests(unittest.TestCase):
    def test_comma_separated_cell(self):
        self.assertEqual(parse_skill_cell(" Procurement, Dashboards,Saved Searches, SuiteScript, suitescript, "),
                         ["Procurement", "Dashboards", "Saved Searches", "SuiteScript"])

    def test_outline_cell_keeps_items_not_headings(self):
        cell = ("Core Technical / Development Modules\n \tSuiteScript 2.x\n\tRESTlets\n\n"
                "Integration Modules\n\tShopify Integration\n\tMicrosoft\n\t\tSharePoint\n\t\tTeams / Graph API")
        self.assertEqual(parse_skill_cell(cell),
                         ["SuiteScript 2.x", "RESTlets", "Shopify Integration", "SharePoint", "Teams / Graph API"])

    def test_import_matches_names_and_reports_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_path = os.path.join(tmp, "skills.json")
            with open(skills_path, "w", encoding="utf-8") as f:
                json.dump({"employees": {
                    "9": {"name": "Prakash Bagul", "skills": []},
                    "47": {"name": "saurabh chaugule", "skills": []},
                    "8": {"name": "Saurabh  More", "skills": []},
                    "5": {"name": "Arpita Bhalerao", "skills": ["Custom Development"]},
                }}, f)
            csv_path = os.path.join(tmp, "sheet.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                f.write("Sr No,Resource Name,Role,Skill Sets,Expertise Level\n"
                        '2,Prakash,NetSuite Solution Architect,"SuiteScript, NetSuite, RESTlets",Expert\n'
                        ',Saurabh Chaugule,NetSuite Developer,"Procurement",Intermediate to Advanced\n'
                        ',Saurabh,NetSuite Developer,"Dashboards",Beginner\n'
                        ',Shivam ,NetSuite Functional Developer,"Procurement",Beginner \n'
                        ",,,,\n")
            result = import_skills_csv(csv_path, "NetSuite", skills_path)

            self.assertEqual([(n, uid) for n, uid, _ in result.updated], [("Prakash", 9), ("Saurabh Chaugule", 47)])
            self.assertEqual([n for n, _ in result.not_matched], ["Saurabh", "Shivam"])
            self.assertIn("could be", result.not_matched[0][1])
            skills = load_skills(skills_path)
            self.assertEqual(skills[9], ["NetSuite", "SuiteScript", "RESTlets"])   # practice first, not repeated
            self.assertEqual(skills[5], ["Custom Development"])                    # untouched
            self.assertEqual(skills[8], [])                                        # ambiguous name not guessed
            self.assertEqual(load_roles(skills_path), {9: "NetSuite Solution Architect", 47: "NetSuite Developer"})
            with open(skills_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["employees"]["47"]["level"], "Intermediate to Advanced")


if __name__ == "__main__":
    unittest.main()
