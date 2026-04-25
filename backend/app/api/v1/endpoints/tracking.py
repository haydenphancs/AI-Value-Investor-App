"""
Tracking Endpoints — Assets feed + Portfolio holdings CRUD + Insights.

After the holdings/watchlist merge, all holding data lives on the
``watchlist_items`` table. The Portfolio Insights feature is opt-in: a user's
watchlist row counts toward insights only when ``shares`` or ``market_value``
is set on it.

Routes:
  GET    /tracking/assets               → TrackingFeedResponse
  GET    /tracking/holdings             → List[PortfolioHoldingResponse]
  POST   /tracking/holdings             → PortfolioHoldingResponse
  PUT    /tracking/holdings/{ticker}    → PortfolioHoldingResponse
  DELETE /tracking/holdings/{ticker}    → message
  PUT    /tracking/assets/holdings      → bulk-update {shares, market_value}
                                          across many watchlist rows in one
                                          call (used by the iOS config sheet)
  GET    /tracking/portfolio-insights   → Optional[PortfolioInsightsResponse]
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
from typing import List, Optional
import logging

from app.database import get_supabase
from app.dependencies import get_current_user_or_guest
from app.integrations.fmp import get_fmp_client
from app.schemas.tracking import (
    TrackingFeedResponse,
    AddHoldingRequest,
    UpdateHoldingRequest,
    PortfolioHoldingResponse,
    PortfolioInsightsResponse,
    BulkHoldingUpdateItem,
)
from app.services.portfolio_insights_service import PortfolioInsightsService
from app.services.tracking_service import TrackingService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Assets Feed ─────────────────────────────────────────────────────


@router.get("/assets", response_model=TrackingFeedResponse)
async def get_tracking_assets(
    user: dict = Depends(get_current_user_or_guest),
):
    """Get enriched watchlist with real-time prices, sparklines, and alerts."""
    service = TrackingService()
    return await service.get_tracking_feed(user["id"])


# ── Portfolio Holdings CRUD ─────────────────────────────────────────


@router.get("/holdings", response_model=List[PortfolioHoldingResponse])
async def get_holdings(
    user: dict = Depends(get_current_user_or_guest),
):
    """Get current user's portfolio holdings.

    For rows with ``shares`` set, ``market_value`` is recomputed from the
    current FMP price so the diversification score stays accurate as the
    market moves. Static rows (shares NULL) keep their stored value.
    """
    service = PortfolioInsightsService()
    return await service.get_holdings(user["id"])


@router.post("/holdings", response_model=PortfolioHoldingResponse)
async def add_holding(
    request: AddHoldingRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Add or upsert a portfolio holding on the user's watchlist.

    Creates the watchlist row if it doesn't exist yet (so this works as a
    one-shot "add to portfolio + watchlist") and stamps it with shares /
    market_value. Sector / country / company_name are auto-enriched from the
    FMP company profile when available.
    """
    if request.shares is None and request.market_value is None:
        raise HTTPException(
            status_code=400,
            detail="Provide either `shares` or `market_value`.",
        )
    if request.shares is not None and request.shares <= 0:
        raise HTTPException(status_code=400, detail="`shares` must be positive.")
    if request.market_value is not None and request.market_value < 0:
        raise HTTPException(
            status_code=400, detail="`market_value` cannot be negative."
        )

    ticker = request.ticker.upper()

    # Enrich with FMP profile + current price (only when we need to seed
    # market_value from shares).
    sector = None
    country = "US"
    company_name = request.company_name or ticker
    current_price: Optional[float] = None
    try:
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = request.company_name or profile.get("companyName", ticker)
            sector = profile.get("sector")
            country = profile.get("country", "US")
        if request.shares is not None and request.market_value is None:
            quote = await fmp.get_stock_price_quote(ticker)
            if quote and quote.get("price"):
                current_price = float(quote["price"])
    except Exception as e:
        logger.warning("Could not enrich holding for %s from FMP: %s", ticker, e)

    if request.market_value is not None:
        market_value = request.market_value
    elif request.shares is not None and current_price is not None:
        market_value = request.shares * current_price
    else:
        market_value = 0.0

    data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "shares": request.shares,
        "market_value": market_value,
        "sector": sector,
        "asset_type": request.asset_type or "Stock",
        "country": country,
    }

    result = (
        supabase.table("watchlist_items")
        .upsert(data, on_conflict="user_id,ticker")
        .execute()
    )
    row = result.data[0] if result.data else data
    return _row_to_holding(row)


@router.put("/holdings/{ticker}", response_model=PortfolioHoldingResponse)
async def update_holding(
    ticker: str,
    request: UpdateHoldingRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Update shares / market_value / asset_type on an existing watchlist row."""
    updates: dict = {}
    if request.shares is not None:
        if request.shares <= 0:
            raise HTTPException(status_code=400, detail="`shares` must be positive.")
        updates["shares"] = request.shares
    if request.market_value is not None:
        if request.market_value < 0:
            raise HTTPException(
                status_code=400, detail="`market_value` cannot be negative."
            )
        updates["market_value"] = request.market_value
    if request.asset_type is not None:
        updates["asset_type"] = request.asset_type

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        supabase.table("watchlist_items")
        .update(updates)
        .eq("user_id", user["id"])
        .eq("ticker", ticker.upper())
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Holding not found")

    return _row_to_holding(result.data[0])


@router.delete("/holdings/{ticker}")
async def delete_holding(
    ticker: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Clear holding values on a watchlist row.

    The row stays on the user's watchlist (price feed + alerts continue) but
    is excluded from Portfolio Insights until they re-enter shares or value.
    To remove the ticker from the watchlist entirely, call
    ``DELETE /api/v1/watchlist`` instead.
    """
    supabase.table("watchlist_items").update(
        {"shares": None, "market_value": None}
    ).eq("user_id", user["id"]).eq("ticker", ticker.upper()).execute()

    return {"message": "Holding cleared"}


# ── Bulk holdings update (Portfolio Insights config sheet) ──────────


@router.put("/assets/holdings")
async def bulk_update_holdings(
    items: List[BulkHoldingUpdateItem],
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Update shares / market_value across many watchlist rows in a single call.

    Used by the iOS Portfolio Insights config sheet, which lets the user fill
    in (or clear) values for every ticker on their watchlist at once.

    A row with both ``shares`` and ``market_value`` set to ``null`` is treated
    as a clear — those values are wiped on the matching watchlist row, and the
    ticker stops counting toward insights. Tickers not present in the user's
    watchlist are silently ignored (the config sheet should never send those).
    """
    if not items:
        return {"message": "No items provided", "updated": 0}

    updated = 0
    errors: List[str] = []
    for item in items:
        ticker = item.ticker.upper()
        if item.shares is not None and item.shares <= 0:
            errors.append(f"{ticker}: `shares` must be positive")
            continue
        if item.market_value is not None and item.market_value < 0:
            errors.append(f"{ticker}: `market_value` cannot be negative")
            continue

        updates = {
            "shares": item.shares,
            "market_value": item.market_value,
        }
        result = (
            supabase.table("watchlist_items")
            .update(updates)
            .eq("user_id", user["id"])
            .eq("ticker", ticker)
            .execute()
        )
        if result.data:
            updated += 1

    if errors:
        # Surface validation problems but keep partial success — better than
        # rejecting the whole save because one row had a typo.
        return {
            "message": "Bulk update completed with errors",
            "updated": updated,
            "errors": errors,
        }
    return {"message": "Bulk update completed", "updated": updated}


def _row_to_holding(row: dict) -> PortfolioHoldingResponse:
    return PortfolioHoldingResponse(
        id=str(row.get("id", "")),
        ticker=row["ticker"],
        company_name=row.get("company_name") or row["ticker"],
        market_value=float(row.get("market_value") or 0),
        shares=float(row["shares"]) if row.get("shares") is not None else None,
        sector=row.get("sector"),
        asset_type=row.get("asset_type") or "Stock",
        country=row.get("country") or "US",
    )


# ── Portfolio Insights ──────────────────────────────────────────────


@router.get(
    "/portfolio-insights",
    response_model=Optional[PortfolioInsightsResponse],
)
async def get_portfolio_insights(
    user: dict = Depends(get_current_user_or_guest),
):
    """Server-computed Portfolio Insights — diversification score, sector
    breakdown, and sub-scores. Returns ``null`` when the user has fewer than
    the minimum holdings required for a meaningful score.
    """
    service = PortfolioInsightsService()
    return await service.compute_insights(user["id"])
