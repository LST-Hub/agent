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

    def send_alert(self, to_email: str, subject: str, body: str, cc_email: str = None) -> bool:
        """
        Sends an alert email.
        
        Args:
            to_email: Recipient address
            subject: Email subject
            body: Email body
            cc_email: Optional CC address
            
        Returns:
            bool: True if sent successfully, False otherwise.
        """
        msg = MIMEText(body)
        msg['Subject'] = f"[AI Ops Alert] {subject}"
        msg['From'] = self.from_email
        msg['To'] = to_email
        if cc_email:
            msg['Cc'] = cc_email

        # Recipients list needs to include To and CC for the envelope
        recipients = [to_email]
        if cc_email:
            recipients.append(cc_email)

        try:
            if self.secure:
                # Use TLS/STARTTLS for secure connection
                with smtplib.SMTP(self.host, self.port) as server:
                    server.starttls()
                    server.login(self.user, self.password)  # Still authenticate with user
                    server.send_message(msg, to_addrs=recipients)
            else:
                # Use plain SMTP without encryption
                with smtplib.SMTP(self.host, self.port) as server:
                    server.login(self.user, self.password)
                    server.send_message(msg, to_addrs=recipients)
            
            logger.info(f"Email sent to {to_email} (CC: {cc_email}): {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

