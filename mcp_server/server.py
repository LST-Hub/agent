"""
LST Ops connector (remote MCP server, Streamable HTTP at <MCP_BASE_URL>/mcp).

Sign-in: Microsoft Entra through FastMCP's OAuth proxy (claude.ai registers itself with this server,
the person logs in with their LST Microsoft account). Then MCP_ALLOWED_USERS decides who gets in.

Settings (.env):
    MCP_BASE_URL               public URL, e.g. https://ops-mcp.lstconsultancy.com
    MCP_AZURE_CLIENT_ID        app registration for the connector (not the agent's Graph app)
    MCP_AZURE_CLIENT_SECRET    its client secret
    TENANT_ID                  LST tenant (already used by the agent)
    MCP_AZURE_SCOPE            scope exposed by that app (default ops.read)
    MCP_JWT_SIGNING_KEY        random secret; signs the connector's own tokens and encrypts stored logins
    MCP_ALLOWED_USERS          see mcp_server/access.py
    MCP_ALLOWED_REDIRECTS      optional, default the claude.ai / claude.com callbacks
    FASTMCP_HOME               where logins are stored (docker: /data, a volume)
Run: python -m mcp_server
"""

import contextvars
import functools
import logging
import os
from typing import Literal, Optional

import anyio
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from mcp_server import tools
from mcp_server.access import allowed_rules, is_allowed, user_email

logger = logging.getLogger("mcp_server")

DEFAULT_REDIRECTS = "https://claude.ai/api/mcp/auth_callback,https://claude.com/api/mcp/auth_callback"

INSTRUCTIONS = """LST Consultancy operations data from TaskSprint (the PM tool) and the Teams allocation chat.
All tools are read-only. Hours are decimal hours. Dates are YYYY-MM-DD; 'today' and 'yesterday' also work.
Answer in short tables. Names can be partial (e.g. 'poonam'). TaskSprint is the system PMs update to fix issues."""


def _auth():
    from fastmcp.server.auth.providers.azure import AzureProvider
    return AzureProvider(
        client_id=os.environ["MCP_AZURE_CLIENT_ID"],
        client_secret=os.environ["MCP_AZURE_CLIENT_SECRET"],
        tenant_id=os.environ["TENANT_ID"],
        base_url=os.environ["MCP_BASE_URL"].rstrip("/"),
        required_scopes=[os.getenv("MCP_AZURE_SCOPE", "ops.read")],
        jwt_signing_key=os.environ["MCP_JWT_SIGNING_KEY"],
        allowed_client_redirect_uris=[u.strip() for u in os.getenv("MCP_ALLOWED_REDIRECTS", DEFAULT_REDIRECTS).split(",")
                                      if u.strip()],
    )


def current_user() -> str:
    """Email of the signed-in person; raises if they are not allowed."""
    token = get_access_token()
    email = user_email(token.claims if token else {})
    if not is_allowed(email, allowed_rules()):
        logger.warning(f"Refused connector use by '{email or 'unknown'}'")
        raise ToolError(f"{email or 'This account'} is not allowed to use LST Ops. Ask the operations team for access.")
    return email


CURRENT_EMAIL: contextvars.ContextVar[str] = contextvars.ContextVar("current_email", default="")


def guarded(fn):
    """Check access, log who asked for what, run the (blocking DB) work in a worker thread so other people
    are not kept waiting, and turn errors into a readable message. Inside fn, CURRENT_EMAIL is the asker."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        email = current_user()
        CURRENT_EMAIL.set(email)
        logger.info(f"{email} -> {fn.__name__} {kwargs}")
        try:
            context = contextvars.copy_context()
            return await anyio.to_thread.run_sync(lambda: context.run(fn, *args, **kwargs))
        except ToolError:
            raise
        except ValueError as exc:
            raise ToolError(str(exc))
        except Exception:
            logger.exception(f"{fn.__name__} failed")
            raise ToolError("Something went wrong on the server; the operations team can see the details in the log.")
    return wrapper


def build(auth=True) -> FastMCP:
    mcp = FastMCP("LST Ops", instructions=INSTRUCTIONS, auth=_auth() if auth else None)
    read_only = {"readOnlyHint": True, "openWorldHint": False}

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request):
        return PlainTextResponse("ok")

    @mcp.tool(annotations=read_only)
    @guarded
    def whoami() -> dict:
        """Who is signed in to LST Ops."""
        return {"email": CURRENT_EMAIL.get()}

    @mcp.tool(annotations={**read_only, "idempotentHint": True})
    @guarded
    def teams_allocations(day: Optional[str] = None, person: Optional[str] = None, project: Optional[str] = None,
                          refresh: bool = False) -> dict:
        """Day plans people posted in the Teams allocation chat (who works on which project, hours, task).
        day: YYYY-MM-DD, 'today' (default) or 'yesterday'. refresh=True re-reads today's chat (takes up to a minute);
        today's chat is also read automatically when nothing is saved yet."""
        return tools.teams_allocations(day=day, person=person, project=project, refresh=refresh)

    @mcp.tool(annotations=read_only)
    @guarded
    def project_health(project: Optional[str] = None, pm: Optional[str] = None,
                       include_data_quality: bool = True) -> dict:
        """Delivery risks (schedule, effort overrun, no activity) and data-quality issues for active projects.
        Filter by part of a project name or PM name."""
        return tools.project_health(project=project, pm=pm, include_data_quality=include_data_quality)

    @mcp.tool(annotations=read_only)
    @guarded
    def daily_operations(start: Optional[str] = None, end: Optional[str] = None, person: Optional[str] = None) -> dict:
        """Timesheet checks: overtime over 8h, plans over 8h, plan vs logged gaps, entries without a ticket,
        billable entries with short descriptions. Default: the last few working days. Max 31 days."""
        return tools.daily_operations(start=start, end=end, person=person)

    @mcp.tool(annotations=read_only)
    @guarded
    def resource_forecast(skill: Optional[str] = None, person: Optional[str] = None) -> dict:
        """Capacity vs demand for the next 15 working days per person (confirmed allocations and Teams plans,
        plus projected run rate), utilisation and free hours. Filter by skill (e.g. 'NetSuite') or person."""
        return tools.resource_forecast(skill=skill, person=person)

    @mcp.tool(annotations=read_only)
    @guarded
    def logged_hours(start: str, end: Optional[str] = None, person: Optional[str] = None,
                     project: Optional[str] = None, group_by: Literal["person", "project", "day"] = "person") -> dict:
        """Hours logged in TaskSprint timesheets between two dates (max 92 days), filtered by person and/or
        project, grouped by person, project or day."""
        return tools.logged_hours(start=start, end=end, person=person, project=project, group_by=group_by)

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    @guarded
    def email_me_report(report: Literal["project-health", "daily-ops", "forecast"]) -> str:
        """Email the full standard report (HTML) to the signed-in person only."""
        return tools.email_report(report, CURRENT_EMAIL.get())

    return mcp


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    if not allowed_rules():
        logger.warning("MCP_ALLOWED_USERS is empty: nobody can use the connector until it is set.")
    build().run(transport="http", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")), path="/mcp")


if __name__ == "__main__":
    main()
