"""
AI Agents Module
================

This module contains all AI agents for the application, organized as:

Base Classes:
- BaseAgent: Abstract base for all agents
- PersonaAgent: Base for investor persona agents

Registry:
- AgentRegistry: Central hub for agent discovery and instantiation

Agent Types:
- Persona Agents: Investor-style analysis (Buffett, Lynch, etc.)
- Analysis Agents: Specialized analysis (News, Technical, etc.)
- Utility Agents: Helper agents (Summarizer, etc.)

Usage:
    from app.agents import AgentRegistry, AgentContext

    registry = AgentRegistry()
    agent = registry.get("buffett")
    result = await agent.execute(context)

Adding New Agents:
    1. Create new file in appropriate subfolder
    2. Inherit from BaseAgent or PersonaAgent
    3. Implement required properties and methods
    4. Registry auto-discovers on startup
"""

# Base classes
from app.agents.base import (
    BaseAgent,
    AgentType,
    AgentStatus,
    AgentContext,
    AgentOutput,
)

# Persona base
from app.agents.persona_agent import PersonaAgent, PersonaConfig

# Registry
from app.agents.registry import AgentRegistry, AgentMetadata, get_agent_registry

__all__ = [
    # Base classes
    "BaseAgent",
    "AgentType",
    "AgentStatus",
    "AgentContext",
    "AgentOutput",
    # Persona
    "PersonaAgent",
    "PersonaConfig",
    # Registry
    "AgentRegistry",
    "AgentMetadata",
    "get_agent_registry",
]
