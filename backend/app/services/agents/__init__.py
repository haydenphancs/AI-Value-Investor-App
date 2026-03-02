"""
Multi-Agent Deep Research System

Four distinct AI investor personas, each with unique research strategies
and analysis styles, powered by Gemini function calling for autonomous
FMP data gathering.
"""

from app.services.agents.research_agent import ResearchAgent
from app.services.agents.persona_config import get_persona_config, PERSONA_KEYS

__all__ = ["ResearchAgent", "get_persona_config", "PERSONA_KEYS"]
