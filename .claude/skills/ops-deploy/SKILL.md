---
name: ops-deploy
description: Deploy and operate the operations agent on the VPS with docker compose and cron - prepare commits, pull and build, the .env checklist, the crontab schedule, agent-db backups, switching from test mode to live, checking logs. Use for "deploy", "push to the VPS", "set up cron", "go live", "back up the agent DB", "commit this".
---

# Deploy and operate on the VPS

The VPS is `145.223.21.61`. The user logs in as `sachin.ubale` (`ssh lst-vps`, key `~/.ssh/lst_vps`) and has sudo.
The agent lives in **`/opt/agent`** (owned by sachin.ubale). Don't run commands there yourself unless the user asks and
the SSH access works with BatchMode. Normally give the user the exact commands.

**The server is shared with ~50 production containers** (TaskSprint, HR, archival, NSE, ...). Only touch the `agent`
compose project: the `agent-agent-run-*` containers and `agent-agent-db`. Never stop, restart, exec into or remove
anything else, and never edit other lines in root's crontab.

## Commit (from this PC)
1. `git status`. Stage only source, tests, skills and data files. Never stage `.env`, generated `*.html` / `*.pdf` or
   `data/teams_samples.json`.
2. All tests must pass: `.venv\Scripts\python.exe -m unittest discover tests`.
3. Write the commit message to a file in the scratchpad and show it to the user. **No Co-Authored-By or Claude
   attribution.**
4. Give the user the cmd commands, e.g.:
   `git add <files>` · `git commit -F "<file>"` · `git push origin main`.

## Deploy on the VPS
```
cd /opt/agent
git pull
docker compose build
docker compose up -d --wait agent-db
docker compose run --rm agent project-health   # smoke test; goes only to EMAIL_TEST_RECIPIENT while that is set
```
`.env` changes need no rebuild: each `docker compose run` reads `.env` again.

## Stuck runs
`docker ps --filter name=agent-agent-run` should normally be empty. A run that has been "Up" for hours is hung.
Check it with `docker logs --tail 50 <name>`, then remove it with `docker rm -f <name>`.

## .env checklist on the VPS (the user edits it)
- `DATABASE_URL`: the PM DB as seen from the server.
- `AGENT_DB_PASSWORD`, plus `AGENT_DATABASE_URL=postgresql+psycopg2://ops_agent:<pw>@agent-db:5432/ops_agent`.
- `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, `TEAMS_CHAT_ID`, `TEAMS_TOKEN_KEY`, `OPENAI_API_KEY`.
- `PM_EMAIL`, `IGNORE_ID`, `TEAMS_PLAN_IGNORE_IDS`, `FOLLOWUP_IGNORE_PM_IDS`.
- Email:
  - `EMAIL_TRANSPORT=graph` with `GRAPH_SENDER_MAILBOX`, or `smtp` with the `EMAIL_SERVER_*` settings;
  - `EMAIL_SEND_DELAY_SECONDS` (3 for graph, 10+ for A2 SMTP).
- **Pilot week:** `EMAIL_TEST_RECIPIENT` and `TEAMS_DM_TEST_RECIPIENT` set to the user.
  **Going live** = clearing both, after the user confirms.

## Teams sender on the VPS
`docker compose run --rm agent teams-signin`. Forward the code to the sender (see the ops-teams skill).

## Crontab (root's crontab, `sudo crontab -e`; **the server clock is UTC**, IST = UTC + 5:30)
```
# IST 08:00 timesheet reminders, 18:00 Wed/Fri weekly, 16:00 Mon summary
30 2 * * 1-5   cd /opt/agent && /usr/bin/docker compose run --rm agent daily            >> /var/log/agent.log 2>&1
30 12 * * 3,5  cd /opt/agent && /usr/bin/docker compose run --rm agent weekly           >> /var/log/agent.log 2>&1
30 10 * * 1    cd /opt/agent && /usr/bin/docker compose run --rm agent monday-summary   >> /var/log/agent.log 2>&1
# IST 11:00 plan check, 11:30 daily ops, 11:35 project health, 12:00 follow-ups, 12:05 forecast
30 5 * * 1-5   cd /opt/agent && /usr/bin/docker compose run --rm agent teams-plan-check >> /var/log/agent.log 2>&1
0 6 * * 1-5    cd /opt/agent && /usr/bin/docker compose run --rm agent daily-ops        >> /var/log/agent.log 2>&1
5 6 * * 1-5    cd /opt/agent && /usr/bin/docker compose run --rm agent project-health   >> /var/log/agent.log 2>&1
30 6 * * 1-5   cd /opt/agent && /usr/bin/docker compose run --rm agent followups        >> /var/log/agent.log 2>&1
35 6 * * 1-5   cd /opt/agent && /usr/bin/docker compose run --rm agent forecast         >> /var/log/agent.log 2>&1
# IST 02:00 agent-db backup
30 20 * * *    cd /opt/agent && /usr/bin/docker compose exec -T agent-db pg_dump -U ops_agent ops_agent | gzip > /opt/agent/backups/agent-db-$(date +\%F).sql.gz
```
- `teams-plan-check` also reads and saves the Teams plans, so it runs before daily-ops and forecast.
- The commands skip weekends and company holidays themselves where that matters.

## Checking a problem
- Logs: `tail -100 logs/<job>.log`.
- Common causes: the tunnel or DB is unreachable, the Teams login expired (run teams-signin again), or Microsoft 365
  sending isn't set up (`ErrorInvalidUser`).
