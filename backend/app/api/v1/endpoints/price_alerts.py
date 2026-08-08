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

from app.api.error_response import ErrorCode, make_error_response
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
    return PriceAlertListResponse(
        items=[price_alert_from_row(r) for r in rows],
        max_per_user=settings.PRICE_ALERT_MAX_PER_USER,
        max_per_ticker=settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER,
    )


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
    seed = None
    try:
        quotes = await get_fmp_client().get_batch_quotes_bulk([request.ticker.upper()])
        for q in quotes or []:
            if str(q.get("symbol") or "").upper() == request.ticker.upper():
                seed = finite_price(q.get("price"))
                break
    except Exception as e:
        logger.warning(
            "price alerts: seed quote for %s failed (%s: %s) — creating with no "
            "baseline; the engine will seed on its next cycle",
            request.ticker, type(e).__name__, e,
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
    return price_alert_from_row(row)


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
