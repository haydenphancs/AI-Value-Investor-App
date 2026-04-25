"""
Tracking Endpoints — Assets feed + Portfolio holdings CRUD + Insights.

Routes:
  GET    /tracking/assets               → TrackingFeedResponse
  GET    /tracking/holdings             → List[PortfolioHoldingResponse]
  POST   /tracking/holdings             → PortfolioHoldingResponse
  PUT    /tracking/holdings/{ticker}    → PortfolioHoldingResponse
  DELETE /tracking/holdings/{ticker}    → message
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
    """Add or upsert a portfolio holding.

    Either ``shares`` or ``market_value`` must be supplied. Sector / country
    are auto-enriched from the FMP company profile when available.
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

    # Enrich with FMP profile (sector/country/name) and a current price so we
    # can seed `market_value` for share-based holdings without forcing the
    # caller to provide it.
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
        # Shares supplied but FMP price lookup failed — store 0 and let the
        # next read recompute when the quote is reachable again.
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
        supabase.table("portfolio_holdings")
        .upsert(data, on_conflict="user_id,ticker")
        .execute()
    )
    row = result.data[0] if result.data else data
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


@router.put("/holdings/{ticker}", response_model=PortfolioHoldingResponse)
async def update_holding(
    ticker: str,
    request: UpdateHoldingRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Update an existing holding's shares, market value, or asset type."""
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
        supabase.table("portfolio_holdings")
        .update(updates)
        .eq("user_id", user["id"])
        .eq("ticker", ticker.upper())
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Holding not found")

    row = result.data[0]
    return PortfolioHoldingResponse(
        id=str(row["id"]),
        ticker=row["ticker"],
        company_name=row.get("company_name") or row["ticker"],
        market_value=float(row.get("market_value") or 0),
        shares=float(row["shares"]) if row.get("shares") is not None else None,
        sector=row.get("sector"),
        asset_type=row.get("asset_type") or "Stock",
        country=row.get("country") or "US",
    )


@router.delete("/holdings/{ticker}")
async def delete_holding(
    ticker: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Remove a holding from user's portfolio."""
    supabase.table("portfolio_holdings").delete().eq(
        "user_id", user["id"]
    ).eq("ticker", ticker.upper()).execute()

    return {"message": "Holding removed"}


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
