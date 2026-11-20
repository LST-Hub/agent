---
name: ops-deploy
description: Deploy and operate the operations agent on the VPS with docker compose and cron - prepare commits, pull and build, the .env checklist, the crontab schedule, agent-db backups, switching from test mode to live, checking logs. Use for "deploy", "push to the VPS", "set up cron", "go live", "back up the agent DB", "commit this".
---

# Deploy and operate on the VPS

The VPS is `root@145.223.21.61`. Don't run commands there yourself unless the user asks and the SSH access works with
BatchMode. Normally give the user the exact commands.

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
cd <agent folder>
git pull
docker compose build
docker compose up -d agent-db
docker compose run --rm agent project-health --dry-run   # smoke test
```

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

## Crontab (working days, server time = IST; adjust if the server is UTC)
```
0 11 * * 1-5  cd /path/agent && docker compose run --rm agent teams-plan-check >> logs/plan-check.log 2>&1
30 11 * * 1-5 cd /path/agent && docker compose run --rm agent daily-ops >> logs/daily-ops.log 2>&1
35 11 * * 1-5 cd /path/agent && docker compose run --rm agent project-health >> logs/project-health.log 2>&1
0 12 * * 1-5  cd /path/agent && docker compose run --rm agent followups >> logs/followups.log 2>&1
5 12 * * 1-5  cd /path/agent && docker compose run --rm agent forecast >> logs/forecast.log 2>&1
0 2 * * *     cd /path/agent && docker compose exec -T agent-db pg_dump -U ops_agent ops_agent | gzip > backups/agent-db-$(date +\%F).sql.gz
```
- `teams-plan-check` also reads and saves the Teams plans, so it runs before daily-ops and forecast.
- The commands skip weekends and company holidays themselves where that matters.

## Checking a problem
- Logs: `tail -100 logs/<job>.log`.
- Common causes: the tunnel or DB is unreachable, the Teams login expired (run teams-signin again), or Microsoft 365
  sending isn't set up (`ErrorInvalidUser`).
