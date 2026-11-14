"""User-set price alerts.

`.signInRequired` on all four routes, and that is a product decision rather than a
technical one: `device_tokens` is FK-bound to `public.users` and auth-only, so push can
never reach a guest. A guest-owned alert would be a rule that can never fire — worse
than no feature, because it looks like one.

⚠️ EVERY read and write is scoped `.eq("user_id", ...)` in the service layer. The
backend holds the service-role key, so RLS is defence in depth and that in-code filter
is the effective wall (SYSTEM_DESIGN_GUIDELINES §9). Filtering PATCH/DELETE on `id`
alone would be a textbook IDOR.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends

from app.api.error_response import ErrorCode, make_error_response, ticker_delisted_error
from app.config import settings
from app.dependencies import get_current_user
from app.integrations.fmp import get_fmp_client
from app.schemas.price_alerts import (
    CreatePriceAlertRequest,
    DeletePriceAlertResponse,
    PriceAlertListResponse,
    PriceAlertResponse,
    UpdatePriceAlertRequest,
    price_alert_from_row,
)
from app.services.price_alert_engine import finite_price
from app.services.price_alert_service import (
    PriceAlertInvalid,
    PriceAlertLimitReached,
    PriceAlertUnavailable,
    get_price_alert_service,
)
from app.services.asset_class import canonical_stored_symbol
from app.services.listing_status_service import (
    PAID_DOOR_WAIT,
    REQUEST_WAIT,
    get_delisted,
    get_delisted_for_symbols,
    peek_delisted,
)
from app.services.price_service import price_source

logger = logging.getLogger(__name__)

router = APIRouter()


def _unavailable(e: Exception):
    return make_error_response(
        ErrorCode.NOTIFICATIONS_UNAVAILABLE,
        message=str(e),
        user_message="We couldn't reach your price alerts right now. Please try again.",
    )


@router.get("", response_model=PriceAlertListResponse)
async def list_price_alerts(
    ticker: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """This user's price alerts, newest first. `ticker` filters to one symbol so the
    detail-screen bell can show its own rules without pulling the whole list."""
    try:
        rows = await asyncio.to_thread(
            get_price_alert_service().list_for_user, user["id"], ticker
        )
    except PriceAlertUnavailable as e:
        return _unavailable(e)
    # A rule on a CONFIRMED delisting is paused by the evaluation loop (no quote, no state
    # write); say so on the row, so the sheet can show "Paused: delisted" (Phase 2, owner
    # 2026-11-13). Free for every ticker that traded in the latest session; bounded by the
    # screens' REQUEST_WAIT otherwise. Unknown → the row reads as before. Nothing persisted.
    listings = await get_delisted_for_symbols(
        [str(r.get("ticker") or "") for r in rows if isinstance(r, dict)],
        wait=REQUEST_WAIT, purpose="the price-alert list", unknown_level=logging.DEBUG,
    )
    return PriceAlertListResponse(
        items=[price_alert_from_row(r, listing=_wire_for(r, listings)) for r in rows],
        max_per_user=settings.PRICE_ALERT_MAX_PER_USER,
        max_per_ticker=settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER,
    )


def _wire_for(row: dict, listings: dict):
    listing = listings.get(str((row or {}).get("ticker") or "").strip().upper())
    return listing.wire() if listing is not None else None


@router.post("", response_model=PriceAlertResponse)
async def create_price_alert(
    request: CreatePriceAlertRequest,
    user: dict = Depends(get_current_user),
):
    """Create one rule.

    Seeds `last_price` from a LIVE quote at creation time. That is load-bearing: with no
    baseline the engine seeds on its first cycle and stays silent, so an alert set at
    "above $250" on a stock already trading at $260 would sit inert until the price dipped
    below $250 and came back — correct, but inexplicable from the user's side. Seeding
    here makes the state deterministic from the first cycle.

    A quote failure is NOT fatal: the alert is created with a NULL baseline and the engine
    seeds it on the next cycle. Refusing to create an alert because a quote call blipped
    would be a much worse trade.
    """
    # Canonicalise the ticker before the seed quote AND before the row is written, using
    # the asset_type the client already sends. A bare coin ticker is ambiguous — "BTC" is
    # both Bitcoin and the Grayscale ETF — so an alert stored bare would be evaluated by
    # the sweeper against whichever the price routing picked, not the one the user was
    # looking at. See `asset_class.canonical_stored_symbol`.
    _raw_ticker = request.ticker
    request.ticker = canonical_stored_symbol(request.ticker, request.asset_type)
    if request.ticker != str(_raw_ticker or "").upper().strip():
        logger.info(
            "price alerts: normalised %s -> %s (asset_type=%s)",
            _raw_ticker, request.ticker, request.asset_type,
        )
    seed = None
    seed_quote = None
    try:
        quotes = await price_source().get_quotes_list([request.ticker.upper()])
        for q in quotes or []:
            if str(q.get("symbol") or "").upper() == request.ticker.upper():
                seed = finite_price(q.get("price"))
                seed_quote = q
                break
    except Exception as e:
        logger.warning(
            "price alerts: seed quote for %s failed (%s: %s) — creating with no "
            "baseline; the engine will seed on its next cycle",
            request.ticker, type(e).__name__, e,
        )

    # A CONFIRMED delisting has no live price to watch: refuse the new rule (Phase 2, owner
    # 2026-11-13). Decided from the seed quote already in hand (its inactive flag), so a ticker
    # that traded in the latest session costs nothing; with no quote, only a delisting already
    # held in memory refuses. Unknown, or not decided within PAID_DOOR_WAIT → created as before.
    ticker_key = request.ticker.strip().upper()
    if seed_quote is not None:
        listing = await get_delisted(ticker_key, quote=seed_quote, wait=PAID_DOOR_WAIT)
    else:
        listing = peek_delisted(ticker_key)
    if listing is not None:
        logger.info(
            "price alerts: create refused for user=%s — %s is a confirmed delisting (%s, %s)",
            user.get("id"), ticker_key, listing.exchange, listing.delisted_date.isoformat(),
        )
        return ticker_delisted_error(
            ticker=ticker_key, delisted_date=listing.delisted_date,
            exchange=listing.exchange, purpose="price_alert",
        )

    try:
        row = await asyncio.to_thread(
            get_price_alert_service().create,
            user["id"],
            ticker=request.ticker,
            kind=request.kind,
            threshold=request.threshold,
            asset_type=request.asset_type,
            repeat_mode=request.repeat_mode,
            note=request.note,
            seed_price=seed,
        )
    except PriceAlertLimitReached as e:
        # 409 + fix_input: the request was well-formed and the ACCOUNT is the conflict,
        # so the client action is "remove one", not "correct what you typed".
        return make_error_response(
            ErrorCode.PRICE_ALERT_LIMIT_REACHED,
            message=str(e),
            user_message=str(e),
        )
    except PriceAlertInvalid as e:
        return make_error_response(
            ErrorCode.INVALID_INPUT, message=str(e), user_message=str(e)
        )
    except PriceAlertUnavailable as e:
        return _unavailable(e)
    return price_alert_from_row(row)


@router.patch("/{alert_id}", response_model=PriceAlertResponse)
async def update_price_alert(
    alert_id: str,
    request: UpdatePriceAlertRequest,
    user: dict = Depends(get_current_user),
):
    """Edit a rule. Changing the threshold RE-ARMS it and clears the baseline — the old
    reading was measured against a different line."""
    patch = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        row = await asyncio.to_thread(
            get_price_alert_service().update, user["id"], alert_id, patch
        )
    except PriceAlertInvalid as e:
        return make_error_response(
            ErrorCode.INVALID_INPUT, message=str(e), user_message=str(e)
        )
    except PriceAlertUnavailable as e:
        return _unavailable(e)
    if row is None:
        # Not "forbidden": a 403 would confirm the id exists and belongs to someone else.
        return make_error_response(
            ErrorCode.PRICE_ALERT_NOT_FOUND,
            message=f"price alert {alert_id} not found for this user",
            user_message="That alert no longer exists.",
        )
    # Keep the "Paused: delisted" caption on an edited row (memory only, no I/O): the sheet
    # replaces the row with this answer.
    held = peek_delisted(str(row.get("ticker") or ""))
    return price_alert_from_row(row, listing=held.wire() if held is not None else None)


@router.delete("/{alert_id}", response_model=DeletePriceAlertResponse)
async def delete_price_alert(
    alert_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete a rule. Idempotent — deleting an already-gone alert answers deleted=false
    rather than erroring, so a double-tap is not a failure."""
    try:
        gone = await asyncio.to_thread(
            get_price_alert_service().delete, user["id"], alert_id
        )
    except PriceAlertUnavailable as e:
        return _unavailable(e)
    return DeletePriceAlertResponse(deleted=gone)
