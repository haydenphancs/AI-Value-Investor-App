"""
Research Pydantic Schemas
Request and response models for deep research reports.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.schemas.common import (
    InvestorPersona,
    ReportStatus,
    BaseResponse,
    TimestampMixin,
    AIMetadata
)


# Research Request Models
# =======================

class ResearchReportCreate(BaseModel):
    """Create deep research report request."""
    stock_id: str
    investor_persona: InvestorPersona
    analysis_period: str = Field(
        default="annual",
        description="Time period for analysis (annual, 5-year, etc.)"
    )
    custom_instructions: Optional[str] = Field(
        None,
        max_length=1000,
        description="Optional custom instructions for the analysis"
    )


class ResearchReportUpdate(BaseModel):
    """Update research report (for rating/feedback)."""
    user_rating: Optional[int] = Field(None, ge=1, le=5)
    user_feedback: Optional[str] = Field(None, max_length=2000)


# Research Response Models
# ========================

class InvestmentThesis(BaseModel):
    """Investment thesis section."""
    summary: str
    key_drivers: List[str] = Field(max_items=5)
    risks: List[str] = Field(max_items=5)
    time_horizon: str = Field(description="short/medium/long-term")
    conviction_level: str = Field(description="high/medium/low")


class MoatAnalysis(BaseModel):
    """Competitive advantage (moat) analysis."""
    moat_rating: str = Field(description="wide/narrow/none")
    moat_sources: List[str] = Field(description="Sources of competitive advantage")
    moat_sustainability: str = Field(description="sustainable/declining/emerging")
    competitive_position: str
    barriers_to_entry: List[str]


class ValuationAnalysis(BaseModel):
    """Valuation assessment."""
    valuation_rating: str = Field(description="undervalued/fairly-valued/overvalued")
    key_metrics: Dict[str, Any]
    peer_comparison: Optional[Dict[str, Any]] = None
    historical_context: Optional[str] = None
    margin_of_safety: Optional[str] = None


class RiskAssessment(BaseModel):
    """Risk factors analysis."""
    overall_risk: str = Field(description="low/medium/high")
    business_risks: List[str]
    financial_risks: List[str]
    market_risks: List[str]
    management_risks: Optional[List[str]] = None
    regulatory_risks: Optional[List[str]] = None


class ResearchReportBrief(BaseResponse, TimestampMixin):
    """Brief research report (list view)."""
    id: str
    user_id: str
    stock_id: str

    # Stock info (embedded)
    stock: Dict[str, Any]

    # Report metadata
    investor_persona: InvestorPersona
    analysis_period: str
    status: ReportStatus

    # Brief content
    title: Optional[str]
    executive_summary: Optional[str] = Field(None, max_length=1000)

    # Performance
    generation_time_seconds: Optional[int]
    views_count: int = 0

    # User interaction
    user_rating: Optional[int] = Field(None, ge=1, le=5)

    # Timestamps
    completed_at: Optional[datetime]

    # Extra fields
    persona_emoji: str = Field(description="Emoji for investor persona")
    is_fresh: bool = Field(description="Completed within last 7 days")
    read_time_minutes: Optional[int] = Field(None, description="Estimated reading time")


class ResearchReportDetail(ResearchReportBrief):
    """Full research report with complete analysis."""

    # Full report sections
    investment_thesis: Optional[InvestmentThesis]
    pros: Optional[List[str]]
    cons: Optional[List[str]]
    moat_analysis: Optional[MoatAnalysis]
    valuation_analysis: Optional[ValuationAnalysis]
    risk_assessment: Optional[RiskAssessment]

    # Additional content
    full_report: Optional[str]  # Markdown formatted
    key_takeaways: Optional[List[str]] = Field(None, max_items=5)
    action_recommendation: Optional[str] = Field(
        None,
        description="buy/hold/sell/watch"
    )

    # Metadata
    report_metadata: Optional[Dict[str, Any]]
    sources_used: Optional[List[Dict[str, Any]]]

    # AI metadata
    ai_metadata: Optional[AIMetadata]
    tokens_used: Optional[int]
    cost_usd: Optional[float]

    # Error info (if failed)
    error_message: Optional[str]

    # User feedback
    user_feedback: Optional[str]

    class Config:
        json_schema_extra = {
            "example": {
                "id": "report-123",
                "investor_persona": "buffett",
                "status": "completed",
                "title": "Deep Dive: Apple Inc. - A Buffett-Style Analysis",
                "executive_summary": "Apple demonstrates exceptional moat through ecosystem lock-in...",
                "pros": [
                    "Dominant market position in premium smartphones",
                    "Strong recurring revenue from services",
                    "Exceptional capital allocation"
                ],
                "cons": [
                    "Heavy dependence on iPhone revenue",
                    "Regulatory scrutiny increasing",
                    "China exposure creates geopolitical risk"
                ]
            }
        }


# Investor Persona Configurations
# ===============================

class InvestorPersonaConfig(BaseModel):
    """Configuration for investor persona."""
    persona: InvestorPersona
    display_name: str
    description: str
    focus_areas: List[str]
    key_metrics: List[str]
    investment_philosophy: str
    typical_holding_period: str
    emoji: str

    class Config:
        json_schema_extra = {
            "example": {
                "persona": "buffett",
                "display_name": "Warren Buffett",
                "description": "Focus on durable competitive advantages and long-term value",
                "focus_areas": ["moat", "management", "capital_allocation"],
                "key_metrics": ["ROE", "FCF", "debt_levels"],
                "investment_philosophy": "Buy wonderful companies at fair prices",
                "typical_holding_period": "10+ years",
                "emoji": "🎩"
            }
        }


# Report Generation Progress
# ==========================

class ReportGenerationProgress(BaseModel):
    """Progress update for report generation."""
    report_id: str
    status: ReportStatus
    progress_percent: int = Field(ge=0, le=100)
    current_step: str
    estimated_time_remaining: Optional[int] = Field(None, description="Seconds remaining")
    steps_completed: List[str]
    steps_remaining: List[str]


class ReportGenerationError(BaseModel):
    """Error during report generation."""
    report_id: str
    error_type: str
    error_message: str
    retry_possible: bool
    suggestions: Optional[List[str]] = None


# Analytics
# =========

class ResearchAnalytics(BaseModel):
    """Analytics for research reports."""
    user_id: str
    total_reports: int
    reports_by_persona: Dict[str, int]
    reports_by_status: Dict[str, int]
    average_rating: Optional[float] = Field(None, ge=1.0, le=5.0)
    total_tokens_used: int
    total_cost_usd: float
    favorite_persona: Optional[InvestorPersona]
    most_researched_stocks: List[Dict[str, Any]]
