"""
Show or change one non-secret setting in .env without opening the file (so passwords
and keys in .env are never printed). Only the keys in ALLOWED can be touched.

Usage:
    python scripts/env_list.py get KEY
    python scripts/env_list.py add KEY VALUE [VALUE ...]      # comma-separated lists (IDs, emails)
    python scripts/env_list.py remove KEY VALUE [VALUE ...]
    python scripts/env_list.py set KEY VALUE                  # replace the whole value ("" to clear)

The VPS has its own .env: a change here is local until the same change is made there.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")

LIST_KEYS = {"IGNORE_ID", "TEAMS_PLAN_IGNORE_IDS", "FOLLOWUP_IGNORE_PM_IDS", "PM_EMAIL", "MCP_ALLOWED_USERS", "MCP_SENDERS", "CHAT_ALLOCATION_USERS"}
VALUE_KEYS = {"EMAIL_TEST_RECIPIENT", "FOLLOWUP_TEST_RECIPIENT", "TEAMS_DM_TEST_RECIPIENT", "EMAIL_TRANSPORT",
              "GRAPH_SENDER_MAILBOX", "EMAIL_SEND_DELAY_SECONDS", "EMAIL_MAX_PER_RUN", "DAILY_OPS_LOOKBACK_DAYS",
              "HEALTH_INACTIVE_DAYS", "FOLLOWUP_REMINDER_DAYS", "TEAMS_LOOKBACK_DAYS", "LOG_LEVEL", "TIMESHEET_REMINDER_CHANNEL",
              "TASKSPRINT_URL", "TASKSPRINT_AGENT_EMAIL"}
ALLOWED = LIST_KEYS | VALUE_KEYS


def read_lines():
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        return f.read().splitlines()


def find(lines, key):
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and "=" in line and not line.lstrip().startswith("#"):
            return i, line.split("=", 1)[1].strip().strip('"').strip("'")
    return None, ""


def split_list(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    action, key, values = sys.argv[1], sys.argv[2], sys.argv[3:]
    if key not in ALLOWED:
        sys.exit(f"Refused: {key} is not an allowed setting. Allowed: {', '.join(sorted(ALLOWED))}")
    lines = read_lines()
    index, current = find(lines, key)

    if action == "get":
        print(f"{key}={current}" if index is not None else f"{key} is not set")
        return
    if action in ("add", "remove"):
        if key not in LIST_KEYS:
            sys.exit(f"{key} is not a list; use set.")
        items = split_list(current)
        for v in (x.strip() for raw in values for x in raw.split(",")):
            if not v:
                continue
            if action == "add" and v not in items:
                items.append(v)
            elif action == "remove" and v in items:
                items.remove(v)
        new = ",".join(items)
    elif action == "set":
        new = values[0] if values else ""
    else:
        sys.exit(__doc__)

    if index is None:
        lines.append(f"{key}={new}")
    else:
        lines[index] = f"{key}={new}"
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{key}: '{current}' -> '{new}'")


if __name__ == "__main__":
    main()
