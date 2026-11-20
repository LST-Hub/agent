"""
Unit tests for gentle email sending: one connection, pause between emails, cap per run,
retry on temporary errors, stop on refusal.
Run with: python -m unittest discover tests
"""

import smtplib
import unittest
from unittest import mock
from services.email_service import EmailService


class FakeServer:
    def __init__(self, errors=()):
        self.errors = list(errors)   # raised one per sendmail call, then success
        self.sent = []

    def sendmail(self, sender, recipients, message):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(recipients)

    def quit(self):
        pass


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        EmailService.reset_run()
        self.sleeps = []
        self.patches = [
            mock.patch.object(EmailService, "_sleep", staticmethod(self.sleeps.append)),
            mock.patch("config.settings.settings.email_send_delay_seconds", 10),
            mock.patch("config.settings.settings.email_transport", "smtp"),
            mock.patch("config.settings.settings.email_max_per_run", 3),
            mock.patch("config.settings.settings.email_retries", 2),
            mock.patch("config.settings.settings.email_retry_wait_seconds", 60),
            mock.patch("config.settings.settings.email_test_recipient", ""),
        ]
        for p in self.patches:
            p.start()
        self.service = EmailService()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        EmailService.reset_run()

    def send(self, to="a@x.com"):
        return self.service.send_alert(to, "Subject", "Body")

    def test_one_connection_and_pause_between_emails(self):
        server = FakeServer()
        with mock.patch.object(EmailService, "_connect", return_value=server) as connect:
            self.assertTrue(self.send("a@x.com"))
            self.assertTrue(self.send("b@x.com"))
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(server.sent), 2)
        self.assertEqual(len(self.sleeps), 1)          # waited before the second email only
        self.assertGreater(self.sleeps[0], 9)

    def test_cap_per_run_stops_further_emails(self):
        server = FakeServer()
        with mock.patch.object(EmailService, "_connect", return_value=server):
            results = [self.send(f"{i}@x.com") for i in range(5)]
        self.assertEqual(results, [True, True, True, False, False])
        self.assertEqual(len(server.sent), 3)

    def test_temporary_error_is_retried(self):
        server = FakeServer([smtplib.SMTPResponseException(451, b"try later")])
        with mock.patch.object(EmailService, "_connect", return_value=server) as connect:
            self.assertTrue(self.send())
        self.assertEqual(connect.call_count, 2)        # reconnected after the error
        self.assertEqual(self.sleeps, [60])

    def test_refusal_stops_the_run(self):
        server = FakeServer([smtplib.SMTPResponseException(550, b"rate limit exceeded")])
        with mock.patch.object(EmailService, "_connect", return_value=server) as connect:
            self.assertFalse(self.send("a@x.com"))
            self.assertFalse(self.send("b@x.com"))
        self.assertEqual(connect.call_count, 1)        # never tried the server again
        self.assertEqual(server.sent, [])

    def test_login_refused_is_not_retried(self):
        with mock.patch.object(EmailService, "_connect",
                               side_effect=smtplib.SMTPAuthenticationError(535, b"blocked")) as connect:
            self.assertFalse(self.send())
            self.assertFalse(self.send())
        self.assertEqual(connect.call_count, 1)

    def test_bad_recipient_does_not_stop_the_others(self):
        server = FakeServer([smtplib.SMTPRecipientsRefused({"bad@x.com": (550, b"no such user")})])
        with mock.patch.object(EmailService, "_connect", return_value=server):
            self.assertFalse(self.send("bad@x.com"))
            self.assertTrue(self.send("good@x.com"))

    def test_secure_false_string_is_false(self):
        from config.settings import SMTPConfig
        with mock.patch.dict("os.environ", {"EMAIL_SERVER_SECURE": "false", "EMAIL_SERVER_PORT": "587"}):
            self.assertFalse(SMTPConfig().secure)
        with mock.patch.dict("os.environ", {"EMAIL_SERVER_SECURE": "true", "EMAIL_SERVER_PORT": "587"}):
            self.assertTrue(SMTPConfig().secure)



class FakeResponse:
    def __init__(self, status, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


class GraphTransportTests(unittest.TestCase):
    def setUp(self):
        EmailService.reset_run()
        self.sleeps = []
        self.patches = [
            mock.patch.object(EmailService, "_sleep", staticmethod(self.sleeps.append)),
            mock.patch.object(EmailService, "_graph_access_token", return_value="token"),
            mock.patch("config.settings.settings.email_transport", "graph"),
            mock.patch("config.settings.settings.graph_sender_mailbox", "ops-agent@x.com"),
            mock.patch("config.settings.settings.email_test_recipient", ""),
            mock.patch("config.settings.settings.email_max_per_run", 60),
            mock.patch("config.settings.settings.email_retries", 2),
        ]
        for p in self.patches:
            p.start()
        self.service = EmailService()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        EmailService.reset_run()

    def test_sends_from_the_agent_mailbox_with_cc_and_attachment(self):
        with mock.patch("requests.post", return_value=FakeResponse(202)) as post:
            ok = self.service.send_alert(["a@x.com"], "Report", "<p>Hi</p>", cc_email="b@x.com", is_html=True,
                                         attachments=[{"filename": "r.xlsx", "data": b"abc"}])
        self.assertTrue(ok)
        url, payload = post.call_args[0][0], post.call_args[1]["json"]
        self.assertTrue(url.endswith("/users/ops-agent@x.com/sendMail"))
        m = payload["message"]
        self.assertEqual(m["body"]["contentType"], "HTML")
        self.assertEqual([r["emailAddress"]["address"] for r in m["toRecipients"]], ["a@x.com"])
        self.assertEqual([r["emailAddress"]["address"] for r in m["ccRecipients"]], ["b@x.com"])
        self.assertEqual(m["attachments"][0]["contentBytes"], "YWJj")

    def test_throttled_request_waits_retry_after(self):
        responses = [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(202)]
        with mock.patch("requests.post", side_effect=responses):
            self.assertTrue(self.service.send_alert("a@x.com", "S", "B"))
        self.assertEqual(self.sleeps, [7])

    def test_permission_error_stops_the_run(self):
        with mock.patch("requests.post", return_value=FakeResponse(403, "Access denied")) as post:
            self.assertFalse(self.service.send_alert("a@x.com", "S", "B"))
            self.assertFalse(self.service.send_alert("b@x.com", "S", "B"))
        self.assertEqual(post.call_count, 1)

    def test_bad_message_does_not_stop_the_others(self):
        with mock.patch("requests.post", side_effect=[FakeResponse(400, "bad address"), FakeResponse(202)]):
            self.assertFalse(self.service.send_alert("bad", "S", "B"))
            self.assertTrue(self.service.send_alert("good@x.com", "S", "B"))


if __name__ == "__main__":
    unittest.main()
