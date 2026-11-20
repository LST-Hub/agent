---
name: ops-run
description: Run the LST operations agent reports on this PC and deliver them safely - project health, daily ops, follow-ups, forecast, Teams allocations, Teams plan check - as email to the user, a dry-run HTML file, or a PDF to forward to PMs. Use when the user asks to "run", "send me", "check", "generate" or "give me" any of these reports, or a report as PDF.
---

# Run an operations report

## 1. Map the request to a command
| User says | Command |
|---|---|
| project health, project status, risky projects | `project-health` |
| daily ops, overtime, plan vs logged, missing tickets | `daily-ops` |
| follow-ups for PMs | `followups` (writes to agent-db) or `followups-list` (read only) |
| forecast, capacity, utilisation, bench | `forecast` |
| what did people plan in Teams, Teams allocations | `teams-allocations` (add `--no-save` if they only want to look) |
| who hasn't posted today's plan | `teams-plan-check` |

## 2. Check what it needs
- **PM DB tunnel** on 127.0.0.1:3307 (every command). Test it with `bash -c '</dev/tcp/127.0.0.1/3307'`.
  If it's down, start it in the background:
  `ssh -i ~/.ssh/tasksprint_vps -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L 3307:localhost:3307 root@145.223.21.61`.
- **agent-db** (followups, Teams commands): `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --wait agent-db`.
  If Docker isn't running, start Docker Desktop first.

## 3. Pick the delivery (default: email to the user only)
- **Email to the user:** `.venv\Scripts\python.exe scripts\local_run.py <command> --email-to <user's email>`.
  Every email goes only to that address, marked [TEST] with the intended recipients in a banner.
- **Just look:** `<command> --dry-run` writes `<report>_<date>.html` in the repo root. Summarise the key numbers in chat.
- **A past or future date:** add `--today YYYY-MM-DD`.
- **Never** send to the real recipients (PMs, resources) from this PC. That's the VPS cron's job.
  If the user explicitly asks for it, explain that and confirm before anything else.
- **Teams plan check:** reminders go only to `--dm-to <someone other than the signed-in account>`, or use `--dry-run`.
  `local_run` refuses otherwise.

## 4. PDF for PMs
1. Run the report with `--dry-run` to get the HTML.
2. Make a print copy in the scratchpad: add `@page{size:A4 landscape;margin:12mm}` and a small base font, and replace
   the email greeting `<p>Hi,</p>` with an `<h1>` title.
3. Convert it with Edge:
   `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="<repo>\<Report_Name_DD-MM-YYYY>.pdf" "file:///<print copy>"`.
4. Screenshot the page with `--screenshot` to check the layout.
5. Tell the user the PDF must not be committed.

## 5. If sending fails
- `ErrorInvalidUser` / 401 / 403 from Microsoft 365: the `GRAPH_SENDER_MAILBOX` or `Mail.Send` setup is not done.
  For this one run only, prefix the command with `EMAIL_TRANSPORT=smtp`, and tell the user.
- `sending stopped earlier in this run`: the mail server refused. Don't retry in a loop; report the reason.

## 6. Report back
- Give the headline numbers and where the result went (inbox, file path).
- Point out anything that looks like bad data, such as identical hours on many tasks or values in the thousands.
