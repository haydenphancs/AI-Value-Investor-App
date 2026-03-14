"""
Stock Overview schemas — response models for GET /stocks/{ticker}/overview.

Reuses shared models from etf.py (KeyStatisticItem, etc.) and adds
stock-specific snapshot / sector / profile models.
"""

from pydantic import BaseModel
from typing import Any, Dict, Optional, List

from app.schemas.etf import (
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    PerformancePeriodResponse,
    MarketStatusResponse,
    BenchmarkSummaryResponse,
    RelatedTickerResponse,
)


class SnapshotMetricResponse(BaseModel):
    name: str
    value: str


class SnapshotItemResponse(BaseModel):
    category: str          # "Profitability", "Growth", "Price", "Financial Health", "Insiders & Ownership"
    rating: int            # 1-5 mapping to Swift SnapshotRatingLevel
    metrics: List[SnapshotMetricResponse]
    full_report_available: bool = True


class SectorIndustryResponse(BaseModel):
    sector: str
    industry: str
    sector_performance: float
    industry_rank: str


class CompanyProfileResponse(BaseModel):
    description: str
    ceo: str
    founded: str
    employees: int
    headquarters: str
    website: str


class StockOverviewResponse(BaseModel):
    """Top-level response for GET /stocks/{ticker}/overview."""
    symbol: str
    company_name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    chart_data: List[Dict[str, Any]]
    key_statistics: List[KeyStatisticItem]
    key_statistics_groups: List[KeyStatisticsGroupResponse]
    performance_periods: List[PerformancePeriodResponse]
    snapshots: List[SnapshotItemResponse]
    sector_industry: SectorIndustryResponse
    company_profile: CompanyProfileResponse
    related_tickers: List[RelatedTickerResponse]
    benchmark_summary: Optional[BenchmarkSummaryResponse] = None
