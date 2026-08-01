"""
User Endpoints
Frontend: GET /users/me, GET /users/me/credits, PATCH /users/me
"""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client
import logging
from typing import Optional

from app.api.error_response import ErrorCode, make_error_response
from app.database import get_supabase
from app.dependencies import (
    get_current_user,
    get_current_user_or_guest,  # TEMP: guest fallback
    GUEST_USER_ID,
)
from app.schemas.user import UserResponse, UserCreditsResponse, UpdateProfileRequest
from app.schemas.subscription import SubscriptionResponse
from app.schemas.settings import (
    UserSettingsResponse,
    UpdateUserSettingsRequest,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
)
from app.services.credit_service import CreditService, CreditServiceUnavailable
from app.services.subscription_service import (
    SubscriptionService,
    display_name_for_tier,
)
from app.services.user_settings_service import (
    UserSettingsService,
    PreferencesTooLarge,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def credits_response_from_rows(rows: list) -> UserCreditsResponse:
    """Build the credits response from a (possibly empty) query result.

    Genuinely no row → optimistic Free-tier default (the signup trigger normally
    seeds a row; this is the rare first-touch safety net). A transient READ ERROR is
    NOT handled here — the caller must surface it as retryable, never fabricate a
    balance (a masked error would show a Pro user "50" and mis-drive the UI).
    """
    if not rows:
        return UserCreditsResponse(total=50, used=0, remaining=50)
    return UserCreditsResponse(**rows[0])


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: dict = Depends(get_current_user),
):
    """Get current user profile."""
    return UserResponse(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        avatar_url=user.get("avatar_url"),
        tier=user.get("tier", "free"),
        created_at=user["created_at"],
        updated_at=user.get("updated_at"),
    )


@router.get("/me/credits", response_model=UserCreditsResponse)
async def get_user_credits(
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """Get current user's credit balance from user_credits table."""
    # Roll the monthly allocation BEFORE reading so a returning user in a NEW month sees their
    # fresh balance. The lazy reset otherwise only fires on a spend (chat/report), so a user who
    # depleted last month and doesn't chat would see a stale remaining=0 here AND the report
    # Generate button would stay disabled. Authenticated non-guest only (ensure_credit_period
    # skips the guest sentinel). Best-effort: a transient RPC blip falls through to the raw read.
    if user["id"] != GUEST_USER_ID:
        try:
            CreditService().ensure_period(user["id"])
        except CreditServiceUnavailable:
            logger.warning(
                "ensure_period unavailable for user=%s — serving raw balance", user["id"]
            )
    # limit(1) (not single()) so a genuine "no row" is an empty list, distinct from a
    # transient read error which raises. We must NOT launder a transient error into a
    # fabricated Free balance — that would show a real user the wrong credits.
    try:
        result = supabase.table("user_credits").select(
            "total, used, remaining, resets_at"
        ).eq("user_id", user["id"]).limit(1).execute()
    except Exception as e:
        logger.error(
            "Credits read failed for user=%s: %s: %s", user["id"], type(e).__name__, e
        )
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            message=f"credits read failed: {type(e).__name__}",
            user_message="Couldn't load your credits right now. Please try again.",
        )

    return credits_response_from_rows(result.data or [])


@router.get("/me/subscription", response_model=SubscriptionResponse)
async def get_my_subscription(
    user: dict = Depends(get_current_user),
):
    """Current user's subscription entitlement. Falls back to the tier on the
    users row (mirrored by receipt-validation webhooks), else Free, when no
    `subscriptions` row exists yet. Auth-only — guests have no subscription."""
    sub = SubscriptionService().get_user_subscription(user["id"])
    if not sub:
        tier = user.get("tier", "free")
        return SubscriptionResponse(
            tier=tier,
            display_name=display_name_for_tier(tier),
            status="active",
        )
    tier = sub.get("tier", "free")
    return SubscriptionResponse(
        tier=tier,
        display_name=display_name_for_tier(tier),
        status=sub.get("status", "active"),
        current_period_end=sub.get("current_period_end"),
        store=sub.get("store"),
    )


@router.get("/me/settings", response_model=UserSettingsResponse)
async def get_my_settings(
    user: dict = Depends(get_current_user),
):
    """Fetch the current user's synced preference blob (appearance, notification
    toggles, general prefs). Empty {} when nothing has been synced yet."""
    prefs = UserSettingsService().get_settings(user["id"])
    return UserSettingsResponse(preferences=prefs)


@router.put("/me/settings", response_model=UserSettingsResponse)
async def update_my_settings(
    request: UpdateUserSettingsRequest,
    user: dict = Depends(get_current_user),
):
    """Full-blob replace of the current user's synced preferences."""
    try:
        prefs = UserSettingsService().upsert_settings(user["id"], request.preferences)
    except PreferencesTooLarge as e:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=str(e),
            user_message="Your settings couldn't be saved. Please try again.",
        )
    return UserSettingsResponse(preferences=prefs)


@router.post("/me/devices", response_model=DeviceRegisterResponse)
async def register_device(
    request: DeviceRegisterRequest,
    user: dict = Depends(get_current_user),
):
    """Register (or re-bind) an APNs device token for push notifications.
    Auth-only: a push token is only useful attached to a real user."""
    ok = UserSettingsService().register_device(
        user_id=user["id"],
        token=request.token,
        platform=request.platform,
        environment=request.environment,
    )
    return DeviceRegisterResponse(registered=ok)


@router.post("/me/claim-guest-data")
async def claim_guest_data(
    user: dict = Depends(get_current_user),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
):
    """Move this install's GUEST watchlist + portfolios onto the signed-in account.

    Without this, the guest-first funnel loses its own work: a user adds tickers
    during first-run onboarding, creates an account, and their watchlist is empty —
    because migration 108 partitions guests by install and a real account keys off
    its user id instead. Signing in would actively cost them data, which is the
    opposite of the intended upgrade.

    Idempotent: rows already moved simply aren't there the second time, and tickers
    the account ALREADY holds are skipped rather than colliding with
    `watchlist_items UNIQUE(user_id, ticker)`.

    Best-effort: partial success is reported, never raised. A failed claim must not
    block sign-in — the user is already authenticated by the time this runs.
    """
    from app.dependencies import GUEST_USER_ID, guest_user_id_for

    bucket = guest_user_id_for(x_guest_id)

    # HARD GUARD. `guest_user_id_for(None)` returns the shared sentinel, and the
    # legacy pre-migration-108 rows still live there. Claiming that bucket would pull
    # OTHER people's tickers into this account — the exact cross-user leak migration
    # 108 exists to close. Only a real per-install bucket is ever claimable.
    if not x_guest_id or bucket == GUEST_USER_ID:
        return {"claimed": {"watchlist_items": 0, "portfolios": 0}, "skipped": "no per-install guest id"}

    user_id = user["id"]
    claimed = {"watchlist_items": 0, "portfolios": 0}

    def _claim() -> None:
        # ── watchlist: skip tickers the account already holds ──────────────
        guest_rows = (
            supabase.table("watchlist_items").select("id,ticker")
            .eq("user_id", bucket).execute().data or []
        )
        if guest_rows:
            owned = {
                r["ticker"] for r in (
                    supabase.table("watchlist_items").select("ticker")
                    .eq("user_id", user_id).execute().data or []
                )
            }
            movable = [r["id"] for r in guest_rows if r.get("ticker") not in owned]
            if movable:
                supabase.table("watchlist_items").update({"user_id": user_id}) \
                    .in_("id", movable).execute()
                claimed["watchlist_items"] = len(movable)
            # Duplicates are dropped, not left orphaned on a bucket nothing reads.
            dupes = [r["id"] for r in guest_rows if r.get("ticker") in owned]
            if dupes:
                supabase.table("watchlist_items").delete().in_("id", dupes).execute()

        # ── portfolios: no unique constraint on name, so move them all. Their
        #    portfolio_items ride along (FK is to portfolios.id, not to the user).
        pf = (
            supabase.table("portfolios").select("id")
            .eq("user_id", bucket).execute().data or []
        )
        if pf:
            supabase.table("portfolios").update({"user_id": user_id}) \
                .in_("id", [r["id"] for r in pf]).execute()
            claimed["portfolios"] = len(pf)

    try:
        await asyncio.to_thread(_claim)
    except Exception as e:
        logger.error(
            "Guest-data claim failed for user=%s bucket=%s: %s: %s",
            user_id, bucket, type(e).__name__, e, exc_info=True,
        )
        # Never fatal: the user IS signed in. Report honestly instead of 500ing.
        return {"claimed": claimed, "error": f"{type(e).__name__}"}

    if claimed["watchlist_items"] or claimed["portfolios"]:
        logger.info(
            "Guest-data claim for user=%s: %d watchlist row(s), %d portfolio(s)",
            user_id, claimed["watchlist_items"], claimed["portfolios"],
        )
    return {"claimed": claimed}


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Update current user profile (display_name, avatar_url)."""
    update_data = request.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = supabase.table("users").update(update_data).eq(
        "id", user["id"]
    ).execute()

    updated = result.data[0] if result.data else user
    return UserResponse(
        id=updated["id"],
        email=updated["email"],
        display_name=updated.get("display_name"),
        avatar_url=updated.get("avatar_url"),
        tier=updated.get("tier", "free"),
        created_at=updated["created_at"],
        updated_at=updated.get("updated_at"),
    )


# User-keyed tables with NO foreign key to public.users, so the auth.users cascade does
# NOT reach them. Each omits the FK deliberately (the shared guest id has to be able to
# write them), which means account deletion has to clear them explicitly.
#   user_learn_progress  — migrations/067, "no FK so the shared guest id works"
#   user_book_progress   — migrations/066, same
#   chat_usage_budget    — migrations/096, same
#   credit_transactions  — migrations/100, described as an append-only audit ledger
#   watchlist_items      — migrations/108. These two USED to cascade; the FKs were
#   portfolios             dropped so guests could be partitioned per install.
#                          Deletion is now this list's job, and forgetting it would
#                          silently leave a deleted user's data behind. Deleting a
#                          `portfolios` row still cascades to `portfolio_items`,
#                          which FKs to portfolios(id) rather than to users.
#   analytics_events     — migrations/107. Keyed on `identity_key`, not `user_id`, so it
#                          is purged separately below (the column name differs).
#
# credit_transactions IS deleted rather than anonymised: the privacy policy promises
# deletion, credits are internal accounting rather than a payment record, and Apple holds
# the actual purchase records independently. Revisit if real billing history ever needs to
# survive deletion for tax or dispute reasons — and if it does, say so in the policy.
_UNLINKED_USER_TABLES: tuple[str, ...] = (
    "user_learn_progress",
    "user_book_progress",
    "chat_usage_budget",
    "credit_transactions",
    "watchlist_items",
    "portfolios",   # migrations/108, same dropped cascade
)

# Same purge, different column. `analytics_events.identity_key` holds the real user id
# for a signed-in user, so their behavioural history would otherwise survive account
# deletion — which the privacy policy promises it does not.
_UNLINKED_IDENTITY_TABLES: tuple[str, ...] = (
    "analytics_events",
)

_RESEARCH_PDF_BUCKET = "research-pdfs"


def _purge_unlinked_rows(supabase: Client, user_id: str) -> dict[str, str]:
    """Delete the un-FK'd user rows the cascade misses. Best-effort per table: one
    failure must not abandon the rest of the purge, but every failure is reported."""
    failures: dict[str, str] = {}
    for table, column in (
        [(t, "user_id") for t in _UNLINKED_USER_TABLES]
        + [(t, "identity_key") for t in _UNLINKED_IDENTITY_TABLES]
    ):
        try:
            supabase.table(table).delete().eq(column, user_id).execute()
        except Exception as e:  # noqa: BLE001 — recorded and surfaced below
            failures[table] = f"{type(e).__name__}: {e}"
            logger.error(
                "Account deletion: failed to purge %s for user=%s: %s: %s",
                table, user_id, type(e).__name__, e,
            )
    return failures


def _purge_research_pdfs(supabase: Client, user_id: str) -> Optional[str]:
    """Delete the user's generated report PDFs from Storage.

    `storage.objects` has no FK to auth.users, and nothing in the app ever deleted these,
    so PDFs — with the user's UUID in the object path — survived account deletion
    indefinitely. Path convention is set by pdf_report_service: reports/<user_id>/<id>.pdf
    """
    prefix = f"reports/{user_id}"
    try:
        bucket = supabase.storage.from_(_RESEARCH_PDF_BUCKET)
        entries = bucket.list(prefix) or []
        names = [e["name"] for e in entries if isinstance(e, dict) and e.get("name")]
        if not names:
            return None
        bucket.remove([f"{prefix}/{name}" for name in names])
        logger.info(
            "Account deletion: removed %d report PDF(s) for user=%s", len(names), user_id
        )
        return None
    except Exception as e:  # noqa: BLE001 — recorded and surfaced below
        logger.error(
            "Account deletion: failed to purge report PDFs for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        return f"{type(e).__name__}: {e}"


@router.delete("/me")
async def delete_account(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Permanently delete the CALLER'S OWN account and all of their data.

    Three steps, in this order:

    1. Storage — remove `research-pdfs/reports/<user_id>/*`. Done FIRST because the
       object path is the only remaining handle on those files; if the auth row went
       first and step 3 then failed, the PDFs would be orphaned with no way to find them.
    2. Un-FK'd tables — `_UNLINKED_USER_TABLES`. These have no foreign key to
       `public.users`, so the cascade in step 3 does not reach them.
    3. `auth.users` — cascades to `public.users` and every FK-linked child table
       (user_credits, subscriptions, research_reports,
       user_settings, device_tokens, whale_follows, chat_sessions → chat_messages, …).

    The previous implementation did step 3 only, and its docstring claimed that removed
    "all of the user's data in one call". It did not: four tables and every generated PDF
    survived. Promising deletion and not delivering it is both an App Review 5.1.1 problem
    and a straightforwardly false statement to the user.

    Partial failure is reported as 500 with the identity row left INTACT, so the caller
    can retry and reach the remaining data. Deleting the auth row first would strand it.

    Auth-only (`get_current_user` 401s guests); the guest guard is defense-in-depth —
    guest data is shared across installs and must never be deletable by one caller.
    """
    user_id = user["id"]
    if user_id == GUEST_USER_ID:
        raise HTTPException(status_code=403, detail="Cannot delete the guest account.")

    failures: dict[str, str] = {}

    # 1. Storage objects (before the identity row disappears).
    pdf_error = _purge_research_pdfs(supabase, user_id)
    if pdf_error:
        failures[f"storage:{_RESEARCH_PDF_BUCKET}"] = pdf_error

    # 2. Tables the cascade cannot reach.
    failures.update(_purge_unlinked_rows(supabase, user_id))

    if failures:
        logger.error(
            "Account deletion aborted for user=%s — auth row kept so a retry can "
            "still reach the remaining data. Failed targets: %s",
            user_id, ", ".join(sorted(failures)),
        )
        raise HTTPException(
            status_code=500,
            detail="Couldn't fully delete your account. Please try again.",
        )

    # 3. Identity row + every FK-linked child table.
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception as e:
        logger.error(
            "Account deletion failed at the auth step for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        raise HTTPException(
            status_code=500,
            detail="Couldn't delete your account. Please try again.",
        )

    logger.info("Account deleted for user=%s (storage + unlinked rows + cascade)", user_id)
    return {"deleted": True}
