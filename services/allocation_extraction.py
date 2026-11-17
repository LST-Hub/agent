"""
AI extraction of resource allocations from free-text Teams messages.

The model only turns text into structured rows (person, project, dates, hours).
Everything after that - matching names to PM-tool records, expanding date ranges
to working days, applying later changes, filling missing hours - is plain code in
services/allocation_matching.py, so it is testable and never guessed.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence
from services.teams_chat_service import ChatMessage
import logging

logger = logging.getLogger(__name__)

TARGET_BATCH_SIZE = 25     # Messages extracted per AI call
CONTEXT_MESSAGES = 10      # Earlier messages shown for references like "same as yesterday"
MAX_EVIDENCE_CHARS = 200
CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass
class RawAllocation:
    """One allocation as stated in a message, before matching to the PM tool."""
    message_id: str
    person: str
    project: Optional[str]          # None: the message clears the person's plan for those days
    start_date: date
    end_date: date
    hours_per_day: Optional[float]  # None: hours not stated
    replaces_earlier: bool
    confidence: str
    evidence: str
    person_text: str = ""           # The person exactly as written in the message
    project_text: Optional[str] = None
    task: str = ""                  # What the hours are for, e.g. "Development", "Ticket 261"


SYSTEM_PROMPT = """You read a Microsoft Teams group chat of a software services company where work allocations are posted: who works on which project, on which days, for how many hours. Turn the allocations in the TARGET messages into rows.

Two styles are used, often mixed:
- Self-reported plans (the most common): a person posts their own plan for the day, listing projects, tasks and hours, without naming themselves. The person is then the SENDER of the message.
- Assignments: someone (usually a manager) names other people, e.g. "Mayur - Protecta full day".

How to fill each row:
- message_ref: the [ref] of the TARGET message the allocation comes from.
- person_text: the person exactly as written in the message ("Mayur", "Rushi", "@Poonam S"). For a self-reported plan, the sender's name as shown.
- person: the exact name from the PEOPLE list when only one person in the list can be meant. If the written name fits more than one person in the list (for example two people share a first name), or nobody in the list, repeat person_text - never pick one.
- project_text: the project exactly as written in the message (null only for a whole-day cancellation).
- project: the exact name from the PROJECTS list when only one project can be meant (abbreviations and partial names count). Otherwise repeat project_text. Leave is a project: use "Leave" for any kind of leave or day off. Use null only for a cancellation that clears all of the person's work on those days.
- start_date / end_date (YYYY-MM-DD): resolve "today", "tomorrow", weekday names, "this week" and "next week" from the message's sent date and weekday. Use the same date for a single day. If no date is stated, use the sent date, or the next working day (Monday to Friday) if the message was sent at or after 17:00. Weekends and holidays inside a range are removed later, so a plain range is fine.
- task: what the time is for, as written ("Development", "ACH Payment", "Ticket 261", "Client Call, Emails"). Empty string if not stated.
- hours_per_day: hours on that project per day, as a number. "30 minutes" is 0.5, "1 hr 15 min" is 1.25, "full day" is 8, "half day" is 4. If hours are not stated, use null - never guess a number.
- replaces_earlier: true only when the message gives a new plan for that person and dates that replaces the earlier one ("instead", "change", "move to", "only X"). "Only X" or "X instead" means a row for X with replaces_earlier true (hours null if not stated). Dropping one project ("not Y", "cancel Y") is a separate row for Y with hours_per_day 0. So "Poonam on Minimalist only today, not archival" is two rows: Minimalist (replaces_earlier true, hours null) and archival (hours 0).
- confidence: "high" when person, project, dates and hours are all explicit; "medium" when you had to resolve a reference or infer the date; "low" when you are unsure the message is an allocation at all.
- evidence: the exact words of the message the row comes from (short).

Rules:
- One row per person, project, task and continuous date range. "Mayur and Rushikesh on Protecta" is two rows.
- A person split across projects or tasks on the same day gets one row per project and task.
- In a self-reported plan, a heading line is usually the project and the next lines are its tasks ("Project: Protecta / Task: Development - 2 hrs", or "SOC 2 Documentation / R&D and Documentation - 6hrs").
- If the same person posts a new plan for the same day, the later message replaces the earlier one (replaces_earlier true) unless it clearly adds to it ("also", "additionally").
- Only extract from TARGET messages. CONTEXT messages are earlier messages, shown only to resolve references such as "same as yesterday" or "continue".
- Include every allocation, even when the person or project is not in the lists; those rows are checked by a person later. Example: "Neha on Apollo full week", with neither name listed, is still a row with person_text and person "Neha", project_text and project "Apollo".
- Skip messages that are not allocations: greetings, questions, status updates, task discussions.
- Messages may mix English with Hindi or Marathi.
- Never invent people, projects, dates or hours that the messages do not support."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "allocations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_ref": {"type": "string"},
                    "person_text": {"type": "string"},
                    "person": {"type": "string"},
                    "project_text": {"type": ["string", "null"]},
                    "project": {"type": ["string", "null"]},
                    "task": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "hours_per_day": {"type": ["number", "null"]},
                    "replaces_earlier": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
                    "evidence": {"type": "string"},
                },
                "required": ["message_ref", "person_text", "person", "project_text", "project", "task", "start_date", "end_date",
                             "hours_per_day", "replaces_earlier", "confidence", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["allocations"],
    "additionalProperties": False,
}


def _format_message(ref: str, m: ChatMessage) -> str:
    return f"[{ref}] {m.sent_at:%a %Y-%m-%d %H:%M} | {m.sender}:\n{m.text}"


def build_user_prompt(targets: Sequence[ChatMessage], context: Sequence[ChatMessage],
                      people: Sequence[str], projects: Sequence[str]) -> str:
    """Prompt body. Returns text where TARGET messages are labelled m1, m2, ..."""
    parts = ["PEOPLE:\n" + "\n".join(f"- {p}" for p in sorted(people)),
             "PROJECTS:\n" + "\n".join(f"- {p}" for p in sorted(projects))]
    if context:
        parts.append("CONTEXT messages (do not extract):\n\n"
                     + "\n\n".join(_format_message(f"c{i}", m) for i, m in enumerate(context, start=1)))
    parts.append("TARGET messages (extract allocations from these):\n\n"
                 + "\n\n".join(_format_message(f"m{i}", m) for i, m in enumerate(targets, start=1)))
    return "\n\n".join(parts)


def parse_extraction(data: Dict, ref_to_id: Dict[str, str]) -> List[RawAllocation]:
    """Validate the model output. Rows with an unknown message, bad dates or impossible hours are dropped."""
    rows = []
    for item in data.get("allocations", []):
        message_id = ref_to_id.get((item.get("message_ref") or "").strip())
        person = (item.get("person") or "").strip()
        if message_id is None or not person:
            logger.warning(f"Dropped extracted row without a valid message or person: {item}")
            continue
        try:
            start = date.fromisoformat(item["start_date"])
            end = date.fromisoformat(item["end_date"])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Dropped extracted row with invalid dates: {item}")
            continue
        if end < start:
            start, end = end, start
        hours = item.get("hours_per_day")
        if hours is not None and not (0 <= float(hours) <= 24):
            logger.warning(f"Dropped extracted row with impossible hours: {item}")
            continue
        project = (item.get("project") or "").strip() or None
        confidence = item.get("confidence") if item.get("confidence") in CONFIDENCE_LEVELS else "low"
        rows.append(RawAllocation(
            message_id=message_id,
            person=person,
            project=project,
            start_date=start,
            end_date=end,
            hours_per_day=None if hours is None else float(hours),
            replaces_earlier=bool(item.get("replaces_earlier")),
            confidence=confidence,
            evidence=(item.get("evidence") or "").strip()[:MAX_EVIDENCE_CHARS],
            person_text=(item.get("person_text") or "").strip() or person,
            project_text=(item.get("project_text") or "").strip() or project,
            task=(item.get("task") or "").strip()[:MAX_EVIDENCE_CHARS],
        ))
    return rows


class OpenAIAllocationExtractor:
    """Extracts allocations with OpenAI structured outputs (JSON schema, strict)."""

    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            if not api_key:
                raise ValueError("OPENAI_API_KEY must be set to extract allocations")
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = model

    def _call(self, user_prompt: str) -> Dict:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user_prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "allocations", "strict": True, "schema": OUTPUT_SCHEMA}},
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise RuntimeError(f"Model refused to extract allocations: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise RuntimeError("Model output was cut off; lower TARGET_BATCH_SIZE")
        return json.loads(choice.message.content)

    def extract(self, messages: Sequence[ChatMessage], people: Sequence[str],
                projects: Sequence[str]) -> List[RawAllocation]:
        """All allocations in `messages` (oldest first), processed in batches with earlier messages as context."""
        rows: List[RawAllocation] = []
        for start in range(0, len(messages), TARGET_BATCH_SIZE):
            targets = messages[start:start + TARGET_BATCH_SIZE]
            context = messages[max(0, start - CONTEXT_MESSAGES):start]
            ref_to_id = {f"m{i}": m.id for i, m in enumerate(targets, start=1)}
            data = self._call(build_user_prompt(targets, context, people, projects))
            batch = parse_extraction(data, ref_to_id)
            logger.info(f"Messages {start + 1}-{start + len(targets)}: {len(batch)} allocations extracted.")
            rows.extend(batch)
        return rows
