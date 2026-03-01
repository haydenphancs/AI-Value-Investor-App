"""
Ticker Report Schemas — comprehensive Pydantic models for the full
TickerReportView screen.

Maps 1:1 to Swift TickerReportData + all nested structs.
Backend sends snake_case; Swift DTOs use explicit CodingKeys.
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ── Atomic Sub-Models ──────────────────────────────────────────────────────────


class ExecutiveSummaryBulletResponse(BaseModel):
    category: str
    text: str
    sentiment: str  # "positive" | "neutral" | "negative"


class MoatTagResponse(BaseModel):
    label: str
    strength: str  # "wide" | "narrow" | "none"


class VitalScoreResponse(BaseModel):
    value: float
    status: str  # "critical" | "warning" | "good" | "neutral"


# ── Key Vitals ─────────────────────────────────────────────────────────────────


class ValuationVitalResponse(BaseModel):
    status: str  # "overpriced" | "fair_value" | "underpriced" | "deep_undervalued"
    current_price: float
    fair_value: float
    upside_potential: float


class MoatVitalResponse(BaseModel):
    overall_rating: str  # "wide" | "narrow" | "none"
    primary_source: str
    tags: List[MoatTagResponse]
    value_label: str
    stability_label: str


class FinancialHealthVitalResponse(BaseModel):
    level: str  # "strong" | "moderate" | "weak" | "critical"
    altman_z_score: float
    altman_z_label: str
    additional_metric: str
    additional_metric_status: str
    fcf_note: str


class RevenueVitalResponse(BaseModel):
    score: VitalScoreResponse
    total_revenue: str
    revenue_growth: float
    top_segment: str
    top_segment_growth: float


class InsiderVitalResponse(BaseModel):
    score: VitalScoreResponse
    sentiment: str  # "positive" | "negative" | "neutral"
    net_activity: str
    buy_count: int
    sell_count: int
    key_insight: str


class MacroVitalResponse(BaseModel):
    score: VitalScoreResponse
    threat_level: str  # "low" | "elevated" | "high" | "severe" | "critical"
    top_risk: str
    risk_trend: str  # "improving" | "stable" | "worsening"
    active_risk_count: int


class ForecastVitalResponse(BaseModel):
    score: VitalScoreResponse
    revenue_cagr: float
    eps_cagr: float
    guidance: str  # "raised" | "maintained" | "lowered"
    outlook: str


class WallStreetVitalResponse(BaseModel):
    score: VitalScoreResponse
    consensus_rating: str  # "strong_buy" | "buy" | "hold" | "sell" | "strong_sell"
    price_target: float
    current_price: float
    upgrades: int
    downgrades: int


class KeyVitalsResponse(BaseModel):
    valuation: Optional[ValuationVitalResponse] = None
    moat: Optional[MoatVitalResponse] = None
    financial_health: Optional[FinancialHealthVitalResponse] = None
    revenue: Optional[RevenueVitalResponse] = None
    insider: Optional[InsiderVitalResponse] = None
    macro: Optional[MacroVitalResponse] = None
    forecast: Optional[ForecastVitalResponse] = None
    wall_street: Optional[WallStreetVitalResponse] = None


# ── Core Thesis ────────────────────────────────────────────────────────────────


class CoreThesisResponse(BaseModel):
    bull_case: List[str]
    bear_case: List[str]


# ── Deep Dive: Fundamentals ───────────────────────────────────────────────────


class DeepDiveMetricResponse(BaseModel):
    label: str
    value: str
    trend: Optional[str] = None  # "up" | "down" | "flat" | null


class DeepDiveMetricCardResponse(BaseModel):
    title: str
    star_rating: int
    metrics: List[DeepDiveMetricResponse]
    quality_label: str


class OverallAssessmentResponse(BaseModel):
    text: str
    average_rating: float
    strong_count: int
    weak_count: int


# ── Deep Dive: Revenue Forecast ───────────────────────────────────────────────


class RevenueProjectionResponse(BaseModel):
    period: str
    revenue: float
    revenue_label: str
    eps: float
    eps_label: str
    is_forecast: bool


class RevenueForecastResponse(BaseModel):
    cagr: float
    eps_growth: float
    management_guidance: str  # "raised" | "maintained" | "lowered"
    projections: List[RevenueProjectionResponse]
    guidance_quote: Optional[str] = None


# ── Deep Dive: Insider & Management ───────────────────────────────────────────


class InsiderTransactionResponse(BaseModel):
    type: str  # "Buys" | "Sells"
    count: int
    shares: str
    value: str


class InsiderDataResponse(BaseModel):
    sentiment: str
    timeframe: str
    transactions: List[InsiderTransactionResponse]
    ownership_note: Optional[str] = None


class KeyManagerResponse(BaseModel):
    name: str
    title: str
    ownership: str
    ownership_value: str


class KeyManagementResponse(BaseModel):
    managers: List[KeyManagerResponse]
    ownership_insight: str


# ── Deep Dive: Price Action ───────────────────────────────────────────────────


class PriceEventResponse(BaseModel):
    tag: str
    date: str
    index: int


class PriceActionResponse(BaseModel):
    prices: List[float]
    current_price: float
    event: Optional[PriceEventResponse] = None
    narrative: str


# ── Deep Dive: Revenue Engine ─────────────────────────────────────────────────


class RevenueSegmentResponse(BaseModel):
    name: str
    current_revenue: float
    previous_revenue: float
    total_revenue: float


class RevenueEngineResponse(BaseModel):
    segments: List[RevenueSegmentResponse]
    total_revenue: float
    revenue_unit: str
    period: str
    analysis_note: Optional[str] = None


# ── Deep Dive: Moat & Competition ─────────────────────────────────────────────


class MarketDynamicsResponse(BaseModel):
    industry: str
    concentration: str  # "monopoly" | "duopoly" | "oligopoly" | "fragmented"
    cagr_5yr: float
    current_tam: float
    future_tam: float
    current_year: str
    future_year: str
    lifecycle_phase: str  # "emerging" | "secular_growth" | "mature" | "declining"


class MoatDimensionResponse(BaseModel):
    name: str
    score: float
    peer_score: float


class CompetitorResponse(BaseModel):
    name: str
    ticker: str
    moat_score: float
    market_share_percent: float
    threat_level: str  # "low" | "moderate" | "high"


class MoatCompetitionResponse(BaseModel):
    market_dynamics: MarketDynamicsResponse
    dimensions: List[MoatDimensionResponse]
    durability_note: str
    competitors: List[CompetitorResponse]
    competitive_insight: str


# ── Deep Dive: Macro & Geopolitical ───────────────────────────────────────────


class MacroRiskFactorResponse(BaseModel):
    category: str  # "inflation" | "interest_rates" | "geopolitical" | etc
    title: str
    impact: float
    description: str
    trend: str  # "improving" | "stable" | "worsening"
    severity: str  # "low" | "elevated" | "high" | "severe" | "critical"


class MacroDataResponse(BaseModel):
    overall_threat_level: str
    headline: str
    risk_factors: List[MacroRiskFactorResponse]
    intelligence_brief: str
    last_updated: str


# ── Deep Dive: Wall Street Consensus ──────────────────────────────────────────


class StockPricePointResponse(BaseModel):
    month: str
    price: float


class SmartMoneyFlowPointResponse(BaseModel):
    month: str
    buy_volume: float
    sell_volume: float


class WallStreetConsensusResponse(BaseModel):
    rating: str  # "strong_buy" | "buy" | "hold" | "sell" | "strong_sell"
    current_price: float
    target_price: float
    low_target: float
    high_target: float
    valuation_status: str
    discount_percent: float
    hedge_fund_note: Optional[str] = None
    hedge_fund_price_data: List[StockPricePointResponse]
    hedge_fund_flow_data: List[SmartMoneyFlowPointResponse]
    momentum_upgrades: int
    momentum_downgrades: int


# ── Critical Factors ──────────────────────────────────────────────────────────


class CriticalFactorResponse(BaseModel):
    title: str
    description: str
    severity: str  # "high" | "medium" | "low"


# ── Full Ticker Report ────────────────────────────────────────────────────────


class TickerReportResponse(BaseModel):
    """Complete ticker report matching Swift TickerReportData."""
    symbol: str
    company_name: str
    exchange: str
    logo_url: Optional[str] = None
    live_date: str

    # Agent & Rating
    agent: str  # "buffett" | "wood" | "lynch" | "dalio"
    quality_score: float

    # Executive Summary
    executive_summary_text: str
    executive_summary_bullets: List[ExecutiveSummaryBulletResponse]

    # Key Vitals (all optional — only noteworthy ones populated)
    key_vitals: KeyVitalsResponse

    # Core Thesis
    core_thesis: CoreThesisResponse

    # Deep Dive: Fundamentals
    fundamental_metrics: List[DeepDiveMetricCardResponse]
    overall_assessment: OverallAssessmentResponse

    # Deep Dive: Revenue Forecast
    revenue_forecast: RevenueForecastResponse

    # Deep Dive: Insider & Management
    insider_data: InsiderDataResponse
    key_management: KeyManagementResponse

    # Deep Dive: Price Action
    price_action: PriceActionResponse

    # Deep Dive: Revenue Engine
    revenue_engine: RevenueEngineResponse

    # Deep Dive: Moat & Competition
    moat_competition: MoatCompetitionResponse

    # Deep Dive: Macro & Geopolitical
    macro_data: MacroDataResponse

    # Deep Dive: Wall Street Consensus
    wall_street_consensus: WallStreetConsensusResponse

    # Critical Factors
    critical_factors: List[CriticalFactorResponse]

    # Disclaimer
    disclaimer_text: str
