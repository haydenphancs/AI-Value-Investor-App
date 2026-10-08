"""
Stock Overview schemas — response models for GET /stocks/{ticker}/overview.

Reuses shared models from etf.py (KeyStatisticItem, etc.) and adds
stock-specific snapshot / sector / profile models.
"""

from datetime import datetime, timezone

from pydantic import BaseModel

from app.schemas.dcf_fair_value import DcfFairValueResponse
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
    # "industry" | "sector": the peer group whose median `name` prints (2026-10-07). The
    # name keeps the literal words "sector avg" / "vs sector" whatever the level, because
    # shipped iOS builds strip the suffix with \s*\([^)]*sector[^)]*\); a build that knows
    # this field swaps in the industry wording when it says "industry". None whenever the
    # name prints no peer median. Optional and additive: older builds ignore it.
    peer_level: Optional[str] = None


# Wire format of `SnapshotItemResponse.computed_at`: ISO-8601 UTC to the second with a
# literal "Z" (what iOS's ISO8601DateFormatter parses by default).
SNAPSHOT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def snapshot_build_time(now: Optional[datetime] = None) -> str:
    """`computed_at` for a snapshot built at ``now`` (default: this instant). A naive
    ``now`` is taken as UTC."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(SNAPSHOT_TIME_FORMAT)


def with_cached_build_time(json_data: Dict[str, Any], cached_at: datetime) -> Dict[str, Any]:
    """Give a `snapshot_cache` row written before `computed_at` existed a build time.

    Such a row has no ``computed_at`` KEY at all; its build time is the row's own
    ``cached_at`` (each service upserts right after the build). A row that carries the key
    keeps its stored value — a cached card shows when it was BUILT, never when it was
    read. Mutates and returns ``json_data`` (callers pass their own copy of the row)."""
    if "computed_at" not in json_data:
        json_data["computed_at"] = snapshot_build_time(cached_at)
    return json_data


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
    # The Caydex Fair Value Estimate (model dcf-v1, documents/research/dcf-methodology-v1.md).
    # Filled only when settings.DCF_ENABLED, and then `dcf` (FMP's model) is None: shipped
    # builds label `dcf` "FMP discounted-cash-flow model", so our value must never travel in
    # that slot. Optional end to end: builds predating it ignore the key.
    caydex_estimate: Optional[DcfFairValueResponse] = None
    # When this card was BUILT (`snapshot_build_time`, ISO-8601 UTC "…Z"), 2026-10-07. A
    # cached card keeps its build time on every tier — the Snapshots header used to print
    # today's date over a card up to 24 h old. None only on a card built by a path that
    # does not stamp it; iOS then shows no date. Optional and additive.
    computed_at: Optional[str] = None


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
    # False when neither the quote nor the profile carried a day change — FMP
    # `/stable/profile` legitimately answers `change: null` for a halted/OTC listing,
    # and the quote leg can fail while the profile lands. `price_change` /
    # `price_change_percent` are then the 0.0 wire placeholder, NOT a flat day: shipped
    # builds decode a plain Double, so the floats cannot become Optional (the `pe_known`
    # / `sector_performance_known` pattern). The equity screen was the FIFTH asset class
    # and the only one without this flag — it painted "▲ +0.00 (+0.00%)" in green with
    # a bullish flash. iOS renders "—" when False. Defaults True so older cached
    # payloads keep their meaning.
    change_known: bool = True
    market_status: MarketStatusResponse
    chart_data: List[Dict[str, Any]]


class StockOverviewResponse(BaseModel):
    """Top-level response for GET /stocks/{ticker}/overview."""
    symbol: str
    company_name: str
    current_price: float
    price_change: float
    price_change_percent: float
    # Twin of `StockOverviewCoreResponse.change_known` — same rule, same source list.
    change_known: bool = True
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
