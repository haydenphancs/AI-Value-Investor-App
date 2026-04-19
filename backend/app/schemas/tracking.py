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
    """A watchlist item enriched with real-time price data."""

    ticker: str
    company_name: str
    price: float = 0.0
    change_percent: float = 0.0
    sparkline_data: List[float] = Field(default_factory=list)
    logo_url: Optional[str] = None
    sector: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None


class AlertResponse(BaseModel):
    """An alert or upcoming event for the user's watchlist.

    The `type` discriminator selects which subset of fields is populated.
    Supported types: "earnings", "market", "whale_trade", "analyst_rating",
    "insider_transaction".
    """

    type: str  # "earnings" | "market" | "whale_trade" | "analyst_rating" | "insider_transaction"
    ticker: Optional[str] = None
    company_name: Optional[str] = None
    title: str
    description: str
    day: Optional[int] = None
    month: Optional[str] = None

    # earnings-only
    report_time: Optional[str] = None  # "before_open" | "after_close"

    # whale_trade
    whale_count: Optional[int] = None
    total_amount: Optional[str] = None
    action: Optional[str] = None  # "bought" | "sold"
    lead_whale_name: Optional[str] = None
    lead_whale_avatar_name: Optional[str] = None
    time_window_label: Optional[str] = None

    # analyst_rating
    firm_name: Optional[str] = None
    rating_action: Optional[str] = None  # "upgrade" | "downgrade" | "initiate" | "reiterate"
    new_rating: Optional[str] = None
    previous_rating: Optional[str] = None
    price_target: Optional[float] = None
    previous_price_target: Optional[float] = None

    # insider_transaction
    insider_name: Optional[str] = None
    insider_title: Optional[str] = None


# Backward-compat alias; remove after all callers migrate.
EarningsAlertResponse = AlertResponse


class TrackingFeedResponse(BaseModel):
    """Aggregated response for the Assets tab."""

    assets: List[TrackedAssetResponse] = Field(default_factory=list)
    alerts: List[AlertResponse] = Field(default_factory=list)


# ── Portfolio Holdings CRUD ─────────────────────────────────────────


class AddHoldingRequest(BaseModel):
    """Request body for adding a portfolio holding."""

    ticker: str
    company_name: Optional[str] = None
    market_value: float
    asset_type: Optional[str] = "Stock"


class UpdateHoldingRequest(BaseModel):
    """Request body for updating a portfolio holding."""

    market_value: Optional[float] = None
    asset_type: Optional[str] = None


class PortfolioHoldingResponse(BaseModel):
    """A single portfolio holding from the database."""

    id: str
    ticker: str
    company_name: str
    market_value: float
    sector: Optional[str] = None
    asset_type: str = "Stock"
    country: str = "US"
