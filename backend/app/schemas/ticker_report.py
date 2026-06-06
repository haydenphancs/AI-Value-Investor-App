"""
Ticker Report Schemas — comprehensive Pydantic models for the full
TickerReportView screen.

Maps 1:1 to Swift TickerReportData + all nested structs.
Backend sends snake_case; Swift DTOs use explicit CodingKeys.
"""

from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List

from app.schemas.holders import SmartMoneyDataSchema


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
    # YoY % vs the prior year. None for the earliest year when no anchor
    # is available (FMP returned fewer than 4 estimates). iOS hides the
    # % label on that bar/dot when null.
    revenue_yoy_pct: Optional[float] = None
    eps: float
    eps_label: str
    eps_yoy_pct: Optional[float] = None
    is_forecast: bool


class RevenueForecastResponse(BaseModel):
    cagr: float
    eps_growth: float
    management_guidance: str  # "raised" | "maintained" | "lowered"
    projections: List[RevenueProjectionResponse]
    guidance_quote: Optional[str] = None
    # Attribution metadata for the quote — only populated when the AI
    # extracted a verbatim quote from the earnings-call transcript.
    # `guidance_speaker`: one of "CFO" | "CEO" | "IR" or null.
    # `guidance_period`: the period the quote covers, e.g. "Q4 2025" |
    # "FY 2026" | null when the speaker didn't tag a period.
    guidance_speaker: Optional[str] = None
    guidance_period: Optional[str] = None
    # Stage-B narrative: the "why" behind the forward trajectory (what drives
    # the projected revenue/EPS growth + what the guidance stance signals).
    # Written by `_revenue_forecast_insight_prompt`; None on the fallback path.
    insight: Optional[str] = None


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
    # 13G total beneficial %, only populated when the manager is tagged
    # "10 percent owner" in their Form 4 typeOfOwner AND a matching
    # IN-type 13G filing is available. iOS renders this as a green
    # "43% owner" chip next to the name.
    percent_ownership: Optional[float] = None


class KeyManagementResponse(BaseModel):
    # Split into two sub-sections so the UI can render them under
    # separate sub-headers. `top_holders` are 10%+ owners (paired with
    # an IN-type 13G filing) — capital control. `officers` are sorted
    # by canonical role rank (CEO → CFO → COO → …) — operational
    # control. A person who qualifies as a top holder is dedup-removed
    # from officers (CIK-keyed) so they don't appear twice.
    top_holders: List[KeyManagerResponse] = []
    officers: List[KeyManagerResponse] = []
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
    change_pct: float
    direction: str
    window_label: str
    tag: str
    # Volatility-aware additions (optional so older cached reports decode).
    # `tier` is the user-facing label (Typical/Notable/Unusual/Extreme).
    # `z_score`, `sigma_daily_pct`, `expected_band_pct` back the sub-label
    # "Normal range: ±X% (Y% daily σ)" on iOS.
    tier: Optional[str] = None
    z_score: Optional[float] = None
    sigma_daily_pct: Optional[float] = None
    expected_band_pct: Optional[float] = None


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
    # None when neither the sector_aggregates batch nor the in-hand peer
    # profiles can produce a CAGR — iOS renders "—" in that case rather
    # than misleading zeros.
    cagr_5yr: Optional[float] = None
    current_tam: float
    future_tam: float
    current_year: str
    future_year: str
    lifecycle_phase: str  # "emerging" | "secular_growth" | "mature" | "declining"
    # Verbatim sentence from the earnings transcript / company description
    # that the AI used to derive `current_tam`/`future_tam`. Empty string
    # when no source quote was found (TAM stays 0 in that case).
    tam_source_quote: Optional[str] = None
    # Short human caption shown under the TAM row identifying which
    # source produced the figure: "Earnings call quote" when AI extracted
    # it from the transcript, "BEA <Sector> value-added (via FRED)" when
    # the FRED industry-proxy was used, None when TAM stayed at 0.
    tam_source_label: Optional[str] = None
    # Grain of the underlying data source: 'industry' (Census 4-digit NAICS
    # or industry-specific FRED), 'sector' (sector-level FRED used as
    # fallback), 'all_industry' (USNGSP fallback). iOS renders a small
    # "⚠ Broader than industry" chip when this is not 'industry' so users
    # know the figure is a proxy. None when TAM came from an AI quote or
    # wasn't populated.
    source_grain: Optional[str] = None


class MoatDimensionResponse(BaseModel):
    name: str
    score: float
    peer_score: float
    # Phase 3A — deterministic moat scoring metadata. When this pillar
    # was scored from real FMP financials + sector benchmarks, `drivers`
    # holds the per-metric breakdown (focal value, sector median, sub-
    # score) and `confidence` is "high" (≥3 metrics) or "medium" (2).
    # When the legacy Gemini Stage A dimension was used as fallback,
    # both fields are None — iOS treats absence as "no driver detail".
    drivers: Optional[List[Dict[str, Any]]] = None
    confidence: Optional[str] = None
    # Which tier produced this pillar: "deterministic" (Phase 3A — FMP
    # metrics + sector medians), "grounded" (Phase 3D — Gemini grounded
    # research with web citations), or "ai_legacy" (Stage A AI fallback
    # when both prior tiers came back empty). Kept separate from
    # `confidence` so iOS can show provenance independently of strength.
    source: Optional[str] = None


class CompetitorResponse(BaseModel):
    name: str
    ticker: str
    # 0–10 — peer's competitive threat to the focal company, computed
    # from ROIC delta vs focal × moat-as-durability multiplier
    # (`_relative_peer_score` in ticker_report_data_collector). 5.0 is
    # the "equal threat" anchor; >6.5 = high, <3.5 = low.
    competitive_score: float
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
    # Analyst price targets are null when FMP has no real consensus coverage.
    # We do NOT fabricate them (previously current × 0.85 / 1.0 / 1.3); iOS
    # renders an honest "no analyst targets" state instead of fake numbers.
    target_price: Optional[float] = None
    low_target: Optional[float] = None
    high_target: Optional[float] = None
    valuation_status: str
    discount_percent: float
    # AI "Insight" — a big-picture synthesis across the whole Wall Street Consensus
    # card: analyst price targets + institutions (13F) + momentum. Written by the
    # Stage-B narrative pass. Optional/defaulted so legacy persisted reports (which
    # stored it under the old `hedge_fund_note` key) re-validate as None until
    # regenerated.
    wall_street_insight: Optional[str] = None
    # NAMING: these `hedge_fund_*` fields = FMP 13F institutional-ownership data;
    # iOS renders them in the report's "Institutions" section
    # (SmartMoneyTab.hedgeFunds = "Institutions"), not a "Hedge Funds" label.
    hedge_fund_price_data: List[StockPricePointResponse]
    hedge_fund_flow_data: List[SmartMoneyFlowPointResponse]
    # Quarterly institutional 13F flow, mirrored verbatim from the Holders
    # tab's `hedge_funds_data` so the report's Institutions chart + net-flow
    # badge match TickerDetail exactly. Optional: legacy persisted reports
    # predate this field (iOS falls back to the monthly chart above).
    hedge_fund_smart_money: Optional[SmartMoneyDataSchema] = None
    momentum_upgrades: int
    momentum_downgrades: int
    # Analyst "maintain"/reiterate count over the SAME trailing-12-month window
    # as upgrades/downgrades (see analyst_service._compute_actions_summary).
    # Defaulted so legacy persisted reports (pre-`momentum_maintains`) re-validate.
    momentum_maintains: int = 0
    # Analyst rating distribution (one most-recent grade per firm), from the SAME
    # `get_grades` source as momentum. iOS aggregates these to Buy/Hold/Sell for the
    # consensus bar. Defaulted so legacy persisted reports re-validate as 0.
    analyst_strong_buy: int = 0
    analyst_buy: int = 0
    analyst_hold: int = 0
    analyst_sell: int = 0
    analyst_strong_sell: int = 0


# ── Critical Factors ──────────────────────────────────────────────────────────


class CriticalFactorResponse(BaseModel):
    title: str
    description: str  # short SIGNAL — what's notable + why it matters
    severity: str  # "high" | "medium" | "low" — priority to watch
    # Forward-looking WATCH action: what to monitor next (Stage B). None on
    # the fallback path / older cached reports — iOS hides the Watch line.
    watch: Optional[str] = None


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
