"""
Commodity detail schemas — response models for the CommodityDetailView screen.

All field names use snake_case. The Swift frontend decodes via
explicit CodingKeys (snake_case raw values).
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
    sp_return_percent: Optional[float] = None
    benchmark_label: str = "S&P 500"


class CommodityChartPointResponse(BaseModel):
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: float
    volume: Optional[float] = None


class CommodityNewsArticleResponse(BaseModel):
    headline: str
    source_name: str
    source_icon: Optional[str] = None
    sentiment: str = "neutral"
    published_at: str
    thumbnail_url: Optional[str] = None
    related_tickers: List[str] = []
    summary_bullets: List[str] = []
    article_url: Optional[str] = None


class CommodityProfileResponse(BaseModel):
    description: str = ""
    category: str = ""
    exchange: str = ""
    trading_hours: str = ""
    contract_size: str = ""
    unit: str = ""
    currency: str = "USD"
    tick_size: str = ""
    major_producers: str = ""
    major_consumers: str = ""


class RelatedCommodityResponse(BaseModel):
    symbol: str
    name: str
    price: float = 0
    change_percent: float = 0


class BenchmarkSummaryResponse(BaseModel):
    """Commodity's copy of the benchmark shape. Same contract as `schemas.etf`’s —
    read the invariant documented there before changing either.

    `badge_threshold` is absent on purpose, so iOS falls back to 0 and shows the verdict
    badge on any non-zero gap.
    """

    avg_annual_return: float
    sp_benchmark: float
    benchmark_name: str = "S&P 500"
    since_date: Optional[str] = None
    window_label: Optional[str] = None
    benchmark_available: bool = True
    alltime_annual_return: Optional[float] = None
    alltime_benchmark: Optional[float] = None
    alltime_since_date: Optional[str] = None


class CommodityQuoteResponse(BaseModel):
    """Light refresh slice for the iOS 30s loop and the range picker.

    Every field name and type is identical to the same-named field on
    `CommodityDetailResponse`, so the client decodes them with DTOs it already has.

    Deliberately EXCLUDES the close-cadence sections a 30-second refresh cannot change —
    `performance_periods`, `benchmark_summary`, `commodity_profile` — and `news_articles`,
    which comes from `GET /commodities/{symbol}/news`. The 30s loop and every range tap
    used to re-request the whole monolith to move a price and a chart.
    """

    symbol: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: str
    # Empty unless `range` was supplied — the loop only needs bars on an intraday chart.
    chart_data: List[CommodityChartPointResponse] = []
    key_statistics_groups: List[KeyStatisticsGroupResponse] = []
    related_commodities: List[RelatedCommodityResponse] = []


class CommodityDetailResponse(BaseModel):
    symbol: str
    name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: str
    chart_data: List[CommodityChartPointResponse]
    key_statistics_groups: List[KeyStatisticsGroupResponse]
    performance_periods: List[PerformancePeriodResponse]
    news_articles: List[CommodityNewsArticleResponse] = []
    commodity_profile: Optional[CommodityProfileResponse] = None
    related_commodities: List[RelatedCommodityResponse] = []
    benchmark_summary: Optional[BenchmarkSummaryResponse] = None
