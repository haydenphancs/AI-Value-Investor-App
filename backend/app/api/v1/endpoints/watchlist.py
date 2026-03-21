"""
Watchlist Endpoints
Frontend: GET /watchlist, POST /watchlist, DELETE /watchlist
DB table: watchlist_items (id, user_id, ticker, company_name, logo_url, added_at)
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_current_user_or_guest
from app.integrations.fmp import get_fmp_client
from app.schemas.watchlist import (
    AddToWatchlistRequest,
    RemoveFromWatchlistRequest,
    WatchlistItemResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_watchlist(
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Get current user's watchlist."""
    user_id = user["id"]
    logger.info("[Watchlist] GET watchlist for user=%s", user_id)

    try:
        result = (
            supabase.table("watchlist_items")
            .select("*")
            .eq("user_id", user_id)
            .order("added_at", desc=True)
            .execute()
        )
        logger.info("[Watchlist] Returned %d items", len(result.data or []))
        return result.data or []
    except Exception as exc:
        logger.error("[Watchlist] DB error fetching watchlist: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch watchlist: {exc}")


@router.post("", status_code=201)
async def add_to_watchlist(
    request: AddToWatchlistRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Add a stock to user's watchlist. Fetches company info from FMP."""
    ticker = request.stock_id.upper().strip()
    user_id = user["id"]
    logger.info("[Watchlist] POST add ticker=%s for user=%s", ticker, user_id)

    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker symbol is required")

    # Check for duplicate
    try:
        existing = (
            supabase.table("watchlist_items")
            .select("id")
            .eq("user_id", user_id)
            .eq("ticker", ticker)
            .execute()
        )
        if existing.data:
            logger.warning("[Watchlist] Duplicate: %s already in watchlist for user=%s", ticker, user_id)
            raise HTTPException(status_code=409, detail=f"{ticker} is already in your watchlist")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Watchlist] DB error checking duplicate for %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")

    # Fetch company info from FMP for display
    company_name = ticker
    logo_url = None
    try:
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = profile.get("companyName", ticker)
            logo_url = profile.get("image")
            logger.info("[Watchlist] FMP profile: %s → %s", ticker, company_name)
        else:
            logger.warning("[Watchlist] FMP returned no profile for %s", ticker)
    except Exception as exc:
        logger.warning("[Watchlist] FMP profile fetch failed for %s: %s", ticker, exc)

    # Insert into DB
    data = {
        "user_id": user_id,
        "ticker": ticker,
        "company_name": company_name,
        "logo_url": logo_url,
    }

    try:
        result = supabase.table("watchlist_items").insert(data).execute()
        item = result.data[0] if result.data else data
        logger.info("[Watchlist] Added %s to watchlist (id=%s)", ticker, item.get("id", "?"))
        return item
    except Exception as exc:
        logger.error("[Watchlist] DB error inserting %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"Failed to add {ticker} to watchlist: {exc}")


@router.delete("")
async def remove_from_watchlist(
    request: RemoveFromWatchlistRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Remove a stock from user's watchlist."""
    ticker = request.stock_id.upper().strip()
    user_id = user["id"]
    logger.info("[Watchlist] DELETE ticker=%s for user=%s", ticker, user_id)

    try:
        result = (
            supabase.table("watchlist_items")
            .delete()
            .eq("user_id", user_id)
            .eq("ticker", ticker)
            .execute()
        )
        if not result.data:
            logger.warning("[Watchlist] Ticker %s not found in watchlist for user=%s", ticker, user_id)
        else:
            logger.info("[Watchlist] Removed %s from watchlist", ticker)

        return {"message": f"{ticker} removed from watchlist"}
    except Exception as exc:
        logger.error("[Watchlist] DB error deleting %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"Failed to remove {ticker}: {exc}")
