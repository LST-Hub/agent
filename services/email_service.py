"""
Email Notification Service.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import atexit
import os
import time
from config.settings import settings
import logging
from typing import Optional, Union, List

logger = logging.getLogger(__name__)

class EmailService:
    """
    Handles sending email notifications for alerts and summaries.

    Sending is gentle on the mail server (shared hosting blocks bursts of logins):
    - one SMTP connection per run, shared by every EmailService instance,
    - EMAIL_SEND_DELAY_SECONDS between messages,
    - at most EMAIL_MAX_PER_RUN messages per run,
    - temporary errors (4xx, dropped connection) are retried with a growing wait,
    - once the server refuses (login refused, rate limit, other 5xx), nothing more
      is sent in this run, so a block is never made worse.
    """

    # Shared by all instances in this process (one run = one process)
    _conn: Optional[smtplib.SMTP] = None
    _last_sent: float = 0.0
    _sent_this_run: int = 0
    _stopped_reason: str = ""
    _sleep = staticmethod(time.sleep)

    def __init__(self):
        self.service = settings.smtp.service
        self.host = settings.smtp.host
        self.port = settings.smtp.port
        self.secure = settings.smtp.secure
        self.user = settings.smtp.user
        self.password = settings.smtp.password
        self.from_email = settings.smtp.from_email
        self.test_recipient = (settings.email_test_recipient or "").strip()
        # smtp (default) or graph (Microsoft 365 sendMail from GRAPH_SENDER_MAILBOX)
        self.transport = settings.email_transport
        self.graph_sender = settings.graph_sender_mailbox or self.from_email or ""

    def _apply_test_redirect(self, to_email, subject, body, cc_email, is_html):
        """
        Test mode (EMAIL_TEST_RECIPIENT set): every email goes only to the test address,
        with the intended recipients shown in a banner. Nobody else receives anything.
        """
        if not self.test_recipient:
            return to_email, subject, body, cc_email
        as_list = lambda v: [] if not v else (list(v) if isinstance(v, list) else [v])  # noqa: E731
        intended_to, intended_cc = ", ".join(as_list(to_email)), ", ".join(as_list(cc_email))
        if is_html:
            import html as html_lib
            banner = (f"<div style='background:#fff3cd;border:1px solid #e0c36c;padding:8px;margin-bottom:12px;'>"
                      f"<strong>TEST MODE</strong> - this email was redirected to you. Intended to: "
                      f"{html_lib.escape(intended_to) or '-'}"
                      + (f"; cc: {html_lib.escape(intended_cc)}" if intended_cc else "") + "</div>")
            body = body.replace("<body>", "<body>" + banner, 1) if "<body>" in body else banner + body
        else:
            body = (f"TEST MODE - redirected. Intended to: {intended_to or '-'}"
                    + (f"; cc: {intended_cc}" if intended_cc else "") + "\n\n" + body)
        logger.info(f"Test mode: '{subject}' redirected from {intended_to} to {self.test_recipient}")
        return self.test_recipient, f"[TEST] {subject}", body, None

    def send_alert(self, to_email: Union[str, List[str]], subject: str, body: str, cc_email: Union[str, List[str]] = None, is_html: bool = False, attachments: List[dict] = None) -> bool:
        """
        Sends an email alert with optional CC and attachments.
        
        Args:
            to_email: Recipient email address or list of addresses
            subject: Email subject
            body: Email body text or HTML
            cc_email: Optional CC email address or list of addresses
            is_html: If True, body is treated as HTML content
            attachments: List of dicts with keys:
                - 'filename': str - name of the file
                - 'data': bytes - file content
        """
        try:
            to_email, subject, body, cc_email = self._apply_test_redirect(to_email, subject, body, cc_email, is_html)
            if self.transport == "graph":
                as_list = lambda v: [] if not v else (list(v) if isinstance(v, list) else [v])  # noqa: E731
                to_list, cc_list = as_list(to_email), as_list(cc_email)
                if not self._deliver_graph(to_list, cc_list, subject, body, is_html, attachments or []):
                    return False
                shown = to_list + cc_list
                logger.info(f"Email sent via Microsoft 365 to {', '.join(shown[:3])}{'...' if len(shown) > 3 else ''}: {subject}")
                return True

            msg = MIMEMultipart()
            msg['From'] = self.from_email

            # Handle single email or list of emails for To
            if isinstance(to_email, list):
                msg['To'] = ', '.join(to_email)
                recipients = to_email.copy()
            else:
                msg['To'] = to_email
                recipients = [to_email]
            
            msg['Subject'] = subject
            
            # Handle single email or list of emails for CC
            if cc_email:
                if isinstance(cc_email, list):
                    msg['Cc'] = ', '.join(cc_email)
                    recipients.extend(cc_email)
                else:
                    msg['Cc'] = cc_email
                    recipients.append(cc_email)
            
            # Attach body as plain text or HTML
            if is_html:
                msg.attach(MIMEText(body, 'html'))
            else:
                msg.attach(MIMEText(body, 'plain'))

            # Attach files if provided
            if attachments:
                for attachment in attachments:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment['data'])
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{attachment["filename"]}"'
                    )
                    msg.attach(part)

            if not self._deliver(recipients, msg.as_string(), subject):
                return False

            to_display = ', '.join(recipients[:3]) + ('...' if len(recipients) > 3 else '')
            logger.info(f"Email sent to {to_display}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------ delivery

    def _connect(self) -> smtplib.SMTP:
        if self.port == 465:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=60)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=60)
            if self.secure or self.port == 587:
                server.starttls()
        if self.user and self.password:
            server.login(self.user, self.password)
        return server

    @classmethod
    def close(cls):
        """Close the shared SMTP connection (called automatically at exit)."""
        if cls._conn is not None:
            try:
                cls._conn.quit()
            except Exception:
                pass
            cls._conn = None

    @classmethod
    def reset_run(cls):
        """Forget the per-run counters (tests, or a long-running process starting a new run)."""
        cls.close()
        cls._last_sent, cls._sent_this_run, cls._stopped_reason = 0.0, 0, ""

    def _stop(self, reason: str, subject: str) -> bool:
        EmailService._stopped_reason = reason[:300]
        logger.error(f"Not sent ('{subject}'): {EmailService._stopped_reason}. No more emails will be sent in this run.")
        return False

    def _gate(self, subject: str) -> bool:
        """False when this run has stopped sending or reached its cap; otherwise waits the pause and returns True."""
        cls = EmailService
        if cls._stopped_reason:
            logger.error(f"Not sent ('{subject}'): sending stopped earlier in this run ({cls._stopped_reason}).")
            return False
        if cls._sent_this_run >= settings.email_max_per_run:
            return self._stop(f"EMAIL_MAX_PER_RUN={settings.email_max_per_run} reached", subject)
        wait = cls._last_sent + settings.email_send_delay_seconds - time.monotonic()
        if cls._last_sent and wait > 0:
            cls._sleep(wait)
        return True

    # ------------------------------------------------------------ Microsoft 365 (Graph sendMail)

    _graph_token: str = ""
    _graph_token_expires: float = 0.0

    def _graph_access_token(self) -> str:
        cls = EmailService
        if cls._graph_token and time.monotonic() < cls._graph_token_expires - 60:
            return cls._graph_token
        if not all([settings.tenant_id, settings.client_id, settings.client_secret]):
            raise ValueError("TENANT_ID, CLIENT_ID and CLIENT_SECRET must be set for EMAIL_TRANSPORT=graph")
        import requests
        r = requests.post(f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token", timeout=30, data={
            "client_id": settings.client_id, "client_secret": settings.client_secret,
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
        r.raise_for_status()
        body = r.json()
        cls._graph_token, cls._graph_token_expires = body["access_token"], time.monotonic() + int(body.get("expires_in", 3600))
        return cls._graph_token

    @staticmethod
    def graph_message(to_list: List[str], cc_list: List[str], subject: str, body: str, is_html: bool,
                      attachments: List[dict]) -> dict:
        """The sendMail request body."""
        import base64
        address = lambda a: {"emailAddress": {"address": a.strip()}}  # noqa: E731
        message = {
            "subject": subject,
            "body": {"contentType": "HTML" if is_html else "Text", "content": body},
            "toRecipients": [address(a) for a in to_list if a and a.strip()],
        }
        if cc_list:
            message["ccRecipients"] = [address(a) for a in cc_list if a and a.strip()]
        if attachments:
            message["attachments"] = [{
                "@odata.type": "#microsoft.graph.fileAttachment", "name": a["filename"],
                "contentBytes": base64.b64encode(a["data"]).decode()} for a in attachments]
        return {"message": message, "saveToSentItems": True}

    def _deliver_graph(self, to_list, cc_list, subject, body, is_html, attachments) -> bool:
        import requests
        if not self.graph_sender:
            return self._stop("GRAPH_SENDER_MAILBOX is not set", subject)
        if not self._gate(subject):
            return False
        cls = EmailService
        payload = self.graph_message(to_list, cc_list, subject, body, is_html, attachments)
        url = f"https://graph.microsoft.com/v1.0/users/{self.graph_sender}/sendMail"
        retries = settings.email_retries
        for attempt in range(retries + 1):
            try:
                r = requests.post(url, json=payload, timeout=120,
                                  headers={"Authorization": f"Bearer {self._graph_access_token()}"})
            except requests.RequestException as e:
                if attempt == retries:
                    return self._stop(f"cannot reach Microsoft 365 ({e})", subject)
                cls._sleep(settings.email_retry_wait_seconds * (attempt + 1))
                continue
            if r.status_code == 202:
                cls._last_sent = time.monotonic()
                cls._sent_this_run += 1
                return True
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                pause = int(r.headers.get("Retry-After") or settings.email_retry_wait_seconds * (attempt + 1))
                logger.warning(f"Microsoft 365 said {r.status_code} for '{subject}'; retrying in {pause}s.")
                cls._sleep(pause)
                continue
            if r.status_code == 400:
                # Something about this message (e.g. a bad address or a too-large attachment); carry on with others
                logger.error(f"Microsoft 365 rejected '{subject}': {r.text[:500]}")
                cls._last_sent = time.monotonic()
                return False
            return self._stop(f"Microsoft 365 said {r.status_code}: {r.text[:200]}", subject)
        return False

    # ------------------------------------------------------------ SMTP

    def _deliver(self, recipients: List[str], message: str, subject: str) -> bool:
        cls = EmailService
        if not self._gate(subject):
            return False

        retries = settings.email_retries
        for attempt in range(retries + 1):
            try:
                if cls._conn is None:
                    cls._conn = self._connect()
                cls._conn.sendmail(self.from_email, recipients, message)
                cls._last_sent = time.monotonic()
                cls._sent_this_run += 1
                return True
            except smtplib.SMTPRecipientsRefused as e:
                # Only this message's address is bad; carry on with the others
                logger.error(f"Recipient refused for '{subject}': {list(e.recipients)}")
                cls._last_sent = time.monotonic()
                return False
            except smtplib.SMTPResponseException as e:
                self.close()
                temporary = 400 <= e.smtp_code < 500 and not isinstance(e, smtplib.SMTPAuthenticationError)
                if not temporary or attempt == retries:
                    return self._stop(f"mail server said {e.smtp_code} {e.smtp_error}", subject)
                pause = settings.email_retry_wait_seconds * (attempt + 1)
                logger.warning(f"Temporary mail error {e.smtp_code} for '{subject}'; retrying in {pause}s.")
                cls._sleep(pause)
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError) as e:
                self.close()
                if attempt == retries:
                    return self._stop(f"cannot reach the mail server ({e})", subject)
                pause = settings.email_retry_wait_seconds * (attempt + 1)
                logger.warning(f"Mail connection problem for '{subject}' ({e}); retrying in {pause}s.")
                cls._sleep(pause)
        return False


atexit.register(EmailService.close)

