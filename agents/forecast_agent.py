"""
Forecast Agent: emails the 15-working-day capacity vs demand forecast to the
configured PM/management recipients.
"""

import html
import logging
import os
from utils.reports import report_path
from typing import List
from database.connection import get_db_session
from services.email_service import EmailService
from services.teams_plans import load_plans_safe
from services.forecast_service import (
    ForecastService, Forecast, update_skills_template, import_skills_csv, UNMAPPED_SKILL,
    HISTORY_WORKING_DAYS, OVERLOAD_UTILISATION, SPARE_UTILISATION,
)
from utils.date_utils import get_current_date
from config.settings import settings

logger = logging.getLogger(__name__)

MAX_PERSON_PROJECTS = 3
OVERLOAD_COLOUR, SPARE_COLOUR = "#d32f2f", "#1976d2"


def _h(seconds: float) -> str:
    return f"{seconds / 3600:,.0f} h"


def _d(d) -> str:
    return d.strftime('%d-%m-%Y')


def _pct(value) -> str:
    return "&ndash;" if value is None else f"{value:.0%}"


class ForecastAgent:
    def __init__(self, dry_run: bool = False):
        self.email_service = EmailService()
        self.pm_email = settings.get_pm_email()
        self.dry_run = dry_run

    def run(self):
        logger.info("Starting Forecast Agent execution...")
        today = get_current_date()

        session_gen = get_db_session()
        session = next(session_gen)
        try:
            forecast = ForecastService(session, settings.get_ignore_id_list()).run(today, teams_loader=load_plans_safe)
        finally:
            session.close()

        subject = f"Resource Forecast - {_d(forecast.days[0])} to {_d(forecast.days[-1])}"
        body = self._build_html_email_body(forecast)

        if self.dry_run:
            out_path = report_path(f"forecast_{today.isoformat()}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(body)
            logger.info(f"Dry run: report written to {out_path}")
            return

        if not self.pm_email:
            raise ValueError("PM_EMAIL environment variable is not set")
        if not self.email_service.send_alert(to_email=self.pm_email, subject=subject, body=body, is_html=True):
            raise RuntimeError("Failed to send forecast email")
        logger.info("Forecast Agent execution finished.")

    # ------------------------------------------------------------ email body

    def _table(self, headings: List[str], rows: List[List[str]], align_right_from: int = 1) -> str:
        lines = ['<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 1200px;">',
                 '<thead><tr style="background-color: #4CAF50; color: white;">']
        lines += [f'<th style="text-align: {"right" if i >= align_right_from else "left"}; padding: 10px;">{h}</th>'
                  for i, h in enumerate(headings)]
        lines.append('</tr></thead><tbody>')
        for row in rows:
            lines.append('<tr>' + "".join(
                f'<td style="padding: 10px; text-align: {"right" if i >= align_right_from else "left"};">{c}</td>'
                for i, c in enumerate(row)) + '</tr>')
        lines.append('</tbody></table>')
        return "\n".join(lines)

    @staticmethod
    def _totals_row(label: str, capacity: int, confirmed: int, projected: int) -> List[str]:
        demand = confirmed + projected
        free = capacity - demand
        colour = OVERLOAD_COLOUR if free < 0 else "inherit"
        return [label, _h(capacity), _h(confirmed), _h(projected), _h(demand),
                _pct(demand / capacity if capacity else None),
                f"<strong style='color:{colour}'>{_h(free)}</strong>"]

    def _build_html_email_body(self, f: Forecast) -> str:
        e = html.escape
        cap = sum(p.capacity for p in f.people)
        conf = sum(p.confirmed for p in f.people)
        proj = sum(p.projected for p in f.people)
        overloaded = [p for p in f.people if p.utilisation is not None and p.utilisation > OVERLOAD_UTILISATION]
        spare = [p for p in f.people if p.utilisation is not None and p.utilisation < SPARE_UTILISATION]

        lines = ["<html><body>", "<p>Hi,</p>",
                 f"<p>Resource forecast for the next <strong>{len(f.days)} working days</strong> "
                 f"({_d(f.days[0])} to {_d(f.days[-1])}) for {len(f.people)} delivery staff.</p>"]
        lines.append(
            f"<p><strong>Summary:</strong> {_h(cap)} capacity, {_h(conf)} confirmed and {_h(proj)} projected demand, "
            f"leaving <strong>{_h(cap - conf - proj)}</strong> free. "
            f"<span style='color:{OVERLOAD_COLOUR}'>{len(overloaded)} over capacity</span>, "
            f"<span style='color:{SPARE_COLOUR}'>{len(spare)} below {SPARE_UTILISATION:.0%}</span>.</p>"
        )
        lines.append(
            "<p style='background:#fff3cd;padding:8px;'><strong>How to read this:</strong> <em>Confirmed</em> is future "
            "allocations entered in the PM tool, plus day plans posted in the Teams allocation chat "
            f"({_h(f.confirmed_teams)} of the confirmed total came from Teams). <em>Projected</em> assumes each person keeps working on their active "
            f"projects at their average pace over the last {HISTORY_WORKING_DAYS} working days "
            f"({_d(f.history[0])} to {_d(f.history[-1])}). It is a planning estimate, not a commitment. "
            "Planned leave is not included, so real free capacity will be lower.</p>"
        )

        # By week
        weeks = {}
        for day in f.daily:
            wk = day["date"].isocalendar()[:2]
            w = weeks.setdefault(wk, {"start": day["date"], "end": day["date"], "capacity": 0, "confirmed": 0, "projected": 0})
            w["end"] = day["date"]
            for k in ("capacity", "confirmed", "projected"):
                w[k] += day[k]
        lines.append("<h3>By week</h3>")
        lines.append(self._table(
            ["Week", "Capacity", "Confirmed", "Projected", "Total demand", "Utilisation", "Free"],
            [self._totals_row(f"{_d(w['start'])} &ndash; {_d(w['end'])}", w["capacity"], w["confirmed"], w["projected"])
             for w in weeks.values()]
            + [self._totals_row("<strong>Total</strong>", cap, conf, proj)],
        ))

        # By skill
        lines.append("<h3>By primary skill</h3>")
        if f.skills_mapped == 0:
            lines.append("<p>No skills mapped yet. Fill in <code>data/employee_skills.json</code> to see capacity and "
                         "demand per skill.</p>")
        else:
            lines.append(self._table(
                ["Skill", "People", "Capacity", "Confirmed", "Projected", "Total demand", "Utilisation", "Free"],
                [[e(s["skill"]), str(s["people"])] + self._totals_row("", s["capacity"], s["confirmed"], s["projected"])[1:]
                 for s in f.skills],
                align_right_from=1,
            ))
            if f.skills_mapped < len(f.people):
                lines.append(f"<p style='font-size: 12px; color: #757575;'>{len(f.people) - f.skills_mapped} people are "
                             f"not mapped to a skill yet (shown as &ldquo;{UNMAPPED_SKILL}&rdquo;).</p>")

        # By role
        if f.roles_mapped:
            lines.append("<h3>By role</h3>")
            lines.append(self._table(
                ["Role", "People", "Capacity", "Confirmed", "Projected", "Total demand", "Utilisation", "Free"],
                [[e(r["role"]), str(r["people"])] + self._totals_row("", r["capacity"], r["confirmed"], r["projected"])[1:]
                 for r in f.roles],
                align_right_from=1,
            ))

        # By person
        lines.append("<h3>By person</h3>")
        person_rows = []
        for p in f.people:
            colour = (OVERLOAD_COLOUR if p.utilisation is not None and p.utilisation > OVERLOAD_UTILISATION
                      else SPARE_COLOUR if p.utilisation is not None and p.utilisation < SPARE_UTILISATION else "inherit")
            top = sorted(p.projects.items(), key=lambda kv: -(kv[1]["confirmed"] + kv[1]["projected"]))
            top_str = ", ".join(f"{e(n)} ({_h(v['confirmed'] + v['projected'])})" for n, v in top[:MAX_PERSON_PROJECTS])
            if len(top) > MAX_PERSON_PROJECTS:
                top_str += f" +{len(top) - MAX_PERSON_PROJECTS} more"
            role = "&ndash;" if p.role == UNMAPPED_SKILL else e(p.role)
            person_rows.append([e(p.name), e(p.skill), role, _h(p.capacity), _h(p.confirmed), _h(p.projected),
                                f"<strong style='color:{colour}'>{_pct(p.utilisation)}</strong>", _h(p.free),
                                top_str or "&ndash;"])
        lines.append(self._table(
            ["Person", "Skill", "Role", "Capacity", "Confirmed", "Projected", "Utilisation", "Free", "Main projects"],
            person_rows, align_right_from=3,
        ).replace('text-align: right; padding: 10px;">Main projects', 'text-align: left; padding: 10px;">Main projects'))

        # By project
        lines.append("<h3>By project</h3>")
        lines.append(self._table(
            ["Project", "Billable", "Confirmed", "Projected", "Total", "People"],
            [[e(x["name"]), "Yes" if x["billable"] else "No", _h(x["confirmed"]), _h(x["projected"]),
              _h(x["confirmed"] + x["projected"]), str(len(x["people"]))] for x in f.projects],
        ))

        # Day by day
        lines.append("<h3>Day by day</h3>")
        lines.append(self._table(
            ["Date", "Capacity", "Confirmed", "Projected", "Total demand", "Utilisation", "Free"],
            [self._totals_row(day["date"].strftime('%a %d-%m-%Y'), day["capacity"], day["confirmed"], day["projected"])
             for day in f.daily],
        ))

        lines.append(
            "<p style='color:#757575; font-size: 12px;'>Capacity: 8 h per working day, excluding weekends, company "
            "holidays and each person's floater holidays. Delivery staff: anyone with at least 2 days of project work "
            f"in the last {HISTORY_WORKING_DAYS} working days. Projected demand excludes leave, idle time and projects "
            "that are completed, closed, cancelled, halted or inactive. Where a confirmed allocation exists for a person, "
            "project and day, it replaces the projection. A Teams day plan is used only for person-days with no PM-tool "
            "allocation, and it replaces all projections for that person and day (it describes the whole day). Sales opportunities are not included (no source yet).</p>"
        )
        lines.append('<p>Thank you,<br/>Operations Agent</p></body></html>')
        return "\n".join(lines)


def refresh_skills_template():
    """Add current delivery staff to data/employee_skills.json (existing skills are kept)."""
    session_gen = get_db_session()
    session = next(session_gen)
    try:
        names = ForecastService(session, settings.get_ignore_id_list()).delivery_staff(get_current_date())
    finally:
        session.close()
    added = update_skills_template(names)
    print(f"Added {added} people to data/employee_skills.json ({len(names)} delivery staff in total).")


def import_skills_sheet(csv_path: str, practice: str = None):
    """Update data/employee_skills.json from a practice skill sheet (CSV)."""
    if not practice:
        # "Skillset Sheet(NetSuite).csv" -> "NetSuite"
        name = os.path.basename(csv_path)
        practice = name[name.find("(") + 1:name.find(")")].strip() if "(" in name and ")" in name else ""
    if not practice:
        raise ValueError("Pass --practice NAME (e.g. --practice NetSuite); it could not be read from the file name")
    result = import_skills_csv(csv_path, practice)
    print(f"Practice: {practice}. Updated {len(result.updated)} people in data/employee_skills.json:")
    for sheet_name, uid, pm_name in result.updated:
        print(f"  {sheet_name:<20} -> {pm_name.strip()} (user {uid})")
    if result.not_matched:
        print(f"Not matched ({len(result.not_matched)}), left unchanged:")
        for sheet_name, reason in result.not_matched:
            print(f"  {sheet_name:<20} {reason}")
