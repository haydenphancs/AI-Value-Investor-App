"""
Agent Registry - Plugin Management System
==========================================

The AgentRegistry is the central hub for managing AI agents.
It provides:
1. Dynamic agent registration
2. Agent discovery by ID or type
3. Auto-discovery of agents in the agents folder
4. Metadata listing for UI display

This enables the "plugin" architecture where new agents can be added
simply by creating a new file and registering the class.

Usage:
    # Get the registry
    registry = AgentRegistry()

    # Register a new agent
    registry.register(BuffettAgent)
    registry.register(PelosiTraderAgent)

    # Get an agent by ID
    agent = registry.get("buffett")
    result = await agent.execute(context)

    # List all persona agents
    personas = registry.list_by_type(AgentType.PERSONA)

    # Get metadata for UI
    metadata = registry.get_metadata()
"""

from typing import Dict, Type, List, Optional, Any
from dataclasses import dataclass
import logging
import importlib
import pkgutil
from pathlib import Path

from app.agents.base import BaseAgent, AgentType, AgentContext, AgentOutput
from app.core.result import Result
from app.core.exceptions import AppException, NotFoundError, ValidationError


logger = logging.getLogger(__name__)


@dataclass
class AgentMetadata:
    """
    Metadata about an agent for UI display.

    Contains information needed to show agents in the mobile app
    without needing to instantiate them.
    """
    agent_id: str
    agent_type: AgentType
    name: str
    description: str
    emoji: str
    is_available: bool = True
    requires_premium: bool = False
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.agent_id,
            "type": self.agent_type.value,
            "name": self.name,
            "description": self.description,
            "emoji": self.emoji,
            "is_available": self.is_available,
            "requires_premium": self.requires_premium,
            "tags": self.tags
        }


class AgentRegistry:
    """
    Central registry for all AI agents.

    Maintains a map of agent IDs to agent classes and provides
    methods for registration, discovery, and instantiation.
    """

    _instance: Optional["AgentRegistry"] = None
    _agents: Dict[str, Type[BaseAgent]] = {}
    _metadata: Dict[str, AgentMetadata] = {}
    _initialized: bool = False

    def __new__(cls) -> "AgentRegistry":
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._agents = {}
            cls._instance._metadata = {}
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize and auto-discover agents."""
        if not self._initialized:
            self._auto_discover()
            self._initialized = True

    # ========================================================================
    # Registration
    # ========================================================================

    def register(
        self,
        agent_class: Type[BaseAgent],
        metadata: Optional[AgentMetadata] = None
    ) -> None:
        """
        Register an agent class.

        Args:
            agent_class: The agent class to register
            metadata: Optional metadata (extracted from class if not provided)

        Raises:
            ValueError: If agent_id is already registered
        """
        # Instantiate to get properties
        temp_instance = agent_class()
        agent_id = temp_instance.agent_id

        if agent_id in self._agents:
            logger.warning(f"Agent '{agent_id}' already registered, skipping")
            return

        self._agents[agent_id] = agent_class

        # Create metadata
        if metadata is None:
            metadata = self._extract_metadata(temp_instance)

        self._metadata[agent_id] = metadata

        logger.info(f"Registered agent: {agent_id} ({agent_class.__name__})")

    def unregister(self, agent_id: str) -> bool:
        """
        Unregister an agent.

        Args:
            agent_id: ID of agent to remove

        Returns:
            True if agent was removed, False if not found
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            del self._metadata[agent_id]
            logger.info(f"Unregistered agent: {agent_id}")
            return True
        return False

    def _extract_metadata(self, agent: BaseAgent) -> AgentMetadata:
        """
        Extract metadata from an agent instance.

        Looks for optional class attributes like PERSONA_NAME, EMOJI, etc.
        """
        # Get class attributes with defaults
        name = getattr(agent, "PERSONA_NAME", None) or \
               getattr(agent, "name", None) or \
               agent.agent_id.replace("_", " ").title()

        emoji = getattr(agent, "PERSONA_EMOJI", None) or \
                getattr(agent, "emoji", "") or "🤖"

        requires_premium = getattr(agent, "REQUIRES_PREMIUM", False)
        tags = getattr(agent, "TAGS", [])

        return AgentMetadata(
            agent_id=agent.agent_id,
            agent_type=agent.agent_type,
            name=name,
            description=agent.description,
            emoji=emoji,
            is_available=True,
            requires_premium=requires_premium,
            tags=tags
        )

    # ========================================================================
    # Discovery
    # ========================================================================

    def _auto_discover(self) -> None:
        """
        Auto-discover and register agents from the agents package.

        Looks for all Python files in the agents directory and imports
        classes that inherit from BaseAgent.
        """
        logger.info("Auto-discovering agents...")

        # Import all persona agents
        try:
            from app.agents.personas import (
                BuffettAgent,
                AckmanAgent,
                MungerAgent,
                LynchAgent,
                GrahamAgent
            )
            for agent_class in [BuffettAgent, AckmanAgent, MungerAgent, LynchAgent, GrahamAgent]:
                try:
                    self.register(agent_class)
                except Exception as e:
                    logger.error(f"Failed to register {agent_class}: {e}")
        except ImportError as e:
            logger.warning(f"Could not import persona agents: {e}")

        # Import utility agents
        try:
            from app.agents.news_summarizer import NewsSummarizerAgent
            self.register(NewsSummarizerAgent)
        except ImportError as e:
            logger.warning(f"Could not import news summarizer: {e}")

        try:
            from app.agents.education_agent import EducationAgent
            self.register(EducationAgent)
        except ImportError as e:
            logger.warning(f"Could not import education agent: {e}")

        logger.info(f"Discovered {len(self._agents)} agents")

    # ========================================================================
    # Retrieval
    # ========================================================================

    def get(self, agent_id: str) -> BaseAgent:
        """
        Get an agent instance by ID.

        Args:
            agent_id: Unique agent identifier

        Returns:
            Agent instance

        Raises:
            NotFoundError: If agent not found
        """
        if agent_id not in self._agents:
            available = list(self._agents.keys())
            raise NotFoundError(
                resource_type="Agent",
                resource_id=agent_id,
                user_message=f"Unknown analysis style '{agent_id}'. Available: {', '.join(available)}"
            )

        agent_class = self._agents[agent_id]
        return agent_class()

    def get_or_none(self, agent_id: str) -> Optional[BaseAgent]:
        """
        Get an agent instance by ID, returning None if not found.

        Args:
            agent_id: Unique agent identifier

        Returns:
            Agent instance or None
        """
        if agent_id not in self._agents:
            return None
        return self._agents[agent_id]()

    def has(self, agent_id: str) -> bool:
        """Check if an agent is registered."""
        return agent_id in self._agents

    def list_ids(self) -> List[str]:
        """Get all registered agent IDs."""
        return list(self._agents.keys())

    def list_by_type(self, agent_type: AgentType) -> List[BaseAgent]:
        """
        Get all agents of a specific type.

        Args:
            agent_type: Type to filter by

        Returns:
            List of agent instances
        """
        return [
            agent_class()
            for agent_class in self._agents.values()
            if agent_class().agent_type == agent_type
        ]

    def list_persona_agents(self) -> List[BaseAgent]:
        """Get all persona agents (convenience method)."""
        return self.list_by_type(AgentType.PERSONA)

    # ========================================================================
    # Metadata
    # ========================================================================

    def get_metadata(self, agent_id: str) -> Optional[AgentMetadata]:
        """
        Get metadata for a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            AgentMetadata or None
        """
        return self._metadata.get(agent_id)

    def list_metadata(
        self,
        agent_type: Optional[AgentType] = None,
        available_only: bool = False
    ) -> List[AgentMetadata]:
        """
        Get metadata for multiple agents.

        Args:
            agent_type: Optional filter by type
            available_only: Only return available agents

        Returns:
            List of AgentMetadata
        """
        result = []
        for metadata in self._metadata.values():
            if agent_type and metadata.agent_type != agent_type:
                continue
            if available_only and not metadata.is_available:
                continue
            result.append(metadata)
        return result

    def get_personas_for_api(self) -> List[Dict[str, Any]]:
        """
        Get persona metadata formatted for API response.

        Returns:
            List of persona dictionaries for the mobile app
        """
        personas = self.list_metadata(agent_type=AgentType.PERSONA, available_only=True)
        return [p.to_dict() for p in personas]

    # ========================================================================
    # Execution
    # ========================================================================

    async def execute(
        self,
        agent_id: str,
        context: AgentContext
    ) -> Result[AgentOutput, AppException]:
        """
        Execute an agent by ID.

        Convenience method that gets the agent and executes it.

        Args:
            agent_id: Agent to execute
            context: Execution context

        Returns:
            Result with AgentOutput or error
        """
        try:
            agent = self.get(agent_id)
            return await agent.execute(context)
        except NotFoundError as e:
            from app.core.result import Failure
            return Failure(e)

    async def execute_multiple(
        self,
        agent_ids: List[str],
        context: AgentContext,
        parallel: bool = False
    ) -> Dict[str, Result[AgentOutput, AppException]]:
        """
        Execute multiple agents with the same context.

        Useful for getting analysis from multiple perspectives.

        Args:
            agent_ids: List of agent IDs to execute
            context: Shared execution context
            parallel: Run agents in parallel

        Returns:
            Dictionary mapping agent_id to Result
        """
        import asyncio
        from app.core.result import Failure

        if parallel:
            # Execute all agents in parallel
            tasks = {
                agent_id: self.execute(agent_id, context)
                for agent_id in agent_ids
            }
            results = await asyncio.gather(
                *[self.execute(aid, context) for aid in agent_ids],
                return_exceptions=True
            )
            return dict(zip(agent_ids, results))
        else:
            # Execute sequentially
            results = {}
            for agent_id in agent_ids:
                results[agent_id] = await self.execute(agent_id, context)
            return results

    # ========================================================================
    # Validation
    # ========================================================================

    def validate_agent_id(self, agent_id: str) -> None:
        """
        Validate that an agent ID exists.

        Args:
            agent_id: ID to validate

        Raises:
            ValidationError: If agent not found
        """
        if agent_id not in self._agents:
            from app.core.exceptions import InvalidPersonaError
            raise InvalidPersonaError(
                persona=agent_id,
                valid_personas=list(self._agents.keys())
            )

    # ========================================================================
    # Factory Methods
    # ========================================================================

    @classmethod
    def reset(cls) -> None:
        """
        Reset the registry (for testing).

        Clears all registered agents and reinitializes.
        """
        if cls._instance:
            cls._instance._agents.clear()
            cls._instance._metadata.clear()
            cls._instance._initialized = False
        cls._instance = None


# ============================================================================
# Global Instance
# ============================================================================

def get_agent_registry() -> AgentRegistry:
    """
    Get the global agent registry.

    Returns:
        AgentRegistry singleton instance
    """
    return AgentRegistry()
