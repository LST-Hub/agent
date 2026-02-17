import datetime
import subprocess
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

now = datetime.datetime.utcnow()
minute = now.minute
hour = now.hour
weekday = now.weekday()  # Monday=0, Wednesday=2, Friday=4

logger.info(f"Scheduler running at UTC: {now.strftime('%Y-%m-%d %H:%M')} | weekday={weekday}")

def run(cmd):
    logger.info(f"Executing: {cmd}")
    subprocess.run(cmd, shell=True)

def in_window(target_hour, target_minute, window=5):
    """
    Check if current time is within a window of the target time.
    Window of 5 means: target_minute to target_minute+5
    This handles Render's scheduling delays.
    """
    target = datetime.datetime(now.year, now.month, now.day, target_hour, target_minute)
    diff = (now - target).total_seconds() / 60  # difference in minutes
    return 0 <= diff < window  # triggers if within 5-minute window

# DAILY — Every day at 8 AM IST → 2:30 AM UTC
if in_window(11, 40):
    logger.info("Triggering Daily Agent...")
    run("python main.py daily")

# WEEKLY — Wednesday & Friday at 6 PM IST → 12:30 UTC
elif in_window(12, 30) and weekday in [2, 4]:
    logger.info("Triggering Weekly Agent...")
    run("python main.py weekly")

# WEEKLY REPORT — Monday at 9 AM IST → 3:30 AM UTC
elif in_window(3, 30) and weekday == 0:
    logger.info("Triggering Weekly Report Agent...")
    run("python main.py weekly-report")


else:
    logger.info("No agent scheduled for this time window. Skipping.")