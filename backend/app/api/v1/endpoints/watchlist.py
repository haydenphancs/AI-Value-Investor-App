"""
Watchlist Endpoints
Frontend: GET /watchlist, POST /watchlist, DELETE /watchlist
DB table: watchlist_items (id, user_id, ticker, company_name, logo_url, added_at)
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_watchlist_identity
from app.integrations.fmp import get_fmp_client
from app.services.tracking_service import invalidate_feed_cache
from app.schemas.watchlist import (
    AddToWatchlistRequest,
    RemoveFromWatchlistRequest,
    WatchlistItemResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_watchlist(
    user: dict = Depends(get_watchlist_identity),
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
    user: dict = Depends(get_watchlist_identity),
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
        # The Assets tab refreshes right after an add and purges portfolio tickers
        # missing from the feed. A stale cached feed here means the just-added
        # ticker reads as an orphan and gets deleted again — the add undoes itself.
        invalidate_feed_cache(user_id)
        _write_through_to_active_portfolio(supabase, user_id, ticker)
        return item
    except Exception as exc:
        logger.error("[Watchlist] DB error inserting %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"Failed to add {ticker} to watchlist: {exc}")


def _write_through_to_active_portfolio(supabase: Client, user_id: str, ticker: str) -> None:
    """Mirror a watchlist add into the user's ACTIVE group.

    The Tracking/Assets tab renders a GROUP, not the watchlist — `filteredAssets` intersects
    the feed with the active portfolio's tickers. But most add paths (onboarding, the Updates
    tab, the detail-screen star) write ONLY `watchlist_items`; just two, both inside the
    Tracking tab itself, also add to a group. So a ticker added anywhere else appeared in
    Updates and was invisible on the very tab named "Tracking" — with no error and no way to
    tell why.

    This used to be scoped to the ONE-group case (`_write_through_to_lone_portfolio`), because
    with several groups the server had no idea which one the user meant: the selection lived in
    a device-local `UserDefaults` string. Migration 126 put it in the database, so the ambiguity
    is gone and the narrow scope became the bug — a user's second group was exactly the point at
    which adds started silently vanishing from Tracking. Now that Home and Updates ALSO follow
    the active group, a ticker that skipped the write-through would be invisible on all three
    surfaces while sitting in the watchlist, so this is what keeps "everything you add shows up
    where you added it" true by construction.

    Best-effort: the watchlist row is already committed and IS the source of truth. A failure
    here must never fail the add — it degrades to the pre-existing behaviour, which the
    `GET /portfolios` backfill then repairs.
    """
    try:
        active = (
            supabase.table("portfolios")
            .select("id")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not active:
            # No active group yet (a user whose first `GET /portfolios` has not run, or a
            # row the 126 backfill has not reached). `_seed_default_portfolio` /
            # `ensure_active_portfolio` populate from the watchlist, so this ticker is not
            # lost — it arrives when the group is created.
            logger.info(
                "[Watchlist] No active group for user=%s — %s stays watchlist-only until "
                "GET /portfolios seeds one",
                user_id, ticker,
            )
            return
        portfolio_id = active[0]["id"]

        existing = (
            supabase.table("portfolio_items")
            .select("position")
            .eq("portfolio_id", portfolio_id)
            .execute()
            .data
            or []
        )
        next_position = max((r.get("position") or 0) for r in existing) + 1 if existing else 0

        # ON CONFLICT DO NOTHING against `portfolio_items_portfolio_id_ticker_key` — a re-add of
        # a ticker already in the portfolio must not raise, and must not move its position.
        supabase.table("portfolio_items").upsert(
            {"portfolio_id": portfolio_id, "ticker": ticker, "position": next_position},
            on_conflict="portfolio_id,ticker",
            ignore_duplicates=True,
        ).execute()
        logger.info(
            "[Watchlist] Mirrored %s into the user's active group %s", ticker, portfolio_id
        )
    except Exception as e:  # noqa: BLE001 — never fail the add for a mirror
        logger.warning(
            "[Watchlist] Could not mirror %s into a portfolio for user=%s (%s: %s) — "
            "GET /portfolios will backfill it",
            ticker, user_id, type(e).__name__, e,
        )


@router.delete("")
async def remove_from_watchlist(
    request: RemoveFromWatchlistRequest,
    user: dict = Depends(get_watchlist_identity),
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

        # Keep the feed honest immediately — a stale cache would keep serving the
        # removed ticker for up to FEED_CACHE_TTL after the row is gone.
        invalidate_feed_cache(user_id)
        return {"message": f"{ticker} removed from watchlist"}
    except Exception as exc:
        logger.error("[Watchlist] DB error deleting %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"Failed to remove {ticker}: {exc}")
