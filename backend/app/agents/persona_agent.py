"""
Persona Agent - Base Class for Investor Personas
================================================

This module provides the base class for investor persona agents
(Buffett, Lynch, Munger, etc.).

PersonaAgent extends BaseAgent with:
1. Standardized persona configuration
2. Financial data formatting
3. Report structure templates
4. Common analysis patterns

Creating a New Persona:
    1. Create a new class inheriting from PersonaAgent
    2. Set class constants (PERSONA_ID, PERSONA_NAME, etc.)
    3. Implement system_prompt property
    4. Optionally override analysis_template property
    5. Register with AgentRegistry

Example (Adding a "Pelosi Trader" Persona):
    class PelosiTraderAgent(PersonaAgent):
        PERSONA_ID = "pelosi"
        PERSONA_NAME = "Nancy Pelosi"
        PERSONA_EMOJI = "🏛️"
        PERSONA_TAGLINE = "Congressional Trading Patterns"
        REQUIRES_PREMIUM = True
        TAGS = ["politics", "insider", "trading"]

        @property
        def system_prompt(self) -> str:
            return '''You analyze stocks based on Congressional trading patterns...'''

        @property
        def analysis_template(self) -> str:
            return '''# {company_name} - Congressional Trading Analysis...'''
"""

from abc import abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json

from app.agents.base import (
    BaseAgent,
    AgentType,
    AgentContext,
    AgentOutput
)
from app.core.result import Result
from app.core.exceptions import AppException, ValidationError


@dataclass
class PersonaConfig:
    """
    Configuration for an investor persona.

    Contains all the static information about a persona
    that can be used for display and documentation.
    """
    id: str
    name: str
    emoji: str
    tagline: str
    description: str
    focus_areas: List[str]
    key_metrics: List[str]
    investment_style: str
    time_horizon: str
    risk_tolerance: str
    requires_premium: bool = False
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API-friendly dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "tagline": self.tagline,
            "description": self.description,
            "focus_areas": self.focus_areas,
            "key_metrics": self.key_metrics,
            "investment_style": self.investment_style,
            "time_horizon": self.time_horizon,
            "risk_tolerance": self.risk_tolerance,
            "requires_premium": self.requires_premium,
            "tags": self.tags
        }


class PersonaAgent(BaseAgent):
    """
    Base class for investor persona agents.

    Provides a standardized framework for creating persona-based
    analysis agents. Subclasses must implement:
    - PERSONA_ID: Unique identifier
    - PERSONA_NAME: Display name
    - system_prompt: The persona's AI system prompt

    Optional overrides:
    - PERSONA_EMOJI: Icon for the persona
    - PERSONA_TAGLINE: Short description
    - analysis_template: Custom report template
    - _extract_structured_data: Custom data extraction
    """

    # ========================================================================
    # Class Constants (Override in Subclasses)
    # ========================================================================

    PERSONA_ID: str = "base_persona"
    PERSONA_NAME: str = "Base Persona"
    PERSONA_EMOJI: str = "🎯"
    PERSONA_TAGLINE: str = "Investment Analysis"
    PERSONA_DESCRIPTION: str = "Base persona for investment analysis."

    # Focus areas for this persona
    FOCUS_AREAS: List[str] = ["fundamentals", "valuation"]
    KEY_METRICS: List[str] = ["P/E", "ROE", "Debt/Equity"]

    # Investment characteristics
    INVESTMENT_STYLE: str = "value"
    TIME_HORIZON: str = "long-term"
    RISK_TOLERANCE: str = "moderate"

    # Access control
    REQUIRES_PREMIUM: bool = False
    TAGS: List[str] = []

    # ========================================================================
    # BaseAgent Implementation
    # ========================================================================

    @property
    def agent_id(self) -> str:
        """Unique identifier for this persona."""
        return self.PERSONA_ID

    @property
    def agent_type(self) -> AgentType:
        """All persona agents are of type PERSONA."""
        return AgentType.PERSONA

    @property
    def description(self) -> str:
        """Description shown to users."""
        return self.PERSONA_DESCRIPTION

    def validate_context(self, context: AgentContext) -> List[str]:
        """
        Validate that context has required data for persona analysis.

        Required:
        - ticker: Stock symbol
        - financial_data: Pre-fetched financial data

        Returns:
            List of validation error messages
        """
        errors = []

        if not context.ticker:
            errors.append("Missing required field: ticker")

        if not context.financial_data:
            errors.append("Missing required field: financial_data")

        return errors

    async def _execute_impl(self, context: AgentContext) -> Dict[str, Any]:
        """
        Execute the persona analysis.

        Steps:
        1. Format financial data for prompt
        2. Build the analysis prompt
        3. Generate AI response
        4. Extract structured data
        5. Return formatted result
        """
        # Format financial context
        financial_context = self._format_financial_data(context.financial_data)

        # Build prompt
        prompt = self._build_prompt(
            ticker=context.ticker,
            company_name=context.company_name or context.ticker,
            financial_context=financial_context,
            custom_instructions=context.get_param("custom_instructions")
        )

        # Generate analysis
        response = await self._generate_text(
            prompt=prompt,
            system_instruction=self.system_prompt,
            max_tokens=context.max_tokens,
            temperature=context.temperature
        )

        raw_text = response.get("text", "")

        # Extract structured components
        structured = self._extract_structured_data(raw_text)

        return {
            "raw_text": raw_text,
            "tokens_used": response.get("tokens_used"),
            "model_version": response.get("model"),
            "persona_id": self.PERSONA_ID,
            "persona_name": self.PERSONA_NAME,
            "persona_emoji": self.PERSONA_EMOJI,
            **structured
        }

    # ========================================================================
    # Abstract Properties (Must Override)
    # ========================================================================

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """
        The system prompt that defines this persona's analysis style.

        This should capture the investment philosophy, focus areas,
        and communication style of the persona.

        Example:
            return '''You are analyzing companies through Warren Buffett's lens.
            Focus on: durable competitive advantages, management quality...'''
        """
        pass

    # ========================================================================
    # Optional Overrides
    # ========================================================================

    @property
    def analysis_template(self) -> str:
        """
        Template for the analysis report structure.

        Override to customize the report format for this persona.
        Use {placeholders} for dynamic content.
        """
        return """# {company_name} ({ticker}) - {persona_name} Analysis

## Executive Summary
{executive_summary}

## The Business
{business_description}

## Competitive Position
{competitive_analysis}

## Financial Analysis
{financial_analysis}

## Investment Case

### Pros
{pros}

### Cons
{cons}

## Valuation Assessment
{valuation}

## Risk Factors
{risks}

## Recommendation
{recommendation}

---
*Analysis generated using {persona_name} methodology*
"""

    def _extract_structured_data(self, text: str) -> Dict[str, Any]:
        """
        Extract structured components from the raw analysis text.

        Override to customize extraction for specific persona formats.

        Args:
            text: Raw analysis text from AI

        Returns:
            Dictionary of structured components
        """
        return {
            "executive_summary": self._extract_section(
                text, ["Executive Summary", "Summary", "Overview"]
            ),
            "pros": self._extract_bullets(
                self._extract_section(text, ["Pros", "Strengths", "What I Like"]) or ""
            ),
            "cons": self._extract_bullets(
                self._extract_section(text, ["Cons", "Weaknesses", "Concerns"]) or ""
            ),
            "moat_analysis": self._extract_section(
                text, ["Moat", "Competitive Advantage", "Competitive Position"]
            ),
            "valuation_notes": self._extract_section(
                text, ["Valuation", "Fair Value", "Price Analysis"]
            ),
            "risk_factors": self._extract_bullets(
                self._extract_section(text, ["Risk", "Concerns", "What Could Go Wrong"]) or ""
            ),
            "recommendation": self._extract_section(
                text, ["Recommendation", "Verdict", "Final Take", "Action"]
            )
        }

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _build_prompt(
        self,
        ticker: str,
        company_name: str,
        financial_context: str,
        custom_instructions: Optional[str] = None
    ) -> str:
        """
        Build the analysis prompt.

        Args:
            ticker: Stock ticker
            company_name: Company name
            financial_context: Formatted financial data
            custom_instructions: Optional user instructions

        Returns:
            Complete prompt string
        """
        prompt = f"""Analyze {company_name} ({ticker}) and provide a comprehensive research report.

FINANCIAL DATA:
{financial_context}

{f"ADDITIONAL INSTRUCTIONS: {custom_instructions}" if custom_instructions else ""}

Provide your analysis using this structure:
{self.analysis_template}

Be thorough, specific, and true to {self.PERSONA_NAME}'s investment philosophy.
Focus on long-term business fundamentals. Ignore short-term price volatility.
"""
        return prompt

    def _format_financial_data(self, data: Dict[str, Any]) -> str:
        """
        Format financial data into a context string for the AI.

        Args:
            data: Raw financial data dictionary

        Returns:
            Formatted string for prompt
        """
        parts = []

        # Company overview
        if data.get("description"):
            parts.append(f"Company: {data['description'][:500]}")

        if data.get("sector"):
            parts.append(f"Sector: {data['sector']}")
        if data.get("industry"):
            parts.append(f"Industry: {data['industry']}")

        # Key financials
        if data.get("market_cap"):
            parts.append(f"Market Cap: ${data['market_cap']:,.0f}")

        # Recent financials
        if data.get("income_statements") and len(data["income_statements"]) > 0:
            recent = data["income_statements"][0]
            parts.append("\nRecent Annual Financials:")
            if recent.get("revenue"):
                parts.append(f"  Revenue: ${recent['revenue']:,.0f}")
            if recent.get("netIncome"):
                parts.append(f"  Net Income: ${recent['netIncome']:,.0f}")
            if recent.get("eps"):
                parts.append(f"  EPS: ${recent['eps']:.2f}")

        # Key ratios
        if data.get("ratios") and len(data["ratios"]) > 0:
            ratios = data["ratios"][0]
            parts.append("\nKey Ratios:")
            if ratios.get("returnOnEquity"):
                parts.append(f"  ROE: {ratios['returnOnEquity']*100:.1f}%")
            if ratios.get("priceEarningsRatio"):
                parts.append(f"  P/E: {ratios['priceEarningsRatio']:.1f}")
            if ratios.get("debtEquityRatio"):
                parts.append(f"  Debt/Equity: {ratios['debtEquityRatio']:.2f}")

        # Growth metrics
        if data.get("key_metrics") and len(data["key_metrics"]) > 0:
            metrics = data["key_metrics"][0]
            parts.append("\nKey Metrics:")
            if metrics.get("revenuePerShare"):
                parts.append(f"  Revenue/Share: ${metrics['revenuePerShare']:.2f}")
            if metrics.get("freeCashFlowPerShare"):
                parts.append(f"  FCF/Share: ${metrics['freeCashFlowPerShare']:.2f}")

        return "\n".join(parts)

    @classmethod
    def get_config(cls) -> PersonaConfig:
        """
        Get the persona configuration.

        Returns:
            PersonaConfig with all persona details
        """
        return PersonaConfig(
            id=cls.PERSONA_ID,
            name=cls.PERSONA_NAME,
            emoji=cls.PERSONA_EMOJI,
            tagline=cls.PERSONA_TAGLINE,
            description=cls.PERSONA_DESCRIPTION,
            focus_areas=cls.FOCUS_AREAS,
            key_metrics=cls.KEY_METRICS,
            investment_style=cls.INVESTMENT_STYLE,
            time_horizon=cls.TIME_HORIZON,
            risk_tolerance=cls.RISK_TOLERANCE,
            requires_premium=cls.REQUIRES_PREMIUM,
            tags=cls.TAGS
        )
