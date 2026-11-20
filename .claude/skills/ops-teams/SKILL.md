---
name: ops-teams
description: Manage the Teams side of the operations agent - sign in the account that sends Teams reminders (device code for Varad, the PM or the user), check or remove that login, test the 11:00 plan-check reminders, and check what was read from the Teams allocation chat. Use for "X is ready to sign in", "who are reminders sent from", "test the Teams reminder", "switch reminders to the PM's account", "what did people post in Teams today".
---

# Teams sign-in and reminders

## Sign someone in (their account becomes the sender)
1. agent-db must be up: `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --wait agent-db`.
2. `TEAMS_TOKEN_KEY` must be in `.env`. It's a secret, so don't read it. If it's missing the command says so, and
   the user adds the key themselves.
3. Start it **in the background** (it waits up to 15 min):
   `PYTHONUNBUFFERED=1 .venv\Scripts\python.exe scripts\local_run.py teams-signin`.
   Then read its output until the line with the code appears.
4. Give the user a message to forward to the person:
   > Open **https://login.microsoft.com/device** in Chrome (Incognito if another Microsoft account is signed in), enter
   > the code **XXXXXXX**, sign in with your own LST account and click Continue/Accept.
5. When the background task finishes, report "Teams reminders will be sent as <name>".
   If the code expired, offer a new one.
6. **On the VPS** the person signs in there instead: `docker compose run --rm agent teams-signin`.
   The local login is not copied to the server.

## Status / sign out
- `scripts\local_run.py teams-signin --status`
- `scripts\local_run.py teams-signout`

## Test the reminders
- `scripts\local_run.py teams-plan-check --dm-to <test person> --email-to <user>`:
  - at most 3 reminders, all to the test person, each marked "[TEST, meant for <name>]";
  - the summary email goes to the user.
- **The test person can't be the signed-in account** (Teams can't message itself). When Varad is signed in, the user's
  own email works as `--dm-to`.
- Only looking: `teams-plan-check --dry-run` writes the summary HTML; nothing is sent.

## Common problems
- **"Approval required" / `Consent/Request` page:** admin consent is missing. In the Azure app → API permissions,
  delegated `Chat.Create`, `ChatMessage.Send`, `User.Read` and `offline_access` must be granted.
- **"login no longer works":** password change or policy. Sign in again.
- **Someone wrongly listed as not posted:** check whether their Teams name matches TaskSprint. If not, add an alias
  (ops-config skill). If they shouldn't post at all, add them to `TEAMS_PLAN_IGNORE_IDS`.
