"""
Index detail schemas — response models for the IndexDetailView screen.

All field names use snake_case. The Swift frontend decodes via
explicit CodingKeys (snake_case raw values), so no aliases needed.
"""

from pydantic import BaseModel
from typing import Optional, List


class KeyStatisticItem(BaseModel):
    label: str
    value: str
    is_highlighted: bool = False


class KeyStatisticsGroupResponse(BaseModel):
    statistics: List[KeyStatisticItem]


class PerformancePeriodResponse(BaseModel):
    label: str
    change_percent: float
    vs_market_percent: Optional[float] = None


class BenchmarkSummaryResponse(BaseModel):
    avg_annual_return: float
    sp_benchmark: float


class MarketStatusResponse(BaseModel):
    status: str  # "open", "closed", "pre_market", "after_hours"
    date: Optional[str] = None  # "Feb 14, 2026"
    time: Optional[str] = None  # "4:00 PM"
    timezone: Optional[str] = None  # "EST"


# ── Snapshot models ──────────────────────────────────────────────


class ValuationSnapshotResponse(BaseModel):
    pe_ratio: float
    forward_pe: float
    earnings_yield: float
    historical_avg_pe: float
    historical_period: str
    story_template: str


class SectorPerformanceEntryResponse(BaseModel):
    sector: str
    change_percent: float


class SectorPerformanceSnapshotResponse(BaseModel):
    sectors: List[SectorPerformanceEntryResponse]
    story_template: str


class MacroForecastItemResponse(BaseModel):
    title: str
    description: str
    signal: str  # "positive", "neutral", "cautious"


class MacroForecastSnapshotResponse(BaseModel):
    indicators: List[MacroForecastItemResponse]
    story_template: str


class IndexSnapshotsDataResponse(BaseModel):
    valuation: ValuationSnapshotResponse
    sector_performance: SectorPerformanceSnapshotResponse
    macro_forecast: MacroForecastSnapshotResponse
    generated_date: str  # "2026-03-01"
    generated_by: str  # "Gemini 2.0 Flash"


# ── Profile ──────────────────────────────────────────────────────


class IndexProfileResponse(BaseModel):
    description: str
    exchange: str
    number_of_constituents: int
    weighting_methodology: str
    inception_date: str
    index_provider: str
    website: str


# ── News ─────────────────────────────────────────────────────────


class IndexNewsArticleResponse(BaseModel):
    headline: str
    source_name: str
    source_icon: Optional[str] = None
    sentiment: str  # "positive", "negative", "neutral"
    published_at: str  # ISO 8601
    thumbnail_url: Optional[str] = None
    related_tickers: List[str] = []
    summary_bullets: List[str] = []
    article_url: Optional[str] = None


# ── Top-level response ───────────────────────────────────────────


class IndexDetailResponse(BaseModel):
    symbol: str
    index_name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    chart_data: List[float]
    key_statistics_groups: List[KeyStatisticsGroupResponse]
    performance_periods: List[PerformancePeriodResponse]
    snapshots_data: IndexSnapshotsDataResponse
    index_profile: IndexProfileResponse
    benchmark_summary: Optional[BenchmarkSummaryResponse] = None
    news_articles: List[IndexNewsArticleResponse] = []
