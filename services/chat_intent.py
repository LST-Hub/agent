"""
What a chat message to the agent is about (anything but a plain "yes" / "no", which are matched
without AI), so plans go to the plan reader and every other message gets an answer. The AI only
classifies; every answer is fixed text in chat_allocations.

intents:
  plan      - states work for today (project / task / hours), or answers the agent's question about it ("2hr")
  request   - wants to be added to a project or task ("add this new task to me", "add me to Archival - X")
  question  - asks how the agent works or what to do
  greeting  - hello, good morning
  thanks    - thank you, ok thanks
  other     - anything else
"""

import json
from dataclasses import dataclass
from typing import Optional, Sequence

INTENTS = ("plan", "request", "question", "greeting", "thanks", "other")
CONTEXT_MESSAGES = 6

SYSTEM_PROMPT = """You classify the LAST message an employee sent in a Microsoft Teams chat with the company's operations
agent. The agent saves the employee's work plan for today (project, task, hours) in the project tool TaskSprint after the
employee confirms it, and asks project managers to add people to projects or tasks. Earlier messages of the chat are
given for context only.

intent:
- "plan": the message states work for today (a project, task and/or hours, e.g. "Archival - Data Validation - 3 h",
  "Protecta full day"), or answers the agent's question about the plan (the agent asked "how many hours?" and the
  employee wrote "2hr"; the agent asked "which task?" and the employee named one).
- "request": the employee asks to be added to a project or a task, or wants a new task created for them
  (e.g. "add this new task to me", "add me to archival data validation test", "I need access to Protecta").
  This is not a plan, even when it names a project and task.
- "question": asks how to post a plan, what the agent does, why something was not saved.
- "greeting": hello, hi, good morning.
- "thanks": thank you, thanks, ok thanks.
- "other": anything else.
project / task: for a request, the project and task exactly as written; null when not written (e.g. "add this new
task to me"). For other intents use null. Messages may mix English with Hindi or Marathi."""

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "project": {"type": ["string", "null"]},
        "task": {"type": ["string", "null"]},
    },
    "required": ["intent", "project", "task"],
    "additionalProperties": False,
}


@dataclass
class Intent:
    intent: str
    project: Optional[str] = None
    task: Optional[str] = None


def build_prompt(text: str, context: Sequence = ()) -> str:
    lines = [f"{m.sender}: {m.text[:500]}" for m in list(context)[-CONTEXT_MESSAGES:]]
    earlier = ("EARLIER MESSAGES (context only):\n" + "\n".join(lines) + "\n\n") if lines else ""
    return f"{earlier}LAST MESSAGE (classify this one):\n{text[:2000]}"


class OpenAIIntentClassifier:
    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            if not api_key:
                raise ValueError("OPENAI_API_KEY must be set to read chat messages")
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        self.client, self.model = client, model

    def classify(self, text: str, context: Sequence = ()) -> Intent:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": build_prompt(text, context)}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "intent", "strict": True, "schema": SCHEMA}},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        intent = data.get("intent") if data.get("intent") in INTENTS else "other"
        return Intent(intent, (data.get("project") or "").strip() or None, (data.get("task") or "").strip() or None)
