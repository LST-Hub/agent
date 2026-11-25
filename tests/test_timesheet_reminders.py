"""Unit tests for missing-timesheet reminders on email / Teams. Run with: python -m unittest discover tests"""

import unittest
from datetime import date

from services import timesheet_reminders as tr
from services.teams_sender import SendResult, TeamsSignInError

DAY = date(2026, 11, 25)


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send_alert(self, to_email, subject, body, **kw):
        self.sent.append((to_email, subject, body))
        return True


class FakeQuery:
    def __init__(self, store, kw=None):
        self.store, self.kw = store, kw

    def filter_by(self, **kw):
        return FakeQuery(self.store, kw)

    def first(self):
        return next((l for l in self.store.logs if all(getattr(l, k) == v for k, v in self.kw.items())), None)


class FakeStore:
    def __init__(self, logs=()):
        self.logs = list(logs)

    def query(self, _):
        return FakeQuery(self)

    def add(self, row):
        self.logs.append(row)

    def commit(self):
        pass

    def close(self):
        pass


class FakeSender:
    sender_name = "LST Ops Agent"

    def __init__(self, fail_for=()):
        self.fail_for, self.sent = set(fail_for), []

    def send(self, to, html):
        self.sent.append((to, html))
        return SendResult(False, "not found") if to in self.fail_for else SendResult(True)


def reminders():
    return [tr.Reminder(1, "Asha", "Asha K", "asha@x.com", missing=[date(2026, 11, 24)]),
            tr.Reminder(2, "Ravi", "Ravi P", "ravi@x.com", incomplete={date(2026, 11, 23): 5400}),
            tr.Reminder(3, "Mona", "Mona S", "mona@x.com", missing=[date(2026, 11, 20)]),
            tr.Reminder(4, "Ojas", "Ojas T", "ojas@x.com", missing=[date(2026, 11, 20)])]


class ReminderTests(unittest.TestCase):
    def run_deliver(self, channel, sender=None, store=None, test_recipient="", sender_error=None):
        email, store = FakeEmail(), store or FakeStore()
        sender = sender or FakeSender()

        def make_sender(_):
            if sender_error:
                raise TeamsSignInError(sender_error)
            return sender
        rs = reminders()
        note = tr.deliver(rs, DAY, channel, email, test_recipient=test_recipient,
                          store_factory=lambda: store, sender_factory=make_sender)
        return rs, email, sender, store, note

    def test_texts(self):
        r = tr.Reminder(2, "Ravi", "Ravi P", "r@x.com", missing=[date(2026, 11, 24)], incomplete={date(2026, 11, 23): 1800})
        self.assertEqual(tr.date_strings(r.missing, r.incomplete), ["2026-11-24", "2026-11-23 (30 minutes remaining)"])
        html = tr.teams_html(r)
        self.assertIn("Tue 24-11-2026 - not submitted", html)
        self.assertIn("Mon 23-11-2026 - 30 minutes still to log", html)
        self.assertNotIn("TEST", html)
        self.assertEqual(tr.email_text(reminders()[0])[0],
                         "Missing/Incomplete Timesheet Reminder")

    def test_email_channel_sends_only_email(self):
        rs, email, sender, _, _ = self.run_deliver("email")
        self.assertEqual(len(email.sent), 4)
        self.assertEqual(sender.sent, [])

    def test_teams_channel_sends_teams_and_emails_only_failures(self):
        rs, email, sender, store, note = self.run_deliver("teams", sender=FakeSender(fail_for={"ravi@x.com"}))
        self.assertEqual(len(sender.sent), 4)
        self.assertEqual([e[0] for e in email.sent], ["ravi@x.com"])
        self.assertEqual(rs[0].status, "Teams sent")
        self.assertTrue(rs[1].status.startswith("Teams failed"))
        self.assertIn("LST Ops Agent", note)

    def test_both_channel(self):
        _, email, sender, _, _ = self.run_deliver("both")
        self.assertEqual(len(sender.sent), 4)
        self.assertEqual(len(email.sent), 4)

    def test_login_problem_falls_back_to_email(self):
        rs, email, _, _, note = self.run_deliver("teams", sender_error="login expired")
        self.assertEqual(len(email.sent), 4)
        self.assertIn("by email instead", note)

    def test_only_once_per_day(self):
        from database.agent_store import NotificationLog
        store = FakeStore([NotificationLog(kind=tr.LOG_KIND, recipients="asha@x.com",
                                           subject=f"{DAY.isoformat()} user 1", success=True)])
        rs, _, sender, _, _ = self.run_deliver("teams", store=store)
        self.assertEqual(rs[0].status, "already reminded on Teams today")
        self.assertNotIn("asha@x.com", [s[0] for s in sender.sent])

    def test_test_mode_sends_at_most_three_to_the_tester(self):
        rs, email, sender, _, _ = self.run_deliver("teams", test_recipient="me@x.com")
        self.assertEqual([s[0] for s in sender.sent], ["me@x.com"] * 3)
        self.assertIn("[TEST, meant for Asha K]", sender.sent[0][1])
        self.assertEqual(email.sent, [])
        self.assertIn("limit", rs[3].status)

    def test_unknown_channel(self):
        with self.assertRaises(ValueError):
            tr.deliver([], DAY, "sms", FakeEmail())


if __name__ == "__main__":
    unittest.main()
