# LST Operations Agent

Python batch agent for LST Consultancy operations. It reads the PM tool **TaskSprint**
(MySQL, read-only) and a Teams allocation chat, and emails reports to PMs. It runs on a VPS
with docker compose; host cron starts each command. Agent state (follow-ups, Teams plans,
Teams login) lives in its own PostgreSQL `agent-db`.

## Commands (`python main.py <command>`)
| Command | What it does |
|---|---|
| `project-health [--dry-run]` | Delivery risk and data-quality flags per project, emailed to PM_EMAIL |
| `daily-ops [--dry-run]` | Overtime, over-planning, plan vs logged, missing tickets, short descriptions |
| `followups [--dry-run]`, `followups-list`, `followups-close <id> [note]` | Per-PM follow-up emails, reminders, escalation |
| `forecast [--dry-run]` | 15 working days of capacity vs confirmed and projected demand |
| `forecast-skills-template`, `forecast-skills-import <csv> [--practice NAME]` | Skill sheet for the forecast |
| `teams-allocations [--sample FILE] [--no-save]` | Read the Teams chat, extract plans with OpenAI, save to agent-db |
| `teams-plan-check [--dry-run] [--dm-to EMAIL]` | 11:00 check: who has not posted today's plan; Teams reminders; summary email |
| `teams-signin [--status]`, `teams-signout` | Account the Teams reminders are sent from (device code sign-in) |
| `daily`, `weekly`, `weekly-report`, `weekly-billable-report`, `monday-summary` | Older timesheet reminders and weekly Excel reports (SharePoint upload) |

## Running locally (this PC)
- Always use `.venv\Scripts\python.exe scripts\local_run.py <command> [args] [--email-to ME] [--today YYYY-MM-DD]`.
  It sends email to Mailpit by default, or only to `--email-to`. It uses the local agent-db and refuses SharePoint uploads.
- Needs: the SSH tunnel to the PM DB on 127.0.0.1:3307:
  `ssh -i ~/.ssh/tasksprint_vps -N -L 3307:localhost:3307 root@145.223.21.61`.
  For agent-db / Mailpit: `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d agent-db mailpit`.
- Tests: `.venv\Scripts\python.exe -m unittest discover tests`. They must pass before any commit.

## Running on the VPS
- If the working folder is `/opt/agent` on `srv707772`, you are on the VPS. Run commands as
  `docker compose run --rm agent <command> [args]`, not `local_run.py`. No tunnel is needed.
- Helper scripts on the VPS:
  - TaskSprint questions (needs the image's Python packages):
    `docker compose run --rm --entrypoint python agent scripts/pm_query.py ...`
  - `.env` lists: `python3 scripts/env_list.py ...` (stdlib only, runs on the host).
- **Code and `data/*.json` are copied into the image.** After `git pull` or any edit under `data/`, run
  `docker compose build`, or the runs keep using the old files. `.env` changes need no rebuild.
- In test mode, all email goes to `EMAIL_TEST_RECIPIENT` from `.env`. For a one-off test, pass
  `-e EMAIL_TEST_RECIPIENT=<user>` to `docker compose run`.
- **The server is shared with many production apps.** Only touch the `agent` compose project (`agent-agent-run-*`,
  `agent-agent-db`). Never stop, restart, exec into or remove other containers, and never change other crontab lines.
  Ask before any `sudo` command.
- The server clock is UTC. The cron schedule is in the ops-deploy skill.

## Rules (always)
- **The TaskSprint DB is read-only.** Never write to it. For questions use `scripts/pm_query.py`; it only allows SELECT.
- **Test mode first.** Never send email or Teams messages to anyone except the user without asking first.
  Use `--email-to <user>` for email and `--dm-to <someone else>` for Teams. Teams cannot message the signed-in account itself.
- **Never print, commit or copy secrets.** Don't read `.env` whole. Use `scripts/env_list.py` for the allowed non-secret
  keys; the user edits secrets (passwords, keys, tokens) themselves.
- **Never commit** `.env`, generated `*.html` / `*.pdf` reports, or `data/teams_samples.json`.
  `AI_OPERATIONS_AGENT_BLUEPRINT.md` is gitignored.
- **Commits:** the user commits from Windows cmd. Show the message first and save it to a file for `git commit -F <file>`.
  **No Co-Authored-By or any Claude attribution: LST-Hub is the only author.**
- **Hours are seconds** in the DB (Timesheet.duration, ResourceAllocation.duration). Always filter `workspaceId = 3`.
- **Matching people by email:** the PM-tool `User.email`, `data/office_emails.json` and `data/active_emp.json` (user id -> email).

## Where settings live
- `.env`:
  - `IGNORE_ID`: excluded from every report.
  - `TEAMS_PLAN_IGNORE_IDS`: not expected to post plans.
  - `FOLLOWUP_IGNORE_PM_IDS`.
  - `PM_EMAIL`: summary report recipients.
  - Test recipients and email transport settings (`EMAIL_TRANSPORT` smtp|graph, `GRAPH_SENDER_MAILBOX`).
- `data/teams_aliases.json`: Teams chat spelling -> exact TaskSprint person or project name.
- `data/holidays.json`: company holidays, plus floaters `{"type": "floater", "employee_ids": [..]}`.
- `data/employee_skills.json`: skills and roles for the forecast.
- The VPS has its own `.env` and its own data files (from git). A local change needs a commit + `git pull` on the VPS,
  or the same `.env` edit there.

## Skills
Use the skills in `.claude/skills/`:
- `ops-run`: run reports.
- `ops-config`: change ignore lists, aliases, holidays.
- `ops-lookup`: answer questions from the data.
- `ops-teams`: Teams sign-in and reminders.
- `ops-deploy`: VPS.
