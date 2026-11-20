---
name: ops-lookup
description: Answer questions from TaskSprint (PM tool) and agent data, read-only - a person's or project's logged hours, timesheet task lists for given dates, allocations, why someone's utilisation or forecast looks the way it does, who logged on a project, IDs for an email or name, Teams plans saved by the agent. Use for "how many hours", "who worked on", "what did X do on", "give me my timesheet tasks for", "why is X at 102%", "what is the ID of".
---

# Look something up (read-only)

## Tools
- **TaskSprint:** `.venv\Scripts\python.exe scripts\pm_query.py "<one SELECT>" [--limit N]`.
  Shortcuts: `--find-user TEXT` and `--find-project TEXT`.
  The script only allows one SELECT / WITH / SHOW / DESCRIBE, run in a READ ONLY transaction.
- **Table columns:** `scripts\pm_query.py "SHOW COLUMNS FROM <Table>"`.
- **Agent data** (saved Teams plans, follow-ups): agent-db on 127.0.0.1:5434, tables `teams_plans`, `followup_records`,
  `notification_log`.
  - Use `followups-list` for follow-ups.
  - For Teams plans, use `services.teams_plans.load_plans_safe(start, end)` via `scripts/local_run.py` or a short script.
- **The tunnel must be up** (see the ops-run skill).

## Data model (workspaceId = 3 on every table)
- `User`: id, name, email, designation, active, billable.
- `Project`: id, name, pmId, statusId, estimatedTime, estimatedEndDate, actualEndDate, active, billable, ticket.
- `Timesheet`:
  - `createdById` = the person who logged the time; `projectId`, `taskId`, `date`, `duration` **(seconds)**.
  - Also `description` and `rejected` (exclude `rejected = 1`).
- `Task`: id, name, projectId, estimatedTime.
- `ResourceAllocation`: allocatedUserId, projectId, date, duration (seconds).
- Hours = `SUM(duration)/3600`. Show hours rounded to 0.1.

## Common questions
- **"What did I do on 9, 12 Nov" (timesheet list):** Timesheet rows for that user and those dates, joined to Project and
  Task. Group by date, then project, then task, with hours and description. The user's TaskSprint id comes from
  `--find-user`.
- **"Hours on project X this month":** `SUM(duration)` by person for that project between the 1st and today.
- **"Why is X at 102% utilisation":** explain with the forecast rules:
  - capacity = 8 h × working days in the next 15 working days, minus floaters;
  - confirmed = TaskSprint allocations, plus Teams plans for days without a TaskSprint allocation;
  - projected = the person's recent average per project, on days with no plan;
  - utilisation = (confirmed + projected) / capacity.
  Show the person's numbers per project.
- **"ID for email":** `--find-user <email>`.

## Answer style
- Plain words and small tables. Hours, not seconds; dates as DD-MM-YYYY.
- Say which filters were used (dates, rejected excluded).
- Flag anything that looks wrong in the data instead of explaining it away.
- Never offer to "fix" data in TaskSprint. The PM fixes it in the tool.
