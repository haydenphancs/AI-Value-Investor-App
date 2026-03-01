"""
Crypto detail schemas — response models for the CryptoDetailView screen.

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
    benchmark_label: str = "BTC"


class CryptoSnapshotResponse(BaseModel):
    category: str  # "Origin and Technology", "Tokenomics", "Next Big Moves", "Risks"
    paragraphs: List[str]


class CryptoProfileResponse(BaseModel):
    description: str
    symbol: str
    launch_date: str
    consensus_mechanism: str
    blockchain: str
    website: str
    whitepaper: Optional[str] = None


class RelatedCryptoResponse(BaseModel):
    symbol: str
    name: str
    price: float
    change_percent: float


class BenchmarkSummaryResponse(BaseModel):
    avg_annual_return: float
    sp_benchmark: float
    benchmark_name: str = "Bitcoin (BTC)"
    since_date: Optional[str] = None
    benchmark_since_date: Optional[str] = None
    badge_threshold: float = 5.0


class CryptoNewsArticleResponse(BaseModel):
    headline: str
    source_name: str
    source_icon: Optional[str] = None
    sentiment: str = "neutral"
    published_at: str
    thumbnail_url: Optional[str] = None
    related_tickers: List[str] = []
    summary_bullets: List[str] = []
    article_url: Optional[str] = None


class CryptoDetailResponse(BaseModel):
    symbol: str
    name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: str  # "24/7 Trading" or "Maintenance"
    chart_data: List[float]
    key_statistics_groups: List[KeyStatisticsGroupResponse]
    performance_periods: List[PerformancePeriodResponse]
    snapshots: List[CryptoSnapshotResponse]
    crypto_profile: CryptoProfileResponse
    related_cryptos: List[RelatedCryptoResponse]
    benchmark_summary: Optional[BenchmarkSummaryResponse] = None
    news_articles: List[CryptoNewsArticleResponse] = []
