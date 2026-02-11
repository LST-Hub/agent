"""
Email Notification Service.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config.settings import settings
import logging

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
        self.from_email = settings.smtp.from_email  # Add this line

    def send_alert(self, to_email: str, subject: str, body: str, cc_email: str = None, is_html: bool = False) -> bool:
        """
        Sends an email alert with optional CC.
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            body: Email body text or HTML
            cc_email: Optional CC email address
            is_html: If True, body is treated as HTML content
        """
        try:
            msg = MIMEMultipart()
            msg['From'] = self.from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            if cc_email:
                msg['Cc'] = cc_email
            
            # Attach body as plain text or HTML
            if is_html:
                msg.attach(MIMEText(body, 'html'))
            else:
                msg.attach(MIMEText(body, 'plain'))
            
            # Recipients list needs to include To and CC for the envelope
            recipients = [to_email]
            if cc_email:
                recipients.append(cc_email)

            if self.secure:
                # For SSL (port 465)
                if self.port == 465:
                    with smtplib.SMTP_SSL(self.host, self.port) as server:
                        server.login(self.user, self.password)
                        server.send_message(msg, to_addrs=recipients)
                # For STARTTLS (port 587 or 25)
                else:
                    with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                        server.ehlo()
                        server.starttls()
                        server.ehlo()
                        server.login(self.user, self.password)
                        server.send_message(msg, to_addrs=recipients)
            else:
                # Plain SMTP without encryption
                with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                    server.ehlo()
                    server.login(self.user, self.password)
                    server.send_message(msg, to_addrs=recipients)
            
            logger.info(f"Email sent to {to_email} (CC: {cc_email}): {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}", exc_info=True)
            return False

