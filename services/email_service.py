"""
Email Notification Service.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
from config.settings import settings
import logging
from typing import Union, List

logger = logging.getLogger(__name__)

class EmailService:
    """
    Handles sending email notifications for alerts and summaries.
    """
    
    def __init__(self):
        self.service = settings.smtp.service
        self.host = settings.smtp.host
        self.port = settings.smtp.port
        self.secure = settings.smtp.secure
        self.user = settings.smtp.user
        self.password = settings.smtp.password
        self.from_email = settings.smtp.from_email

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

            if self.secure:
                # For SSL (port 465)
                if self.port == 465:
                    with smtplib.SMTP_SSL(self.host, self.port) as server:
                        server.login(self.user, self.password)
                        server.sendmail(self.from_email, recipients, msg.as_string())
                # For STARTTLS (port 587 or 25)
                else:
                    with smtplib.SMTP(self.host, self.port) as server:
                        server.starttls()
                        server.login(self.user, self.password)
                        server.sendmail(self.from_email, recipients, msg.as_string())
            else:
                # Plain SMTP without encryption
                with smtplib.SMTP(self.host, self.port) as server:
                    server.login(self.user, self.password)
                    server.sendmail(self.from_email, recipients, msg.as_string())
            
            to_display = ', '.join(recipients[:3]) + ('...' if len(recipients) > 3 else '')
            logger.info(f"Email sent to {to_display}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            return False

