"""
Investor Persona Agents
=======================

This module contains all investor persona agent implementations.
Each persona represents a distinct investment philosophy and analysis style.

Available Personas:
- BuffettAgent: Value investing, moats, long-term focus
- AckmanAgent: Activist investing, catalysts, operational improvements
- MungerAgent: Mental models, inversion, quality focus
- LynchAgent: Growth at reasonable price, PEG ratio
- GrahamAgent: Deep value, margin of safety, quantitative

Adding a New Persona:
1. Create a new class inheriting from PersonaAgent
2. Set all class constants (PERSONA_ID, etc.)
3. Implement the system_prompt property
4. Add to __all__ and imports in this file
5. The registry will auto-discover it on startup

"""

from app.agents.personas.buffett import BuffettAgent
from app.agents.personas.ackman import AckmanAgent
from app.agents.personas.munger import MungerAgent
from app.agents.personas.lynch import LynchAgent
from app.agents.personas.graham import GrahamAgent

__all__ = [
    "BuffettAgent",
    "AckmanAgent",
    "MungerAgent",
    "LynchAgent",
    "GrahamAgent",
]

# Persona ID to class mapping for quick lookup
PERSONA_MAP = {
    "buffett": BuffettAgent,
    "ackman": AckmanAgent,
    "munger": MungerAgent,
    "lynch": LynchAgent,
    "graham": GrahamAgent,
}


def get_persona_class(persona_id: str):
    """Get persona class by ID."""
    return PERSONA_MAP.get(persona_id)


def list_persona_ids():
    """Get list of all persona IDs."""
    return list(PERSONA_MAP.keys())
