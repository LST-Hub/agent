"""
Unit tests for the daily Teams plan check and the Teams sender login store.
Run with: python -m unittest discover tests
"""

import unittest
from datetime import date, datetime
from unittest import mock
from cryptography.fernet import Fernet
from database.agent_store import get_agent_session
from services.allocation_matching import NameMatcher
from services.teams_chat_service import ChatMessage
from services.teams_plan_check import find_missing, over_planned, reminder_html, sender_ids

TUE = date(2026, 11, 17)
H = 3600
NO_FLOATER = lambda d, uid: False


def row(user, hours, project="P", day=TUE):
    return {"userId": user, "userName": f"User {user}", "projectName": project, "date": day, "seconds": hours * H}


class FindMissingTests(unittest.TestCase):
    def test_lists_expected_people_without_a_plan(self):
        missing = find_missing({1: "Zara", 2: "Amit", 3: "Bina"}, {1}, [], TUE, NO_FLOATER)
        self.assertEqual([m.name for m in missing], ["Amit", "Bina"])

    def test_ignored_and_floater_people_are_skipped(self):
        missing = find_missing({1: "A", 2: "B", 3: "C"}, set(), {2}, TUE, lambda d, uid: uid == 3)
        self.assertEqual([m.user_id for m in missing], [1])


class SenderTests(unittest.TestCase):
    def test_only_todays_senders_matched_to_people(self):
        msgs = [ChatMessage("1", datetime(2026, 11, 17, 9, 30), "Prakash Bagul", "x"),
                ChatMessage("2", datetime(2026, 11, 16, 9, 30), "Poonam Patil", "x"),
                ChatMessage("3", datetime(2026, 11, 17, 9, 40), "Stranger", "x")]
        matcher = NameMatcher({1: "Prakash Bagul", 2: "Poonam Patil"})
        self.assertEqual(sender_ids(msgs, TUE, matcher.match), {1})


class OverPlannedTests(unittest.TestCase):
    def test_only_days_over_eight_hours(self):
        over = over_planned([row(1, 6, "A"), row(1, 4, "B"), row(2, 8), row(3, 9, day=date(2026, 11, 18))], TUE)
        self.assertEqual([(o["userName"], o["seconds"] // H) for o in over], [("User 1", 10)])


class ReminderTextTests(unittest.TestCase):
    def test_test_mode_names_the_intended_person(self):
        text = reminder_html("Prakash", TUE, intended_for="Prakash Bagul")
        self.assertIn("[TEST, meant for Prakash Bagul]", text)
        self.assertIn("Automated reminder", text)
        self.assertNotIn("TEST", reminder_html("Prakash", TUE))


class LoginStoreTests(unittest.TestCase):
    def setUp(self):
        self.session = get_agent_session("sqlite://")
        self.key = mock.patch("config.settings.settings.teams_token_key", Fernet.generate_key().decode())
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.session.close()

    def test_token_stored_encrypted_and_replaced_on_new_sign_in(self):
        from database.agent_store import TeamsSenderLogin
        from services.teams_sender import _store_refresh_token, login_status, sign_out
        _store_refresh_token(self.session, "secret-1", {"id": "u1", "displayName": "Sachin", "userPrincipalName": "s@x.com"})
        _store_refresh_token(self.session, "secret-2", {"id": "u2", "displayName": "PM", "userPrincipalName": "pm@x.com"})
        [login] = self.session.query(TeamsSenderLogin).all()
        self.assertNotIn("secret", login.refresh_token)
        self.assertTrue(login_status(self.session).startswith("PM (pm@x.com)"))
        self.assertTrue(sign_out(self.session))
        self.assertIsNone(login_status(self.session))

    def test_sender_refuses_message_to_itself(self):
        from services.teams_sender import TeamsSender, _store_refresh_token
        _store_refresh_token(self.session, "rt", {"id": "u1", "displayName": "Sachin", "userPrincipalName": "s@x.com"})
        with mock.patch.object(TeamsSender, "_refresh", return_value="token"):
            result = TeamsSender(self.session).send("S@x.com", "hi")
        self.assertFalse(result.ok)

    def test_missing_login_raises_sign_in_error(self):
        from services.teams_sender import TeamsSender, TeamsSignInError
        with self.assertRaises(TeamsSignInError):
            TeamsSender(self.session)


if __name__ == "__main__":
    unittest.main()
