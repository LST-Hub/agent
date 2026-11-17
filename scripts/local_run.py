"""
Run any main.py command locally without anyone else receiving anything:
- By default all email goes to Mailpit (http://localhost:8025), never the real mail server.
- With --email-to ADDRESS, email goes through the real mail server in .env, but every
  message is redirected to ADDRESS only (EMAIL_TEST_RECIPIENT test mode).
- Agent state (follow-ups, saved Teams plans) goes to the local agent-db container.
- Commands that upload to SharePoint are refused.

Prerequisites:
    1. SSH tunnel to the PM database open (DATABASE_URL in .env points at it).
    2. docker compose -f docker-compose.yml -f docker-compose.local.yml up -d agent-db mailpit
       (Mailpit is not needed with --email-to; agent-db is needed for followups commands)

Usage:
    .venv\\Scripts\\python.exe scripts\\local_run.py <command> [args] [--today YYYY-MM-DD] [--email-to ADDRESS]

Examples:
    scripts\\local_run.py project-health
    scripts\\local_run.py followups --today 2026-11-16
    scripts\\local_run.py forecast --email-to me@example.com
"""

import os
import runpy
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHAREPOINT_COMMANDS = {"weekly-report", "weekly-billable-report", "monday-summary"}
NO_EMAIL_COMMANDS = {"teams-allocations", "followups-list", "forecast-skills-template", "forecast-skills-import", "tree"}
MAILPIT = ("127.0.0.1", 1025)
AGENT_DB = ("127.0.0.1", 5434)


def _reachable(addr) -> bool:
    try:
        with socket.create_connection(addr, timeout=2):
            return True
    except OSError:
        return False


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if "--today" in args:
        i = args.index("--today")
        os.environ["AGENT_TODAY"] = args[i + 1]
        del args[i:i + 2]

    email_to = None
    if "--email-to" in args:
        i = args.index("--email-to")
        email_to = args[i + 1].strip()
        del args[i:i + 2]
        if "@" not in email_to:
            sys.exit(f"--email-to needs an email address, got '{email_to}'")

    command = args[0].lower()
    if command in SHAREPOINT_COMMANDS:
        sys.exit(f"Refusing '{command}' locally: it uploads Excel files to the real SharePoint.")

    from dotenv import dotenv_values
    env_file = dotenv_values(os.path.join(ROOT, ".env"))

    # Set before the app imports config.settings; load_dotenv never overrides existing variables.
    if email_to:
        # Real mail server from .env, but every message goes only to email_to
        os.environ["EMAIL_TEST_RECIPIENT"] = email_to
        os.environ["FOLLOWUP_TEST_RECIPIENT"] = email_to
    else:
        if command not in NO_EMAIL_COMMANDS and not _reachable(MAILPIT):
            sys.exit("Mailpit is not running on 127.0.0.1:1025. Start it with:\n"
                     "  docker compose -f docker-compose.yml -f docker-compose.local.yml up -d agent-db mailpit\n"
                     "or send to your own inbox with --email-to you@example.com")
        os.environ.update({
            "EMAIL_SERVER_HOST": MAILPIT[0],
            "EMAIL_SERVER_PORT": str(MAILPIT[1]),
            "EMAIL_SERVER_SECURE": "",      # plain SMTP, which Mailpit accepts
        })
    # Agent state never goes to the server's database from a local run
    needs_agent_db = command.startswith("followups") or (command == "teams-allocations" and "--sample" not in args
                                                         and "--no-save" not in args)
    if _reachable(AGENT_DB):
        password = env_file.get("AGENT_DB_PASSWORD") or ""
        os.environ["AGENT_DATABASE_URL"] = f"postgresql+psycopg2://ops_agent:{password}@{AGENT_DB[0]}:{AGENT_DB[1]}/ops_agent"
    elif needs_agent_db:
        sys.exit("agent-db is not running on 127.0.0.1:5434 (see the docker compose command above).")
    else:
        os.environ["AGENT_DATABASE_URL"] = ""   # forecast / daily-ops run without Teams plans

    print(f"[local] email -> " + (f"{email_to} only (real mail server, test mode)" if email_to
                                   else "Mailpit (http://localhost:8025)")
          + (f", today = {os.environ['AGENT_TODAY']}" if os.getenv("AGENT_TODAY") else ""))

    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    sys.argv = [os.path.join(ROOT, "main.py")] + args
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
