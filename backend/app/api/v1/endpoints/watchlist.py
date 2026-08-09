"""
Watchlist Endpoints
Frontend: GET /watchlist, POST /watchlist, DELETE /watchlist
DB table: watchlist_items (id, user_id, ticker, company_name, logo_url, added_at)
"""

from typing import Optional

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
            # On the watchlist already — but that no longer means "already where the user
            # is looking". Groups are a SUBSET of the watchlist, and Home, Updates and
            # Tracking all render the ACTIVE group now, so a ticker sitting in another
            # group (or in none) is invisible on every screen while still tripping this
            # check. A bare 409 then tells the user a ticker they cannot see anywhere is
            # "already in your watchlist", and no retry can ever fix it.
            #
            # Converge instead: mirror it into the active group and report success. Only a
            # ticker that is ALREADY in the active group is a true no-op duplicate, and it
            # keeps its 409 so the Tracking search still says "you have this one".
            if not _ticker_in_active_group(supabase, user_id, ticker):
                _write_through_to_active_portfolio(supabase, user_id, ticker)
                invalidate_feed_cache(user_id)
                logger.info(
                    "[Watchlist] %s was on the watchlist but outside the active group for "
                    "user=%s — mirrored in rather than 409-ing",
                    ticker, user_id,
                )
                return {"ticker": ticker, "message": f"{ticker} added"}
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
            # Two very different states look identical here, and only one of them repairs
            # itself:
            #
            #   * ZERO groups — `GET /portfolios` has never run for this identity.
            #     `_seed_default_portfolio` builds "Holdings" FROM the watchlist, so this
            #     ticker arrives with it. Genuinely safe to skip.
            #   * groups but NONE ACTIVE — reachable whenever `_ensure_active_portfolio`
            #     fails transiently after a delete (that endpoint logs a warning and still
            #     returns 200, and its `other_count == 0` guard means a group always
            #     survives), or when the claim path's heal fails on sign-up.
            #
            # Skipping the second state loses the ticker PERMANENTLY: no repair path
            # writes `portfolio_items` for it. `ensure_active_portfolio` only flips a
            # boolean, `_seed_default_portfolio` requires zero groups, and
            # `_backfill_lone_empty_portfolio` requires exactly one group with zero items.
            # So the row sits in `watchlist_items` while Home, Updates AND Tracking all
            # read the active group — invisible on every surface at once, which is the
            # exact failure this whole change exists to eliminate.
            #
            # Heal first, then retry. `ensure_active_portfolio` promotes deterministically
            # by (sort_order, created_at, id) — the same group `GET /portfolios` would pick
            # — so this cannot file the ticker anywhere the heal would not have.
            healed_id = _heal_active_group(supabase, user_id)
            if not healed_id:
                logger.info(
                    "[Watchlist] No group for user=%s — %s stays watchlist-only until "
                    "GET /portfolios seeds one",
                    user_id, ticker,
                )
                return
            logger.warning(
                "[Watchlist] user=%s had groups but none active; promoted %s before "
                "mirroring %s",
                user_id, healed_id, ticker,
            )
            portfolio_id = healed_id
        else:
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
        _delete_through_from_groups(supabase, user_id, ticker)
        return {"message": f"{ticker} removed from watchlist"}
    except Exception as exc:
        logger.error("[Watchlist] DB error deleting %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"Failed to remove {ticker}: {exc}")


def _ticker_in_active_group(supabase: Client, user_id: str, ticker: str) -> bool:
    """Is this ticker already in the user's active group?

    The precise test for "a duplicate the user can actually SEE". Distinguishing it from
    "on the watchlist somewhere" is what stops `POST /watchlist` from 409-ing on a ticker
    that is invisible on all three surfaces.

    Errs toward TRUE on a read failure: a spurious 409 is a confusing message, whereas a
    spurious success would run the write-through against unknown state and report an add
    that may not have happened.
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
            return False
        rows = (
            supabase.table("portfolio_items")
            .select("ticker")
            .eq("portfolio_id", active[0]["id"])
            .eq("ticker", ticker)
            .limit(1)
            .execute()
            .data
            or []
        )
        return bool(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[Watchlist] Active-group membership check failed for %s user=%s (%s: %s) — "
            "treating as a duplicate",
            ticker, user_id, type(e).__name__, e,
        )
        return True


def _heal_active_group(supabase: Client, user_id: str) -> Optional[str]:
    """Promote a group to active when the user has some but none is flagged, else None.

    Thin wrapper over the `ensure_active_portfolio` RPC (migration 126) so the write-through
    does not have to duplicate the promotion rule. Returns the active group's id, or None
    when the user genuinely owns no groups (or the RPC failed — a caller that treats those
    alike is correct, because both mean "there is nowhere to mirror to right now").
    """
    try:
        result = supabase.rpc(
            "ensure_active_portfolio", {"p_user_id": user_id}
        ).execute()
        return str(result.data) if result.data else None
    except Exception as e:  # noqa: BLE001 — the caller degrades; never fail the add
        logger.warning(
            "[Watchlist] ensure_active_portfolio failed for user=%s (%s: %s)",
            user_id, type(e).__name__, e,
        )
        return None


def _delete_through_from_groups(supabase: Client, user_id: str, ticker: str) -> None:
    """Remove the ticker from every one of the user's groups.

    The mirror image of `_write_through_to_active_portfolio`, and it has to exist for the
    same reason that one does. `portfolio_items.ticker` is bare TEXT with no foreign key
    to `watchlist_items` (the only FK is `portfolio_id → portfolios.id`), so deleting the
    watchlist row leaves the group row behind.

    That orphan used to be invisible: Home and the Updates chips both read
    `watchlist_items` directly, so a removal took effect everywhere at once and only the
    Tracking tab consulted groups. Now BOTH read `portfolio_items` through
    `active_group_service.get_active_group`, and neither intersects the result back
    against `watchlist_items` — so the orphan renders, with a live quote and no company
    name (the metadata lookup misses the row that was just deleted). The user removes a
    ticker, watches it disappear from Tracking, and finds it still sitting on Home and in
    the Updates strip.

    ALL groups, not just the active one: the ticker is gone from the master watchlist, so
    it cannot legitimately remain in any subset of it — and `set_portfolio_tickers`
    enforces exactly that invariant (`portfolio_items ⊆ watchlist_items`) on its own path.

    Best-effort, like the add-side mirror: the watchlist row is already deleted and IS the
    source of truth. Failing the request here would report a removal that did happen as an
    error. Logged, never silent.
    """
    try:
        group_rows = (
            supabase.table("portfolios")
            .select("id")
            .eq("user_id", user_id)
            .execute()
            .data
            or []
        )
        group_ids = [r["id"] for r in group_rows if r.get("id")]
        if not group_ids:
            return
        removed = (
            supabase.table("portfolio_items")
            .delete()
            .in_("portfolio_id", group_ids)
            .eq("ticker", ticker)
            .execute()
            .data
            or []
        )
        if removed:
            logger.info(
                "[Watchlist] Delete-through removed %s from %d group(s) for user=%s",
                ticker, len(removed), user_id,
            )
    except Exception as e:  # noqa: BLE001 — never fail a completed delete for the mirror
        logger.warning(
            "[Watchlist] Could not delete-through %s from groups for user=%s (%s: %s) — "
            "it will keep rendering on Home and Updates until the row is cleaned up",
            ticker, user_id, type(e).__name__, e,
        )
