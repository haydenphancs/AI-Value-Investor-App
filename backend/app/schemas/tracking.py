"""
Tracking Schemas — Pydantic models for the Tracking Assets feed.

Endpoints:
  GET  /api/v1/tracking/assets        → TrackingFeedResponse
  GET  /api/v1/tracking/holdings      → List[PortfolioHoldingResponse]
  POST /api/v1/tracking/holdings      → PortfolioHoldingResponse
  PUT  /api/v1/tracking/holdings/{t}  → PortfolioHoldingResponse
  DELETE /api/v1/tracking/holdings/{t} → message
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ── Tracked Assets Feed ─────────────────────────────────────────────


class TrackedAssetResponse(BaseModel):
    """A watchlist item enriched with real-time price data.

    ``shares`` / ``market_value`` / ``asset_type`` carry the user's portfolio
    holding info for this ticker. They're populated when the user has opted
    this ticker into Portfolio Insights via the config sheet, and ``null``
    otherwise. iOS uses them to pre-fill the config sheet inputs.
    """

    ticker: str
    company_name: str
    price: float = 0.0
    change_percent: float = 0.0
    # Previous trading day's close (authoritative, from the FMP quote). The
    # sparkline's dotted baseline anchors to this; nullable for degraded rows.
    previous_close: Optional[float] = None
    sparkline_data: List[float] = Field(default_factory=list)
    logo_url: Optional[str] = None
    sector: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    shares: Optional[float] = None
    market_value: Optional[float] = None
    asset_type: Optional[str] = None


class WhaleTradeItemResponse(BaseModel):
    """One ticker entry inside a rolled-up whale-trade alert."""

    ticker: str
    company_name: str
    whale_count: int
    amount: str  # e.g. "$2.4B" (13F) or "$50K – $250K" (congress range)
    raw_amount: float  # midpoint dollars — sort key + legacy re-aggregation
    # Summed STOCK Act bounds so iOS re-aggregates an honest RANGE after trimming
    # to the active portfolio. 13F: low == high == exact; congress open-ended
    # top bucket: high is None. Optional for backward-compat with old clients.
    raw_amount_low: Optional[float] = None
    raw_amount_high: Optional[float] = None
    is_congress: bool = False  # True → amount is a STOCK Act range/estimate, not exact
    lead_whale_id: Optional[str] = None
    lead_whale_name: Optional[str] = None
    lead_whale_avatar_name: Optional[str] = None


class AnalystRatingItemResponse(BaseModel):
    """One firm-update entry inside a rolled-up analyst-rating alert."""

    ticker: str
    firm_name: str
    rating_action: str  # "upgrade" | "downgrade" | "initiate" | "reiterate"
    new_rating: str
    previous_rating: Optional[str] = None
    price_target: Optional[float] = None
    previous_price_target: Optional[float] = None
    day: Optional[int] = None
    month: Optional[str] = None


class InsiderTransactionItemResponse(BaseModel):
    """One insider entry inside a rolled-up insider-transaction alert."""

    ticker: str
    insider_name: str
    insider_title: str
    amount: str  # e.g. "$348K"
    raw_amount: float  # numeric dollars — lets iOS re-aggregate after trimming to active portfolio
    day: Optional[int] = None
    month: Optional[str] = None


class AlertResponse(BaseModel):
    """An alert or upcoming event for the user's watchlist.

    Rolled-up types (whale_trade, analyst_rating, insider_transaction) carry
    their per-ticker breakdown under the corresponding `*_items` list.
    """

    type: str  # "earnings" | "market" | "whale_trade" | "analyst_rating" | "insider_transaction"
    title: str
    description: str

    # earnings / market
    ticker: Optional[str] = None
    company_name: Optional[str] = None
    day: Optional[int] = None
    month: Optional[str] = None
    report_time: Optional[str] = None  # "before_open" | "after_close"
    # earnings consensus — split out so iOS can render "EPS $X · Rev $Y" cleanly
    # in the detail view's Consensus row instead of repeating the full
    # description sentence.
    eps_estimate: Optional[float] = None
    revenue_estimate: Optional[float] = None

    # shared rollup fields (whale_trade, insider_transaction)
    action: Optional[str] = None  # "bought" | "sold"
    total_amount: Optional[str] = None
    time_window_label: Optional[str] = None

    # rollup item lists
    whale_trade_items: Optional[List[WhaleTradeItemResponse]] = None
    analyst_rating_items: Optional[List[AnalystRatingItemResponse]] = None
    insider_transaction_items: Optional[List[InsiderTransactionItemResponse]] = None


# Backward-compat alias; remove after all callers migrate.
EarningsAlertResponse = AlertResponse


class TrackingFeedResponse(BaseModel):
    """Aggregated response for the Assets tab."""

    assets: List[TrackedAssetResponse] = Field(default_factory=list)
    alerts: List[AlertResponse] = Field(default_factory=list)


# ── Portfolio Holdings CRUD ─────────────────────────────────────────


class AddHoldingRequest(BaseModel):
    """Request body for adding a portfolio holding.

    Either ``shares`` or ``market_value`` must be supplied. When ``shares`` is
    set, the backend recomputes ``market_value`` from the current FMP price on
    every read so the diversification score stays accurate as the market moves.
    """

    ticker: str
    company_name: Optional[str] = None
    shares: Optional[float] = None
    market_value: Optional[float] = None
    asset_type: Optional[str] = "Stock"


class UpdateHoldingRequest(BaseModel):
    """Request body for updating a portfolio holding."""

    shares: Optional[float] = None
    market_value: Optional[float] = None
    asset_type: Optional[str] = None


class PortfolioHoldingResponse(BaseModel):
    """A single portfolio holding.

    ``market_value`` is refreshed against the current FMP price when ``shares``
    is set; otherwise it's the static dollar amount the user entered.

    ``market_cap`` / ``industry`` / ``beta`` are profile signals used by the
    diversification scorer (market-cap mix, future correlation proxy). They're
    optional — a degraded row simply drops out of the dimensions that need them.
    """

    id: str
    ticker: str
    company_name: str
    market_value: float
    shares: Optional[float] = None
    sector: Optional[str] = None
    asset_type: str = "Stock"
    country: str = "US"
    market_cap: Optional[float] = None
    industry: Optional[str] = None
    beta: Optional[float] = None


class BulkHoldingUpdateItem(BaseModel):
    """One row of the bulk-update payload.

    ``shares = null && market_value = null`` clears the holding values for that
    ticker (the row stays on the watchlist, but is excluded from insights).
    """

    ticker: str
    shares: Optional[float] = None
    market_value: Optional[float] = None


# ── Portfolio Insights (diversification health score) ───────────────


class AllocationResponse(BaseModel):
    """One slice of a breakdown donut (sector / market-cap / region)."""

    name: str
    percentage: float  # 0..100


class DiversificationSubScoreResponse(BaseModel):
    """One additive dimension of the diversification score.

    ``points`` is what this dimension earned out of its ``max_points`` budget.
    The dimensions' ``max_points`` sum to 100, so the bars add up to the overall
    score (the "old way" — each bar contributes points to the whole). ``zone``
    drives the bar color on iOS.
    """

    key: str         # "position" | "sector" | "marketcap"
    label: str       # human-readable, e.g. "Position Balance"
    points: int      # earned, 0..max_points
    max_points: int  # budget for this dimension
    zone: str        # "green" | "yellow" | "red"


class PortfolioInsightsResponse(BaseModel):
    """Server-computed Portfolio Insights payload.

    Null when the user has fewer than the minimum holdings for a meaningful
    score. ``score`` (0..100) is the SUM of the sub-score points; the four
    dimensions (position / sector / concentration / market-cap) each contribute
    additive points. ``effective_holdings`` (1 / HHI) is an intuitive caption.
    """

    score: int   # 0..100 = sum of sub-score points
    zone: str    # "green" | "yellow" | "red" — overall bar color
    effective_holdings: float
    message: str
    sector_count: int
    sub_scores: List[DiversificationSubScoreResponse]
    sector_allocations: List[AllocationResponse]
    marketcap_allocations: List[AllocationResponse]
    holdings_count: int
    total_value: float
