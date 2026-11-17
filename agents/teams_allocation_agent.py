"""
Teams Allocation Agent: reads allocation messages from the Teams group chat (or a
local sample file), extracts allocations with AI, matches them to PM-tool users and
projects, saves the matched day plans to the agent database and writes a review report.

The saved plans are used by the forecast (as confirmed demand) and daily-ops (plan vs
logged). Review items are never saved. Runs on a sample file are never saved.
"""

import html
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List
from database.connection import get_db_session
from database.repositories.project_repo import ProjectRepository
from database.repositories.user_repo import UserRepository
from services.allocation_extraction import OpenAIAllocationExtractor
from services.allocation_matching import (
    NameMatcher, Resolution, load_aliases, resolve_allocations, person_day_totals, STANDARD_DAY_SECONDS,
)
from services.project_health_service import CLOSED_STATUSES
from services.teams_chat_service import ChatMessage, TeamsChatService, load_sample_messages
from services.teams_plans import save_plans
from utils.date_utils import get_current_date
from config.settings import settings

logger = logging.getLogger(__name__)

CONFIDENCE_COLOUR = {"high": "#2e7d32", "medium": "#ef6c00", "low": "#d32f2f"}


def _hours(seconds: int) -> str:
    return f"{seconds / 3600:g}"


class TeamsAllocationAgent:
    def __init__(self, sample_path: str = None, save: bool = None):
        self.sample_path = sample_path
        self.save = (sample_path is None) if save is None else save
        self.inactive_projects = set()
        self.save_note = ""

    def _load_messages(self) -> List[ChatMessage]:
        if self.sample_path:
            messages = load_sample_messages(self.sample_path)
            logger.info(f"Loaded {len(messages)} sample messages from {self.sample_path}.")
            return messages
        service = TeamsChatService(settings.tenant_id, settings.client_id, settings.client_secret,
                                   settings.teams_chat_id, settings.teams_utc_offset_minutes)
        since = datetime.combine(get_current_date() - timedelta(days=settings.teams_lookback_days), datetime.min.time())
        return service.fetch_messages(since)

    @staticmethod
    def _load_records():
        """Active people; active and inactive projects that are not completed, closed or cancelled."""
        session = next(get_db_session())
        try:
            people = {u.id: u.name for u in UserRepository(session).get_active_users()}
            rows = [p for p in ProjectRepository(session).get_projects_for_matching()
                    if (p.get("statusName") or "").strip().lower() not in CLOSED_STATUSES]
        finally:
            session.close()
        projects = {p["id"]: p["name"] for p in rows}
        active_projects = {p["id"] for p in rows if p.get("active")}
        return people, projects, active_projects

    def run(self) -> Resolution:
        logger.info("Starting Teams Allocation Agent execution...")
        messages = self._load_messages()
        people, projects, active_projects = self._load_records()
        self.inactive_projects = set(projects) - active_projects
        person_aliases, project_aliases = load_aliases()

        extractor = OpenAIAllocationExtractor(settings.ai.api_key, settings.ai.extraction_model)
        # The AI sees active projects only; inactive ones are still matched from the written words
        raw_rows = extractor.extract(messages, list(people.values()),
                                     [name for pid, name in projects.items() if pid in active_projects])
        resolution = resolve_allocations(raw_rows, messages, NameMatcher(people, person_aliases),
                                         NameMatcher(projects, project_aliases, preferred=active_projects))
        # People on IGNORE_ID are matched (so their messages are not flagged) but left out of the results
        ignored = set(settings.get_ignore_id_list())
        ignored_names = {people[i] for i in ignored if i in people}
        resolution.planned = [p for p in resolution.planned if p.user_id not in ignored]
        resolution.review = [i for i in resolution.review if i.sender not in ignored_names]
        for p in resolution.planned:
            if p.project_id in self.inactive_projects:
                p.match_note = "; ".join(n for n in (p.match_note, "project is inactive in the PM tool") if n)

        self.save_note = self._save(resolution, messages)

        out_path = os.path.abspath(f"teams_allocations_{get_current_date().isoformat()}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(self._build_report(messages, len(raw_rows), resolution))

        logger.info(f"{len(messages)} messages, {len(raw_rows)} allocations extracted, "
                    f"{len(resolution.planned)} person-day rows matched, {len(resolution.review)} need review.")
        logger.info(f"Report written to {out_path}")
        return resolution

    def _save(self, resolution: Resolution, messages: List[ChatMessage]) -> str:
        if not self.save:
            return "Not saved (sample file or --no-save)."
        from database.agent_store import get_agent_session
        session = get_agent_session()
        try:
            removed, saved = save_plans(session, resolution.planned, {m.id for m in messages})
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        logger.info(f"Saved {saved} plan rows to the agent database ({removed} older rows replaced).")
        return f"Saved {saved} person-day rows to the agent database ({removed} older rows replaced)."

    # ------------------------------------------------------------ report

    @staticmethod
    def _table(headings: List[str], rows: List[List[str]]) -> str:
        lines = ['<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; width: 100%;">',
                 '<thead><tr style="background-color: #4CAF50; color: white;">']
        lines += [f'<th style="text-align: left;">{h}</th>' for h in headings]
        lines.append('</tr></thead><tbody>')
        lines += ['<tr>' + "".join(f'<td style="vertical-align: top;">{c}</td>' for c in row) + '</tr>' for row in rows]
        lines.append('</tbody></table>')
        return "\n".join(lines)

    def _build_report(self, messages: List[ChatMessage], extracted: int, r: Resolution) -> str:
        e = html.escape
        source = f"sample file {e(self.sample_path)}" if self.sample_path else "Teams chat"
        totals = person_day_totals(r.planned)
        names = {p.user_id: p.user_name for p in r.planned}
        over = sorted(((d, names[uid], s) for (uid, d), s in totals.items() if s > STANDARD_DAY_SECONDS))

        lines = ["<html><head><meta charset='utf-8'><title>Teams allocations</title></head>"
                 "<body style='font-family: Segoe UI, Arial, sans-serif; font-size: 14px;'>",
                 "<h2>Teams allocations: extraction check</h2>",
                 f"<p>Source: {source}. {len(messages)} messages read, {extracted} allocations extracted, "
                 f"<strong>{len(r.planned)}</strong> person-day rows matched, "
                 f"<strong style='color:#d32f2f'>{len(r.review)}</strong> need review. {e(self.save_note)}</p>"]

        lines.append("<h3>Needs review</h3>")
        if r.review:
            lines.append("<p>Not used until fixed. Add the missing name to <code>data/teams_aliases.json</code> "
                         "if the PM tool uses a different spelling.</p>")
            lines.append(self._table(
                ["Sent", "By", "Person", "Project", "Reason", "Message text"],
                [[f"{i.sent_at:%d-%m %H:%M}", e(i.sender), e(i.person), e(i.project or "(all)"), e(i.reason), e(i.evidence)]
                 for i in r.review]))
        else:
            lines.append("<p>Nothing to review.</p>")

        if over:
            lines.append("<h3>Allocated more than 8 hours</h3>")
            lines.append(self._table(["Date", "Person", "Hours"],
                                     [[f"{d:%a %d-%m-%Y}", e(n), _hours(s)] for d, n, s in over]))

        lines.append("<h3>Matched allocations</h3>")
        lines.append(self._table(
            ["Date", "Person", "Project", "Task", "Hours", "Confidence", "Sent", "Message text", "Notes"],
            [[f"{p.date:%a %d-%m-%Y}", e(p.user_name), e(p.project_name), e(p.task) or "&ndash;",
              _hours(p.seconds) + ("" if p.hours_stated else "*"),
              f"<span style='color:{CONFIDENCE_COLOUR.get(p.confidence, 'inherit')}'>{p.confidence}</span>",
              f"{p.sent_at:%d-%m %H:%M} {e(p.sender)}", e(p.evidence), e(p.match_note)]
             for p in r.planned]))
        lines.append("<p style='color:#757575; font-size: 12px;'>* Hours not stated in the message; recorded as 0 h "
                     "(not used for plan vs logged, and not counted as confirmed demand in the forecast). Weekends, company holidays and the person's floater holidays are skipped. "
                     "When a later message changes a plan, only the latest plan is shown.</p>")

        lines.append("<h3>Messages read</h3>")
        lines.append(self._table(["Sent", "By", "Text"],
                                 [[f"{m.sent_at:%a %d-%m %H:%M}", e(m.sender), e(m.text).replace("\n", "<br>")]
                                  for m in messages]))
        lines.append("</body></html>")
        return "\n".join(lines)
