"""
Watchlist Endpoints
Frontend: GET /watchlist, POST /watchlist, DELETE /watchlist
DB table: watchlist_items (id, user_id, ticker, company_name, logo_url, added_at)
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_current_user
from app.integrations.fmp import get_fmp_client
from app.schemas.watchlist import AddToWatchlistRequest, RemoveFromWatchlistRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_watchlist(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get current user's watchlist."""
    result = supabase.table("watchlist_items").select("*").eq(
        "user_id", user["id"]
    ).order("added_at", desc=True).execute()

    return result.data


@router.post("")
async def add_to_watchlist(
    request: AddToWatchlistRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Add a stock to user's watchlist. Fetches company info from FMP."""
    ticker = request.stock_id.upper()

    # Check for duplicate
    existing = supabase.table("watchlist_items").select("id").eq(
        "user_id", user["id"]
    ).eq("ticker", ticker).execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="Stock already in watchlist")

    # Fetch company info from FMP for display
    company_name = ticker
    logo_url = None
    try:
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = profile.get("companyName", ticker)
            logo_url = profile.get("image")
    except Exception as e:
        logger.warning(f"Could not fetch FMP profile for {ticker}: {e}")

    data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "logo_url": logo_url,
    }

    result = supabase.table("watchlist_items").insert(data).execute()
    return result.data[0] if result.data else data


@router.delete("")
async def remove_from_watchlist(
    request: RemoveFromWatchlistRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Remove a stock from user's watchlist."""
    ticker = request.stock_id.upper()

    supabase.table("watchlist_items").delete().eq(
        "user_id", user["id"]
    ).eq("ticker", ticker).execute()

    return {"message": "Stock removed from watchlist"}
