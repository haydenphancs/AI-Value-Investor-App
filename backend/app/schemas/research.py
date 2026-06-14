"""
Research schemas — aligned with Supabase research_reports table and Swift frontend models.

Swift frontend uses .convertFromSnakeCase / .convertToSnakeCase on its JSONDecoder/Encoder,
so all field names here are snake_case and map 1-to-1 with iOS property names.

Key alignment points:
  - iOS GenerateResearchRequest sends: stock_id, investor_persona
  - iOS ResearchReportDetail expects: stock_id (= ticker alias), overall_score, fair_value_estimate
  - DB report_status enum: pending | processing | completed | failed
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict, Any


# ── Request Models ───────────────────────────────────────────────────────────


class GenerateResearchRequest(BaseModel):
    stock_id: str = Field(description="Ticker symbol (e.g. AAPL)")
    investor_persona: str = Field(description="Persona key (e.g. warren_buffett)")
    # Phase 3 forward-compat: lets iOS opt into a faster Stage A+B-only
    # generation path (no agentic deep-research loop). Today the
    # backend treats both values the same, but the field shape is
    # locked so a future fast-path doesn't require an iOS rebuild.
    priority: Literal["fast", "deep"] = Field(
        default="deep",
        description=(
            "Depth/latency tradeoff. 'deep' runs the full agentic loop "
            "(~60s, default). 'fast' is reserved for a future Stage A+B "
            "only path (~15s) and currently behaves the same as 'deep'."
        ),
    )


class RateReportRequest(BaseModel):
    rating: int = Field(ge=1, le=5, description="Star rating 1-5")
    feedback: Optional[str] = None


# ── Response Models ──────────────────────────────────────────────────────────


class ResearchGenerationResponse(BaseModel):
    report_id: str
    status: str
    estimated_seconds: Optional[int] = 60
    poll_url: Optional[str] = None


class ResearchStatusResponse(BaseModel):
    report_id: str
    status: str
    progress: int = 0
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    # Phase 3: machine-readable code split out of error_message so iOS
    # can route to a specific UI (retry, upgrade, check-symbol, etc.)
    # without parsing the human string. Null on success; null for
    # legacy failure rows that pre-date this contract.
    error_code: Optional[str] = None
    estimated_time_remaining: Optional[int] = None


# ── Structured Sub-Models (JSONB columns in DB) ─────────────────────────────


class InvestmentThesis(BaseModel):
    summary: Optional[str] = None
    key_drivers: Optional[List[str]] = None
    risks: Optional[List[str]] = None
    time_horizon: Optional[str] = None
    conviction_level: Optional[str] = None


class MoatAnalysis(BaseModel):
    moat_rating: Optional[str] = None
    moat_sources: Optional[List[str]] = None
    moat_sustainability: Optional[str] = None
    competitive_position: Optional[str] = None
    barriers_to_entry: Optional[List[str]] = None


class ValuationAnalysis(BaseModel):
    valuation_rating: Optional[str] = None
    key_metrics: Optional[Dict[str, Any]] = None
    historical_context: Optional[str] = None
    margin_of_safety: Optional[str] = None


class RiskAssessment(BaseModel):
    overall_risk: Optional[str] = None
    business_risks: Optional[List[str]] = None
    financial_risks: Optional[List[str]] = None
    market_risks: Optional[List[str]] = None


# ── Full Report Detail (matches DB research_reports + iOS ResearchReportDetail) ─


class ResearchReportDetail(BaseModel):
    id: str
    user_id: str
    stock_id: Optional[str] = None  # mirrors ticker for iOS stockId
    ticker: str
    company_name: Optional[str] = None
    industry: Optional[str] = None  # populated from FMP profile at insert time
    investor_persona: str
    status: str

    # Report content
    title: Optional[str] = None
    executive_summary: Optional[str] = None
    investment_thesis: Optional[InvestmentThesis] = None
    pros: Optional[List[str]] = None
    cons: Optional[List[str]] = None
    moat_analysis: Optional[MoatAnalysis] = None
    valuation_analysis: Optional[ValuationAnalysis] = None
    risk_assessment: Optional[RiskAssessment] = None
    full_report: Optional[str] = None
    key_takeaways: Optional[List[str]] = None
    action_recommendation: Optional[str] = None

    # Scoring (from home_feed migration)
    overall_score: Optional[float] = None  # 0-100
    fair_value_estimate: Optional[float] = None

    # Generation metadata
    generation_time_seconds: Optional[int] = None
    tokens_used: Optional[int] = None

    # Timestamps
    created_at: str
    completed_at: Optional[str] = None

    # User feedback
    user_rating: Optional[int] = None
    user_feedback: Optional[str] = None

    # Credit lifecycle (migration 041): drives the iOS "[Refunded]"
    # chip on failed cards. Always False on completed reports.
    is_refunded: bool = False
    credits_charged: int = 5

    # Detailed-analysis PDF (migration 064). pdf_status: pending|ready|failed.
    # Null on reports created before the PDF feature until backfilled.
    pdf_status: Optional[str] = None
    pdf_generated_at: Optional[str] = None


class ResearchReportListItem(BaseModel):
    """Lightweight model for the reports list (GET /research/reports).

    `industry` and `current_step` are required by the iOS Reports tab
    card to render the industry subtitle and the live "Analyzing
    fundamentals..." text under the progress bar while a report is
    in-flight.
    """
    id: str
    stock_id: Optional[str] = None
    ticker: str
    company_name: Optional[str] = None
    industry: Optional[str] = None
    investor_persona: str
    status: str
    title: Optional[str] = None
    executive_summary: Optional[str] = None
    overall_score: Optional[float] = None
    fair_value_estimate: Optional[float] = None
    progress: Optional[int] = None
    current_step: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    user_rating: Optional[int] = None
    # Credit lifecycle (migration 041): drives the iOS "[Refunded]"
    # chip on failed cards.
    is_refunded: bool = False
    # Detailed-analysis PDF readiness (migration 064): pending|ready|failed.
    pdf_status: Optional[str] = None


class PersonaResponse(BaseModel):
    id: str
    key: str
    name: str
    title: Optional[str] = None
    tagline: Optional[str] = None
    style: Optional[str] = None
    description: Optional[str] = None
    key_principles: Optional[List[str]] = None
    accent_color: Optional[str] = None
    icon_name: Optional[str] = None
    focus: Optional[str] = None
    famous_quotes: Optional[List[str]] = None
    is_active: bool = True


# ── Trending Analyses ────────────────────────────────────────────────────────


class TrendingCompanyResponse(BaseModel):
    ticker: str
    name: str
    price: Optional[str] = None
    market_cap: Optional[str] = None


class TrendingAnalysisResponse(BaseModel):
    title: str
    description: str
    companies: List[TrendingCompanyResponse]
    interest_percent: int
    system_icon_name: str
    icon_background_color: str  # hex without leading '#'
