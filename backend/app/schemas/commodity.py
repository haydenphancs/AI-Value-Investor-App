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
    avg_annual_return: float
    sp_benchmark: float
    benchmark_name: str = "S&P 500"
    since_date: Optional[str] = None


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
