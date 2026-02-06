"""
AI Agent Layer.
Handles optional LLM integration for smart summaries.
"""

from typing import Optional
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

class AIAgent:
    """
    Wraps LLM calls to generate operational insights.
    """
    
    def __init__(self):
        self.api_key = settings.ai.api_key
        self.enabled = bool(self.api_key)

    def generate_weekly_summary(self, metrics_text: str) -> str:
        """
        Generates a natural language summary of the weekly metrics.
        
        Args:
            metrics_text: Raw text or JSON dump of weekly metrics.
            
        Returns:
            str: AI-generated summary.
        """
        if not self.enabled:
            return "AI summarization disabled."
            
        logger.info("Generating AI summary...")
        
        # Placeholder for OpenAI call
        # response = openai.ChatCompletion.create(...)
        
        return f"AI Summary of metrics: {metrics_text[:50]}..."
