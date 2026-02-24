"""
AI Agent Layer.
Handles optional LLM integration for smart summaries.
"""

from typing import Optional
from config.settings import settings
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

class AIAgent:
    """
    Wraps LLM calls to generate operational insights.
    """
    
    def __init__(self):
        self.api_key = settings.ai.api_key
        self.enabled = bool(self.api_key)
        self.client = OpenAI(api_key=self.api_key) if self.enabled else None

    def generate_weekly_summary(self, metrics_text: str) -> str:
        """
        Generates a natural language summary of what a resource worked on
        for a specific project during the week, based on their timesheet descriptions.
        
        Args:
            metrics_text: Formatted text containing resource name, project name,
                          tasks worked on, and all timesheet descriptions for the week.
            
        Returns:
            str: AI-generated summary of the work done.
        """
        if not self.enabled:
            logger.warning("AI summarization disabled - no API key configured.")
            return "AI summarization disabled."
        
        logger.info("Generating AI summary...")
        
        system_prompt = """You are a professional project manager assistant.
Your job is to read timesheet descriptions filled by a resource for a specific project during the week,
and generate a concise, clear summary of what work was done.

Guidelines:
- Summarize the key activities and accomplishments in 2-4 sentences
- Use professional language
- Focus on WHAT was done, not HOW MANY hours
- If descriptions are vague or missing, note that descriptions were insufficient
- Do not repeat the resource name or project name in the summary
- Keep it under 100 words"""

        user_prompt = f"""Based on the following timesheet data, write a brief summary of the work done by this resource on this project for the week:

{metrics_text}

Write a concise summary of the work completed:"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=150,
                temperature=0.3,
            )
            
            summary = response.choices[0].message.content.strip()
            logger.info("AI summary generated successfully.")
            return summary

        except Exception as e:
            logger.error(f"Error generating AI summary: {e}", exc_info=True)
            return f"Summary unavailable due to an error: {str(e)}"
