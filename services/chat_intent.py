"""
What a chat message that is not a work plan (and not "yes" / "no") is about, so the agent can
answer every message. The AI only classifies; every answer is fixed text in chat_allocations.

intents:
  request   - wants to be added to a project or task ("add this new task to me", "assign me to Archival")
  question  - asks how the agent works or what to do
  greeting  - hello, good morning
  thanks    - thank you, ok thanks
  other     - anything else
"""

import json
from dataclasses import dataclass
from typing import Optional

INTENTS = ("request", "question", "greeting", "thanks", "other")

SYSTEM_PROMPT = """You classify one Microsoft Teams message that an employee sent to the company's operations agent.
The agent saves the employee's daily work plan (project, task, hours) in the project tool TaskSprint, and asks project
managers to add people to projects or tasks. The message is NOT a work plan.

intent:
- "request": the employee wants to be added to a project or a task, or wants a new task created for them
  (e.g. "add this new task to me", "please assign me to Archival", "I need access to Protecta").
- "question": asks how to post a plan, what the agent does, why something was not saved.
- "greeting": hello, hi, good morning.
- "thanks": thank you, thanks, ok thanks.
- "other": anything else.
project / task: for a request, the project and task exactly as written in the message; null when not written
(e.g. "add this new task to me" has neither). Messages may mix English with Hindi or Marathi."""

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


class OpenAIIntentClassifier:
    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            if not api_key:
                raise ValueError("OPENAI_API_KEY must be set to read chat messages")
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        self.client, self.model = client, model

    def classify(self, text: str) -> Intent:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text[:2000]}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "intent", "strict": True, "schema": SCHEMA}},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        intent = data.get("intent") if data.get("intent") in INTENTS else "other"
        return Intent(intent, (data.get("project") or "").strip() or None, (data.get("task") or "").strip() or None)
