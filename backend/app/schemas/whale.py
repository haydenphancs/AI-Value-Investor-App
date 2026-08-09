"""
Whale Schemas — Pydantic models for whale tracking endpoints.

Maps exactly to the iOS Swift Codable shapes in:
  - TrackingModels.swift (TrendingWhale, WhaleTradeGroupActivity)
  - WhaleProfileModels.swift (WhaleProfile, WhaleHolding, WhaleTradeGroup, WhaleTrade)

Endpoints:
  GET    /api/v1/whales                                → List[TrendingWhaleResponse]
  GET    /api/v1/whales/activity                       → List[WhaleTradeGroupActivityResponse]
  GET    /api/v1/whales/{whale_id}/profile             → WhaleProfileResponse
  GET    /api/v1/whales/{whale_id}/trade-groups        → List[WhaleTradeGroupResponse]
  GET    /api/v1/whales/{whale_id}/trade-groups/{id}   → WhaleTradeGroupResponse
  POST   /api/v1/whales/{whale_id}/follow              → FollowResponse
  DELETE /api/v1/whales/{whale_id}/follow              → FollowResponse
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ── Whale listing ────────────────────────────────────────────────────


class TrendingWhaleResponse(BaseModel):
    """List item for whale listings. Maps to Swift TrendingWhale."""

    id: str
    name: str
    category: str  # "investors", "institutions", "politicians", "crypto"
    avatar_url: Optional[str] = None
    followers_count: int = 0
    is_following: bool = False
    title: str = ""
    description: str = ""
    recent_trade_count: int = 0
    # Firm a person-fronted whale runs ("Bridgewater Associates" for Ray Dalio).
    # None for firm-branded institutions and politicians. Always displayed with
    # the name (one merged profile per 13F filer since migration 080).
    firm_name: Optional[str] = None


# ── Whale profile sub-models ────────────────────────────────────────


class WhaleSectorAllocationResponse(BaseModel):
    """Sector allocation item. Maps to Swift WhaleSectorAllocation."""

    id: str
    name: str
    percentage: float
    color_hex: str = "6B7280"


class WhaleHoldingResponse(BaseModel):
    """Individual holding. Maps to Swift WhaleHolding."""

    id: str
    ticker: str
    company_name: str
    logo_url: Optional[str] = None
    allocation: float
    change_percent: float = 0.0
    asset_type: str = "stock"


class WhaleTradeResponse(BaseModel):
    """Individual trade. Maps to Swift WhaleTrade."""

    id: str
    ticker: str
    company_name: str
    action: str  # "BOUGHT" or "SOLD"
    trade_type: str  # "New", "Increased", "Decreased", "Closed"
    amount: float
    amount_range: Optional[str] = None  # STOCK Act bucket, e.g. "$1,001 - $15,000"
    previous_allocation: float = 0.0
    new_allocation: float = 0.0
    date: str  # transaction date
    disclosure_date: Optional[str] = None  # congress: when it became public
    asset_type: str = "stock"


class WhaleTradeGroupResponse(BaseModel):
    """Batch of trades. Maps to Swift WhaleTradeGroup."""

    id: str
    date: str  # sort/dedup key — disclosure date for congress, filing date for 13F
    trade_count: int
    net_action: str  # "BOUGHT" or "SOLD"
    net_amount: float  # midpoint sum — internal/13F precise; NOT shown for congress
    net_amount_range: Optional[str] = None  # congress: summed STOCK Act range label
    disclosure_date: Optional[str] = None  # congress only
    transaction_date: Optional[str] = None  # congress only (when trades happened)
    summary: Optional[str] = None
    insights: List[str] = Field(default_factory=list)
    trades: List[WhaleTradeResponse] = Field(default_factory=list)


class WhaleBehaviorSummaryResponse(BaseModel):
    """Maps to Swift WhaleBehaviorSummary."""

    action: str
    primary_focus: str
    secondary_action: str
    secondary_focus: str


class WhaleProfileResponse(BaseModel):
    """Full whale profile. Maps to Swift WhaleProfile."""

    id: str
    name: str
    title: str
    description: str
    # Optional with default None: whale_profile_cache replays cached JSON built
    # before this field existed — must tolerate its absence.
    firm_name: Optional[str] = None
    avatar_url: Optional[str] = None
    risk_profile: str = ""
    portfolio_value: float = 0.0
    ytd_return: float = 0.0
    sector_exposure: List[WhaleSectorAllocationResponse] = Field(
        default_factory=list
    )
    current_holdings: List[WhaleHoldingResponse] = Field(default_factory=list)
    recent_trade_groups: List[WhaleTradeGroupResponse] = Field(
        default_factory=list
    )
    recent_trades: List[WhaleTradeResponse] = Field(default_factory=list)
    behavior_summary: WhaleBehaviorSummaryResponse
    sentiment_summary: str = ""
    is_following: bool = False
    data_source: str = ""
    return_source: str = ""
    return_label: str = ""

    # ── Stat-tile provenance (migration 127) ─────────────────────────
    #
    # ⚠️ EVERY FIELD BELOW MUST STAY DEFAULTED, for two independent reasons:
    #
    #   1. `whale_profile_cache` replays assembled JSON back through this model
    #      (`WhaleProfileResponse(**row["profile_json"])`), so a REQUIRED field
    #      would 500 every cached profile for up to 24h after deploy.
    #   2. iOS decodes this shape; a field it cannot find must not be fatal.
    #
    # ⚠️ And `ytd_return` / `portfolio_value` above must stay NON-Optional
    # `float`. The shipped `WhaleProfileDTO` declares them non-Optional `Double`
    # with their keys in `CodingKeys`, so a JSON `null` throws `typeMismatch`
    # and fails the WHOLE profile decode on every installed build. The status
    # fields below are how "don't believe this number" travels instead.

    # ok | insufficient_history | unavailable. "" = a row written before 127,
    # which the client treats as "trust the legacy value".
    return_status: str = ""
    # Calendar years compounded into `ytd_return`. None => not a multi-year
    # CAGR, so the client omits the "· N-yr" suffix instead of inventing one.
    return_window_years: Optional[int] = None
    portfolio_status: str = ""
    # "Q2 2026" — the quarter the 13F snapshot describes. None for congressional
    # filers, who report on a monthly cadence and have no 13F quarter.
    portfolio_as_of: Optional[str] = None
    filing_date: Optional[str] = None


# ── Activity feed ────────────────────────────────────────────────────


class WhaleTradeGroupActivityResponse(BaseModel):
    """Timeline item for activity feed. Maps to Swift WhaleTradeGroupActivity."""

    id: str
    whale_id: str = ""
    entity_name: str
    entity_avatar_name: str = ""
    entity_firm_name: Optional[str] = None  # firm shown with a person-fronted name
    category: Optional[str] = None  # "investors" | "institutions" | "politicians" | "crypto"
    action: str  # "BOUGHT" or "SOLD"
    trade_count: int
    total_amount: str  # Signed, formatted by _format_amount: "+$4.34B" / "-$2.1M" (iOS renders verbatim)
    summary: Optional[str] = None
    date: str


# ── Alerts ──────────────────────────────────────────────────────────


class WhaleAlertBannerResponse(BaseModel):
    """Alert banner for large whale activities. Maps to Swift WhaleAlertBanner."""

    id: str
    title: str
    description: str
    ticker: Optional[str] = None
    action_title: str = "View Full Alert"


# ── Follow ───────────────────────────────────────────────────────────


class FollowResponse(BaseModel):
    """Response for follow/unfollow actions."""

    is_following: bool
    followers_count: int
