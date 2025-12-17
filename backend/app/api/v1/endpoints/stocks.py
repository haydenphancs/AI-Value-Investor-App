"""
Stock Management Endpoints
Handles stock data, watchlists, and company information.
Requirements: Section 4.4 - Company's Fundamental Information
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, get_optional_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class StockSearch(BaseModel):
    ticker: str
    company_name: str
    sector: Optional[str]
    market_cap: Optional[float]


class WatchlistAdd(BaseModel):
    stock_id: str
    alert_on_news: bool = True
    alert_threshold_percentage: Optional[float] = None


# Endpoints
# =========

@router.get("/search")
async def search_stocks(
    query: str = Query(..., min_length=1),
    limit: int = Query(10, le=50),
    supabase: Client = Depends(get_supabase)
):
    """
    Search for stocks by ticker or company name.
    Uses full-text search on search_vector column.

    Args:
        query: Search query (ticker or company name)
        limit: Maximum results to return
        supabase: Supabase client

    Returns:
        list: Matching stocks
    """
    # Use Supabase full-text search
    result = supabase.table("stocks").select(
        "id, ticker, company_name, sector, market_cap, logo_url"
    ).or_(
        f"ticker.ilike.%{query}%,company_name.ilike.%{query}%"
    ).eq("is_active", True).limit(limit).execute()

    return result.data


@router.get("/{ticker}")
async def get_stock_details(
    ticker: str,
    supabase: Client = Depends(get_supabase)
):
    """
    Get detailed information about a specific stock.
    Section 4.4 - Company's Fundamental Information

    Args:
        ticker: Stock ticker symbol
        supabase: Supabase client

    Returns:
        dict: Stock details
    """
    result = supabase.table("stocks").select("*").eq(
        "ticker", ticker.upper()
    ).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=404,
            detail=f"Stock {ticker} not found"
        )

    return result.data


@router.get("/{ticker}/fundamentals")
async def get_stock_fundamentals(
    ticker: str,
    limit: int = Query(10, le=50),
    supabase: Client = Depends(get_supabase)
):
    """
    Get fundamental data for a stock (10-K, financials).
    Section 4.4 - Company's Fundamental Information

    Args:
        ticker: Stock ticker symbol
        limit: Number of periods to return
        supabase: Supabase client

    Returns:
        list: Fundamental data
    """
    # Get stock ID first
    stock = supabase.table("stocks").select("id").eq(
        "ticker", ticker.upper()
    ).single().execute()

    if not stock.data:
        raise HTTPException(status_code=404, detail="Stock not found")

    # Get fundamentals
    result = supabase.table("company_fundamentals").select("*").eq(
        "stock_id", stock.data["id"]
    ).order("fiscal_year", desc=True).order(
        "fiscal_quarter", desc=True
    ).limit(limit).execute()

    return result.data


@router.get("/{ticker}/earnings")
async def get_stock_earnings(
    ticker: str,
    upcoming: bool = True,
    supabase: Client = Depends(get_supabase)
):
    """
    Get earnings data for a stock.

    Args:
        ticker: Stock ticker symbol
        upcoming: If True, return upcoming earnings; else return past
        supabase: Supabase client

    Returns:
        list: Earnings data
    """
    stock = supabase.table("stocks").select("id").eq(
        "ticker", ticker.upper()
    ).single().execute()

    if not stock.data:
        raise HTTPException(status_code=404, detail="Stock not found")

    result = supabase.table("earnings").select("*").eq(
        "stock_id", stock.data["id"]
    ).eq("has_occurred", not upcoming).order(
        "earnings_date", desc=not upcoming
    ).limit(10).execute()

    return result.data


# Watchlist Endpoints
# ===================

@router.get("/watchlist/me")
async def get_my_watchlist(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get current user's watchlist.
    References: stocks table, watchlists table

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        list: Watchlist with stock details
    """
    result = supabase.table("watchlists").select(
        """
        *,
        stock:stocks(*)
        """
    ).eq("user_id", user["id"]).order("added_at", desc=True).execute()

    return result.data


@router.post("/watchlist")
async def add_to_watchlist(
    watchlist_item: WatchlistAdd,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Add a stock to user's watchlist.

    Args:
        watchlist_item: Watchlist item data
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Created watchlist item
    """
    data = {
        "user_id": user["id"],
        **watchlist_item.model_dump()
    }

    result = supabase.table("watchlists").insert(data).execute()

    return result.data[0] if result.data else {}


@router.delete("/watchlist/{stock_id}")
async def remove_from_watchlist(
    stock_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Remove a stock from user's watchlist.

    Args:
        stock_id: Stock ID to remove
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Deletion confirmation
    """
    supabase.table("watchlists").delete().eq(
        "user_id", user["id"]
    ).eq("stock_id", stock_id).execute()

    return {"message": "Stock removed from watchlist"}
