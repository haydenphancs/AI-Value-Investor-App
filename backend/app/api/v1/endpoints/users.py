"""
User Endpoints
Frontend: GET /users/me, GET /users/me/credits, PATCH /users/me
"""

from fastapi import APIRouter, Depends, HTTPException
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
)

_RESEARCH_PDF_BUCKET = "research-pdfs"


def _purge_unlinked_rows(supabase: Client, user_id: str) -> dict[str, str]:
    """Delete the un-FK'd user rows the cascade misses. Best-effort per table: one
    failure must not abandon the rest of the purge, but every failure is reported."""
    failures: dict[str, str] = {}
    for table in _UNLINKED_USER_TABLES:
        try:
            supabase.table(table).delete().eq("user_id", user_id).execute()
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
       (user_credits, subscriptions, watchlist_items, portfolios, research_reports,
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
