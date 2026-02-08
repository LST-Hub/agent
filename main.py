"""
Main entry point for the Operations Agent.
Usage:
    python main.py daily
    python main.py weekly

"""
import sys
import os
import logging
from agents.daily_agent import DailyAgent
from agents.weekly_agent import WeeklyAgent

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def print_tree(startpath):
    for root, dirs, files in os.walk(startpath):
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * (level)
        print('{}{}/'.format(indent, os.path.basename(root)))
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            print('{}{}'.format(subindent, f))

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py [daily|weekly|tree]")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "daily":
        logger.info("Running Daily Agent...")
        agent = DailyAgent()
        agent.run()
    elif command == "weekly":
        logger.info("Running Weekly Agent...")
        agent = WeeklyAgent()
        agent.run()
    elif command == "tree":
        print_tree(".")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
