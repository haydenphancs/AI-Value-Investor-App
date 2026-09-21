"""
Portfolios Endpoints — named groupings of tickers within a user's watchlist.

A portfolio is a user-named subset of the master watchlist_items table. Tickers
in portfolio_items.ticker must already exist as a watchlist row for the same
user (enforced at the service layer; a portfolio can never contain a ticker
the user isn't tracking).

Each portfolio_items row also carries optional per-portfolio holding values —
``shares`` and ``market_value`` — that drive the Portfolio Insights
diversification score for the active portfolio. These are independent across
portfolios: GOOGL with 10 shares set in "Holdings" doesn't leak into a
separate "Tech" portfolio.

The first call to GET /portfolios for a user with no rows lazily seeds a
default "Holdings" portfolio populated from their existing watchlist (carrying
over each row's shares / market_value), so the iOS client never has to
special-case the empty state.

Routes:
  GET    /portfolios                         → PortfolioListResponse
  POST   /portfolios                         → PortfolioResponse
  PUT    /portfolios/reorder                 → message
  PUT    /portfolios/{portfolio_id}          → PortfolioResponse
  DELETE /portfolios/{portfolio_id}          → message
  PUT    /portfolios/{portfolio_id}/tickers  → PortfolioResponse  (membership; preserves holdings)
  PUT    /portfolios/{portfolio_id}/holdings → PortfolioResponse  (per-portfolio shares / market_value)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from app.utils.supabase_errors import retry_idempotent_async
from app.database import get_supabase
from app.dependencies import get_watchlist_identity
from app.schemas.tracking import PortfolioInsightsResponse
from app.services.asset_class import canonical_stored_symbol
from app.services.portfolio_insights_service import PortfolioInsightsService
from app.services.tracking_service import invalidate_feed_cache
from app.utils.supabase_errors import is_unique_violation
from app.utils.supabase_async import sb_exec
from app.utils.postgrest_paging import fetch_all_rows
import asyncio
import math

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Schemas ─────────────────────────────────────────────────────────


class PortfolioItemResponse(BaseModel):
    """A ticker inside a portfolio with optional per-portfolio holding values."""

    ticker: str
    shares: Optional[float] = None
    market_value: Optional[float] = None


class PortfolioResponse(BaseModel):
    id: str
    name: str
    sort_order: int
    items: List[PortfolioItemResponse]
    created_at: datetime
    updated_at: datetime
    # Exactly one of the user's portfolios is active (migration 126). It is what Home and
    # Updates follow, so it is server state now — it used to be a device-local UserDefaults
    # string, which is why those two screens could never track the group the user picked.
    is_active: bool = False


class PortfolioListResponse(BaseModel):
    portfolios: List[PortfolioResponse]


class CreatePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class RenamePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class SetTickersRequest(BaseModel):
    tickers: List[str]


class ReorderPortfoliosRequest(BaseModel):
    portfolio_ids: List[str]


class HoldingItem(BaseModel):
    """One row of the per-portfolio holdings bulk-update payload.

    Setting both ``shares`` and ``market_value`` to ``null`` clears the
    holding values for that ticker — the row stays in the portfolio but
    stops counting toward the diversification score.
    """

    ticker: str
    shares: Optional[float] = None
    market_value: Optional[float] = None


class SetPortfolioHoldingsRequest(BaseModel):
    items: List[HoldingItem]


# `portfolio_items.shares` is numeric(20,4) and `market_value` numeric(20,2): 16 and 18
# integer digits. Reject before writing anything — see `set_portfolio_holdings`.
_MAX_SHARES = 1e16
_MAX_MARKET_VALUE = 1e18


# ── Helpers ─────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    return name.strip()


def _row_to_portfolio(row: dict, items: List[PortfolioItemResponse]) -> PortfolioResponse:
    return PortfolioResponse(
        id=str(row["id"]),
        name=row["name"],
        sort_order=int(row.get("sort_order") or 0),
        items=items,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_active=bool(row.get("is_active") or False),
    )


def _fetch_portfolio_items(
    supabase: Client, portfolio_id: str
) -> List[PortfolioItemResponse]:
    # PAGED, like `_fetch_user_portfolios`. This is the body of EVERY write response —
    # PUT /tickers, rename, activate, PUT /holdings — and a single `.order("position")`
    # read is clamped to ~1,000 rows by PostgREST. iOS `PortfolioStore.syncTickers`
    # adopts the PUT response as the truth, so a truncated answer shrank the local list
    # and the next whole-list PUT DELETED the rows the client never saw. Page on the
    # unique id, then order by position in Python.
    rows = fetch_all_rows(
        lambda: supabase.table("portfolio_items")
        .select("id,ticker,position,shares,market_value")
        .eq("portfolio_id", portfolio_id),
        order_by="id",
        what=f"portfolio_items for portfolio={portfolio_id}",
    )
    rows.sort(key=lambda r: int(r.get("position") or 0))
    return [
        PortfolioItemResponse(
            ticker=r["ticker"],
            shares=r.get("shares"),
            market_value=r.get("market_value"),
        )
        for r in rows
    ]


def _read_watchlist_seed_rows(supabase: Client, user_id: str) -> List[Dict[str, Any]]:
    """Every watchlist row for the user, newest first — PAGED past PostgREST's ~1,000-row
    clamp (a truncated read here seeded a group missing the user's oldest tickers, and the
    feed's own truncated read made the client purge them). Paged on the unique id, then
    ordered by `added_at` in Python."""
    rows = fetch_all_rows(
        lambda: supabase.table("watchlist_items")
        .select("id,ticker,added_at,shares,market_value")
        .eq("user_id", user_id),
        order_by="id",
        what=f"watchlist seed rows for user={user_id}",
    )
    rows.sort(key=lambda r: str(r.get("added_at") or ""), reverse=True)
    return rows


def _fetch_user_portfolios(supabase: Client, user_id: str) -> List[PortfolioResponse]:
    """Return all of the user's portfolios with their items, ordered by sort_order."""
    rows = (
        supabase.table("portfolios")
        .select("*")
        .eq("user_id", user_id)
        .order("sort_order")
        .execute()
        .data
        or []
    )
    if not rows:
        return []

    portfolio_ids = [r["id"] for r in rows]
    # PAGED. PostgREST clamps every answer to ~1,000 rows: a user whose groups held more
    # got a silently truncated GET, `PortfolioStore` took it as the truth, and the next
    # `PUT /tickers` (a whole-list replace) DELETED the rows it never saw. Page on the
    # unique id, then order by position in Python — `position` is unique only per group.
    item_rows = fetch_all_rows(
        lambda: supabase.table("portfolio_items")
        .select("id,portfolio_id,ticker,position,shares,market_value")
        .in_("portfolio_id", portfolio_ids),
        order_by="id",
        what=f"portfolio_items for user={user_id}",
    )
    item_rows.sort(key=lambda r: (str(r.get("portfolio_id")), int(r.get("position") or 0)))

    by_portfolio: dict[str, List[PortfolioItemResponse]] = {
        pid: [] for pid in portfolio_ids
    }
    for item in item_rows:
        by_portfolio[item["portfolio_id"]].append(
            PortfolioItemResponse(
                ticker=item["ticker"],
                shares=item.get("shares"),
                market_value=item.get("market_value"),
            )
        )

    return [_row_to_portfolio(r, by_portfolio.get(r["id"], [])) for r in rows]


def _seed_default_portfolio(supabase: Client, user_id: str) -> None:
    """Create a "Holdings" portfolio populated from the user's watchlist.

    Carries over each watchlist row's ``shares`` / ``market_value`` so a user
    who already filled in Insights values doesn't lose them on the first call
    after migration 038.
    """
    seed_rows = _read_watchlist_seed_rows(supabase, user_id)

    # Seeded ACTIVE: this is the user's only group, so it is by definition the one Home,
    # Updates and Tracking should follow. Leaving it inactive would leave every surface
    # falling back until the user happened to open Tracking and pick one.
    try:
        inserted = (
            supabase.table("portfolios")
            .insert(
                {
                    "user_id": user_id,
                    "name": "Holdings",
                    "sort_order": 0,
                    "is_active": True,
                }
            )
            .execute()
            .data
        )
    except Exception as e:
        # Only a UNIQUE violation is survivable here. Anything else re-raises —
        # this must not become a generic error-swallower.
        if not is_unique_violation(e):
            raise
        inserted = None

    if not inserted:
        # RACE: someone else created "Holdings" for this user between the
        # zero-row `_fetch_user_portfolios` above and this insert. Two real
        # producers, both observed in production:
        #   * a concurrent GET /portfolios — ContentView mounts every tab at
        #     launch, so TrackingViewModel.init and the Home fetch both hit this
        #     route (the same window `_backfill_lone_empty_portfolio` documents);
        #   * POST /users/me/claim-guest-data moving the guest's "Holdings" over.
        # Adopting the winner beats 500-ing GET /api/v1/portfolios, which is what
        # the bare `.data[0]` used to do.
        existing = (
            supabase.table("portfolios")
            .select("id")
            .eq("user_id", user_id)
            .eq("name", "Holdings")
            .limit(1)
            .execute()
            .data
            or []
        )
        if not existing:
            # The 23505 was on some OTHER constraint — do NOT swallow it.
            raise RuntimeError(
                f"portfolios: 'Holdings' insert hit a unique violation for "
                f"user={user_id} but no such row exists — the constraint was not "
                f"portfolios_user_id_name_key"
            )
        logger.info(
            "portfolios: concurrent seed for user=%s — adopting the existing "
            "'Holdings' group; items are left to the winner",
            user_id,
        )
        # Deliberately NOT falling through to the item insert: those rows would
        # collide on portfolio_items_portfolio_id_ticker_key against whatever the
        # winner wrote. If the winner seeded an EMPTY group (it read the watchlist
        # before onboarding wrote to it), `_backfill_lone_empty_portfolio` repairs
        # it on the next GET /portfolios — that heal already exists, so there is
        # no second repair path to maintain here.
        return

    portfolio_row = inserted[0]

    if seed_rows:
        item_rows = [
            {
                "portfolio_id": portfolio_row["id"],
                "ticker": (r["ticker"] or "").upper(),
                "position": i,
                "shares": r.get("shares"),
                "market_value": r.get("market_value"),
            }
            for i, r in enumerate(seed_rows)
            if r.get("ticker")
        ]
        if item_rows:
            # ON CONFLICT DO NOTHING — the same call `_backfill_lone_empty_portfolio` makes,
            # and NOT a plain `.insert`. The group above is inserted ACTIVE and is visible
            # to `POST /watchlist` the moment it lands, so a star tapped on a detail screen
            # in the same second is mirrored into it by `_write_through_to_active_portfolio`
            # before this line runs. A plain multi-row INSERT then hit
            # `portfolio_items_portfolio_id_ticker_key` on the mirrored ticker, the WHOLE
            # statement was rejected, `GET /portfolios` answered 500 — and on the next GET
            # the group held only the mirrored row, so `_backfill_lone_empty_portfolio`
            # (`if only.items: return`) never healed the rest: every other watchlist ticker
            # stayed invisible on Tracking, Home and Updates until re-added by hand.
            # DO NOTHING also tolerates an intra-batch duplicate (two watchlist spellings
            # collapsing under `.upper()`), which DO UPDATE would not.
            supabase.table("portfolio_items").upsert(
                item_rows, on_conflict="portfolio_id,ticker", ignore_duplicates=True
            ).execute()


#: Slack between a row's `created_at` and `updated_at` at INSERT time. Both default to
#: `now()` in the same statement so they are normally identical, but they are two separate
#: evaluations and a dump/restore can round them differently; a second is far below any
#: human edit and far above that noise.
_NEVER_EDITED_SLACK_SECONDS = 2.0


def _has_been_edited(p: "PortfolioResponse") -> bool:
    """True when the user has saved this group at least once since it was created."""
    # `getattr`, not attribute access: this probe sits on the `GET /portfolios` path
    # BEFORE its try/except, so a shape without the timestamps would 500 the list rather
    # than skip a best-effort heal.
    created = getattr(p, "created_at", None)
    updated = getattr(p, "updated_at", None)
    if created is None or updated is None:
        # Unknown provenance: treat as edited, i.e. do NOT touch the user's data.
        return True
    try:
        return (updated - created).total_seconds() > _NEVER_EDITED_SLACK_SECONDS
    except TypeError:
        # One side naive, one aware — cannot compare. Fail toward leaving data alone.
        return True


def _backfill_lone_empty_portfolio(supabase: Client, user_id: str, portfolios):
    """Repair the one-shot-seed race: a lone EMPTY portfolio next to a non-empty watchlist.

    `_seed_default_portfolio` populates "Holdings" from the watchlist, but it runs on the FIRST
    `GET /portfolios` — and `ContentView` mounts every tab at launch, so `TrackingViewModel.init`
    fires that GET seconds before first-run onboarding writes a single ticker. The seed therefore
    reads an empty watchlist, creates the portfolio with zero items, and the `if not portfolios:`
    guard means it can never run again. Nothing else reconciles the two tables, so the user ends
    up with tickers on their watchlist (visible in Updates) and a permanently empty Assets tab —
    exactly the "No tickers yet" state, unfixable except by re-adding each ticker by hand.

    Deliberately NARROW: only when the user has EXACTLY ONE portfolio, it holds zero items,
    AND it has never been edited.

    ⚠️ That last condition is load-bearing, and this docstring used to assert the opposite
    ("the only way to empty your lone portfolio is to remove every ticker, which removes it
    from the watchlist too"). It does not: a group is a SUBSET of the watchlist by design,
    `set_portfolio_tickers` only ever writes `portfolio_items`, and iOS documents the same
    ("removes the ticker from the active portfolio only. The master watchlist is
    untouched"). So a user with one group who swipe-deletes every ticker reaches exactly
    this state ON PURPOSE — and an unconditional heal put all of them back on the next cold
    start, every time, which is the symptom this repair claims to have closed.

    `updated_at > created_at` is the honest signal for "the user has saved this group at
    least once": `_seed_default_portfolio` inserts with both defaulted to `now()`, and every
    mutation path (`PUT /tickers`, rename, reorder, activate) bumps `updated_at`. A group
    that was never touched is the one-shot-seed race; a group the user emptied is not.
    """
    if len(portfolios) != 1:
        return portfolios
    only = portfolios[0]
    # `only.items` — NOT `getattr(only, "tickers", None)`, which this read as until
    # 2026-09-12. `PortfolioResponse` has no `tickers` attribute, so the guard evaluated
    # None on EVERY call and the "deliberately narrow, only when it holds zero items"
    # contract above was never enforced: every `GET /portfolios` for a single-group user
    # re-seeded the group from `watchlist_items`. Because a group is a SUBSET of the
    # watchlist by design, removing a ticker from the group leaves its watchlist row
    # alone — so the next launch silently put it back, and the user's removal looked like
    # it had never happened. A plain attribute access is also what keeps this honest: a
    # rename now breaks the build instead of silently disarming the guard.
    if only.items:
        return portfolios
    if _has_been_edited(only):
        # Emptied on purpose. Re-seeding here silently reverses the user's own removal on
        # every launch — see the docstring.
        return portfolios

    try:
        seed_rows = _read_watchlist_seed_rows(supabase, user_id)
        item_rows = [
            {
                "portfolio_id": only.id,
                "ticker": (r["ticker"] or "").upper(),
                "position": i,
                "shares": r.get("shares"),
                "market_value": r.get("market_value"),
            }
            for i, r in enumerate(seed_rows)
            if r.get("ticker")
        ]
        if not item_rows:
            return portfolios
        supabase.table("portfolio_items").upsert(
            item_rows, on_conflict="portfolio_id,ticker", ignore_duplicates=True
        ).execute()
        logger.info(
            "Backfilled %d watchlist ticker(s) into empty portfolio %s for user=%s",
            len(item_rows), only.id, user_id,
        )
        return _fetch_user_portfolios(supabase, user_id)
    except Exception as e:
        # Best-effort repair: never fail the list because the heal didn't work.
        logger.warning(
            "Portfolio backfill failed for user=%s (%s: %s) — serving portfolios as-is",
            user_id, type(e).__name__, e,
        )
        return portfolios


def _get_portfolio_or_404(supabase: Client, user_id: str, portfolio_id: str) -> dict:
    """Fetch a portfolio row scoped to user_id; raise 404 if missing."""
    result = (
        supabase.table("portfolios")
        .select("*")
        .eq("user_id", user_id)
        .eq("id", portfolio_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return result.data[0]


def _name_taken(
    supabase: Client, user_id: str, name: str, exclude_id: Optional[str] = None
) -> bool:
    """Case-insensitive name conflict check within the user's portfolios."""
    rows = (
        supabase.table("portfolios")
        .select("id,name")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    target = name.casefold()
    for row in rows:
        if row.get("name", "").casefold() == target and row["id"] != exclude_id:
            return True
    return False


def _ensure_active_portfolio(supabase: Client, user_id: str) -> Optional[str]:
    """Guarantee the user has exactly one active group; return its id.

    Delegates to the `ensure_active_portfolio` RPC (migration 126) rather than doing a
    read-then-write here: the promotion has to be atomic, and PostgREST cannot express
    "activate the first one only if none is active" in a single statement. The RPC is a
    no-op when a group is already active, so calling it on the common path is cheap.

    Best-effort by design. Home and Updates both degrade honestly when no group is
    active, so a failed heal must not fail the request that noticed it — but it is
    logged, because a persistently unhealed user silently loses the group-aware
    behaviour on two screens.
    """
    try:
        result = supabase.rpc(
            "ensure_active_portfolio", {"p_user_id": user_id}
        ).execute()
        return str(result.data) if result.data else None
    except Exception as e:
        logger.warning(
            "ensure_active_portfolio failed for user=%s (%s: %s) — "
            "Home/Updates will fall back to the master watchlist",
            user_id, type(e).__name__, e,
        )
        return None


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=PortfolioListResponse)
async def list_portfolios(
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    """List the user's portfolios (with items + per-portfolio holdings).

    Lazy-seeds a default "Holdings" portfolio on first call so the iOS client
    never has to special-case the empty state.
    """
    portfolios = (await asyncio.to_thread(_fetch_user_portfolios, supabase, user["id"]))
    if not portfolios:
        (await asyncio.to_thread(_seed_default_portfolio, supabase, user["id"]))
        portfolios = (await asyncio.to_thread(_fetch_user_portfolios, supabase, user["id"]))
    else:
        portfolios = (await asyncio.to_thread(_backfill_lone_empty_portfolio, supabase, user["id"], portfolios))

    # Heal a user with groups but none active: every row predating migration 126's backfill,
    # anything the backfill missed, and the window after a delete whose heal failed. This is
    # the one endpoint every client calls on launch, so it is the natural repair point — and
    # the check is local (no round-trip) on the overwhelmingly common healthy path.
    if portfolios and not any(p.is_active for p in portfolios):
        if (await asyncio.to_thread(_ensure_active_portfolio, supabase, user["id"])):
            portfolios = (await asyncio.to_thread(_fetch_user_portfolios, supabase, user["id"]))

    return PortfolioListResponse(portfolios=portfolios)


@router.post("", response_model=PortfolioResponse)
async def create_portfolio(
    request: CreatePortfolioRequest,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    name = _normalize_name(request.name)
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    if (await asyncio.to_thread(_name_taken, supabase, user["id"], name)):
        raise HTTPException(
            status_code=409, detail=f'A portfolio named "{name}" already exists.'
        )

    # New portfolio appends at the end of the user's existing list.
    existing = (
        (await sb_exec(
            supabase.table("portfolios")
            .select("sort_order")
            .eq("user_id", user["id"])
            .order("sort_order", desc=True)
            .limit(1)
        ))
        .data
        or []
    )
    next_order = (existing[0]["sort_order"] + 1) if existing else 0

    # Creating a group does NOT switch to it — that would move Home and Updates out from
    # under the user as a side effect of an action that only said "make a new list", and
    # the new group is empty, so all three surfaces would go blank. The client activates
    # explicitly when the user picks it. The sole exception is the very first group, which
    # has to be active or the user has none.
    row = (
        (await sb_exec(
            supabase.table("portfolios")
            .insert(
            {
            "user_id": user["id"],
            "name": name,
            "sort_order": next_order,
            "is_active": False,
            }
            )
        ))
        .data[0]
    )

    # Insert-then-heal rather than `is_active: not existing` on the insert itself. Two
    # concurrent first-creates would BOTH compute `not existing == True` and collide on
    # idx_portfolios_one_active_per_user — a 500 on a plain "New Portfolio" tap. Unlike
    # the seed path, distinct names mean the unique-name constraint does not serialise
    # them. The RPC is idempotent and no-ops when another caller already claimed it.
    if not existing:
        if (await asyncio.to_thread(_ensure_active_portfolio, supabase, user["id"])) == str(row["id"]):
            row["is_active"] = True

    return _row_to_portfolio(row, [])


@router.put("/reorder")
async def reorder_portfolios(
    request: ReorderPortfoliosRequest,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    """Bulk-update sort_order from the order of the supplied portfolio_ids."""
    rows = (
        (await sb_exec(
            supabase.table("portfolios")
            .select("id")
            .eq("user_id", user["id"])
        ))
        .data
        or []
    )
    owned_ids = {r["id"] for r in rows}
    for pid in request.portfolio_ids:
        if pid not in owned_ids:
            raise HTTPException(
                status_code=404, detail=f"Portfolio {pid} not found"
            )

    now = datetime.utcnow().isoformat()
    for index, pid in enumerate(request.portfolio_ids):
        (await sb_exec(
            supabase.table("portfolios").update(
            {"sort_order": index, "updated_at": now}
            ).eq("user_id", user["id"]).eq("id", pid)
        ))

    return {"message": "Reordered", "count": len(request.portfolio_ids)}


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
async def rename_portfolio(
    portfolio_id: str,
    request: RenamePortfolioRequest,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    name = _normalize_name(request.name)
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))
    if (await asyncio.to_thread(_name_taken, supabase, user["id"], name, exclude_id=portfolio_id)):
        raise HTTPException(
            status_code=409, detail=f'A portfolio named "{name}" already exists.'
        )

    row = (
        (await sb_exec(
            supabase.table("portfolios")
            .update({"name": name, "updated_at": datetime.utcnow().isoformat()})
            .eq("user_id", user["id"])
            .eq("id", portfolio_id)
        ))
        .data[0]
    )

    items = (await asyncio.to_thread(_fetch_portfolio_items, supabase, portfolio_id))
    return _row_to_portfolio(row, items)


@router.delete("/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: str,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))

    # Don't let the user delete their last portfolio — leaves them with no
    # active context. The iOS UI hides the destructive button in that state,
    # but we backstop it here too.
    other_count = (
        (await sb_exec(
            supabase.table("portfolios")
            .select("id", count="exact")
            .eq("user_id", user["id"])
            .neq("id", portfolio_id)
        ))
        .count
        or 0
    )
    if other_count == 0:
        raise HTTPException(
            status_code=409, detail="Cannot delete your only portfolio."
        )

    (await sb_exec(
        supabase.table("portfolios").delete().eq("user_id", user["id"]).eq(
        "id", portfolio_id
        )
    ))

    # Deleting the ACTIVE group would otherwise leave the user with none, and Home plus
    # Updates would silently fall back to the whole master watchlist under a stale label.
    # Promote a survivor immediately. Unconditional because it is a cheap no-op when the
    # deleted group was not the active one.
    (await asyncio.to_thread(_ensure_active_portfolio, supabase, user["id"]))
    invalidate_feed_cache(user["id"])
    return {"message": "Portfolio deleted"}


@router.put("/{portfolio_id}/activate", response_model=PortfolioResponse)
async def activate_portfolio(
    portfolio_id: str,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    """Make this the user's active group — the one Home, Updates and Tracking follow.

    Server-side because it has to be: the selection used to live in a device-local
    `UserDefaults` string, so the backend could not make the other two screens follow it,
    and switching groups on one device did not follow the user to another.
    """
    (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))

    try:
        switched = (await sb_exec(
                       supabase.rpc(
                       "set_active_portfolio",
                       {"p_user_id": user["id"], "p_portfolio_id": portfolio_id},
                       )
                   )).data
    except Exception as e:
        logger.error(
            "set_active_portfolio failed for user=%s portfolio=%s: %s: %s",
            user["id"], portfolio_id, type(e).__name__, e, exc_info=True,
        )
        raise HTTPException(
            status_code=503, detail="Could not switch groups. Please try again."
        )

    if not switched:
        # The RPC re-checks ownership under a row lock, so this is the narrow window where
        # the portfolio was deleted between the 404 check above and the switch.
        raise HTTPException(status_code=404, detail="Portfolio not found")

    # Home reads the group directly, but Tracking's assets feed is cached per user for 30s
    # and is scoped by the active group — without this the user switches groups and the
    # Assets tab keeps showing the previous one until the TTL lapses.
    invalidate_feed_cache(user["id"])

    row = (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))
    return _row_to_portfolio(row, (await asyncio.to_thread(_fetch_portfolio_items, supabase, portfolio_id)))


@router.put("/{portfolio_id}/tickers", response_model=PortfolioResponse)
async def set_portfolio_tickers(
    portfolio_id: str,
    request: SetTickersRequest,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    """Replace the portfolio's ticker membership.

    Tickers must already exist on the user's watchlist; unknown ones are
    silently dropped (the iOS Add Asset flow always pushes the ticker to the
    master watchlist before calling this endpoint, so this is a defensive
    skip rather than a normal path).

    Per-portfolio holding values (``shares`` / ``market_value``) are
    PRESERVED for tickers that remain in the portfolio after the swap; new
    tickers come in with no holdings; removed tickers lose theirs.
    """
    (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))

    # Dedupe + uppercase while preserving order.
    seen: set[str] = set()
    requested: List[str] = []
    for raw in request.tickers:
        symbol = (raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        requested.append(symbol)

    # Restrict to tickers already on the master watchlist — matching RAW first, then the
    # CANONICAL spelling, never both at once.
    #
    # 🔴 This used to match the raw spelling only, and the delete+reinsert below is
    # DESTRUCTIVE. After migration 160 a coin lives on the watchlist as "BTCUSD"; a
    # client still saying "BTC" for it (stale local state, the shipped build) matched
    # nothing, so the position was dropped — with its hand-entered `shares` — and the
    # response said success. Raw wins when it exists (a bare row is the listed security,
    # e.g. the Grayscale ETF); the pair is the fallback for the coin. `DELETE /watchlist`
    # and `PUT /tracking/holdings/{ticker}` follow the same rule.
    accepted: List[str] = []
    if requested:
        lookup: List[str] = []
        for sym in requested:
            for cand in (sym, canonical_stored_symbol(sym, None)):
                if cand not in lookup:
                    lookup.append(cand)
        # The user's WHOLE watchlist, paged — not `.in_("ticker", lookup)`. That single
        # read was clamped to ~1,000 rows by PostgREST, so with >1,000 valid tickers the
        # tail was logged as "dropped (on neither spelling)" and REMOVED from the group by
        # the delete+reinsert below, on the first PUT, with a 200. (It also put an
        # ~8.5 KB `in.()` list on the query string for 1,051 tickers.) Filtering in Python
        # against the paged seed read costs the same one request for a normal watchlist.
        wanted = set(lookup)
        watchlist = [
            row for row in (await asyncio.to_thread(
                _read_watchlist_seed_rows, supabase, user["id"]
            ))
            if str(row.get("ticker") or "").upper() in wanted
        ]
        valid = {row["ticker"].upper() for row in watchlist if row.get("ticker")}
        dropped: List[str] = []
        for sym in requested:
            canon = canonical_stored_symbol(sym, None)
            chosen = sym if sym in valid else (canon if canon != sym and canon in valid else None)
            if chosen is None:
                dropped.append(sym)
            elif chosen not in accepted:
                # `["BTC", "BTCUSD"]` with only the pair stored must not insert it twice
                # (unique (portfolio_id, ticker)).
                accepted.append(chosen)
        if dropped:
            logger.warning(
                "[Portfolios] PUT tickers dropped %s (on neither spelling of the watchlist) "
                "user=%s portfolio=%s", dropped, user["id"], portfolio_id,
            )

    # Capture existing holdings so kept tickers don't lose shares /
    # market_value when we delete + reinsert below.
    #
    # PAGED. This snapshot is the ONLY carrier of hand-entered `shares` / `market_value`
    # across the delete+reinsert, and a bare `.select().eq()` returned an arbitrary
    # ~1,000 of a larger group's rows: every kept ticker past the clamp was reinserted
    # with `shares: None`, silently, with a 200.
    existing_items = await asyncio.to_thread(
        fetch_all_rows,
        lambda: supabase.table("portfolio_items")
        .select("id,ticker,shares,market_value")
        .eq("portfolio_id", portfolio_id),
        order_by="id",
        what=f"portfolio_items snapshot portfolio={portfolio_id}",
    )
    existing_holdings = {
        (item["ticker"] or "").upper(): {
            "shares": item.get("shares"),
            "market_value": item.get("market_value"),
        }
        for item in existing_items
    }

    # DELETE + INSERT as ONE replayable unit.
    #
    # These are two independent PostgREST statements with no transaction between them, and
    # `rows` is the only carrier of the user's hand-entered `shares` / `market_value` for
    # the tickers being KEPT. If the INSERT failed after the DELETE committed — a Supabase
    # 520 edge page (`project_supabase_transient_520`), or a 23505 because a concurrent
    # watchlist write-through slipped a row into the now-empty group — the group was left
    # EMPTY and those holdings were gone for good: the client's retry re-reads
    # `existing_items` (now []) and restores membership with every `shares` NULL, so the
    # loss looks like "you never entered them".
    #
    # `retry_idempotent_async` takes a callable for exactly this shape; its own docstring
    # names it ("wrapping a delete-then-insert sequence forces the whole block, including
    # its leading DELETE, into one replayable unit"). Re-running the block is safe: the
    # DELETE is idempotent and the INSERT rebuilds the same rows from the snapshot taken
    # BEFORE the block, which is why `existing_holdings` is captured outside it.
    def _replace_items() -> None:
        supabase.table("portfolio_items").delete().eq(
            "portfolio_id", portfolio_id
        ).execute()
        if accepted:
            rows = []
            for i, t in enumerate(accepted):
                prior = existing_holdings.get(t, {})
                rows.append(
                    {
                        "portfolio_id": portfolio_id,
                        "ticker": t,
                        "position": i,
                        "shares": prior.get("shares"),
                        "market_value": prior.get("market_value"),
                    }
                )
            # UPSERT (merge on the group's unique key), not INSERT. The comment above
            # names a concurrent watchlist write-through slipping a row into the group as
            # one trigger of the replay — but a 23505 is a deterministic PostgREST error,
            # so `retry_idempotent_async` deliberately does NOT retry it: the block raised
            # once and the group stayed EMPTY (F15-8). Merge-duplicates makes the
            # snapshot's shares/position win over the slipped row without raising.
            supabase.table("portfolio_items").upsert(
                rows, on_conflict="portfolio_id,ticker",
            ).execute()

    try:
        # `retry_idempotent_async`, NOT the sync twin. `set_portfolio_tickers` is
        # `async def`, and the sync form's backoff is a bare `time.sleep` in the
        # coroutine's own frame: three attempts is ~0.75 s of FROZEN event loop plus up to
        # six serialised blocking PostgREST round trips, on the single Railway uvicorn
        # worker — worst exactly when Supabase is degraded and the most requests are
        # queued. The async form runs the same sync callable via `asyncio.to_thread` with
        # the same idempotency contract; its own docstring names this as the reason it
        # exists. ⚠️ Note `is_transient_supabase_error` deliberately EXCLUDES 23505, so a
        # duplicate-key collision is re-raised on attempt 1 rather than retried — the
        # error log below is what surfaces it.
        await retry_idempotent_async(
            _replace_items,
            what=f"portfolio_items replace portfolio={portfolio_id}",
            logger=logger,
        )
    except Exception as exc:
        # The group may now be EMPTY on disk while the client still shows the old list.
        # Say so loudly with the ids — this is a data-loss window, not a failed read.
        logger.error(
            "[Portfolios] PUT tickers failed to replace items for portfolio=%s user=%s "
            "(%s: %s) — the group may be empty and per-ticker holdings lost",
            portfolio_id, user["id"], type(exc).__name__, exc, exc_info=True,
        )
        raise

    (await sb_exec(
        supabase.table("portfolios").update(
        {"updated_at": datetime.utcnow().isoformat()}
        ).eq("id", portfolio_id)
    ))

    # Membership changed → the cached Assets feed is stale. Left alone, the next
    # refresh reads pre-write state and the client's orphan purge acts on it.
    invalidate_feed_cache(user["id"])

    refreshed = (
        (await sb_exec(
            supabase.table("portfolios")
            .select("*")
            .eq("id", portfolio_id)
        ))
        .data[0]
    )
    items = (await asyncio.to_thread(_fetch_portfolio_items, supabase, portfolio_id))
    return _row_to_portfolio(refreshed, items)


@router.put("/{portfolio_id}/holdings", response_model=PortfolioResponse)
async def set_portfolio_holdings(
    portfolio_id: str,
    request: SetPortfolioHoldingsRequest,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    """Bulk-update shares / market_value for tickers within a portfolio.

    Used by the iOS Portfolio Insights config sheet, which now scopes
    holdings per portfolio (rather than the older watchlist-global flow).
    Only updates tickers already in this portfolio (others are silently
    ignored — the sheet should never send those). Setting both fields to
    ``null`` clears that ticker's holding values: it stays in the portfolio
    but stops counting toward the diversification score.
    """
    (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))

    # VALIDATE EVERYTHING BEFORE WRITING ANYTHING.
    #
    # This used to validate and write in ONE loop, then raise 400 at the end — so a payload
    # with one bad row persisted every good row that preceded it and still answered a
    # failure. iOS routes a non-2xx through `reportMutationFailure` and reverts its
    # optimistic UI, so the user saw their edit undone while the server kept half of it,
    # and the two only disagreed until something forced a refetch. A 400 must mean nothing
    # happened.
    # Sign, FINITENESS and MAGNITUDE. The columns are numeric(20,4) / numeric(20,2), so a
    # value at or past 1e16 shares / 1e18 dollars raises 22003 on THAT row's UPDATE after
    # the earlier rows were already written — exactly the half-persisted state the loop
    # above this comment was split to prevent. NaN/inf would do the same (or 500 first).
    errors: List[str] = []
    for item in request.items:
        ticker = item.ticker.upper()
        for label, value, ceiling in (
            ("shares", item.shares, _MAX_SHARES),
            ("market_value", item.market_value, _MAX_MARKET_VALUE),
        ):
            if value is None:
                continue
            if not math.isfinite(value):
                errors.append(f"{ticker}: {label} must be a finite number")
            elif value < 0:
                errors.append(f"{ticker}: {label} cannot be negative")
            elif value >= ceiling:
                errors.append(f"{ticker}: {label} is too large")
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    # ONE replayable unit for the whole loop, like `_replace_items` above (F15-7): each row
    # was its own UPDATE with no retry, so a transient 5xx on row 3 of 5 answered a failure
    # after rows 1-2 had committed — iOS reverted its optimistic sheet while the server kept
    # half the edit. Every UPDATE here sets absolute values, so replaying the loop from the
    # top is idempotent. Runs off the loop; `.execute()` directly (no `sb_exec` inside a
    # thread).
    def _write_holdings() -> None:
        for item in request.items:
            # Raw first, then the canonical spelling — the row for a coin is "BTCUSD", and
            # a client sending "BTC" for it used to update 0 rows and report success.
            raw_ticker = item.ticker.upper()
            canonical = canonical_stored_symbol(raw_ticker, None)
            values = {"shares": item.shares, "market_value": item.market_value}
            result = (
                supabase.table("portfolio_items")
                .update(values)
                .eq("portfolio_id", portfolio_id)
                .eq("ticker", raw_ticker)
                .execute()
            )
            if not result.data and canonical != raw_ticker:
                (
                    supabase.table("portfolio_items")
                    .update(values)
                    .eq("portfolio_id", portfolio_id)
                    .eq("ticker", canonical)
                    .execute()
                )

    await retry_idempotent_async(
        _write_holdings, what=f"holdings for portfolio={portfolio_id}",
    )

    (await sb_exec(
        supabase.table("portfolios").update(
        {"updated_at": datetime.utcnow().isoformat()}
        ).eq("id", portfolio_id)
    ))

    refreshed = (
        (await sb_exec(
            supabase.table("portfolios")
            .select("*")
            .eq("id", portfolio_id)
        ))
        .data[0]
    )
    items = (await asyncio.to_thread(_fetch_portfolio_items, supabase, portfolio_id))
    return _row_to_portfolio(refreshed, items)


@router.get(
    "/{portfolio_id}/insights",
    response_model=Optional[PortfolioInsightsResponse],
)
async def get_portfolio_insights(
    portfolio_id: str,
    user: dict = Depends(get_watchlist_identity),
    supabase: Client = Depends(get_supabase),
):
    """Server-computed Portfolio Insights for ONE portfolio — the 0..100
    diversification health score, sub-scores and breakdown allocations (no
    nudges: ``message`` is a neutral descriptor; any hint is derived on the
    client). Scores this portfolio's ``portfolio_items`` holdings joined with
    the metadata on the user's watchlist rows. Returns ``null`` when the
    portfolio has fewer than the minimum holdings for a meaningful score.
    """
    (await asyncio.to_thread(_get_portfolio_or_404, supabase, user["id"], portfolio_id))
    service = PortfolioInsightsService()
    return await service.compute_insights_for_portfolio(user["id"], portfolio_id)
