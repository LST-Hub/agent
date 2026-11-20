---
name: ops-config
description: Change the operations agent's settings by prompt, without code - ignore or un-ignore people (IGNORE_ID, TEAMS_PLAN_IGNORE_IDS, FOLLOWUP_IGNORE_PM_IDS), report recipients (PM_EMAIL), test recipients, Teams name aliases, company and floater holidays. Use when the user says things like "ignore X", "stop reminding X", "X doesn't need to post plans", "add Y to the report emails", "map 'LST Website' to LST Website Work", "add a holiday".
---

# Change an agent setting

Always: find the exact record first, make the change, show before -> after, and say what is needed for the VPS.

## People and projects: find IDs (read-only)
- `.venv\Scripts\python.exe scripts\pm_query.py --find-user <name or email>`
- `.venv\Scripts\python.exe scripts\pm_query.py --find-project <name>`

If more than one match, ask which one. If none, say so; don't guess an ID.

## .env lists and values (never open .env directly)
`.venv\Scripts\python.exe scripts\env_list.py get|add|remove|set KEY [VALUES]`

| Request | Key |
|---|---|
| Leave someone out of every report (bots, external recruiters, ex-staff) | `IGNORE_ID` |
| Someone doesn't need to post a daily Teams plan (PMs, management) | `TEAMS_PLAN_IGNORE_IDS` |
| A PM should never get follow-up emails | `FOLLOWUP_IGNORE_PM_IDS` |
| Who gets the summary reports | `PM_EMAIL` (comma-separated emails) |
| Test mode for all email / Teams reminders | `EMAIL_TEST_RECIPIENT` / `TEAMS_DM_TEST_RECIPIENT` |
| Email via Microsoft 365 or SMTP | `EMAIL_TRANSPORT` (graph / smtp), `GRAPH_SENDER_MAILBOX` |

`env_list.py` refuses secrets (passwords, keys, tokens). If the user wants a secret changed, tell them which line to
edit themselves.

## Teams name aliases: `data/teams_aliases.json`
- For names in the Teams chat that the matcher can't resolve; they appear under "Needs review" in the teams-allocations
  report.
- Format: `{"people": {"chat spelling": "Exact TaskSprint name"}, "projects": {...}}`.
- Use `--find-user` / `--find-project` to get the exact target name.
- Keep the JSON valid. Check with `.venv\Scripts\python.exe -c "import json;json.load(open('data/teams_aliases.json'))"`.

## Holidays: `data/holidays.json`
- Company holiday: `{"date": "YYYY-MM-DD", "name": "..."}`.
- Floater (only some people): `{"date": "YYYY-MM-DD", "name": "...", "type": "floater", "employee_ids": [33]}`.
  Add the id to an existing floater entry for that date if there is one.
- Keep the list sorted by date and the JSON valid.

## After the change
- Run `.venv\Scripts\python.exe -m unittest discover tests` if a data file changed.
- Tell the user the VPS needs it too:
  - **.env keys:** make the same edit in the VPS `.env`. Give the exact line, e.g. `IGNORE_ID=12,45,113`.
  - **data/*.json:** commit and `git pull` on the VPS.
    Prepare the commit message in a file for `git commit -F`, with no Claude attribution.

## Not a setting
New behaviour, e.g. "project X may only log 40 h a month", needs a rule type in code first. Say so and offer to build
it. Don't fake it with a setting.
