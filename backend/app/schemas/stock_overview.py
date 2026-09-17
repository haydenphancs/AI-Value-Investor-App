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
    # Optional scoring exposure for the deterministic card-verdict generator
    # (card_verdict.generate_card_verdict). `metric_key` is the canonical key
    # ("gross_margin", "pe", "altman_z", …); `score` is the 1-5 sector-relative
    # score (None = informational / unscored). Backward-compatible: absent on the
    # Financials-tab decoder and on legacy cached snapshots.
    metric_key: Optional[str] = None
    score: Optional[int] = None


class DcfEstimateResponse(BaseModel):
    """FMP's discounted-cash-flow value for the Analysis tab's Valuation Meter.

    An INTRINSIC-VALUE estimate as of `as_of` — the present value of FMP's projected
    free cash flows at a generic cost of capital — never a price forecast. It is a
    mechanical single-stage model on trailing cash flow, so it reads far below price on
    fast growers (AAPL 135.83 vs 332.41, TER 43.73 vs 341.15 on 2026-09-17); iOS shows it
    as a labelled model value with a caveat past ±50%, and computes the gap against the
    LIVE header price (the snapshot is cached 24h, so no gap is sent on the wire).

    `status`: "ok" (value present), "negative_cash_flow" (FMP's model is ≤ 0 — a
    loss-maker like PLUG; no value is sent and iOS explains why). A missing model is
    simply an absent `dcf` on the snapshot.
    """
    status: str = "ok"
    value: Optional[float] = None
    as_of: Optional[str] = None


class SnapshotItemResponse(BaseModel):
    category: str          # "Profitability", "Growth", "Price", "Financial Health", "Insiders & Ownership"
    rating: int            # 0 = unavailable, 1-5 mapping to Swift SnapshotRatingLevel
    metrics: List[SnapshotMetricResponse]
    full_report_available: bool = True
    # Continuous pre-round composite (1.0–5.0) — the persona scorer maps this to a
    # 0-10 factor so the card's industry-relative score drives the final per-persona
    # score. Optional/back-compat: None on legacy cached snapshots; iOS ignores it.
    weighted_score: Optional[float] = None
    # Only the "Price" (valuation) snapshot fills this; Optional on the wire and in Swift
    # (shipped builds and the other four snapshot categories never carry it).
    dcf: Optional[DcfEstimateResponse] = None


class SectorIndustryResponse(BaseModel):
    sector: str
    industry: str
    sector_performance: float
    industry_rank: str
    # False when no sector row matched (the group had < 5 members, or the profile's
    # sector name does not normalise to the screener's). `sector_performance` is then
    # the 0.0 wire placeholder, NOT a flat day — shipped builds decode a plain Double,
    # so the float cannot become Optional; this is the `pe_known` pattern. iOS renders
    # "—" when False. Defaults True so older cached snapshots keep their meaning.
    sector_performance_known: bool = True


class CompanyProfileResponse(BaseModel):
    description: str
    ceo: str
    founded: str
    employees: int
    headquarters: str
    website: str
    sector: str = "N/A"
    industry: str = "N/A"
    sector_performance: float = 0.0
    # Twin of `SectorIndustryResponse.sector_performance_known` (same value, same flag).
    sector_performance_known: bool = True


class StockOverviewCoreResponse(BaseModel):
    """Fast subset for GET /stocks/{ticker}/overview/core — price + chart + name.

    Served for the instant first paint of the stock detail Overview tab: the
    client renders the price header + chart from this the moment it arrives, then
    the full /overview call (fired in parallel) supersedes it with every section.
    Field names/types mirror StockOverviewResponse exactly so iOS reuses its
    chart-point decode. See stock_overview_service.get_overview_core.
    """
    symbol: str
    company_name: str
    current_price: float
    price_change: float
    price_change_percent: float
    market_status: MarketStatusResponse
    chart_data: List[Dict[str, Any]]


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
