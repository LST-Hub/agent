import unittest
from datetime import date, datetime
from unittest import mock

from services.allocation_extraction import RawAllocation
from services.allocation_matching import NameMatcher
from services.chat_allocations import build_plan, has_content, pick_task, pm_message_html, reply_html
from services.tasksprint_api import TaskSprintClient, TaskSprintError
from services.teams_chat_service import ChatMessage
from services.teams_sender import SendResult

TODAY = date(2026, 11, 27)
PROJECTS = {1: "Protecta", 2: "Minimalist", 3: "Old Project", 4: "Apollo"}
ACTIVE = {1, 2, 4}
MEMBER = {1, 2, 3}
TASKS = {
    1: [{"id": 11, "name": "Development", "projectId": 1, "assigned": True},
        {"id": 12, "name": "Client Call", "projectId": 1, "assigned": True},
        {"id": 13, "name": "Testing", "projectId": 1, "assigned": False}],
    2: [{"id": 21, "name": "Support", "projectId": 2, "assigned": True}],
}


def raw(project="Protecta", task="Development", hours=2.0, person="Sachin Ubale", day=TODAY, end=None,
        msg="m1", replaces=False):
    return RawAllocation(message_id=msg, person=person, project=project, start_date=day, end_date=end or day,
                         hours_per_day=hours, replaces_earlier=replaces, confidence="high",
                         evidence=f"{project} {task} {hours}", person_text=person, project_text=project, task=task)


def plan_for(rows, existing=()):
    return build_plan(rows, {"Sachin Ubale"}, TODAY, NameMatcher(PROJECTS, preferred=ACTIVE), ACTIVE, MEMBER,
                      TASKS, list(existing))


class PickTaskTests(unittest.TestCase):
    def test_written_task_matches_assigned_task(self):
        task, problem, _ = pick_task("dev", "Protecta", TASKS[1])
        self.assertEqual(task["id"], 11)
        self.assertEqual(problem, "")

    def test_task_the_person_is_not_on(self):
        task, problem, _ = pick_task("Testing", "Protecta", TASKS[1])
        self.assertIsNone(task)
        self.assertIn("not on task 'Testing'", problem)

    def test_only_task_is_used_when_none_written(self):
        task, _, note = pick_task("", "Minimalist", TASKS[2])
        self.assertEqual(task["id"], 21)
        self.assertIn("only task", note)

    def test_asks_when_several_tasks_and_no_match(self):
        task, problem, _ = pick_task("Design", "Protecta", TASKS[1])
        self.assertIsNone(task)
        self.assertIn("Client Call, Development", problem)

    def test_no_assigned_task(self):
        task, problem, _ = pick_task("", "X", [{"id": 1, "name": "A", "projectId": 9, "assigned": False}])
        self.assertIsNone(task)
        self.assertIn("not on any task", problem)


class BuildPlanTests(unittest.TestCase):
    def test_plan_rows_and_same_task_added_up(self):
        p = plan_for([raw(hours=2), raw(task="Development", hours=1.5), raw("Minimalist", "", 4)])
        self.assertEqual([(r.project_id, r.task_id, r.seconds) for r in p.rows],
                         [(2, 21, 4 * 3600), (1, 11, int(3.5 * 3600))])
        self.assertEqual(p.problems, [])

    def test_rejects_other_people_dates_cancellations_and_missing_hours(self):
        p = plan_for([raw(person="Mayur"), raw(day=date(2026, 11, 30)), raw(hours=0), raw(project=None),
                      raw(hours=None)])
        self.assertEqual(p.rows, [])
        self.assertEqual(len(p.problems), 5)
        self.assertIn("only your own plan", p.problems[0])
        self.assertIn("only today's plan", p.problems[1])
        self.assertIn("not supported yet", p.problems[2])
        self.assertIn("how many hours", p.problems[4])

    def test_project_checks(self):
        p = plan_for([raw("Apollo"), raw("Old Project"), raw("Nonexistent Thing")])
        self.assertEqual([(n.project_id, n.project_name, n.posted) for n in p.not_member],
                         [(4, "Apollo", ["Development, 2 h"])])
        self.assertTrue(any("not active" in x for x in p.problems))
        self.assertTrue(any("not found" in x for x in p.problems))

    def test_already_allocated_rows_are_skipped(self):
        p = plan_for([raw(hours=2), raw(task="Client Call", hours=1)], existing=[{"projectId": 1, "taskId": 11}])
        self.assertEqual([r.task_id for r in p.rows], [12])
        self.assertEqual(len(p.skipped), 1)

    def test_no_plan_means_no_reply(self):
        self.assertFalse(has_content(plan_for([])))

    def test_reply_lists_saved_rows_and_escapes(self):
        p = plan_for([raw(hours=9), raw("Apollo")])
        text = reply_html("Sachin Ubale", TODAY, p, {4: "I have asked the PM, Asha <PM>, to add you"})
        self.assertIn("Saved your plan for 27-11-2026", text)
        self.assertIn("Protecta / Development: 9 h", text)
        self.assertIn("more than 8 h", text)
        self.assertIn("Apollo: I have asked the PM, Asha &lt;PM&gt;, to add you", text)

    def test_pm_message(self):
        p = plan_for([raw("Apollo", "Design", 3)])
        text = pm_message_html("Asha K", "Sachin Ubale", TODAY, p.not_member[0], intended_for="Asha K")
        self.assertIn("[TEST, meant for Asha K]", text)
        self.assertIn("Hi Asha, Sachin Ubale posted a work plan for today (Fri 27-11-2026) on <b>Apollo</b> "
                      "(Design, 3 h)", text)


class FakeResponse:
    def __init__(self, status, body, cookies=None):
        self.status_code, self._body, self.headers, self.text = status, body, {}, str(body)
        self._cookies = cookies or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHttp:
    def __init__(self, login_ok=True, post_result=None):
        self.cookies, self.calls, self.login_ok = {}, [], login_ok
        self.post_result = post_result or FakeResponse(200, {"success": True, "data": [{"count": 1}]})

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return FakeResponse(200, {"csrfToken": "abc"})

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if self.login_ok:
            self.cookies["__Secure-next-auth.session-token"] = "jwt"
            return FakeResponse(200, {"url": "https://x/"})
        return FakeResponse(401, {"url": "https://x/api/auth/error?error=CredentialsSignin"})

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.post_result


class TaskSprintClientTests(unittest.TestCase):
    def test_login_and_create_allocation_body(self):
        http = FakeHttp()
        c = TaskSprintClient("https://ts.example/", "agent@x.com", "pw", http=http)
        c.create_allocation(7, TODAY, [{"employeeId": 60, "taskId": 11, "seconds": 7200}])
        login = http.calls[1]
        self.assertEqual(login[1], "https://ts.example/api/auth/callback/credentials")
        self.assertEqual(login[2]["data"]["csrfToken"], "abc")
        method, url, kw = http.calls[-1]
        self.assertEqual((method, url), ("POST", "https://ts.example/api/v1/resource-allocation"))
        self.assertEqual(kw["json"], {"projectId": 7, "date": "2026-11-27", "repetationType": "DontRepeat",
                                      "tasksData": [{"employeeId": 60, "taskId": 11, "duration": 7200}]})

    def test_login_failure(self):
        c = TaskSprintClient("https://ts.example", "agent@x.com", "bad", http=FakeHttp(login_ok=False))
        with self.assertRaises(TaskSprintError):
            c.login()

    def test_refused_request(self):
        http = FakeHttp(post_result=FakeResponse(500, {"success": False, "message": "Some Internal Error"}))
        c = TaskSprintClient("https://ts.example", "agent@x.com", "pw", http=http)
        with self.assertRaises(TaskSprintError):
            c.create_allocation(7, TODAY, [{"employeeId": 60, "taskId": 11, "seconds": 3600}])

    def test_settings_required(self):
        with self.assertRaises(TaskSprintError):
            TaskSprintClient("", "a@x.com", "pw")


class FakeTeams:
    sender_id = "agent-id"

    def __init__(self, messages):
        self.messages, self.sent = messages, []

    def open_chat(self, email):
        return "chat-1"

    def read_chat(self, chat_id, since):
        return self.messages

    def send(self, to, html_text):
        self.sent.append((to, html_text))
        return SendResult(True)


class FakeExtractor:
    def __init__(self, rows):
        self.rows, self.seen = rows, []

    def extract(self, messages, people, projects):
        self.seen.append([m.id for m in messages])
        return [r for r in self.rows if r.message_id in {m.id for m in messages}]


class FakeClient:
    def __init__(self, fail_projects=()):
        self.created, self.fail = [], set(fail_projects)

    def create_allocation(self, project_id, day, tasks):
        if project_id in self.fail:
            raise TaskSprintError("POST /resource-allocation: 500")
        self.created.append((project_id, day, tasks))


class FakeQuery:
    def __init__(self, store, kw=None):
        self.store, self.kw = store, kw or {}

    def filter(self, *_):
        return self

    def filter_by(self, **kw):
        return FakeQuery(self.store, kw)

    def first(self):
        return next((r for r in self.store.logs if all(getattr(r, k) == v for k, v in self.kw.items())), None)

    def all(self):
        return [(r.message_id,) for r in self.store.rows]


class FakeStore:
    def __init__(self):
        self.rows, self.logs = [], []

    def query(self, *_):
        return FakeQuery(self)

    def add(self, row):
        (self.logs if type(row).__name__ == "NotificationLog" else self.rows).append(row)

    def commit(self):
        pass


class User:
    id, name, email = 60, "Sachin Ubale", "sachin@x.com"


class AgentTests(unittest.TestCase):
    test_to = ""

    def run_agent(self, messages, rows, client=None, store=None, dry_run=False):
        from agents import chat_allocation_agent as ca
        teams, client, store = FakeTeams(messages), client or FakeClient(), store or FakeStore()
        agent = ca.ChatAllocationAgent(dry_run=dry_run, today=TODAY, teams=teams, client=client,
                                       extractor=FakeExtractor(rows), store=store)
        session = mock.MagicMock()
        repos = {
            "ProjectRepository": [{"id": i, "name": n, "active": i in ACTIVE, "statusName": "In Progress",
                                   "pmId": 70 + i, "pmName": "Asha K", "pmEmail": f"pm{i}@x.com"}
                                  for i, n in PROJECTS.items()],
            "ProjectUserRepository": sorted(MEMBER),
            "TaskRepository": [t for ts in TASKS.values() for t in ts],
            "ResourceAllocationRepository": [],
        }
        with mock.patch.object(ca, "get_db_session", lambda: iter([session])), \
                mock.patch.object(ca.ProjectRepository, "get_projects_for_matching", lambda s: repos["ProjectRepository"]), \
                mock.patch.object(ca.ProjectUserRepository, "get_member_project_ids", lambda s, u: repos["ProjectUserRepository"]), \
                mock.patch.object(ca.TaskRepository, "get_active_tasks_in_projects", lambda s, p, u: repos["TaskRepository"]), \
                mock.patch.object(ca.ResourceAllocationRepository, "get_user_day_allocations", lambda s, u, d: []), \
                mock.patch.object(ca, "load_aliases", lambda: ({}, {})), \
                mock.patch.object(ca.settings, "chat_allocation_users", "sachin@x.com"), \
                mock.patch.object(ca.settings, "teams_dm_test_recipient", self.test_to), \
                mock.patch.object(ca, "_id_to_email", lambda: {}), \
                mock.patch.object(ca.ChatAllocationAgent, "_load_people", lambda self, e: [("sachin@x.com", User())]):
            result = agent.run()
        return result, teams, client, store

    def msg(self, mid, sender_id="me", hour=10):
        return ChatMessage(mid, datetime(2026, 11, 27, hour, 0), "Sachin Ubale", "Protecta - Development - 2h",
                           sender_id=sender_id)

    def test_saves_replies_and_handles_each_message_once(self):
        messages = [self.msg("m1"), self.msg("r1", sender_id="agent-id")]
        rows = [raw(msg="m1"), raw("Minimalist", "", 3, msg="m1")]
        store = FakeStore()
        _, teams, client, _ = self.run_agent(messages, rows, store=store)
        self.assertEqual(sorted(c[0] for c in client.created), [1, 2])
        self.assertEqual(client.created[0][2][0]["employeeId"], 60)
        self.assertEqual(len(teams.sent), 1)
        self.assertIn("Saved your plan", teams.sent[0][1])
        self.assertEqual([(r.message_id, r.status) for r in store.rows], [("m1", "saved")])
        # A second run finds nothing new
        _, teams2, client2, _ = self.run_agent(messages, rows, store=store)
        self.assertEqual(client2.created, [])
        self.assertEqual(teams2.sent, [])

    def test_dry_run_writes_and_sends_nothing(self):
        store = FakeStore()
        result, teams, client, _ = self.run_agent([self.msg("m1")], [raw(msg="m1")], store=store, dry_run=True)
        self.assertEqual(client.created, [])
        self.assertEqual(teams.sent, [])
        self.assertEqual(store.rows, [])
        self.assertIn("Would save", result[0]["reply"])

    def test_refused_project_is_reported(self):
        rows = [raw(msg="m1"), raw("Minimalist", "", 3, msg="m1")]
        store = FakeStore()
        _, teams, client, _ = self.run_agent([self.msg("m1")], rows, client=FakeClient(fail_projects={2}), store=store)
        self.assertEqual([c[0] for c in client.created], [1])
        self.assertIn("TaskSprint did not save it", teams.sent[0][1])
        self.assertEqual(store.rows[0].status, "partly")

    def test_message_without_plan_gets_no_reply(self):
        store = FakeStore()
        _, teams, _, _ = self.run_agent([self.msg("m1")], [], store=store)
        self.assertEqual(teams.sent, [])
        self.assertEqual(store.rows[0].status, "no_plan")

    def test_pm_is_told_once_per_day(self):
        store = FakeStore()
        _, teams, client, _ = self.run_agent([self.msg("m1")], [raw("Apollo", "Design", 3, msg="m1")], store=store)
        self.assertEqual(client.created, [])
        self.assertEqual([t for t, _ in teams.sent], ["pm4@x.com", "sachin@x.com"])
        self.assertIn("Sachin Ubale posted a work plan", teams.sent[0][1])
        self.assertIn("Apollo: I have asked the PM, Asha K, to add you", teams.sent[1][1])
        self.assertEqual(store.rows[0].status, "needs_fix")
        # A new message for the same project the same day: the PM is not asked again
        _, teams2, _, _ = self.run_agent([self.msg("m2", hour=11)], [raw("Apollo", "Design", 2, msg="m2")], store=store)
        self.assertEqual([t for t, _ in teams2.sent], ["sachin@x.com"])
        self.assertIn("already asked the PM", teams2.sent[0][1])

    def test_pm_message_goes_to_test_recipient(self):
        self.test_to = "tester@x.com"
        _, teams, _, _ = self.run_agent([self.msg("m1")], [raw("Apollo", "Design", 3, msg="m1")])
        self.assertEqual(teams.sent[0][0], "tester@x.com")
        self.assertIn("[TEST, meant for Asha K]", teams.sent[0][1])


if __name__ == "__main__":
    unittest.main()
