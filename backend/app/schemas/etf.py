"""
ETF detail schemas — response models for the ETFDetailView screen.

All field names use snake_case. The Swift frontend decodes via
explicit CodingKeys (snake_case raw values), so no aliases needed.
"""

from pydantic import BaseModel
from typing import Any, Dict, List, Optional


# ── Shared models (same shape as index schemas) ─────────────────


class KeyStatisticItem(BaseModel):
    label: str
    value: str
    is_highlighted: bool = False
    color_state: Optional[str] = None  # "warning" (red), "squeeze" (green), or None


class KeyStatisticsGroupResponse(BaseModel):
    statistics: List[KeyStatisticItem]


class PerformancePeriodResponse(BaseModel):
    label: str
    change_percent: float
    vs_market_percent: Optional[float] = None
    sp_return_percent: Optional[float] = None


class MarketStatusResponse(BaseModel):
    status: str  # "open", "closed", "pre_market", "after_hours"
    date: Optional[str] = None
    time: Optional[str] = None
    timezone: Optional[str] = None


class BenchmarkSummaryResponse(BaseModel):
    avg_annual_return: float
    sp_benchmark: float
    benchmark_name: Optional[str] = None
    since_date: Optional[str] = None
    benchmark_since_date: Optional[str] = None
    badge_threshold: Optional[float] = None
    alltime_annual_return: Optional[float] = None
    alltime_benchmark: Optional[float] = None
    alltime_since_date: Optional[str] = None


# ── ETF-specific snapshot models ─────────────────────────────────


class ETFIdentityRatingResponse(BaseModel):
    score: int  # 1-5
    max_score: int  # always 5
    volatility_label: str  # "Low Volatility", "Moderate Volatility", "High Volatility"


class ETFStrategyResponse(BaseModel):
    hook: str  # ≤120 chars plain English
    tags: List[str]  # e.g. ["Passive", "Large Cap Blend", "Index"]


class ETFDividendPaymentResponse(BaseModel):
    dividend_per_share: str  # e.g. "$1.7742"
    ex_dividend_date: str  # e.g. "Dec 20, 2025"
    pay_date: str  # e.g. "Jan 31, 2026"


class ETFDividendHistoryResponse(BaseModel):
    symbol: str
    pay_frequency: str  # Monthly | Quarterly | Semi-Annually | Annually
    total_dividends: int
    dividends: List[ETFDividendPaymentResponse]


class ETFNetYieldResponse(BaseModel):
    expense_ratio: float  # e.g. 0.0945
    fee_context: str  # "You pay $X per year on a $10,000 investment."
    dividend_yield: float  # e.g. 1.22
    pay_frequency: str  # Monthly | Quarterly | Semi-Annually | Annually
    yield_context: str  # "You earn ~$X per year on a $10,000 investment."
    verdict: str  # "This fund pays you Nx more in dividends than it charges in fees."
    last_dividend_payment: ETFDividendPaymentResponse
    dividend_history: List[ETFDividendPaymentResponse]


class ETFAssetAllocationResponse(BaseModel):
    equities: float
    bonds: float
    crypto: float
    # Commodity/gold funds belong in neither equities nor cash; kept optional with
    # a 0.0 default so pre-existing cached ETF payloads (which lack the key) still
    # validate and the iOS decoder (commodities is Optional) never breaks.
    commodities: float = 0.0
    cash: float
    total_assets: str  # e.g. "$562.3B"


class ETFSectorWeightResponse(BaseModel):
    name: str
    weight: float


class ETFTopHoldingResponse(BaseModel):
    symbol: str
    name: str
    weight: float


class ETFConcentrationResponse(BaseModel):
    top_n: int  # usually 10
    weight: float  # sum of top N holdings percentage
    insight: str  # risk interpretation


class ETFHoldingsRiskResponse(BaseModel):
    asset_allocation: ETFAssetAllocationResponse
    top_sectors: List[ETFSectorWeightResponse]
    top_holdings: List[ETFTopHoldingResponse]
    concentration: ETFConcentrationResponse


class ETFProfileResponse(BaseModel):
    description: str
    symbol: str
    etf_company: str
    asset_class: str
    inception_date: str
    domicile: str
    index_tracked: str
    website: str


class RelatedTickerResponse(BaseModel):
    symbol: str
    name: str
    price: float
    change_percent: float


class ETFNewsArticleResponse(BaseModel):
    headline: str
    source_name: str
    source_icon: Optional[str] = None
    sentiment: str  # "positive", "negative", "neutral"
    published_at: str
    thumbnail_url: Optional[str] = None
    related_tickers: List[str] = []
    summary_bullets: List[str] = []
    article_url: Optional[str] = None


# ── Top-level response ───────────────────────────────────────────


class ETFDetailResponse(BaseModel):
    symbol: str
    name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    chart_data: List[Dict[str, Any]]
    key_statistics: List[KeyStatisticItem]
    key_statistics_groups: List[KeyStatisticsGroupResponse]
    performance_periods: List[PerformancePeriodResponse]
    identity_rating: ETFIdentityRatingResponse
    strategy: ETFStrategyResponse
    net_yield: ETFNetYieldResponse
    holdings_risk: ETFHoldingsRiskResponse
    etf_profile: ETFProfileResponse
    related_etfs: List[RelatedTickerResponse]
    benchmark_summary: Optional[BenchmarkSummaryResponse] = None
    news_articles: List[ETFNewsArticleResponse] = []
