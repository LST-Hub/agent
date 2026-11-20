"""
Unit tests for the email test-mode redirect. Run with: python -m unittest discover tests
"""

import unittest
from email import message_from_string
from unittest.mock import patch
from services.email_service import EmailService


def send(test_recipient, **kwargs):
    """Send through a mocked SMTP server; returns (envelope recipients, parsed message)."""
    EmailService.reset_run()
    service = EmailService()
    service.test_recipient = test_recipient
    service.secure, service.port, service.transport = False, 25, "smtp"
    with patch("services.email_service.smtplib.SMTP") as smtp:
        ok = service.send_alert(**kwargs)
    EmailService.reset_run()
    assert ok
    _from, recipients, raw = smtp.return_value.sendmail.call_args[0]
    return recipients, message_from_string(raw)


class TestModeTests(unittest.TestCase):
    def test_everything_goes_only_to_test_recipient(self):
        recipients, msg = send("me@example.com", to_email=["pm1@example.com", "pm2@example.com"],
                               cc_email="boss@example.com", subject="Report", body="<html><body><p>Hi</p></body></html>",
                               is_html=True)
        self.assertEqual(recipients, ["me@example.com"])
        self.assertEqual(msg["To"], "me@example.com")
        self.assertIsNone(msg["Cc"])
        self.assertEqual(msg["Subject"], "[TEST] Report")
        html = msg.get_payload()[0].get_payload(decode=True).decode()
        self.assertIn("TEST MODE", html)
        self.assertIn("pm1@example.com, pm2@example.com", html)
        self.assertIn("cc: boss@example.com", html)

    def test_plain_text_banner(self):
        recipients, msg = send("me@example.com", to_email="emp@example.com", subject="Reminder", body="Fill timesheet")
        self.assertEqual(recipients, ["me@example.com"])
        text = msg.get_payload()[0].get_payload(decode=True).decode()
        self.assertTrue(text.startswith("TEST MODE - redirected. Intended to: emp@example.com"))

    def test_no_redirect_when_not_set(self):
        recipients, msg = send("", to_email="pm@example.com", cc_email=["a@example.com"], subject="Report", body="x")
        self.assertEqual(recipients, ["pm@example.com", "a@example.com"])
        self.assertEqual(msg["Subject"], "Report")


if __name__ == "__main__":
    unittest.main()
