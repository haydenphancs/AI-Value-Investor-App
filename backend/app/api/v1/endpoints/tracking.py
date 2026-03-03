"""
Tracking Endpoints — Assets feed + Portfolio holdings CRUD.

Routes:
  GET    /tracking/assets              → TrackingFeedResponse (enriched watchlist + alerts)
  GET    /tracking/holdings            → List of portfolio holdings
  POST   /tracking/holdings            → Add/upsert a holding
  PUT    /tracking/holdings/{ticker}   → Update a holding
  DELETE /tracking/holdings/{ticker}   → Remove a holding
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_current_user
from app.integrations.fmp import get_fmp_client
from app.schemas.tracking import (
    TrackingFeedResponse,
    AddHoldingRequest,
    UpdateHoldingRequest,
)
from app.services.tracking_service import TrackingService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Assets Feed ─────────────────────────────────────────────────────


@router.get("/assets", response_model=TrackingFeedResponse)
async def get_tracking_assets(
    user: dict = Depends(get_current_user),
):
    """Get enriched watchlist with real-time prices, sparklines, and alerts."""
    service = TrackingService()
    return await service.get_tracking_feed(user["id"])


# ── Portfolio Holdings CRUD ─────────────────────────────────────────


@router.get("/holdings")
async def get_holdings(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get current user's portfolio holdings for diversification scoring."""
    result = (
        supabase.table("portfolio_holdings")
        .select("*")
        .eq("user_id", user["id"])
        .order("market_value", desc=True)
        .execute()
    )
    return result.data or []


@router.post("/holdings")
async def add_holding(
    request: AddHoldingRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Add or upsert a portfolio holding. Enriches with FMP sector/country data."""
    ticker = request.ticker.upper()

    # Enrich with FMP data
    sector = None
    country = "US"
    company_name = request.company_name or ticker
    try:
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = request.company_name or profile.get("companyName", ticker)
            sector = profile.get("sector")
            country = profile.get("country", "US")
    except Exception as e:
        logger.warning("Could not fetch FMP profile for %s: %s", ticker, e)

    data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "market_value": request.market_value,
        "sector": sector,
        "asset_type": request.asset_type or "Stock",
        "country": country,
    }

    result = (
        supabase.table("portfolio_holdings")
        .upsert(data, on_conflict="user_id,ticker")
        .execute()
    )
    return result.data[0] if result.data else data


@router.put("/holdings/{ticker}")
async def update_holding(
    ticker: str,
    request: UpdateHoldingRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Update an existing portfolio holding's market value or asset type."""
    updates = {}
    if request.market_value is not None:
        updates["market_value"] = request.market_value
    if request.asset_type is not None:
        updates["asset_type"] = request.asset_type

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        supabase.table("portfolio_holdings")
        .update(updates)
        .eq("user_id", user["id"])
        .eq("ticker", ticker.upper())
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Holding not found")

    return result.data[0]


@router.delete("/holdings/{ticker}")
async def delete_holding(
    ticker: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Remove a holding from user's portfolio."""
    supabase.table("portfolio_holdings").delete().eq(
        "user_id", user["id"]
    ).eq("ticker", ticker.upper()).execute()

    return {"message": "Holding removed"}
