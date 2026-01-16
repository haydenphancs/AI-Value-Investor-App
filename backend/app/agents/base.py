"""
Agent Base Classes - Plugin Architecture for AI Agents
======================================================

This module defines the abstract base classes for all AI agents in the system.
It provides a flexible plugin architecture that allows easy addition of new
agents (like "Pelosi Trader") without modifying existing code.

Architecture:
┌─────────────────────────────────────────────────────────────────┐
│                        BaseAgent                                 │
│  - Abstract base for ALL agents                                  │
│  - Defines common interface (execute, validate, etc.)            │
│  - Provides logging, error handling, metrics                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌────────────────┐  ┌────────────────┐
│PersonaAgent   │  │AnalysisAgent  │  │UtilityAgent   │
│(Buffett, etc.)│  │(News, Tech)   │  │(Summarizer)   │
└───────────────┘  └────────────────┘  └────────────────┘

Usage:
    # Define a new persona agent
    class PelosiTraderAgent(PersonaAgent):
        PERSONA_ID = "pelosi"
        PERSONA_NAME = "Nancy Pelosi"
        PERSONA_EMOJI = "🏛️"

        @property
        def system_prompt(self) -> str:
            return "Analyze based on Congressional trading patterns..."

    # Register it
    registry = AgentRegistry()
    registry.register(PelosiTraderAgent)

    # Use it
    agent = registry.get("pelosi")
    result = await agent.execute(context)
"""

from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging
import time
import asyncio

from app.core.result import Result, Success, Failure
from app.core.exceptions import (
    AppException,
    GeminiError,
    ServiceTimeoutError,
    ValidationError
)


# ============================================================================
# Agent Types and Enums
# ============================================================================

class AgentType(str, Enum):
    """Types of agents in the system."""
    PERSONA = "persona"          # Investor persona agents (Buffett, Lynch, etc.)
    ANALYSIS = "analysis"        # Analysis agents (Technical, Sentiment, etc.)
    UTILITY = "utility"          # Utility agents (Summarizer, Formatter, etc.)
    EDUCATION = "education"      # Educational content agents


class AgentStatus(str, Enum):
    """Execution status of an agent."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


# ============================================================================
# Agent Context and Output
# ============================================================================

@dataclass
class AgentContext:
    """
    Input context for agent execution.

    Contains all data needed for the agent to perform its task.
    Agents should not fetch additional data themselves.
    """
    # Required fields
    request_id: str

    # Common context
    user_id: Optional[str] = None
    ticker: Optional[str] = None
    company_name: Optional[str] = None

    # Financial data (pre-fetched)
    financial_data: Optional[Dict[str, Any]] = None
    market_data: Optional[Dict[str, Any]] = None

    # News/content context
    news_articles: Optional[List[Dict[str, Any]]] = None
    content_chunks: Optional[List[Dict[str, Any]]] = None

    # Custom parameters
    parameters: Dict[str, Any] = field(default_factory=dict)

    # Execution constraints
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout_seconds: int = 60

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a parameter from the context."""
        return self.parameters.get(key, default)


@dataclass
class AgentOutput:
    """
    Output from agent execution.

    Contains the result, metadata, and metrics.
    """
    # Result
    content: Dict[str, Any]
    raw_text: str

    # Metadata
    agent_id: str
    agent_type: AgentType
    status: AgentStatus

    # Metrics
    execution_time_ms: int
    tokens_used: Optional[int] = None
    model_version: Optional[str] = None

    # Error info (if failed)
    error_message: Optional[str] = None
    error_code: Optional[str] = None

    # Timestamps
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "raw_text": self.raw_text,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type.value,
            "status": self.status.value,
            "execution_time_ms": self.execution_time_ms,
            "tokens_used": self.tokens_used,
            "model_version": self.model_version,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }


# ============================================================================
# Abstract Base Agent
# ============================================================================

class BaseAgent(ABC):
    """
    Abstract base class for all AI agents.

    Provides:
    - Standardized execution interface
    - Logging and metrics
    - Error handling
    - Timeout management
    - Validation hooks

    All agents must implement:
    - agent_id: Unique identifier
    - agent_type: Type category
    - execute(): Main execution logic
    - validate_context(): Input validation
    """

    def __init__(self, gemini_client: Optional[Any] = None):
        """
        Initialize the agent.

        Args:
            gemini_client: Optional Gemini client for AI operations
        """
        self._gemini = gemini_client
        self._logger = logging.getLogger(self.__class__.__name__)
        self._status = AgentStatus.IDLE

    # ========================================================================
    # Abstract Properties (Must Implement)
    # ========================================================================

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """
        Unique identifier for this agent.

        Used for registration and lookup.
        Example: "buffett", "news_summarizer", "technical_analysis"
        """
        pass

    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        """
        Type category for this agent.

        Used for grouping and filtering.
        """
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Human-readable description of what this agent does.

        Shown to users in the UI.
        """
        pass

    # ========================================================================
    # Abstract Methods (Must Implement)
    # ========================================================================

    @abstractmethod
    async def _execute_impl(self, context: AgentContext) -> Dict[str, Any]:
        """
        Internal execution implementation.

        This is where the actual agent logic goes.
        Should return a dictionary with the result.

        Args:
            context: Validated execution context

        Returns:
            Dictionary with result data

        Raises:
            AppException: On errors
        """
        pass

    @abstractmethod
    def validate_context(self, context: AgentContext) -> List[str]:
        """
        Validate the input context.

        Returns a list of validation error messages.
        Empty list means valid.

        Args:
            context: Context to validate

        Returns:
            List of error messages (empty if valid)
        """
        pass

    # ========================================================================
    # Public Interface
    # ========================================================================

    @property
    def logger(self) -> logging.Logger:
        """Get the logger for this agent."""
        return self._logger

    @property
    def status(self) -> AgentStatus:
        """Get current execution status."""
        return self._status

    @property
    def gemini(self) -> Any:
        """Get the Gemini client."""
        if self._gemini is None:
            from app.integrations.gemini import GeminiClient
            self._gemini = GeminiClient()
        return self._gemini

    async def execute(
        self,
        context: AgentContext
    ) -> Result[AgentOutput, AppException]:
        """
        Execute the agent with the given context.

        This is the main public interface. It handles:
        - Validation
        - Timeout management
        - Error handling
        - Metrics collection

        Args:
            context: Execution context with all required data

        Returns:
            Result with AgentOutput or error
        """
        start_time = time.time()
        self._status = AgentStatus.RUNNING
        self.logger.info(f"Starting execution: {self.agent_id} (request: {context.request_id})")

        try:
            # Validate context
            errors = self.validate_context(context)
            if errors:
                self._status = AgentStatus.FAILED
                return Failure(ValidationError(
                    message=f"Context validation failed: {', '.join(errors)}",
                    user_message="Invalid input for this analysis."
                ))

            # Execute with timeout
            try:
                result = await asyncio.wait_for(
                    self._execute_impl(context),
                    timeout=context.timeout_seconds
                )
            except asyncio.TimeoutError:
                self._status = AgentStatus.TIMEOUT
                return Failure(ServiceTimeoutError(
                    service_name=self.agent_id,
                    timeout_seconds=context.timeout_seconds
                ))

            # Calculate metrics
            execution_time_ms = int((time.time() - start_time) * 1000)
            self._status = AgentStatus.COMPLETED

            output = AgentOutput(
                content=result,
                raw_text=result.get("raw_text", ""),
                agent_id=self.agent_id,
                agent_type=self.agent_type,
                status=AgentStatus.COMPLETED,
                execution_time_ms=execution_time_ms,
                tokens_used=result.get("tokens_used"),
                model_version=result.get("model_version"),
                completed_at=datetime.utcnow()
            )

            self.logger.info(
                f"Execution completed: {self.agent_id} "
                f"({execution_time_ms}ms, {output.tokens_used or 0} tokens)"
            )

            return Success(output)

        except AppException as e:
            self._status = AgentStatus.FAILED
            self.logger.error(f"Execution failed: {self.agent_id} - {e}")
            return Failure(e)

        except Exception as e:
            self._status = AgentStatus.FAILED
            self.logger.exception(f"Unexpected error in {self.agent_id}: {e}")
            return Failure(GeminiError(str(e)))

    async def execute_or_raise(self, context: AgentContext) -> AgentOutput:
        """
        Execute and raise on failure.

        Convenience method when you want exceptions propagated.

        Args:
            context: Execution context

        Returns:
            AgentOutput on success

        Raises:
            AppException: On failure
        """
        result = await self.execute(context)
        return result.unwrap()

    # ========================================================================
    # Helper Methods for Subclasses
    # ========================================================================

    async def _generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Generate text using Gemini.

        Wrapper around the Gemini client with standard error handling.

        Args:
            prompt: User prompt
            system_instruction: Optional system prompt
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Dictionary with text, tokens_used, model

        Raises:
            GeminiError: On API errors
        """
        try:
            response = await self.gemini.generate_text(
                prompt=prompt,
                system_instruction=system_instruction,
                max_tokens=max_tokens,
                temperature=temperature
            )
            return response
        except Exception as e:
            raise GeminiError(str(e))

    def _extract_section(
        self,
        text: str,
        headers: List[str],
        default: Optional[str] = None
    ) -> Optional[str]:
        """
        Extract a section from markdown-like text by header.

        Args:
            text: Full text
            headers: List of possible header variations
            default: Default value if not found

        Returns:
            Extracted section text or default
        """
        text_upper = text.upper()
        for header in headers:
            if header.upper() in text_upper:
                start = text_upper.find(header.upper())
                # Find next section
                end = len(text)
                for marker in ["##", "\n\n\n", "---"]:
                    pos = text.find(marker, start + len(header))
                    if pos > 0:
                        end = min(end, pos)
                return text[start:end].strip()
        return default

    def _extract_bullets(self, text: str, max_items: int = 10) -> List[str]:
        """
        Extract bullet points from text.

        Args:
            text: Text containing bullet points
            max_items: Maximum items to extract

        Returns:
            List of bullet point strings
        """
        bullets = []
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith(("-", "•", "*", "✓", "✗", "→")):
                clean = line[1:].strip()
                if clean:
                    bullets.append(clean)
                    if len(bullets) >= max_items:
                        break
        return bullets
