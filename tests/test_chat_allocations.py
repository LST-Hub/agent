import unittest
from datetime import date, datetime
from unittest import mock

from services.allocation_extraction import RawAllocation
from services.allocation_matching import NameMatcher
from services.chat_allocations import (
    ADD_TO_PROJECT, ANY_TASK, ASSIGN_TASK, CREATE_TASK, build_plan, classify_reply, has_content, pick_task,
    pm_message_html, reply_html, requested_html,
)
from services.chat_intent import Intent
from services.tasksprint_api import TaskSprintClient, TaskSprintError
from services.teams_chat_service import ChatMessage
from services.teams_sender import SendResult

TODAY = date(2026, 11, 27)
PROJECTS = {1: "Protecta", 2: "Minimalist", 3: "Old Project", 4: "Apollo", 5: "LST NetSuite Data Archival Solution"}
ACTIVE = {1, 2, 4, 5}
MEMBER = {1, 2, 3, 5}
TASKS = {
    1: [{"id": 11, "name": "Development", "projectId": 1, "assigned": True},
        {"id": 12, "name": "Client Call", "projectId": 1, "assigned": True},
        {"id": 13, "name": "Testing", "projectId": 1, "assigned": False}],
    2: [{"id": 21, "name": "Support", "projectId": 2, "assigned": True}],
    5: [{"id": 51, "name": "Data Validation", "projectId": 5, "assigned": True},
        {"id": 52, "name": "POC of Archival", "projectId": 5, "assigned": True}],
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
    def pick(self, written, project_id=1):
        return pick_task(written, project_id, PROJECTS[project_id], TASKS.get(project_id, []))

    def test_written_task_matches_assigned_task(self):
        task, problem, _, request = self.pick("dev")
        self.assertEqual((task["id"], problem, request), (11, "", None))

    def test_text_starting_with_a_task_name(self):
        task, _, note, request = self.pick("Data Validation Testing (AP and AR TRANSACTION Record to be cross "
                                           "checked of Prajwal)", 5)
        self.assertEqual(task["id"], 51)
        self.assertIsNone(request)
        self.assertIn("read as task Data Validation", note)

    def test_task_the_person_is_not_on_goes_to_the_pm(self):
        task, _, _, request = self.pick("Testing")
        self.assertIsNone(task)
        self.assertEqual((request.kind, request.task), (ASSIGN_TASK, "Testing"))
        self.assertIn("not on task 'Testing'", request.problem())

    def test_unknown_task_goes_to_the_pm(self):
        task, _, _, request = self.pick("Design")
        self.assertIsNone(task)
        self.assertEqual((request.kind, request.task), (CREATE_TASK, "Design"))
        self.assertIn("Your tasks: Client Call, Development", request.problem())

    def test_only_task_is_used_when_none_written(self):
        task, _, note, _ = self.pick("", 2)
        self.assertEqual(task["id"], 21)
        self.assertIn("only task", note)

    def test_asks_which_task_when_several_and_none_written(self):
        task, problem, _, request = self.pick("")
        self.assertIsNone(task)
        self.assertIsNone(request)
        self.assertIn("which task", problem)

    def test_no_assigned_task(self):
        task, _, _, request = pick_task("", 9, "X", [{"id": 1, "name": "A", "projectId": 9, "assigned": False}])
        self.assertIsNone(task)
        self.assertEqual(request.kind, ANY_TASK)


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
        p = plan_for([raw("Apollo"), raw("Apollo", "Design", 1), raw("Old Project"), raw("Nonexistent Thing")])
        self.assertEqual([(r.kind, r.project_id, r.posted) for r in p.requests],
                         [(ADD_TO_PROJECT, 4, ["Development, 2 h", "Design, 1 h"])])
        self.assertTrue(any("not active" in x for x in p.problems))
        self.assertTrue(any("not found" in x for x in p.problems))

    def test_already_allocated_rows_are_skipped(self):
        p = plan_for([raw(hours=2), raw(task="Client Call", hours=1)], existing=[{"projectId": 1, "taskId": 11}])
        self.assertEqual([r.task_id for r in p.rows], [12])
        self.assertEqual(len(p.skipped), 1)

    def test_no_plan_means_no_reply(self):
        self.assertFalse(has_content(plan_for([])))

    def test_reply_asks_for_yes_and_escapes(self):
        p = plan_for([raw(hours=9), raw("Apollo")])
        key = p.requests[0].key
        text = reply_html(TODAY, p, {key: "I have asked the PM, Asha <PM>, to add you to project Apollo."})
        self.assertIn("I read your plan for 27-11-2026", text)
        self.assertIn("Reply yes to save it", text)
        self.assertIn("Protecta / Development: 9 h", text)
        self.assertIn("more than 8 h", text)
        self.assertIn("Apollo: you are not a member of this project in TaskSprint. I have asked the PM, "
                      "Asha &lt;PM&gt;, to add you to project Apollo.", text)
        saved = reply_html(TODAY, p, state="saved")
        self.assertIn("Saved your plan", saved)
        self.assertNotIn("Reply yes", saved)

    def test_classify_reply(self):
        for text in ("yes", "Yes!", "ok", "Confirm", "haan", "\U0001F44D", "✅"):
            self.assertEqual(classify_reply(text), "yes", text)
        for text in ("no", "Cancel", "don't save"):
            self.assertEqual(classify_reply(text), "no", text)
        for text in ("Protecta - Development - 2h", "yes, but Protecta 3h", ""):
            self.assertEqual(classify_reply(text), "plan", text)

    def test_pm_messages(self):
        p = plan_for([raw("Apollo", "Design", 3), raw("Protecta", "Design", 1), raw("Protecta", "Testing", 2)])
        by_kind = {r.kind: r for r in p.requests}
        text = pm_message_html("Asha K", "Sachin Ubale", TODAY, by_kind[ADD_TO_PROJECT], intended_for="Asha K")
        self.assertIn("[TEST, meant for Asha K]", text)
        self.assertIn("Hi Asha, Sachin Ubale posted a work plan for today (Fri 27-11-2026) on <b>Apollo</b> "
                      "(Design, 3 h), but is not a member of this project", text)
        self.assertIn("has no task <b>Design</b>. Please create it",
                      pm_message_html("Asha K", "Sachin Ubale", TODAY, by_kind[CREATE_TASK]))
        self.assertIn("is not on the task <b>Testing</b>",
                      pm_message_html("Asha K", "Sachin Ubale", TODAY, by_kind[ASSIGN_TASK]))

    def test_requested_html(self):
        self.assertIn("Tell me the project and the task", requested_html([]))
        self.assertIn("Asha K: add you to project Apollo", requested_html([("Asha K", "add you to project Apollo")]))


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

    def extract(self, messages, people, projects, context=()):
        self.seen.append([m.id for m in messages])
        self.context = [m.text for m in context]
        return [r for r in self.rows if r.message_id in {m.id for m in messages}]


class FakeIntents:
    def __init__(self, by_text=None):
        self.by_text = by_text or {}

    def classify(self, text, context=()):
        return self.by_text.get(text, Intent("plan"))


class FakeClient:
    def __init__(self, fail_projects=()):
        self.created, self.fail = [], set(fail_projects)

    def create_allocation(self, project_id, day, tasks):
        if project_id in self.fail:
            raise TaskSprintError("POST /resource-allocation: 500")
        self.created.append((project_id, day, tasks))


class FakeQuery:
    """Enough of a SQLAlchemy query for the agent: filter_by/order_by/first/all on a model, filter/all on a column."""

    def __init__(self, store, model, column=None, kw=None):
        self.store, self.model, self.column, self.kw = store, model, column, kw or {}

    def _rows(self):
        return [r for r in self.store.objects if type(r).__name__ == self.model
                and all(getattr(r, k) == v for k, v in self.kw.items())]

    def filter(self, *_):
        return self

    def filter_by(self, **kw):
        return FakeQuery(self.store, self.model, self.column, kw)

    def order_by(self, *_):
        return self      # the agent only orders by id descending: first() returns the newest

    def first(self):
        rows = self._rows()
        return rows[-1] if rows else None

    def all(self):
        rows = self._rows()
        return [(getattr(r, self.column),) for r in rows] if self.column else rows


class FakeStore:
    def __init__(self):
        self.objects = []

    def query(self, what):
        if hasattr(what, "__tablename__"):
            return FakeQuery(self, what.__name__)
        return FakeQuery(self, what.class_.__name__, what.key)     # a column such as Model.message_id

    def add(self, row):
        self.objects.append(row)

    def commit(self):
        pass

    def of(self, model):
        return [r for r in self.objects if type(r).__name__ == model]


class User:
    id, name, email = 60, "Sachin Ubale", "sachin@x.com"


class AgentTests(unittest.TestCase):
    test_to = ""
    ignore_ids, pm_ids = "", ""      # never the real .env lists

    def run_agent(self, messages, rows, client=None, store=None, dry_run=False, intents=None):
        from agents import chat_allocation_agent as ca
        teams, client, store = FakeTeams(messages), client or FakeClient(), store or FakeStore()
        self.extractor = FakeExtractor(rows)
        agent = ca.ChatAllocationAgent(dry_run=dry_run, today=TODAY, teams=teams, client=client,
                                       extractor=self.extractor, store=store, intents=intents or FakeIntents())
        session = mock.MagicMock()
        projects = [{"id": i, "name": n, "active": i in ACTIVE, "statusName": "In Progress",
                     "pmId": 70 + i, "pmName": "Asha K", "pmEmail": f"pm{i}@x.com"} for i, n in PROJECTS.items()]
        with mock.patch.object(ca, "get_db_session", lambda: iter([session])), \
                mock.patch.object(ca.ProjectRepository, "get_projects_for_matching", lambda s: projects), \
                mock.patch.object(ca.ProjectUserRepository, "get_member_project_ids", lambda s, u: sorted(MEMBER)), \
                mock.patch.object(ca.TaskRepository, "get_active_tasks_in_projects",
                                  lambda s, p, u: [t for ts in TASKS.values() for t in ts]), \
                mock.patch.object(ca.ResourceAllocationRepository, "get_user_day_allocations", lambda s, u, d: []), \
                mock.patch.object(ca, "load_aliases", lambda: ({}, {})), \
                mock.patch.object(ca.settings, "chat_allocation_users", "sachin@x.com"), \
                mock.patch.object(ca.settings, "teams_dm_test_recipient", self.test_to), \
                mock.patch.object(ca.settings, "ignore_ids", self.ignore_ids), \
                mock.patch.object(ca.settings, "chat_pm_ids", self.pm_ids), \
                mock.patch.object(ca, "_id_to_email", lambda: {}), \
                mock.patch.object(ca.ChatAllocationAgent, "_load_people", lambda self, e: [("sachin@x.com", User())]):
            result = agent.run()
        return result, teams, client, store

    def msg(self, mid, text="Protecta - Development - 2h", sender_id="me", hour=10):
        return ChatMessage(mid, datetime(2026, 11, 27, hour, 0), "Sachin Ubale", text, sender_id=sender_id)

    @staticmethod
    def statuses(store):
        return [(r.message_id, r.status) for r in store.of("ChatAllocationMessage")]

    def test_plan_waits_for_yes_then_saves_once(self):
        store = FakeStore()
        rows = [raw(msg="m1"), raw("Minimalist", "", 3, msg="m1")]
        _, teams, client, _ = self.run_agent([self.msg("m1"), self.msg("r1", sender_id="agent-id")], rows, store=store)
        self.assertEqual(client.created, [])                      # nothing saved before yes
        self.assertIn("Reply yes to save it", teams.sent[0][1])
        self.assertEqual(self.statuses(store), [("m1", "pending")])

        # "yes" in the next run saves the waiting plan
        history = [self.msg("m1"), self.msg("y1", "yes", hour=11)]
        _, teams2, client2, _ = self.run_agent(history, rows, store=store)
        self.assertEqual(sorted(c[0] for c in client2.created), [1, 2])
        self.assertEqual(client2.created[0][2][0]["employeeId"], 60)
        self.assertIn("Saved your plan", teams2.sent[0][1])
        self.assertEqual(store.of("ChatPendingPlan")[0].status, "confirmed")

        # Nothing new: no reply, nothing saved again; a second yes finds nothing waiting
        _, teams3, client3, _ = self.run_agent(history, rows, store=store)
        self.assertEqual((teams3.sent, client3.created), ([], []))
        _, teams4, client4, _ = self.run_agent(history + [self.msg("y2", "ok", hour=12)], rows, store=store)
        self.assertEqual(client4.created, [])
        self.assertIn("no plan waiting", teams4.sent[0][1])

    def test_plan_and_yes_in_the_same_run(self):
        _, teams, client, _ = self.run_agent([self.msg("m1"), self.msg("y1", "\U0001F44D", hour=11)], [raw(msg="m1")])
        self.assertEqual([c[0] for c in client.created], [1])
        self.assertEqual(len(teams.sent), 1)
        self.assertIn("Reply yes to save it", teams.sent[0][1])
        self.assertIn("Saved your plan", teams.sent[0][1])

    def test_new_plan_replaces_the_waiting_one(self):
        store = FakeStore()
        self.run_agent([self.msg("m1")], [raw(hours=2, msg="m1")], store=store)
        self.run_agent([self.msg("m2", "Protecta - Development - 5h", hour=11)], [raw(hours=5, msg="m2")], store=store)
        _, _, client, _ = self.run_agent([self.msg("y1", "yes", hour=12)], [], store=store)
        self.assertEqual(client.created[0][2][0]["seconds"], 5 * 3600)
        self.assertEqual([p.status for p in store.of("ChatPendingPlan")], ["replaced", "confirmed"])

    def test_no_drops_the_waiting_plan(self):
        store = FakeStore()
        self.run_agent([self.msg("m1")], [raw(msg="m1")], store=store)
        _, teams, client, _ = self.run_agent([self.msg("n1", "no", hour=11)], [], store=store)
        self.assertEqual(client.created, [])
        self.assertIn("not saved", teams.sent[0][1])
        self.assertEqual(store.of("ChatPendingPlan")[0].status, "cancelled")

    def test_dry_run_saves_and_sends_nothing(self):
        store = FakeStore()
        result, teams, client, _ = self.run_agent([self.msg("m1")], [raw(msg="m1"), raw("Apollo", msg="m1")],
                                                  store=store, dry_run=True)
        self.assertEqual((client.created, teams.sent, store.objects), ([], [], []))
        self.assertIn("dry run", result[0]["reply"])
        self.assertIn("would be asked to add the person to project Apollo", result[0]["reply"])

    def test_refused_project_is_reported(self):
        store = FakeStore()
        rows = [raw(msg="m1"), raw("Minimalist", "", 3, msg="m1")]
        self.run_agent([self.msg("m1")], rows, store=store)
        _, teams, client, _ = self.run_agent([self.msg("y1", "yes", hour=11)], [], client=FakeClient(fail_projects={2}),
                                             store=store)
        self.assertEqual([c[0] for c in client.created], [1])
        self.assertIn("TaskSprint did not save it", teams.sent[0][1])
        self.assertEqual(store.of("ChatPendingPlan")[0].status, "partly")

    def test_every_other_message_gets_an_answer(self):
        store = FakeStore()
        intents = FakeIntents({"good morning": Intent("greeting"), "thanks": Intent("thanks"),
                               "how does this work?": Intent("question")})
        _, teams, _, _ = self.run_agent([self.msg("m1", "good morning"), self.msg("m2", "thanks", hour=11),
                                         self.msg("m3", "how does this work?", hour=12)], [], store=store,
                                        intents=intents)
        reply = teams.sent[0][1]
        self.assertIn("Hello! Post today's plan", reply)
        self.assertIn("You're welcome", reply)
        self.assertIn("I save your work plan for today", reply)
        self.assertEqual(self.statuses(store), [("m1", "greeting"), ("m2", "thanks"), ("m3", "question")])

    def test_pm_is_told_once_per_day_and_add_this_task_is_answered(self):
        store = FakeStore()
        _, teams, client, _ = self.run_agent([self.msg("m1")], [raw("Apollo", "Design", 3, msg="m1")], store=store)
        self.assertEqual(client.created, [])
        self.assertEqual([t for t, _ in teams.sent], ["pm4@x.com", "sachin@x.com"])
        self.assertIn("Sachin Ubale posted a work plan", teams.sent[0][1])
        self.assertIn("I have asked the PM, Asha K, to add you to project Apollo.", teams.sent[1][1])
        self.assertEqual(self.statuses(store), [("m1", "needs_fix")])
        self.assertEqual(store.of("ChatPendingPlan"), [])
        # The same request later the same day: the PM is not asked again
        _, teams2, _, _ = self.run_agent([self.msg("m2", hour=11)], [raw("Apollo", "Design", 2, msg="m2")], store=store)
        self.assertEqual([t for t, _ in teams2.sent], ["sachin@x.com"])
        self.assertIn("already asked the PM, Asha K, today", teams2.sent[0][1])
        # "add this new task to me" without naming it: the agent says what it already asked
        intents = FakeIntents({"add this new task to me": Intent("request")})
        _, teams3, _, _ = self.run_agent([self.msg("m3", "add this new task to me", hour=12)], [], store=store,
                                         intents=intents)
        self.assertIn("Asha K: add you to project Apollo", teams3.sent[0][1])

    def test_follow_up_adds_to_the_waiting_plan_and_sees_the_conversation(self):
        store = FakeStore()
        self.run_agent([self.msg("m1")], [raw(hours=2, msg="m1")], store=store)
        question = self.msg("a1", "Which project is the 3 h for?", sender_id="agent-id", hour=11)
        _, teams, _, _ = self.run_agent([self.msg("m1"), question, self.msg("m2", "Minimalist 3hr", hour=12)],
                                        [raw("Minimalist", "", 3, msg="m2")], store=store)
        self.assertEqual(self.extractor.context, ["Protecta - Development - 2h", "Which project is the 3 h for?"])
        self.assertIn("Protecta / Development: 2 h", teams.sent[0][1])
        self.assertIn("Minimalist / Support: 3 h", teams.sent[0][1])
        _, _, client, _ = self.run_agent([self.msg("y1", "yes", hour=13)], [], store=store)
        self.assertEqual(sorted(c[0] for c in client.created), [1, 2])

    def test_replacing_message_drops_the_waiting_rows(self):
        store = FakeStore()
        self.run_agent([self.msg("m1")], [raw(hours=2, msg="m1")], store=store)
        _, teams, _, _ = self.run_agent([self.msg("m2", "only Minimalist 3h", hour=11)],
                                        [raw("Minimalist", "", 3, msg="m2", replaces=True)], store=store)
        self.assertNotIn("Protecta", teams.sent[0][1])

    def test_hours_without_project_are_asked_about(self):
        _, teams, _, _ = self.run_agent([self.msg("m1", "2hr")], [raw(project=None, task="", hours=2, msg="m1")])
        self.assertIn("2 h: for which project and task?", teams.sent[0][1])
        self.assertNotIn("cancelling", teams.sent[0][1])

    def test_request_is_not_read_as_a_plan(self):
        text = "add me to archival data validation test"
        intents = FakeIntents({text: Intent("request", "archival", "data validation test")})
        _, teams, _, _ = self.run_agent([self.msg("m1", text)], [raw(hours=None, msg="m1")], intents=intents)
        self.assertEqual(self.extractor.seen, [])                  # never sent to the plan reader
        self.assertEqual([t for t, _ in teams.sent], ["pm5@x.com", "sachin@x.com"])
        self.assertIn("to add task 'data validation test' in LST NetSuite Data Archival Solution", teams.sent[1][1])

    def test_missing_task_goes_to_the_pm(self):
        _, teams, _, _ = self.run_agent([self.msg("m1")], [raw("Protecta", "Design", 3, msg="m1")])
        self.assertEqual([t for t, _ in teams.sent], ["pm1@x.com", "sachin@x.com"])
        self.assertIn("has no task <b>Design</b>", teams.sent[0][1])
        self.assertIn("there is no task 'Design'", teams.sent[1][1])
        self.assertIn("to add task 'Design' in Protecta for you", teams.sent[1][1])

    def test_request_naming_project_and_task(self):
        intents = FakeIntents({"add me to Protecta testing": Intent("request", "Protecta", "Testing"),
                               "add me to Protecta development": Intent("request", "Protecta", "Development")})
        _, teams, _, _ = self.run_agent([self.msg("m1", "add me to Protecta testing")], [], intents=intents)
        self.assertEqual([t for t, _ in teams.sent], ["pm1@x.com", "sachin@x.com"])
        self.assertIn("to put you on task 'Testing' in Protecta", teams.sent[1][1])
        _, teams2, _, _ = self.run_agent([self.msg("m2", "add me to Protecta development")], [], intents=intents)
        self.assertIn("already on task Development", teams2.sent[0][1])

    def test_pm_message_goes_to_test_recipient(self):
        self.test_to = "tester@x.com"
        _, teams, _, _ = self.run_agent([self.msg("m1")], [raw("Apollo", "Design", 3, msg="m1")])
        self.assertEqual(teams.sent[0][0], "tester@x.com")
        self.assertIn("[TEST, meant for Asha K]", teams.sent[0][1])

    def test_ignored_pm_only_messaged_when_in_pm_list(self):
        self.ignore_ids = "74"
        _, teams, _, _ = self.run_agent([self.msg("m1")], [raw("Apollo", "Design", 3, msg="m1")])
        self.assertEqual([t for t, _ in teams.sent], ["sachin@x.com"])
        self.assertIn("Ask the PM, Asha K.", teams.sent[0][1])
        self.pm_ids = "74"
        _, teams2, _, _ = self.run_agent([self.msg("m1")], [raw("Apollo", "Design", 3, msg="m1")])
        self.assertEqual([t for t, _ in teams2.sent], ["pm4@x.com", "sachin@x.com"])


class WatchTests(unittest.TestCase):
    def test_office_hours(self):
        from agents.chat_allocation_agent import in_office_hours
        self.assertTrue(in_office_hours(datetime(2026, 11, 27, 8, 30)))
        self.assertFalse(in_office_hours(datetime(2026, 11, 27, 20, 30)))
        self.assertFalse(in_office_hours(datetime(2026, 11, 28, 12, 0)))   # Saturday

    def test_watch_runs_in_office_hours_and_survives_errors(self):
        from agents import chat_allocation_agent as ca
        times = iter([datetime(2026, 11, 27, 7, 0), datetime(2026, 11, 27, 10, 0), datetime(2026, 11, 27, 10, 1)])
        sleeps, runs = [], []

        def fake_run(agent):
            runs.append(agent)
            if len(runs) == 1:
                raise RuntimeError("Graph hiccup")
            return []

        with mock.patch.object(ca.ChatAllocationAgent, "run", fake_run), \
                mock.patch.object(ca.ChatAllocationAgent, "_make_teams", staticmethod(lambda store: FakeTeams([]))), \
                mock.patch("database.agent_store.get_agent_session", lambda: mock.MagicMock()):
            ca.watch(interval=20, sleep=sleeps.append, now=lambda: next(times), rounds=3)
        self.assertEqual(sleeps, [300, 20, 20])
        self.assertEqual(len(runs), 2)


if __name__ == "__main__":
    unittest.main()
