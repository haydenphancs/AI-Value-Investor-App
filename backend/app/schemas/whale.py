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

    # ── Tier gate (services/entitlements.whale_follow_limit) ─────────
    # True when THIS caller may not track THIS whale: a Free account outside its
    # single free slot, or a Pro account already at 10. The row still renders in
    # full — only the Follow button locks — because the roster is the feature's
    # own demo. Defaulted so an already-shipped client decodes this unchanged.
    is_locked: bool = False

    # True when the caller DOES follow this whale but their plan doesn't surface it.
    #
    # Follows are truncated on read, never deleted ("truncate, never destroy"), so a Free
    # account that was Pro — or that followed before this gate existed — still holds rows
    # for whales the activity feed will not serve. Without this flag the followed-whale
    # avatar row showed all of them while the feed below it showed one, which reads as a
    # bug rather than as a plan limit.
    #
    # Deliberately NOT folded into `is_locked`: that one force-unlocks a followed whale so
    # the user can always unfollow, and it also gates the profile. These are different
    # questions. Defaulted for the same already-shipped-client reason.
    is_following_inactive: bool = False

    # ── Activity disclosure ──────────────────────────────────────────
    # Whether this filer is still producing data. A fund can deregister, a politician can
    # retire or simply stop trading, and the roster previously had NO date signal of any
    # kind — a filer that stopped in 2019 rendered identically to one that filed last week.
    #
    # `activity_status` is one of `_whale_common.ACTIVITY_*`; `""` means "not computed"
    # (a legacy row) and the client renders nothing. Defaulted so an already-shipped
    # client decodes this unchanged.
    activity_status: str = ""
    # The sentence the chip shows, e.g. "Last filed Q3 2025" or
    # "No trades disclosed since Nov 2025". Empty whenever there is nothing to disclose,
    # so the client never has to build copy from the status enum.
    activity_label: str = ""


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

    # ── Tier gate (services/entitlements.whale_detail_unlocked) ──────
    #
    # When `is_locked`, the POSITION-LEVEL detail has been withheld server-side:
    # `current_holdings`, `recent_trade_groups`, `recent_trades` come back empty,
    # `sentiment_summary` blank and `behavior_summary` neutral. Everything a
    # reader needs to judge the investor stays — header, bio, risk profile, the
    # stat tiles, and `sector_exposure` — so the profile is a real preview rather
    # than a wall.
    #
    # Defaulted for the same two reasons the provenance block above is: cached
    # profile JSON predating this field replays through the same model, and iOS
    # must not treat an absent key as fatal.
    # ── Activity disclosure (migration 145) ──────────────────────────
    #
    # Same two fields as the roster, plus the raw date and the CURATED note.
    #
    # ⚠️ Defaulted for the same two reasons the provenance block above is: cached profile
    # JSON predating these fields replays through this model, and iOS must not treat an
    # absent key as fatal.
    #
    # Note the deliberate asymmetry with the stat tiles: `portfolio_value` and
    # `ytd_return` stay honest NUMBERS even for a dormant filer — Burry's $1.37B really
    # was his Q3 2025 book. The fix for a stale figure is disclosure, never deletion.
    activity_status: str = ""
    activity_label: str = ""
    # MAX(whale_trade_groups.date): newest 13F filing date or congressional disclosure.
    last_activity_date: Optional[str] = None
    # CURATED prose from whale_registry.json, shown verbatim. Overrides `activity_label`
    # on the profile — a stated reason beats an inferred one. None when the filer is
    # active or when nobody has written a note.
    lifecycle_note: Optional[str] = None

    is_locked: bool = False
    # "pro" when locked — the plan the server enforced, so the paywall never
    # carries a second copy of the tier ladder.
    tier_required: Optional[str] = None


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
