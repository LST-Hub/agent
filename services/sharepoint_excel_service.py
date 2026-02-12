"""
SharePoint Excel Service for exporting reports.
"""

import requests
import pandas as pd
from datetime import datetime
from typing import List, Dict
import logging
import re
from config.settings import settings

logger = logging.getLogger(__name__)

class SharePointExcelService:
    """
    Handles exporting data to Excel files on SharePoint using Microsoft Graph API.
    """
    
    def __init__(self):
        self.tenant_id = settings.tenant_id
        self.client_id = settings.client_id
        self.client_secret = settings.client_secret
        self.site_id = settings.site_id
        self.drive_id = settings.drive_id
        self.file_name = settings.file_name
        self.table_name = settings.table_name
        self.access_token = None
        
        # Validate required settings
        self._validate_settings()
    
    def _validate_settings(self):
        """Validate that all required SharePoint settings are configured."""
        required_settings = {
            'tenant_id': self.tenant_id,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'site_id': self.site_id,
            'drive_id': self.drive_id,
            'file_name': self.file_name
        }
        
        missing = [key for key, value in required_settings.items() if not value]
        
        if missing:
            error_msg = f"Missing required SharePoint settings: {', '.join(missing)}"
            logger.error(error_msg)
            logger.error("Please check your .env file and ensure all SharePoint credentials are set")
            raise ValueError(error_msg)
        
        logger.info("SharePoint settings validated successfully")
        logger.info(f"Tenant ID: {self.tenant_id[:10]}...")
        logger.info(f"Client ID: {self.client_id[:10]}...")
    
    def _clean_text_for_excel(self, text):
        """
        Remove illegal characters from text for Excel compatibility.
        Removes control characters (0x00-0x1F) except tab, newline, and carriage return.
        """
        if text is None:
            return ""
        
        if not isinstance(text, str):
            text = str(text)
        
        # Remove illegal XML characters (control characters except tab, newline, carriage return)
        # Pattern matches characters 0x00-0x08, 0x0B-0x0C, 0x0E-0x1F
        illegal_chars_pattern = re.compile(r'[\x00-\x08\x0B-\x0C\x0E-\x1F]')
        cleaned_text = illegal_chars_pattern.sub('', text)
        
        return cleaned_text
    
    def _get_access_token(self) -> str:
        """Get Microsoft Graph API access token."""
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"
        }
        
        try:
            logger.info(f"Requesting access token from: {url}")
            logger.info(f"Using client_id: {self.client_id[:10]}...")
            
            response = requests.post(url, data=data)
            
            # Log response details for debugging
            logger.info(f"Token request status code: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"Token request failed with status: {response.status_code}")
                logger.error(f"Response: {response.text}")
                
                # Parse error details if available
                try:
                    error_data = response.json()
                    error_description = error_data.get('error_description', 'No description')
                    error_code = error_data.get('error', 'Unknown error')
                    logger.error(f"Error code: {error_code}")
                    logger.error(f"Error description: {error_description}")
                except:
                    pass
            
            response.raise_for_status()
            self.access_token = response.json()["access_token"]
            logger.info("Successfully obtained access token")
            return self.access_token
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error getting access token: {e}")
            logger.error("Please verify your SharePoint credentials:")
            logger.error("1. TENANT_ID is correct")
            logger.error("2. CLIENT_ID is correct")
            logger.error("3. CLIENT_SECRET is valid and not expired")
            logger.error("4. The App Registration has the correct permissions")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting access token: {e}")
            raise
    
    def _get_headers(self) -> dict:
        """Get authorization headers."""
        if not self.access_token:
            self._get_access_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def update_excel_file(self, data: List[Dict], week_start: datetime) -> str:
        """
        Update Excel file on SharePoint with weekly timesheet data.
        Returns the SharePoint file URL.
        """
        try:
            logger.info(f"Processing {len(data)} timesheet records")
            
            # Convert data to DataFrame
            df = pd.DataFrame(data)
            
            logger.info(f"DataFrame created with shape: {df.shape}")
            
            # Clean all text columns to remove illegal characters
            text_columns = ['description', 'rejectionNote', 'userName', 'userEmail', 'projectName', 'status']
            for col in text_columns:
                if col in df.columns:
                    df[col] = df[col].apply(self._clean_text_for_excel)
                    logger.info(f"Cleaned column: {col}")
            
            # Format the data
            if not df.empty:
                # Map boolean values
                df['approved'] = df['approved'].map({1: 'Yes', 0: 'No', True: 'Yes', False: 'No'})
                df['rejected'] = df['rejected'].map({1: 'Yes', 0: 'No', True: 'Yes', False: 'No'})
                
                # Format dates
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                
                if 'rejectedAt' in df.columns:
                    df['rejectedAt'] = pd.to_datetime(df['rejectedAt'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
                
                # Handle None/NaN values
                df = df.fillna('')
                
                logger.info("Data formatting completed")
            
            # Generate file name with week info
            week_str = week_start.strftime('%Y-W%U')
            file_name = f"{self.file_name}_{week_str}.xlsx"
            logger.info(f"Target file name: {file_name}")
            
            # Upload or update the Excel file
            logger.info("Creating Excel content...")
            excel_content = self._create_excel_content(df)
            
            # Build SharePoint file URL
            file_url = f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drives/{self.drive_id}/root:/{file_name}"
            upload_url = f"{file_url}:/content"
            
            logger.info(f"Uploading to SharePoint: {upload_url}")
            
            response = requests.put(
                upload_url,
                headers=self._get_headers(),
                data=excel_content
            )
            
            if response.status_code not in [200, 201]:
                logger.error(f"Upload failed with status: {response.status_code}")
                logger.error(f"Response: {response.text}")
            
            response.raise_for_status()
            
            file_info = response.json()
            sharepoint_url = file_info.get('webUrl', '')
            
            logger.info(f"Successfully updated Excel file: {file_name}")
            logger.info(f"SharePoint URL: {sharepoint_url}")
            return sharepoint_url
            
        except Exception as e:
            logger.error(f"Failed to update Excel file: {e}", exc_info=True)
            raise
    
    def _create_excel_content(self, df: pd.DataFrame) -> bytes:
        """Create Excel file content from DataFrame."""
        import io
        
        try:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Weekly Timesheets', index=False)
                
                # Auto-adjust column widths
                worksheet = writer.sheets['Weekly Timesheets']
                for idx, col in enumerate(df.columns):
                    try:
                        max_length = max(
                            df[col].astype(str).apply(len).max(),
                            len(str(col))
                        )
                        # Excel column letter (A, B, C, ...)
                        col_letter = self._get_column_letter(idx)
                        worksheet.column_dimensions[col_letter].width = min(max_length + 2, 50)
                    except Exception as e:
                        logger.warning(f"Could not adjust width for column {col}: {e}")
            
            buffer.seek(0)
            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Error creating Excel content: {e}", exc_info=True)
            raise
    
    def _get_column_letter(self, idx: int) -> str:
        """Convert column index to Excel column letter (0->A, 1->B, etc.)"""
        letter = ""
        while idx >= 0:
            letter = chr(65 + (idx % 26)) + letter
            idx = idx // 26 - 1
        return letter
    
    def get_file_url(self, week_start: datetime) -> str:
        """Get the SharePoint URL for a specific week's file."""
        week_str = week_start.strftime('%Y-W%U')
        file_name = f"{self.file_name}_{week_str}.xlsx"
        
        try:
            file_url = f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drives/{self.drive_id}/root:/{file_name}"
            response = requests.get(file_url, headers=self._get_headers())
            response.raise_for_status()
            
            return response.json().get('webUrl', '')
        except Exception as e:
            logger.error(f"Failed to get file URL: {e}")
            return ""