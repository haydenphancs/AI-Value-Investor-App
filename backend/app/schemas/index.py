"""
Index detail schemas — response models for the IndexDetailView screen.

All field names use snake_case. The Swift frontend decodes via
explicit CodingKeys (snake_case raw values), so no aliases needed.
"""

from pydantic import BaseModel
from typing import Optional, List


class ChartDataPointResponse(BaseModel):
    date: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: float
    volume: Optional[float] = None


class KeyStatisticItem(BaseModel):
    label: str
    value: str
    is_highlighted: bool = False
    color_state: Optional[str] = None  # "bullish", "bearish", or None


class KeyStatisticsGroupResponse(BaseModel):
    statistics: List[KeyStatisticItem]


class PerformancePeriodResponse(BaseModel):
    label: str
    change_percent: float
    vs_market_percent: Optional[float] = None
    sp_return_percent: Optional[float] = None


class BenchmarkSummaryResponse(BaseModel):
    avg_annual_return: float
    sp_benchmark: float
    alltime_annual_return: Optional[float] = None
    alltime_benchmark: Optional[float] = None
    alltime_since_date: Optional[str] = None


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
    # Attribution string. MUST stay provider-neutral ("Cay AI") — naming the
    # underlying model here would breach the identity rule in
    # services/agents/persona_config.py, and this field ships in the wire payload.
    generated_by: str  # "Cay AI"


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


class IndexQuoteResponse(BaseModel):
    """Light refresh slice for the iOS 30-second loop and the range picker.

    Every field name and type is identical to the same-named field on
    `IndexDetailResponse`, so the client decodes them with DTOs it already has.

    Deliberately EXCLUDES the close-cadence sections a 30-second refresh cannot change —
    `performance_periods`, `snapshots_data`, `index_profile`, `benchmark_summary` — and
    `news_articles`, which the client reads from `GET /indices/{symbol}/news` and never
    took from this payload. `snapshots_data` alone is a deep required object graph
    (valuation + sector performance + macro forecast, each with its own story template);
    re-sending it every 30 seconds to move one number was most of the payload.
    """

    symbol: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    # Empty unless `range` was supplied — the loop only needs bars on an intraday chart.
    chart_data: List[ChartDataPointResponse] = []
    key_statistics_groups: List[KeyStatisticsGroupResponse] = []


class IndexCoreResponse(BaseModel):
    """FIRST-PAINT slice: the header line and, when it is free, the chart.

    Not a light version of the refresh slice — a different job. `IndexQuoteResponse` is a
    PROJECTION of the assembled build, so on a cold cache it costs exactly what the full
    detail costs and cannot serve first paint. This is assembled from the two CHEAP
    per-section builders only (`_get_quote` + `_get_chart`), so it answers in ~0.3s while
    the full response is still gathering.

    Every field name and type is identical to the same-named field on
    `IndexDetailResponse`, so the client decodes them with DTOs it already has and the
    core -> full swap is seamless. `previous_close` is deliberately absent: iOS derives it
    as `currentPrice - priceChange` (IndexDetailModels.swift), so shipping it would be a
    second source for one number.
    """

    symbol: str
    index_name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    # Empty when the bars would have cost a multi-thousand-row history pull — see
    # `IndexService._get_chart(fast_only=True)`. The full response fills them in.
    chart_data: List[ChartDataPointResponse] = []


class IndexDetailResponse(BaseModel):
    symbol: str
    index_name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    chart_data: List[ChartDataPointResponse]
    key_statistics_groups: List[KeyStatisticsGroupResponse]
    performance_periods: List[PerformancePeriodResponse]
    snapshots_data: IndexSnapshotsDataResponse
    index_profile: IndexProfileResponse
    benchmark_summary: Optional[BenchmarkSummaryResponse] = None
    news_articles: List[IndexNewsArticleResponse] = []
