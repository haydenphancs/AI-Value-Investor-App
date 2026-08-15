"""
Billing Endpoints
Frontend: GET /billing/plans  (public tier catalog for the paywall)

The current user's subscription lives under /users/me/subscription (auth-only).
This router only exposes the public, guest-safe tier catalog so the paywall
renders for signed-out users too.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.error_response import ErrorCode, make_error_response
from app.config import settings
from app.dependencies import get_current_user
from app.integrations.app_store import (
    AppStoreNotConfigured,
    AppStoreVerificationFailed,
    extract_transaction_from_notification,
    verify_notification,
    verify_signed_transaction,
)
from app.schemas.subscription import (
    CreditPackResponse, CreditPackCatalogResponse,
    PlanResponse, PlanCatalogResponse,
    VerifyPurchaseRequest, VerifyPurchaseResponse,
)
from app.services.iap_service import (
    IAPError,
    PurchaseAccountMismatch,
    PurchaseBoundToAnotherAccount,
    PurchaseRevoked,
    UnknownProduct,
    get_iap_service,
)
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/plans", response_model=PlanCatalogResponse)
async def get_plans():
    """Public tier catalog (Free / Pro / Max) with live pricing + per-action
    credit costs. No auth — the paywall must render for guests."""
    plans = SubscriptionService().get_plan_catalog()
    return PlanCatalogResponse(
        plans=[PlanResponse(**p) for p in plans],
        report_cost=settings.REPORT_CREDIT_COST,
        chat_cost=settings.CHAT_CREDIT_COST,
    )


@router.get("/credit-packs", response_model=CreditPackCatalogResponse)
async def get_credit_packs():
    """Public consumable credit-pack catalog. No auth — same reasoning as `/plans`: the
    Buy Credits screen must render before we know who is looking, and nothing here is
    sensitive (it is the same pricing Apple shows on the storefront).

    Only the CATALOG is public. Buying is `POST /verify`, which is sign-in-required —
    consumables are not restorable by Apple, so credits have to attach to a real account
    or a reinstall loses money the user actually spent."""
    packs = SubscriptionService().get_credit_pack_catalog()
    return CreditPackCatalogResponse(
        packs=[CreditPackResponse(**p) for p in packs],
        report_cost=settings.REPORT_CREDIT_COST,
        chat_cost=settings.CHAT_CREDIT_COST,
    )


@router.post("/verify", response_model=VerifyPurchaseResponse)
async def verify_purchase(
    request: VerifyPurchaseRequest,
    user: dict = Depends(get_current_user),
):
    """Verify an Apple-signed StoreKit 2 transaction and apply the entitlement.

    This is the trust boundary for paid access. The client sends the JWS Apple gave it; we
    verify Apple's signature and certificate chain, and only then grant a tier. Nothing the
    client asserts about what it bought is used — the tier comes from the *verified*
    payload's `productId`.

    Idempotent by design. StoreKit replays `Transaction.updates` on every launch, restore
    re-submits, and the webhook can arrive for the same purchase, so this is called far more
    often than a purchase happens. Entitlement is keyed on `originalTransactionId`, and
    credits come from the monthly allocation rather than per-delivery, so a replay cannot
    mint credits.

    Guests are rejected: entitlement has to attach to a real account, and the shared guest
    id would grant a tier to every signed-out install at once. That rejection is enforced
    entirely by the `get_current_user` dependency — see below.
    """
    # No guest check here, and that is deliberate rather than an omission.
    #
    # `get_current_user` is the STRICT dependency: a missing credential raises AUTH_REQUIRED,
    # an unverifiable one is rejected rather than downgraded to guest (`.claude/rules/auth.md`
    # §4), and a token whose `sub` has no `public.users` row raises AUTH_ACCOUNT_NOT_FOUND. It
    # can only ever return a real account row, and it never stamps `is_guest`.
    #
    # This used to test `user["id"] == GUEST_USER_ID`, which was unconditionally dead — nothing
    # in the backend mints a token for the sentinel. Keeping it invited the opposite mistake:
    # per-install guest ids never equal the sentinel either (migrations 108/110/111), so that
    # comparison is the known-wrong guest test, and leaving one in the money path as a model to
    # copy is worse than having none.
    user_id = user["id"]

    # 1. Verify with Apple's chain. Never trust the client's own description of the purchase.
    try:
        # OFF the event loop. The App Store Server Library builds an X.509 chain and, in
        # Sandbox/Production, makes a blocking OCSP request — on the single uvicorn worker
        # that stalls every other in-flight request for the duration.
        payload = await asyncio.to_thread(
            verify_signed_transaction, request.signed_transaction
        )
    except AppStoreNotConfigured as e:
        # OUR misconfiguration, not a bad receipt. 503 so the client retries rather than
        # telling the user their legitimate purchase was invalid.
        logger.error("IAP verification unavailable: %s", e)
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=503,
            message="Purchase verification is temporarily unavailable",
            user_message=(
                "We couldn't confirm your purchase just now. It's safe — reopen the app "
                "shortly and it will be applied."
            ),
        )
    except AppStoreVerificationFailed as e:
        # Hostile or corrupt input. The reason is logged, never returned — an error that
        # explains why it rejected you is an oracle for forging one that passes.
        logger.warning("IAP verification rejected for user=%s: %s", user_id, e)
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            status_code=400,
            message="Transaction could not be verified",
            user_message=(
                "We couldn't verify that purchase with Apple. If you were charged, "
                "contact support and we'll sort it out."
            ),
        )

    # 2. Apply it. `apply_verified_transaction` routes on the VERIFIED payload's productId:
    # a subscription reconciles the tier, a consumable credit pack grants credits exactly
    # once against `credit_purchases (environment, transaction_id)`. Both return the same
    # summary shape, and every error arm below serves both.
    try:
        result = get_iap_service().apply_verified_transaction(user_id, payload)
    except PurchaseRevoked as e:
        # Apple refunded or cancelled this purchase. TERMINAL and finishable — distinct from
        # the UnknownProduct arm below, which stays unfinished on purpose so a missing
        # `credit_packs` row can self-heal on the next redelivery.
        logger.warning("IAP purchase already revoked for user=%s: %s", user_id, e)
        return make_error_response(
            ErrorCode.PURCHASE_REVOKED,
            message=str(e),
            details={"transaction": "revoked"},
        )

    except UnknownProduct as e:
        # Verified but unmapped: a REAL purchase we can't price. The user paid, so this is
        # ours to fix — loud log, honest message, no silent free tier.
        logger.error("IAP verified but unmapped product for user=%s: %s", user_id, e)
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            status_code=400,
            message=f"Unmapped product: {payload.get('productId')}",
            user_message=(
                "That purchase went through but we couldn't match it to a plan. "
                "Contact support and we'll apply it."
            ),
        )
    except PurchaseAccountMismatch as e:
        # MUST precede the `PurchaseBoundToAnotherAccount` arm below — it is a subclass, and
        # the money consequence is opposite. Nothing was recorded and nobody was credited, so
        # the client must keep the transaction UNFINISHED: when the buying account signs in,
        # StoreKit redelivers it and the same call grants it. Finishing it here (which
        # PURCHASE_ALREADY_LINKED tells the client to do) would delete a purchase the user paid
        # for, with no redelivery left to repair it.
        logger.warning(
            "IAP: user=%s submitted a transaction whose appAccountToken names another "
            "account: %s", user_id, e,
        )
        return make_error_response(
            ErrorCode.PURCHASE_ACCOUNT_MISMATCH,
            message="Transaction belongs to a different account",
        )
    except PurchaseBoundToAnotherAccount as e:
        # TERMINAL — must precede the generic `IAPError` arm below.
        #
        # Ownership of a transaction never moves, so this condition can never clear. Answering
        # 503 "reopen the app shortly and it will be applied" told StoreKit to retry: the
        # client never calls `Transaction.finish()`, `Transaction.updates` re-delivers on every
        # launch, and the user waits forever for something that cannot happen. 409 is the
        # honest answer and lets the client finish the transaction.
        logger.warning(
            "IAP: user=%s submitted a transaction owned by another account: %s", user_id, e
        )
        return make_error_response(
            ErrorCode.PURCHASE_ALREADY_LINKED,
            message="Transaction is bound to a different account",
        )
    except IAPError as e:
        logger.error("IAP entitlement failed for user=%s: %s", user_id, e)
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=503,
            message="Could not apply the entitlement",
            user_message=(
                "Your purchase was verified but we couldn't apply it yet. Reopen the app "
                "shortly and it will be applied."
            ),
        )

    return VerifyPurchaseResponse(
        tier=result["winning_tier"],
        status=result["status"],
        current_period_end=result["current_period_end"],
        was_replay=result["was_replay"],
        # Defaulted `.get`s so the subscription path — which does not set them — keeps its
        # exact previous response, and so this line cannot break if a future branch omits one.
        kind=result.get("kind", "subscription"),
        credits_granted=result.get("credits_granted", 0),
        credits_spendable=result.get("credits_spendable"),
    )


@router.post("/app-store-notifications")
async def app_store_notifications(
    request: Request,
):
    """App Store Server Notifications V2 webhook.

    Without this, a cancellation, refund, or failed renewal never reaches us and a lapsed
    subscriber keeps their tier indefinitely — the client only ever tells us about
    *purchases*.

    Unauthenticated by necessity (Apple calls it), but not untrusted: the payload is a JWS
    verified against Apple's certificate chain exactly like a client transaction, and the
    inner transaction is verified separately rather than trusted via the envelope.

    Always answers 200 once the signature checks out. Apple retries non-2xx for days, so
    returning an error for something we've decided to ignore (an unmapped product, a
    transaction we have no user for) would just generate retries forever. What happened is
    recorded in `outcome` and the logs.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed body")

    signed_payload = (body or {}).get("signedPayload")
    if not signed_payload:
        raise HTTPException(status_code=400, detail="Missing signedPayload")

    try:
        notification = await asyncio.to_thread(verify_notification, signed_payload)
    except AppStoreNotConfigured as e:
        # 503 is right here: Apple SHOULD retry, because the fault is ours and transient.
        logger.error("IAP webhook verification unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Verification unavailable")
    except AppStoreVerificationFailed as e:
        # 400, not 503 — a payload that fails Apple's own signature check will never verify,
        # so inviting retries is pointless.
        logger.warning("IAP webhook rejected an unverifiable payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        transaction = await asyncio.to_thread(
            extract_transaction_from_notification, notification
        )
    except AppStoreVerificationFailed as e:
        logger.warning("IAP webhook: inner transaction failed verification: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        outcome, _user_id = get_iap_service().apply_notification(notification, transaction)
    except IAPError:
        # Transient (DB) failure — let Apple retry.
        raise HTTPException(status_code=503, detail="Could not apply notification")

    return {"received": True, "outcome": outcome}
