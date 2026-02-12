"""
Test script to validate SharePoint configuration.
"""

import logging
from config.settings import settings
from services.sharepoint_excel_service import SharePointExcelService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_sharepoint_credentials():
    """Test SharePoint credentials and configuration."""
    
    logger.info("=== Testing SharePoint Configuration ===")
    
    # Step 1: Check environment variables
    logger.info("\n1. Checking environment variables...")
    logger.info(f"TENANT_ID: {'✓ Set' if settings.tenant_id else '✗ Missing'}")
    logger.info(f"CLIENT_ID: {'✓ Set' if settings.client_id else '✗ Missing'}")
    logger.info(f"CLIENT_SECRET: {'✓ Set' if settings.client_secret else '✗ Missing'}")
    logger.info(f"SITE_ID: {'✓ Set' if settings.site_id else '✗ Missing'}")
    logger.info(f"DRIVE_ID: {'✓ Set' if settings.drive_id else '✗ Missing'}")
    logger.info(f"FILE_NAME: {'✓ Set' if settings.file_name else '✗ Missing'}")
    
    if not all([settings.tenant_id, settings.client_id, settings.client_secret]):
        logger.error("\n✗ Missing required SharePoint credentials!")
        logger.error("Please check your .env file")
        return False
    
    # Step 2: Try to get access token
    logger.info("\n2. Testing authentication...")
    try:
        service = SharePointExcelService()
        service._get_access_token()
        logger.info("✓ Successfully obtained access token")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to get access token: {e}")
        logger.error("\nPlease verify:")
        logger.error("1. Your Azure AD App Registration is active")
        logger.error("2. CLIENT_ID and TENANT_ID are correct")
        logger.error("3. CLIENT_SECRET is valid and not expired")
        logger.error("4. The app has 'Sites.ReadWrite.All' permission")
        return False

if __name__ == "__main__":
    success = test_sharepoint_credentials()
    if success:
        logger.info("\n=== ✓ SharePoint configuration is valid ===")
    else:
        logger.error("\n=== ✗ SharePoint configuration has issues ===")