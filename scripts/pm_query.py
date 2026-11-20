"""
Read-only questions to the PM tool (TaskSprint) database, for Claude skills and quick checks.

Only SELECT / WITH / SHOW / DESCRIBE statements are accepted, and they run inside a
READ ONLY transaction, so nothing in the PM tool can be changed from here.
Needs the SSH tunnel to the PM database (DATABASE_URL in .env).

Usage:
    python scripts/pm_query.py --find-user TEXT        # by name or email (active and inactive)
    python scripts/pm_query.py --find-project TEXT     # by name
    python scripts/pm_query.py "SELECT ... FROM Timesheet ..." [--limit 200]

Workspace: always filter on workspaceId = 3. Timesheet.duration and allocation
durations are in seconds; Timesheet.createdById is the person who logged the time.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

READ_ONLY = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.I)
FORBIDDEN = re.compile(r"\b(insert|update|delete|replace|drop|alter|create|truncate|grant|revoke|rename|call|lock|set|load|handler|outfile|dumpfile|sleep|benchmark)\b", re.I)


def check_sql(sql: str) -> str:
    """The statement if it is a single read-only statement; raises ValueError otherwise."""
    body = sql.strip().rstrip(";").strip()
    if ";" in body:
        raise ValueError("Only one statement at a time.")
    if not READ_ONLY.match(body):
        raise ValueError("Only SELECT / WITH / SHOW / DESCRIBE statements are allowed.")
    if FORBIDDEN.search(re.sub(r"'[^']*'|\"[^\"]*\"", "''", body)):
        raise ValueError("The statement contains a write keyword; refused.")
    return body


def run(sql: str, params: dict = None, limit: int = 200):
    from sqlalchemy import text
    from database.connection import get_db_session
    session = next(get_db_session())
    try:
        session.execute(text("SET SESSION TRANSACTION READ ONLY"))
        session.execute(text("START TRANSACTION READ ONLY"))
        result = session.execute(text(sql), params or {})
        columns = list(result.keys())
        rows = result.fetchmany(limit + 1)
        session.rollback()
    finally:
        session.close()
    return columns, rows


def show(columns, rows, limit: int):
    if not rows:
        print("(no rows)")
        return
    shown = [[("" if v is None else str(v)) for v in r] for r in rows[:limit]]
    widths = [min(60, max(len(c), *(len(r[i]) for r in shown))) for i, c in enumerate(columns)]
    print(" | ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in shown:
        print(" | ".join(v[:60].ljust(w) for v, w in zip(r, widths)))
    print(f"({len(shown)} row(s)" + (f"; more than {limit}, use --limit" if len(rows) > limit else "") + ")")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    limit = 200
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i:i + 2]

    if args[0] == "--find-user":
        like = f"%{' '.join(args[1:]).strip()}%"
        columns, rows = run("SELECT id, name, email, designation, active, billable FROM User "
                            "WHERE workspaceId = 3 AND (name LIKE :q OR email LIKE :q) ORDER BY active DESC, name",
                            {"q": like}, limit)
    elif args[0] == "--find-project":
        like = f"%{' '.join(args[1:]).strip()}%"
        columns, rows = run("SELECT p.id, p.name, p.active, pm.name AS pm, p.estimatedTime, p.estimatedEndDate, p.actualEndDate FROM Project p "
                            "LEFT JOIN User pm ON pm.id = p.pmId AND pm.workspaceId = 3 "
                            "WHERE p.workspaceId = 3 AND p.name LIKE :q ORDER BY p.active DESC, p.name",
                            {"q": like}, limit)
    else:
        try:
            sql = check_sql(" ".join(args))
        except ValueError as exc:
            sys.exit(f"Refused: {exc}")
        columns, rows = run(sql, limit=limit)
    show(columns, rows, limit)


if __name__ == "__main__":
    main()
