"""
Email Notification Service.
"""

import smtplib
from email.mime.text import MIMEText
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

    def send_alert(self, to_email: str, subject: str, body: str) -> bool:
        """
        Sends an alert email.
        
        Args:
            to_email: Recipient address
            subject: Email subject
            body: Email body
            
        Returns:
            bool: True if sent successfully, False otherwise.
        """
        msg = MIMEText(body)
        msg['Subject'] = f"[AI Ops Alert] {subject}"
        msg['From'] = self.from_email  # Use from_email instead of self.user
        msg['To'] = to_email

        try:
            if self.secure:
                # Use TLS/STARTTLS for secure connection
                with smtplib.SMTP(self.host, self.port) as server:
                    server.starttls()
                    server.login(self.user, self.password)  # Still authenticate with user
                    server.send_message(msg)
            else:
                # Use plain SMTP without encryption
                with smtplib.SMTP(self.host, self.port) as server:
                    server.login(self.user, self.password)
                    server.send_message(msg)
            
            logger.info(f"Email sent to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

