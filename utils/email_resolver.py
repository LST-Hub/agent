"""
Email Resolver Utility.
Resolves user emails from an external JSON file mapping User IDs to Office Emails.
"""

import json
import os
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class EmailResolver:
    _instance = None
    _emails: Dict[str, str] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EmailResolver, cls).__new__(cls)
            cls._instance._load_emails()
        return cls._instance

    def _load_emails(self):
        """Loads email mapping from JSON file."""
        file_path = os.path.join(os.getcwd(), 'data', 'active_emp.json')
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    self._emails = json.load(f)
                logger.info(f"Loaded {len(self._emails)} emails from {file_path}")
            else:
                logger.warning(f"Email mapping file not found at {file_path}")
                self._emails = {}
        except Exception as e:
            logger.error(f"Failed to load email mapping: {e}")
            self._emails = {}

    def get_office_email(self, user_id: int) -> Optional[str]:
        """
        Returns the office email for a given user ID.
        Returns None if not found in the mapping.
        """
        # Convert ID to string for JSON lookup
        return self._emails.get(str(user_id))
