"""
SharePoint Excel Service for exporting reports.
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict
import logging
import re
import io
import pandas as pd
from openpyxl.utils import get_column_letter
from config.settings import settings
from agent.ai_service import AIAgent
from datetime import datetime, timedelta
from utils.date_utils import is_holiday, is_weekend

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
    
    def _get_tab_name(self, week_start: datetime) -> str:
        """Generate Excel sheet tab name like 'week-16-20' from week date range."""
        week_end = week_start + timedelta(days=4)  # Mon–Fri
        if week_start.month == week_end.month:
            return f"week-{week_start.day}-{week_end.day}"
        else:
            # Cross-month week e.g. Apr 28 – May 2 → "week-Apr28-May02"
            return f"week-{week_start.strftime('%b%d')}-{week_end.strftime('%b%d')}"
        
    def _download_existing_file(self, file_name: str) -> bytes | None:
        """
        Download existing Excel file from SharePoint.
        Returns file bytes if found, None if file doesn't exist yet.
        """
        try:
            url = (
                f"https://graph.microsoft.com/v1.0/sites/{self.site_id}"
                f"/drives/{self.drive_id}/root:/{file_name}:/content"
            )
            response = requests.get(url, headers=self._get_headers())
            if response.status_code == 404:
                logger.info(f"File '{file_name}' not found on SharePoint — will create new.")
                return None
            response.raise_for_status()
            logger.info(f"Downloaded existing file '{file_name}' ({len(response.content)} bytes)")
            return response.content
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            logger.error(f"Error downloading existing file: {e}")
            raise
        
    def update_excel_file(
        self,
        data: List[Dict],
        week_start: datetime,
        summary_data: List[Dict] = None,   # kept for backward compat, no longer used
        project_data: List[Dict] = None,   # kept for backward compat, no longer used
        sheet_type: str = "timesheets"
    ) -> tuple:
        try:
            type_file_map = {
                "timesheets": "Weekly_Timesheets.xlsx",
                "summary":    "Weekly_Summary.xlsx",
                "project":    "Project_Summary.xlsx",
            }
            file_name = type_file_map.get(sheet_type, f"{sheet_type}.xlsx")
            tab_name  = self._get_tab_name(week_start)
            logger.info(f"[{sheet_type}] Target file: '{file_name}' | New tab: '{tab_name}'")

            # --- Build DataFrame based on sheet_type using `data` for all types ---
            if sheet_type == "timesheets":
                df = pd.DataFrame(data)
                text_columns = ['description', 'rejectionNote', 'userName', 'userEmail', 'projectName', 'status']
                for col in text_columns:
                    if col in df.columns:
                        df[col] = df[col].apply(self._clean_text_for_excel)
                if not df.empty:
                    df['approved'] = df['approved'].map({1: 'Yes', 0: 'No', True: 'Yes', False: 'No'})
                    df['rejected'] = df['rejected'].map({1: 'Yes', 0: 'No', True: 'Yes', False: 'No'})
                    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                    if 'rejectedAt' in df.columns:
                        df['rejectedAt'] = pd.to_datetime(df['rejectedAt'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
                    df = df.fillna('')

            elif sheet_type == "summary":
                if not data:
                    logger.warning("No data provided for summary sheet, skipping.")
                    return None, None, None
                df = self._build_summary_dataframe(data, week_start)  # ✅ uses `data`

            elif sheet_type == "project":
                if not data:
                    logger.warning("No data provided for project sheet, skipping.")
                    return None, None, None
                df = self._build_project_dataframe(data, week_start)  # ✅ uses `data`

            else:
                df = pd.DataFrame(data)

            if df is None or df.empty:
                logger.warning(f"Empty DataFrame for sheet_type='{sheet_type}', skipping.")
                return None, None, None

            # Download existing file (if any) and append new tab
            existing_bytes = self._download_existing_file(file_name)
            excel_content  = self._create_excel_content(df, tab_name=tab_name, existing_bytes=existing_bytes)

            # Upload back to SharePoint
            upload_url = (
                f"https://graph.microsoft.com/v1.0/sites/{self.site_id}"
                f"/drives/{self.drive_id}/root:/{file_name}:/content"
            )
            logger.info(f"Uploading '{file_name}' to SharePoint...")
            response = requests.put(upload_url, headers=self._get_headers(), data=excel_content)
            response.raise_for_status()

            sharepoint_url = response.json().get('webUrl', '')
            logger.info(f"Uploaded successfully: {sharepoint_url}")
            return sharepoint_url, excel_content, file_name

        except Exception as e:
            logger.error(f"Failed to update Excel file: {e}", exc_info=True)
            raise

    def _build_summary_dataframe(self, summary_data: List[Dict], week_start: datetime) -> pd.DataFrame:
        """
        Build aggregated summary DataFrame grouped by user, with AI summaries per project.
        - 'work' rows: generate AI summary
        - 'leave' rows: show 'Full Day Leave' or leave type, no AI summary
        - 'idle' rows: show idle hours, no AI summary
        Calculates idle hours based on 8hrs/day * 5 working days = 40 hrs/week.
        """
        ai_agent = AIAgent()
        total_weekly_seconds = 8 * 5 * 3600  # 40 hrs

        working_days = 0
        for i in range(7):
            day = week_start + timedelta(days=i)
            day_date = day.date() if isinstance(day, datetime) else day
            if not is_weekend(day_date) and not is_holiday(day_date):
                working_days += 1
        total_weekly_seconds = working_days * 8 * 3600

        # Step 1: Compute per-user worked seconds used to calculate remaining idle capacity.
        # IMPORTANT: exclude 'idle' rows from this sum so logged idle hours
        # appear in "Total Hours Worked" but do NOT reduce the "Idle Hours (Week)".
        user_totals: Dict[int, int] = {}
        for row in summary_data:
            uid = row["userId"]
            row_type = row.get("rowType", "work")
            # only count productive work and leave toward used capacity
            if row_type in ("work", "leave"):
                user_totals[uid] = user_totals.get(uid, 0) + (row["totalWorkedSeconds"] or 0)

        def fmt(seconds):
            if not seconds:
                return "0 hrs"
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h} hrs {m} mins" if m else f"{h} hrs"

        rows = []
        for row in summary_data:
            project_name = (row.get("projectName") or "Idle").strip()
            row_type = row.get("rowType", "work")
            uid = row["userId"]

            # ✅ Idle hours is the SAME for ALL rows of the same user
            # = 40 hrs - (sum of only work + leave hours for that user)
            # Idle logged hours are NOT added — they are part of the 40 hr capacity
            user_worked = user_totals.get(uid, 0)
            idle_seconds = max(total_weekly_seconds - user_worked, 0)

            is_billable = row.get("billable", False)
            billable_label = "Yes" if is_billable else "No"

            if row_type == "leave":
                task_name = (row.get("taskNames") or "").strip()
                leave_label = task_name if task_name and task_name != "N/A" else project_name
                rows.append({
                    "Resource Name":      row["userName"],
                    "Project Name":       project_name,
                    "Billable":           "No",
                    "Tasks Worked On":    leave_label,
                    "Allocated Hours":    row.get("allocatedHours", "N/A"),
                    "Total Hours Worked": row["totalWorkedFormatted"],
                    "Idle Hours (Week)":  fmt(idle_seconds),
                    #"AI Summary":         "The resource was on leave during this period.",
                })

            elif row_type == "idle":
                # ✅ Total Hours Worked = 0 (idle is not productive work)
                # ✅ Idle Hours = 40 - actual_worked (same as other rows for this user)
                # This means if user worked 3 hrs on LST + 5 hrs idle logged,
                # idle hours = 40 - 3 = 37 hrs for BOTH rows
                rows.append({
                    "Resource Name":      row["userName"],
                    "Project Name":       "Idle",
                    "Billable":           "No",
                    "Tasks Worked On":    "N/A",
                    "Allocated Hours":    "N/A",
                    "Total Hours Worked": row.get("totalWorkedFormatted", "0 hrs"),
                    "Idle Hours (Week)":  fmt(idle_seconds),  # ✅ Same 37 hrs, no extra addition
                    #"AI Summary":         "No project assigned. Resource logged idle hours.",
                })

            else:
                descriptions = (row.get("allDescriptions") or "").strip()
                real_descriptions = [
                    d.strip() for d in descriptions.split("|")
                    if d.strip() and len(d.strip()) > 3
                ]

                # if real_descriptions:
                #     metrics_text = (
                #         f"Resource: {row['userName']}\n"
                #         f"Project: {project_name}\n"
                #         f"Tasks worked on: {row['taskNames']}\n"
                #         f"Work descriptions:\n" + "\n".join(f"- {d}" for d in real_descriptions)
                #     )
                #     logger.info(f"Generating AI summary for {row['userName']} on {project_name}")
                #     #ai_summary = ai_agent.generate_weekly_summary(metrics_text)
                # else:
                #     logger.info(f"No descriptions for {row['userName']} on {project_name}, skipping AI")
                #     #ai_summary = "No descriptions provided for this project."

                rows.append({
                    "Resource Name":      row["userName"],
                    "Project Name":       project_name,
                    "Billable":           billable_label,
                    "Tasks Worked On":    row["taskNames"],
                    "Allocated Hours":    row.get("allocatedHours", "N/A"),
                    "Total Hours Worked": row["totalWorkedFormatted"],
                    "Idle Hours (Week)":  fmt(idle_seconds),
                    #"AI Summary":         self._clean_text_for_excel(ai_summary),
                })

        df = pd.DataFrame(rows)
        return df

    def _build_project_dataframe(self, project_data: List[Dict], week_start: datetime) -> pd.DataFrame:
        """Build a DataFrame from project summary data for the Project Summary sheet."""
        if not project_data:
            return pd.DataFrame()

        def _fmt_seconds(sec):
            if not sec:
                return "0 hrs"
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            return f"{h} hrs {m} mins" if m else f"{h} hrs"

        proj_rows = []
        for p in project_data:
            proj_rows.append({
                "Project Name": p.get("projectName", ""),
                "Weekly Report": "",
                "Comments": "",
                "Rahul Remarks": "",
                "Pro. Type": p.get("billable", ""),
                "PM": p.get("pmName", ""),
                "All Hours Approved/Rejected in TaskSprint?": f"Approved: {_fmt_seconds(p.get('approvedBillableSeconds'))} | Rejected: {_fmt_seconds(p.get('rejectedSeconds'))}",
                "Current Status": p.get("statusName", ""),
                "Approved Billable hours": _fmt_seconds(p.get("approvedBillableSeconds")),
                "No of hours booked in TS": _fmt_seconds(p.get("totalWorkedSeconds"))
            })

        return pd.DataFrame(proj_rows)


    def _create_excel_content(
        self,
        df: pd.DataFrame,
        tab_name: str,
        existing_bytes: bytes = None,
        # legacy params kept for backward compatibility
        summary_df: pd.DataFrame = None,
        project_df: pd.DataFrame = None,
    ) -> bytes:
        """
        Append `df` as a new sheet named `tab_name` into `existing_bytes` workbook.
        If `existing_bytes` is None, creates a fresh workbook.
        If a sheet with `tab_name` already exists it will be overwritten.
        """
        try:
            from openpyxl import load_workbook

            buffer = io.BytesIO()

            if existing_bytes:
                # Load the existing workbook
                wb_buffer = io.BytesIO(existing_bytes)
                with pd.ExcelWriter(wb_buffer, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                    # Reload workbook so ExcelWriter keeps all existing sheets
                    df.to_excel(writer, sheet_name=tab_name, index=False)
                    self._auto_adjust_columns(writer, df, tab_name)
                wb_buffer.seek(0)
                buffer.write(wb_buffer.read())
            else:
                # No existing file — create from scratch
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name=tab_name, index=False)
                    self._auto_adjust_columns(writer, df, tab_name)

            buffer.seek(0)
            logger.info(f"Excel content created — tab '{tab_name}' written successfully.")
            return buffer.getvalue()

        except Exception as e:
            logger.error(f"Error creating Excel content: {e}", exc_info=True)
            raise

    def _auto_adjust_columns(self, writer, df: pd.DataFrame, sheet_name: str):
        """Auto-adjust column widths for a given sheet."""
        worksheet = writer.sheets[sheet_name]
        for idx, col in enumerate(df.columns):
            try:
                max_length = max(
                    df[col].astype(str).apply(len).max() if not df.empty else 0,
                    len(str(col))
                )
                col_letter = get_column_letter(idx + 1)
                worksheet.column_dimensions[col_letter].width = min(max_length + 2, 60)
            except Exception as e:
                logger.warning(f"Could not adjust width for column {col}: {e}")

    def get_file_url(self, week_start: datetime, sheet_type: str = "timesheets") -> str:
        """Get the SharePoint URL for a specific report type's permanent file."""
        type_file_map = {
            "timesheets": "Weekly_Timesheets.xlsx",
            "summary":    "Weekly_Summary.xlsx",
            "project":    "Project_Summary.xlsx",
        }
        file_name = type_file_map.get(sheet_type, f"{sheet_type}.xlsx")

        try:
            url = (
                f"https://graph.microsoft.com/v1.0/sites/{self.site_id}"
                f"/drives/{self.drive_id}/root:/{file_name}"
            )
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json().get('webUrl', '')
        except Exception as e:
            logger.error(f"Failed to get file URL for '{file_name}': {e}")
            return ""