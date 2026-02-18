"""
Excel export service for billable project reports.
"""

import pandas as pd
from datetime import datetime
from typing import List, Dict
import logging
from services.sharepoint_excel_service import SharePointExcelService

logger = logging.getLogger(__name__)

class BillableExcelService(SharePointExcelService):
    """
    Handles exporting billable project data to Excel on SharePoint.
    """
    
    def export_billable_report(
        self, 
        report_data: List[Dict], 
        week_start: datetime
    ) -> str:
        """
        Export billable project report to Excel on SharePoint.
        
        Returns SharePoint file URL.
        """
        try:
            logger.info(f"Processing {len(report_data)} billable projects")
            
            # Create main project summary sheet
            project_summary = self._create_project_summary(report_data)
            
            # Create resource details sheet
            resource_details = self._create_resource_details(report_data)
            
            # Create missing timesheets sheet
            missing_timesheets = self._create_missing_timesheets(report_data)
            
            # Generate file name
            week_str = week_start.strftime('%Y-W%U')
            file_name = f"Billable_Projects_Report_{week_str}.xlsx"
            
            # Create Excel with multiple sheets
            excel_content = self._create_multi_sheet_excel(
                project_summary,
                resource_details,
                missing_timesheets
            )
            
            # Upload to SharePoint
            file_url = f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drives/{self.drive_id}/root:/{file_name}"
            upload_url = f"{file_url}:/content"
            
            import requests
            response = requests.put(
                upload_url,
                headers=self._get_headers(),
                data=excel_content
            )
            
            response.raise_for_status()
            
            file_info = response.json()
            sharepoint_url = file_info.get('webUrl', '')
            
            logger.info(f"Successfully uploaded billable report: {file_name}")
            return sharepoint_url
            
        except Exception as e:
            logger.error(f"Failed to export billable report: {e}", exc_info=True)
            raise
    
    def _create_project_summary(self, report_data: List[Dict]) -> pd.DataFrame:
        """Create project summary DataFrame."""
        summary_rows = []
        
        for project in report_data:
            summary_rows.append({
                "Project ID": project["project_id"],
                "Project Name": project["project_name"],
                "PM Name": project["pm_name"],
                "Status": project["status_name"],
                "Estimated End Date": project["estimated_end_date"],
                "Actual End Date": project["actual_end_date"],
                "Week Start": project["week_start"],
                "Week End": project["week_end"],
                "Total Allocated Hours": project["total_allocated_hours"],
                "Total Worked Hours": project["total_worked_hours"],
                "Approved Billable Hours": project["approved_billable_hours"],
                "Variance": project["variance"],
                "Variance Type": project["variance_type"]
            })
        
        return pd.DataFrame(summary_rows)
    
    def _create_resource_details(self, report_data: List[Dict]) -> pd.DataFrame:
        """Create resource details DataFrame with task breakdown."""
        detail_rows = []
        
        for project in report_data:
            for resource in project["resources"]:
                # Add resource summary row
                base_row = {
                    "Project ID": project["project_id"],
                    "Project Name": project["project_name"],
                    "User ID": resource["user_id"],
                    "User Name": resource["user_name"],
                    "User Email": resource["user_email"],
                    "Allocated Hours": resource["allocated_hours"],
                    "Worked Hours": resource["worked_hours"],
                    "Approved Hours": resource["approved_hours"],
                    "Variance": resource["variance"]
                }
                
                # Add task breakdown
                if resource["tasks"]:
                    for task in resource["tasks"]:
                        detail_rows.append({
                            **base_row,
                            "Task ID": task["task_id"],
                            "Task Name": task["task_name"],
                            "Task Hours": task["hours_spent"]
                        })
                else:
                    detail_rows.append({
                        **base_row,
                        "Task ID": "",
                        "Task Name": "No tasks",
                        "Task Hours": 0
                    })
        
        return pd.DataFrame(detail_rows)
    
    def _create_missing_timesheets(self, report_data: List[Dict]) -> pd.DataFrame:
        """Create missing timesheets DataFrame."""
        missing_rows = []
        
        for project in report_data:
            for missing in project["missing_timesheets"]:
                missing_rows.append({
                    "Project ID": project["project_id"],
                    "Project Name": project["project_name"],
                    "User ID": missing["user_id"],
                    "User Name": missing["user_name"],
                    "User Email": missing["user_email"],
                    "Missing Date": missing["missing_date"]
                })
        
        return pd.DataFrame(missing_rows)
    
    def _create_multi_sheet_excel(
        self,
        project_summary: pd.DataFrame,
        resource_details: pd.DataFrame,
        missing_timesheets: pd.DataFrame
    ) -> bytes:
        """Create Excel file with multiple sheets."""
        import io
        
        buffer = io.BytesIO()
        
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            # Write each sheet
            project_summary.to_excel(
                writer, 
                sheet_name='Project Summary', 
                index=False
            )
            resource_details.to_excel(
                writer, 
                sheet_name='Resource Details', 
                index=False
            )
            missing_timesheets.to_excel(
                writer, 
                sheet_name='Missing Timesheets', 
                index=False
            )
            
            # Auto-adjust column widths for each sheet
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(cell.value)
                        except:
                            pass
                    
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
        
        buffer.seek(0)
        return buffer.getvalue()