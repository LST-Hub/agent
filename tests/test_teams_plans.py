"""
Unit tests for saved Teams day plans and their use in daily-ops and the forecast.
Run with: python -m unittest discover tests
"""

import unittest
from datetime import date, datetime
from database.agent_store import TeamsPlan, get_agent_session
from services.allocation_matching import PlannedDay
from services.daily_operations_service import find_allocation_variances, find_over_allocations
from services.forecast_service import build_forecast
from services.teams_plans import SOURCE_PM, SOURCE_TEAMS, load_plans, merge_with_pm, save_plans

MON, TUE = date(2026, 11, 16), date(2026, 11, 17)
H = 3600
NO_FLOATER = lambda d, uid: False


def plan(user=1, project=10, day=MON, hours=8, mid="m1", name="Prakash Bagul", project_name="Protecta"):
    return PlannedDay(user, name, project, project_name, day, hours * H, True, "high", mid,
                      datetime(2026, 11, 16, 10, 0), name, "evidence", task="Development")


def row(user=1, project=10, day=MON, hours=8, source=None):
    r = {"userId": user, "userName": f"User {user}", "projectId": project, "projectName": f"Project {project}",
         "pmName": "", "date": day, "seconds": hours * H}
    if source:
        r["source"] = source
    return r


class SaveLoadTests(unittest.TestCase):
    def setUp(self):
        self.session = get_agent_session("sqlite://")

    def tearDown(self):
        self.session.close()

    def test_save_and_load_as_allocation_rows(self):
        removed, saved = save_plans(self.session, [plan(hours=6), plan(project=11, hours=2)], {"m1"})
        self.assertEqual((removed, saved), (0, 2))
        rows = sorted(load_plans(self.session, MON, MON), key=lambda r: r["projectId"])
        self.assertEqual([(r["projectId"], r["seconds"], r["source"]) for r in rows],
                         [(10, 6 * H, SOURCE_TEAMS), (11, 2 * H, SOURCE_TEAMS)])
        self.assertEqual(rows[0]["task"], "Development")

    def test_rerun_on_same_messages_does_not_duplicate(self):
        save_plans(self.session, [plan()], {"m1"})
        removed, saved = save_plans(self.session, [plan()], {"m1"})
        self.assertEqual((removed, saved), (1, 1))
        self.assertEqual(self.session.query(TeamsPlan).count(), 1)

    def test_newer_plan_replaces_the_day(self):
        # Monday's plan came from an older message no longer in the lookback window
        save_plans(self.session, [plan(project=10, mid="old")], {"old"})
        save_plans(self.session, [plan(project=11, mid="new")], {"new"})
        [r] = load_plans(self.session, MON, MON)
        self.assertEqual(r["projectId"], 11)

    def test_cancelled_plan_from_a_re_read_message_is_removed(self):
        save_plans(self.session, [plan(mid="m1")], {"m1"})
        # m1 read again, but a later message cancelled the day, so no plan comes out
        save_plans(self.session, [], {"m1", "m2"})
        self.assertEqual(load_plans(self.session, MON, MON), [])

    def test_other_days_and_people_are_kept(self):
        save_plans(self.session, [plan(day=TUE, mid="old"), plan(user=2, mid="old2")], {"old", "old2"})
        save_plans(self.session, [plan(user=1, mid="new")], {"new"})
        kept = {(r["userId"], r["date"]) for r in load_plans(self.session, MON, TUE)}
        self.assertEqual(kept, {(1, MON), (1, TUE), (2, MON)})


class MergeTests(unittest.TestCase):
    def test_pm_tool_wins_for_the_person_and_day(self):
        merged = merge_with_pm([row(project=10, hours=4)], [row(project=11, hours=8, source=SOURCE_TEAMS)])
        self.assertEqual([(r["projectId"], r["source"]) for r in merged], [(10, SOURCE_PM)])

    def test_teams_fills_days_without_pm_allocation(self):
        merged = merge_with_pm([row(day=MON)], [row(day=TUE, source=SOURCE_TEAMS), row(user=2, source=SOURCE_TEAMS)])
        self.assertEqual(sorted((r["userId"], r["date"], r["source"]) for r in merged),
                         [(1, MON, SOURCE_PM), (1, TUE, SOURCE_TEAMS), (2, MON, SOURCE_TEAMS)])

    def test_pm_datetime_dates_are_compared_as_dates(self):
        pm = row()
        pm["date"] = datetime(2026, 11, 16)
        self.assertEqual(len(merge_with_pm([pm], [row(source=SOURCE_TEAMS)])), 1)


class DailyOpsWithTeamsTests(unittest.TestCase):
    def test_variance_against_teams_plan_carries_source(self):
        plans = merge_with_pm([], [row(hours=6, source=SOURCE_TEAMS), row(project=11, hours=2, source=SOURCE_TEAMS)])
        logged = [row(hours=8)]
        variances = find_allocation_variances(plans, logged, [MON])
        self.assertEqual([(v["projectName"], v["diff"] // H, v["source"]) for v in variances],
                         [("Project 10", 2, SOURCE_TEAMS), ("Project 11", -2, SOURCE_TEAMS)])

    def test_teams_plan_without_hours_is_not_compared(self):
        plans = [dict(row(hours=0, source=SOURCE_TEAMS), hours_stated=False)]
        self.assertEqual(find_allocation_variances(plans, [row(hours=8)], [MON]), [])

    def test_over_planned_day_from_teams(self):
        plans = merge_with_pm([], [row(hours=7, source=SOURCE_TEAMS), row(project=11, hours=3, source=SOURCE_TEAMS)])
        [o] = find_over_allocations(plans, [MON])
        self.assertEqual((o["over"] // H, o["source"]), (2, SOURCE_TEAMS))


class ForecastWithTeamsTests(unittest.TestCase):
    def test_teams_plan_replaces_projection_for_that_day(self):
        allocations = merge_with_pm([], [row(project=11, hours=8, source=SOURCE_TEAMS)])
        f = build_forecast([MON, TUE], [], {1: {10: 4 * H}}, {1: "User 1"}, allocations,
                           {10: {"name": "Project 10"}, 11: {"name": "Project 11"}}, {}, floater=NO_FLOATER)
        [p] = f.people
        # Monday: the Teams plan covers the whole day (no projection on project 10); Tuesday: 4 h projected
        self.assertEqual((p.confirmed // H, p.projected // H, p.capacity // H), (8, 4, 16))

    def test_teams_plan_without_hours_keeps_the_projection(self):
        allocations = merge_with_pm([], [dict(row(project=10, hours=0, source=SOURCE_TEAMS), hours_stated=False)])
        f = build_forecast([MON], [], {1: {10: 4 * H}}, {1: "User 1"}, allocations,
                           {10: {"name": "Project 10"}}, {}, floater=NO_FLOATER)
        [p] = f.people
        self.assertEqual((p.confirmed // H, p.projected // H), (0, 4))

    def test_pm_allocation_still_replaces_only_its_project(self):
        f = build_forecast([MON], [], {1: {10: 4 * H}}, {1: "User 1"}, merge_with_pm([row(project=11, hours=2)], []),
                           {10: {"name": "Project 10"}, 11: {"name": "Project 11"}}, {}, floater=NO_FLOATER)
        [p] = f.people
        self.assertEqual((p.confirmed // H, p.projected // H), (2, 4))


if __name__ == "__main__":
    unittest.main()
