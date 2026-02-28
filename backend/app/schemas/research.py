"""Research schemas matching DB research_reports + frontend polling models."""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class GenerateResearchRequest(BaseModel):
    stock_id: str  # ticker symbol
    persona: str   # e.g. "warren_buffett"


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
    estimated_time_remaining: Optional[int] = None


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
    barriers_to_entry: Optional[str] = None


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


class RateReportRequest(BaseModel):
    rating: int  # 1-5
    feedback: Optional[str] = None


class ResearchReportDetail(BaseModel):
    id: str
    user_id: str
    ticker: str
    company_name: Optional[str] = None
    investor_persona: str
    status: str
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
    generation_time_seconds: Optional[int] = None
    tokens_used: Optional[int] = None
    created_at: str
    completed_at: Optional[str] = None
    user_rating: Optional[int] = None
    user_feedback: Optional[str] = None


class ResearchReportListItem(BaseModel):
    id: str
    ticker: str
    company_name: Optional[str] = None
    investor_persona: str
    status: str
    title: Optional[str] = None
    executive_summary: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    user_rating: Optional[int] = None


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
