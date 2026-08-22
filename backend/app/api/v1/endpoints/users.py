"""
User Endpoints
Frontend: GET /users/me, GET /users/me/credits, PATCH /users/me
"""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client
import logging
from typing import Optional

from app.api.error_response import ErrorCode, auth_error, make_error_response
from app.database import get_auth_client, get_supabase
from app.dependencies import (
    AvatarRateLimit,
    ProfileRateLimit,
    get_current_user,
    get_current_user_or_guest,  # TEMP: guest fallback
    get_profile_identity,
    GUEST_USER_ID,
)
from app.schemas.investor_profile import (
    InvestorProfileResponse,
    UpdateInvestorProfileRequest,
)
from app.services.agents.investor_profile_prompt import may_apply_profile
from app.services.entitlements import required_tier_for_signals
from app.services import avatar_service
from app.services.avatar_service import (
    AvatarError,
    AvatarInvalidImageError,
    AvatarStorageError,
    AvatarTooLargeError,
)
from app.services.user_investor_profile_service import (
    ProfileUnreadable,
    get_user_investor_profile_service,
    is_empty_profile,
    merge_profiles,
    would_personalize,
)
from app.schemas.user import (
    UserResponse,
    UserCreditsResponse,
    UpdateProfileRequest,
    UpdateAvatarRequest,
)
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
    PreferencesUnreadable,
    PreferencesEmptyAfterSanitize,
)
from app.schemas.notifications import (
    MarkReadRequest,
    MarkReadResponse,
    NotificationListResponse,
)
from app.services.notification_inbox_service import (
    DEFAULT_PAGE,
    NotificationInboxUnavailable,
    get_notification_inbox_service,
)
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

router = APIRouter()

#: Bounded re-read attempts for the guest→account portfolio move. The window
#: between reading the account's portfolio NAMES and the UPDATE that re-points the
#: guest's rows is real: `_seed_default_portfolio` can insert "Holdings" inside it
#: (iOS fires GET /portfolios from TrackingViewModel.init while
#: AppState.onAuthenticated fires the claim), producing 23505 on
#: portfolios_user_id_name_key — the `/users/me/claim-guest-data` Sentry issue.
#: Small and bounded: each attempt re-reads, so it converges rather than looping.
_CLAIM_PORTFOLIO_ATTEMPTS = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def credits_response_from_rows(rows: list) -> UserCreditsResponse:
    """Build the credits response from a (possibly empty) query result.

    Genuinely no row → optimistic Free-tier default (the signup trigger normally
    seeds a row; this is the rare first-touch safety net). A transient READ ERROR is
    NOT handled here — the caller must surface it as retryable, never fabricate a
    balance (a masked error would show a Pro user "50" and mis-drive the UI).

    ⚠️ Built FIELD BY FIELD, not `UserCreditsResponse(**row)`. The splat was correct while the
    row and the response were the same four columns, but `user_credits` now carries a second
    pool (`purchased_total` / `purchased_used`, migration 117) and Pydantic v2 IGNORES extra
    keys — so a splat would silently serve the GRANTED-ONLY balance with no exception and no
    log. A user who just paid $9.99 would see their old balance and a disabled Generate button.

    Also tolerant of the columns being ABSENT: this endpoint must keep working if the code
    deploys before migration 117 is applied (`.get(..., 0)` — the same posture
    `iap_service._EVENT_AT_COLUMN` takes for `subscriptions.last_event_at`).
    """
    if not rows:
        return UserCreditsResponse(
            total=50, used=0, remaining=50,
            granted_remaining=50, purchased_remaining=0,
            granted_total=50, granted_used=0,
        )

    row = rows[0]
    granted_total = int(row.get("total") or 0)
    granted_used = int(row.get("used") or 0)
    purchased_total = int(row.get("purchased_total") or 0)
    purchased_used = int(row.get("purchased_used") or 0)

    granted_remaining = granted_total - granted_used
    purchased_remaining = purchased_total - purchased_used

    return UserCreditsResponse(
        total=granted_total + purchased_total,
        used=granted_used + purchased_used,
        remaining=granted_remaining + purchased_remaining,
        resets_at=row.get("resets_at"),
        granted_remaining=granted_remaining,
        purchased_remaining=purchased_remaining,
        granted_total=granted_total,
        granted_used=granted_used,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get current user profile.

    Does two things to `avatar_url` on the way out, both best-effort and both silent to the
    caller when they fail:

    1. **Imports a third-party avatar once.** `public.handle_new_auth_user()` copies
       `raw_user_meta_data->>'avatar_url'` into `public.users` at signup, so every Google
       sign-in arrives already pointing at `lh3.googleusercontent.com`. Left alone, the app
       re-fetches from Google on every render — disclosing the user's IP to a third party our
       privacy policy's service-provider list does not name, and leaving the image outside our
       retention and deletion story. We re-host it once, here, on the first authenticated read
       after signup, and rewrite the column.
    2. **Signs it.** The bucket is private (migration 152).
    """
    user_id = user["id"]
    avatar_url = user.get("avatar_url")

    if avatar_service.is_external(avatar_url):
        imported = await avatar_service.import_external_avatar(user_id, avatar_url)
        if imported:
            try:
                await asyncio.to_thread(
                    lambda: supabase.table("users")
                    .update({"avatar_url": imported, "updated_at": _now_iso()})
                    .eq("id", user_id)
                    .execute()
                )
                avatar_url = imported
            except Exception as exc:  # noqa: BLE001 — non-fatal, see below
                # The object is stored but the column still points at Google. Not fatal and
                # not silent: we keep serving the external URL and retry on the next read.
                # The orphan is swept by the account-deletion purge.
                logger.warning(
                    "[Avatar] could not persist the imported avatar for user=%s: %s",
                    user_id, f"{type(exc).__name__}: {exc}",
                )

    return UserResponse(
        id=user_id,
        email=user["email"],
        display_name=user.get("display_name"),
        avatar_url=await avatar_service.signed_avatar_url(user_id, avatar_url),
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
    # `select("*")` rather than an explicit column list: naming `purchased_total` here would
    # 500 every credits read if this deploys before migration 117 is applied, and the ordering
    # of a hand-applied migration versus a deploy is not guaranteed. The row is 9 small columns
    # and `credits_response_from_rows` defaults anything missing.
    try:
        result = supabase.table("user_credits").select(
            "*"
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
    toggles, general prefs). Empty {} when nothing has been synced yet.

    A READ FAILURE is 503 SETTINGS_UNAVAILABLE, never an empty 200 — the client
    treats "server state known" as permission to full-replace the row, so the two
    cases must not look alike. See the ErrorCode's comment.
    """
    try:
        prefs = UserSettingsService().get_settings(user["id"])
    except PreferencesUnreadable as e:
        return make_error_response(
            ErrorCode.SETTINGS_UNAVAILABLE,
            message=str(e),
        )
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
    except PreferencesEmptyAfterSanitize as e:
        # Refused rather than honoured: a full-replace write whose every value was
        # unsupported would clear the row and answer 200, which is indistinguishable
        # from success. An intentional clear sends an explicitly empty object.
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=str(e),
            user_message="Your settings couldn't be saved. Please try again.",
        )
    return UserSettingsResponse(preferences=prefs)


def _profile_response(profile: dict, user: dict) -> InvestorProfileResponse:
    """Shape the stored profile for the wire, including the tier verdict.

    `applied` DELEGATES to `may_apply_profile` — the same pure gate the chat path calls —
    rather than re-deriving the verdict here. It used to be `signals_unlocked(tier) and
    not is_empty_profile(profile)`, which is two of that gate's four arms: it omitted
    `consented_at` and `CHAT_PERSONALIZATION_ENABLED`. A consented, entitled reader was
    therefore told "Cay AI tailors how it explains things" while the feature flag was off
    and nothing was being tailored — the exact lie the paragraph below forbids, and the
    default state this build ships in. Two arms cannot be kept in sync by hand across two
    files; one call cannot drift at all.

    `required_tier` comes from the matching entitlements helper so the paywall names
    exactly the plan the server enforced.

    An all-default profile reports `applied=False` whatever the tier: there is nothing
    to personalize with, and claiming otherwise would be a lie in the UI.
    """
    empty = is_empty_profile(profile)
    return InvestorProfileResponse(
        experience_level=profile["experience_level"],
        explanation_style=profile["explanation_style"],
        answer_depth=profile["answer_depth"],
        topics=profile["topics"],
        learning_goals=profile["learning_goals"],
        follow_signals=profile["follow_signals"],
        has_profile=profile.get("has_profile", False),
        is_empty=empty,
        answered_fields=profile.get("answered_fields") or [],
        # Three distinct questions, three distinct fields. `is_empty` = stated nothing;
        # `would_personalize` = their answers would change output; `applied` = it is
        # changing output right now. One boolean used to answer the first two, and for a
        # reader who chose both middle options they disagree — see migration 134.
        would_personalize=would_personalize(profile),
        profile_version=profile.get("profile_version", 1),
        consented_at=profile.get("consented_at"),
        applied=may_apply_profile(profile, user.get("tier")),
        required_tier=required_tier_for_signals(user.get("tier")),
    )


@router.get("/me/investor-profile", response_model=InvestorProfileResponse)
async def get_my_investor_profile(
    user: dict = Depends(get_profile_identity),
    _rate: None = ProfileRateLimit,
):
    """The reader's learning preferences, plus whether they are being APPLIED.

    Guest-allowed: the profile is captured during first-run onboarding, before an
    account exists (see `get_profile_identity`).

    A read failure is 503, never an all-defaults 200 — the client would treat defaults
    as "the server has no profile" and happily overwrite a real row with them. Same
    distinction `/me/settings` draws for the same reason.
    """
    try:
        profile = get_user_investor_profile_service().get_profile(user["id"])
    except ProfileUnreadable as e:
        return make_error_response(ErrorCode.SETTINGS_UNAVAILABLE, message=str(e))
    return _profile_response(profile, user)


@router.put("/me/investor-profile", response_model=InvestorProfileResponse)
async def update_my_investor_profile(
    request: UpdateInvestorProfileRequest,
    user: dict = Depends(get_profile_identity),
    _rate: None = ProfileRateLimit,
):
    """Save the reader's learning preferences.

    Partial by design — every field is optional so an onboarding step the user skipped
    doesn't block the ones they answered. `model_dump(exclude_none=True)` is what makes
    an omitted field keep its stored/default value instead of being cleared; an
    intentional clear sends an explicit empty list.

    Saving is allowed on EVERY tier. Whether it is applied is a separate question the
    response answers, so a Free user sees their answers acknowledged rather than a
    control that silently does nothing.
    """
    payload = request.model_dump(exclude_none=True)
    # Tri-state, and `exclude_none` is what preserves it: absent → don't touch stored
    # consent, True → grant, False → revoke. A plain `.pop(key, False)` would read
    # "field omitted" as "revoke", so every ordinary preference edit would silently
    # withdraw consent.
    consent = payload.pop("accepted_personalization_terms", None)

    # A body that says nothing writes nothing. `upsert_profile({})` used to INSERT a row
    # carrying only `user_id` + `updated_at`, which takes every column default — a phantom
    # that reports `has_profile: true, is_empty: true` and, until the claim step learned to
    # merge, was enough to make guest-data claiming delete the reader's real answers.
    # iOS guards this client-side (`OnboardingViewModel.savePreferences` has
    # `guard hasAnyPreference`), with a comment explaining precisely this hazard; the server
    # had no equivalent, so any retry or other client could still create one.
    #
    # A consent-only write is NOT empty and deliberately still creates the row: the consent
    # timestamp is the thing being recorded, and a reader may grant before stating anything.
    if not payload and consent is None:
        return _profile_response(
            get_user_investor_profile_service().get_profile(user["id"]), user
        )

    try:
        profile = get_user_investor_profile_service().upsert_profile(
            user["id"], payload, consent=consent,
        )
    except ProfileUnreadable as e:
        return make_error_response(ErrorCode.SETTINGS_UNAVAILABLE, message=str(e))
    return _profile_response(profile, user)


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


@router.delete("/me/devices", response_model=DeviceRegisterResponse)
async def unregister_device(
    request: DeviceRegisterRequest,
    user: dict = Depends(get_current_user),
):
    """Detach an APNs device token on sign-out.

    Without this the token stays bound to the account that registered it: `device_tokens.token`
    is UNIQUE and only a NEW registration re-binds it, but a signed-out client has no session
    to register with. The device keeps receiving the previous account's watchlist alerts —
    on a phone that is showing the signed-out guest UI, and to someone who may not be them.

    Auth-only and scoped to the caller, so a token that has already re-bound to another account
    cannot be detached by a stale client.
    """
    ok = UserSettingsService().unregister_device(user_id=user["id"], token=request.token)
    # `registered` reports the token's state AFTER the call, reusing the same response model:
    # a successful detach leaves it unregistered (False); a failure leaves it registered (True).
    return DeviceRegisterResponse(registered=not ok)


def _merge_portfolio_items(supabase: Client, source_id: str, target_id: str) -> None:
    """Move a guest portfolio's holdings into an account portfolio of the same name.

    Deduped on `portfolio_items_portfolio_id_ticker_key UNIQUE (portfolio_id, ticker)`: a
    ticker the target already holds keeps the ACCOUNT's row (it carries the shares the user
    entered while signed in, which is the more considered figure) and the guest row is
    dropped rather than stranded. Everything else is re-pointed, so the holdings survive.
    """
    guest_items = (
        supabase.table("portfolio_items").select("id,ticker")
        .eq("portfolio_id", source_id).execute().data or []
    )
    if not guest_items:
        return
    owned_tickers = {
        r["ticker"] for r in (
            supabase.table("portfolio_items").select("ticker")
            .eq("portfolio_id", target_id).execute().data or []
        )
    }
    movable = [r["id"] for r in guest_items if r.get("ticker") not in owned_tickers]
    if movable:
        supabase.table("portfolio_items").update({"portfolio_id": target_id}) \
            .in_("id", movable).execute()
    dupes = [r["id"] for r in guest_items if r.get("ticker") in owned_tickers]
    if dupes:
        supabase.table("portfolio_items").delete().in_("id", dupes).execute()


@router.post("/me/claim-guest-data")
async def claim_guest_data(
    user: dict = Depends(get_current_user),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
):
    """Move this install's GUEST watchlist + portfolios + Learn progress onto the signed-in account.

    Without this, the guest-first funnel loses its own work: a user adds tickers
    during first-run onboarding, creates an account, and their watchlist is empty —
    because migration 108 partitions guests by install and a real account keys off
    its user id instead. Signing in would actively cost them data, which is the
    opposite of the intended upgrade.

    The same argument applies to Learn: `get_learn_identity` partitions guests per
    install too, so a guest who completed lessons/articles/cores and bookmarked books
    would sign in to a Learn tab reading zero. `user_learn_progress` covers all four
    content types (book_core / journey_lesson / money_move / book_bookmark) and its
    `user_id` is a bare uuid with no FK, so the rows can simply be re-pointed.

    Idempotent: rows already moved simply aren't there the second time, and keys
    the account ALREADY holds are skipped rather than colliding with
    `watchlist_items UNIQUE(user_id, ticker)` /
    `user_learn_progress UNIQUE(user_id, content_type, item_key)`.

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
        return {
            "claimed": {
                "watchlist_items": 0, "portfolios": 0, "portfolios_merged": 0,
                "learn_progress": 0, "research_reports": 0, "chat_sessions": 0,
                "investor_profile": 0,
            },
            "skipped": "no per-install guest id",
        }

    user_id = user["id"]
    claimed = {
        "watchlist_items": 0, "portfolios": 0, "portfolios_merged": 0,
        "learn_progress": 0, "research_reports": 0, "chat_sessions": 0,
        "investor_profile": 0,
    }
    # Per-step failures. One table must never abort the ones after it — see `_claim`.
    step_failures: dict[str, str] = {}

    def _claim_watchlist() -> None:
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

    def _claim_portfolios() -> None:
        # ── portfolios: dedupe on NAME, then move. ──────────────────────────
        #
        # ⚠️ `portfolios` HAS a unique constraint: `portfolios_user_id_name_key
        # UNIQUE (user_id, name)`. The comment that used to sit here said the opposite and
        # the code moved every row unconditionally, so the UPDATE raised 23505 — and because
        # the whole `_claim()` body shared ONE try, that exception skipped the three steps
        # after it. Learn progress, research reports and chat sessions were silently never
        # claimed.
        #
        # And the collision is not rare, it is the DEFAULT: `_seed_default_portfolio`
        # (portfolios.py) lazily inserts a portfolio literally named "Holdings" on the first
        # `GET /portfolios` for EVERY identity — the guest bucket included. So any user who
        # opened the Assets tab as a guest and had also opened it while signed in (a previous
        # session on this device, another device, or a `.restoring` window that sent requests
        # tokenless) hit it.
        #
        # Merge rather than rename: "Holdings" means "everything I own" to the user, and two
        # portfolios with that name is not a state they asked for. `portfolio_items` FKs to
        # `portfolios.id`, so re-pointing the ITEMS carries the holdings across — deduped on
        # `portfolio_items_portfolio_id_ticker_key UNIQUE (portfolio_id, ticker)`, with the
        # account's own row winning, the same rule the watchlist and Learn steps use.
        guest_pf = (
            supabase.table("portfolios").select("id,name,is_active")
            .eq("user_id", bucket).execute().data or []
        )
        # Which group the guest was actually LOOKING at. Worth carrying over: the claim
        # moves rows with `is_active` cleared (it must — see below), and the heal at the
        # end then promotes by (sort_order, created_at, id), i.e. "Holdings". So a guest
        # who created "Tech", activated it, and then signed up would silently land back on
        # Holdings, with Home's header, the Updates chips AND the Tracking tab all
        # switching under them and nothing saying why.
        guest_active_id = next(
            (str(r["id"]) for r in guest_pf if r.get("is_active")), None
        )
        if guest_pf:
            owned_pf: dict = {}
            for attempt in range(_CLAIM_PORTFOLIO_ATTEMPTS):
                # RE-READ on every attempt — this is what makes the retry converge.
                # `_seed_default_portfolio` (portfolios.py) can insert "Holdings" for
                # THIS account between this read and the UPDATE below: iOS fires
                # GET /portfolios from TrackingViewModel.init while
                # AppState.onAuthenticated fires this claim. A blind replay of the
                # same `movable` list would just collide again; re-reading drops the
                # newly-conflicting name into the merge path instead.
                owned_pf = {
                    r["name"]: r["id"] for r in (
                        supabase.table("portfolios").select("id,name")
                        .eq("user_id", user_id).execute().data or []
                    )
                }
                movable = [r["id"] for r in guest_pf if r.get("name") not in owned_pf]
                if not movable:
                    break
                try:
                    # ⚠️ `is_active` must be cleared IN THE SAME UPDATE that re-points the row.
                    # `idx_portfolios_one_active_per_user` (migration 126) is a partial UNIQUE
                    # index on `user_id WHERE is_active`, and a guest who used the Assets tab
                    # always has an active group — so moving it onto an account that also has
                    # one raises 23505. That is the exact failure mode this whole block was
                    # rewritten to fix once already: one shared `try` means the exception
                    # skips every later claim step, silently losing Learn progress, research
                    # reports and chat sessions.
                    #
                    # A PostgREST UPDATE is ONE SQL statement, so it is all-or-nothing —
                    # there is no partial move to reconcile before retrying.
                    supabase.table("portfolios").update(
                        {"user_id": user_id, "is_active": False}
                    ).in_("id", movable).execute()
                    claimed["portfolios"] = len(movable)
                    break
                except Exception as e:
                    if (
                        not is_unique_violation(e)
                        or attempt == _CLAIM_PORTFOLIO_ATTEMPTS - 1
                    ):
                        # Not a race, or out of attempts → let the per-step handler
                        # record it; the four OTHER claim steps still run.
                        raise
                    logger.warning(
                        "Guest-data claim: portfolio move hit a unique violation for "
                        "user=%s (concurrent seed) — re-reading owned names and "
                        "retrying (%d/%d)",
                        user_id, attempt + 1, _CLAIM_PORTFOLIO_ATTEMPTS,
                    )

            for row in guest_pf:
                target_id = owned_pf.get(row.get("name"))
                if not target_id:
                    continue
                _merge_portfolio_items(supabase, row["id"], target_id)
                # The now-empty guest portfolio is dropped rather than stranded on a bucket
                # nothing reads — same as the watchlist and Learn duplicates.
                supabase.table("portfolios").delete().eq("id", row["id"]).execute()
                claimed["portfolios_merged"] += 1

            # Restore the guest's own selection when it survived the move AND the account
            # had no active group of its own; otherwise fall back to the heal, which the
            # account's existing choice wins outright.
            #
            # `set_active_portfolio` is safe to call unconditionally here: it clears any
            # other active row for this user in the same transaction, so it cannot trip the
            # unique index no matter what the account already had.
            try:
                account_has_active = bool(
                    supabase.table("portfolios").select("id")
                    .eq("user_id", user_id).eq("is_active", True).limit(1)
                    .execute().data
                )
                if guest_active_id and not account_has_active and guest_active_id in movable:
                    supabase.rpc(
                        "set_active_portfolio",
                        {"p_user_id": user_id, "p_portfolio_id": guest_active_id},
                    ).execute()
                else:
                    supabase.rpc(
                        "ensure_active_portfolio", {"p_user_id": user_id}
                    ).execute()
            except Exception as e:
                # Best-effort: `GET /portfolios` repairs this on the next launch. Never let it
                # abort the remaining claim steps — that is the 23505 lesson above.
                logger.warning(
                    "claim: ensure_active_portfolio failed for user=%s (%s: %s) — "
                    "GET /portfolios will heal it",
                    user_id, type(e).__name__, e,
                )

    def _claim_learn() -> None:
        # ── Learn progress: completions AND book bookmarks live in one table,
        #    discriminated by content_type. Dedupe on (content_type, item_key) —
        #    UNIQUE(user_id, content_type, item_key) means re-pointing a row the
        #    account already holds would raise, so those are dropped instead.
        #    The account's own row is the keeper: it may carry an earlier
        #    completed_at, and for bookmarks that timestamp is the sort key.
        guest_learn = (
            supabase.table("user_learn_progress").select("id,content_type,item_key")
            .eq("user_id", bucket).execute().data or []
        )
        if guest_learn:
            owned_keys = {
                (r["content_type"], r["item_key"]) for r in (
                    supabase.table("user_learn_progress").select("content_type,item_key")
                    .eq("user_id", user_id).execute().data or []
                )
            }
            movable, dupes = [], []
            for r in guest_learn:
                key = (r.get("content_type"), r.get("item_key"))
                (dupes if key in owned_keys else movable).append(r["id"])
            if movable:
                supabase.table("user_learn_progress").update({"user_id": user_id}) \
                    .in_("id", movable).execute()
                claimed["learn_progress"] = len(movable)
            # Same as the watchlist: don't strand duplicates on a bucket nothing reads.
            if dupes:
                supabase.table("user_learn_progress").delete().in_("id", dupes).execute()

    def _claim_reports() -> None:
        # ── research reports: move them wholesale ──────────────────────────
        #    Migration 110 partitions these per install too, so without this a guest who
        #    generated a report and THEN signed up would find their Reports tab empty —
        #    having spent their one free guest report to get there. No unique constraint
        #    to collide with (the id is a uuid), so every row moves.
        guest_reports = (
            supabase.table("research_reports").select("id")
            .eq("user_id", bucket).execute().data or []
        )
        if guest_reports:
            # Reset the PDF alongside the move. `pdf_path` is stamped at generation time as
            # `reports/<user_id>/<report_id>.pdf` with the id the report had THEN — the guest
            # bucket. Re-pointing user_id without clearing it leaves the object under a prefix
            # `_purge_research_pdfs` never lists, so account deletion would orphan it forever
            # (and with the row gone, the path is the only handle that existed). The PDF is
            # derived data: clearing it makes the app regenerate on demand from the frozen
            # report, which is cheap and keeps the deletion promise honest.
            supabase.table("research_reports").update({
                "user_id": user_id,
                "pdf_path": None,
                "pdf_status": "pending",
                "pdf_generated_at": None,
            }).in_("id", [r["id"] for r in guest_reports]).execute()
            claimed["research_reports"] = len(guest_reports)

    def _claim_chats() -> None:
        # ── chat sessions (migration 111) ──────────────────────────────────
        #    Same argument as reports: someone who asked Cay AI about a ticker and THEN
        #    signed up would find their history empty. `chat_messages` rides along — it
        #    references chat_sessions(id), not user_id, so moving the session moves the
        #    conversation. No unique constraint to collide with (the id is a uuid).
        guest_chats = (
            supabase.table("chat_sessions").select("id")
            .eq("user_id", bucket).execute().data or []
        )
        if guest_chats:
            supabase.table("chat_sessions").update({"user_id": user_id}).in_(
                "id", [c["id"] for c in guest_chats]
            ).execute()
            claimed["chat_sessions"] = len(guest_chats)

    def _claim_investor_profile() -> None:
        # ── investor profile: the ACCOUNT's own answers win outright. ──────────
        #
        # `user_id` is the PRIMARY KEY here, so unlike the watchlist there is at most
        # one row per side and re-pointing the guest row onto an id that already has
        # one would raise a unique violation rather than merge. The account's profile
        # is the more deliberate artifact (edited in Settings, post-signup), so the
        # guest row is dropped in that case rather than silently overwriting it.
        #
        # This step exists because the profile is captured during FIRST-RUN onboarding,
        # before the account: without it, answering the questions and then signing up
        # would throw the answers away — the same "signing in costs you data" failure
        # this endpoint exists to prevent.
        # No `.limit(1)`: user_id is the PRIMARY KEY, so at most one row can match —
        # and the sibling claim steps read the same way.
        #
        # ⚠️ THE TIE-BREAK IS ON CONTENT, NOT ON EXISTENCE. This used to drop the guest row
        # whenever the account had *any* row, including an all-defaults phantom one — and a
        # phantom is trivially created (an empty PUT, or a consent-only write on a user with
        # no row). The costly case is the `.restoring` window: the client keeps sending
        # `X-Guest-Id` with the token disarmed, so a consent grant lands on the GUEST bucket
        # and answers 200; the session then heals and this step deleted that grant, leaving
        # `investor_profile: 0`, no error, and a toggle that silently reads Off again.
        #
        # Every sibling step here merges. This one now does too: the account keeps anything
        # it has actually stated, and the guest row fills the gaps rather than being thrown
        # away. Consent is COALESCEd — a grant on either side is a grant, and the earlier
        # timestamp is the honest one because that is when the reader actually accepted.
        guest_rows = (
            supabase.table("user_investor_profile").select("*")
            .eq("user_id", bucket).execute().data or []
        )
        if not guest_rows:
            return
        guest_row = guest_rows[0]
        account_rows = (
            supabase.table("user_investor_profile").select("*")
            .eq("user_id", user_id).execute().data or []
        )
        if not account_rows:
            supabase.table("user_investor_profile").update({"user_id": user_id}) \
                .eq("user_id", bucket).execute()
            claimed["investor_profile"] = 1
            return

        account_row = account_rows[0]
        merged = merge_profiles(account_row, guest_row)
        if merged:
            supabase.table("user_investor_profile").update(merged) \
                .eq("user_id", user_id).execute()
            claimed["investor_profile"] = 1
        # The guest row has been folded in (or had nothing to add) — drop it either way so
        # the per-install bucket does not linger unclaimable.
        supabase.table("user_investor_profile").delete().eq("user_id", bucket).execute()

    def _bucket_has_anything() -> bool:
        """One round trip that answers "is there anything to claim at all?".

        This endpoint runs on EVERY cold launch of EVERY signed-in user — the client's
        transition key is process-scoped by design (see `AppState.claimGuestDataIfNeeded`'s
        note on why a persisted latch would be wrong) — and its steady-state answer is "no".
        Reaching that answer used to cost six sequential PostgREST round trips.

        Fails OPEN: any problem here falls through to the six-step path below, so the code is
        safe to deploy before migration 144 is applied (the RPC simply 404s and we log it).
        """
        try:
            result = supabase.rpc("guest_bucket_has_data", {"p_bucket": bucket}).execute()
        except Exception as e:  # noqa: BLE001 — degradation must be loud, not fatal
            logger.warning(
                "Guest-data claim: probe RPC unavailable, falling back to the six-step scan "
                "for user=%s bucket=%s: %s: %s (is migration 144 applied?)",
                user_id, bucket, type(e).__name__, e,
            )
            return True
        # `.data` is the scalar the function returned. Anything unexpected → fall through.
        if result.data is False:
            return False
        return True

    def _claim() -> None:
        """Run every step, isolated.

        These five used to share ONE `try`, so the FIRST table to raise skipped every table
        after it — and the order is not arbitrary: portfolios sits second, and it raised 23505
        on a name collision for a large class of users (see `_claim_portfolios`). The three
        steps behind it — Learn progress, research reports, chat sessions — were the ones that
        silently never ran, and the endpoint still answered 200.

        Isolating per step means one broken table costs that table only. Each failure is
        recorded and reported; `claim-guest-data` is best-effort by design (the user is already
        signed in, so this must never 500), but best-effort has to mean "tried them all".
        """
        if not _bucket_has_anything():
            # Nothing in this install's guest bucket. Every step below would issue its own
            # SELECT and short-circuit on an empty result, so skipping them all is exactly
            # equivalent — six round trips lighter.
            logger.debug(
                "Guest-data claim: empty bucket for user=%s bucket=%s — nothing to do",
                user_id, bucket,
            )
            return

        for name, step in (
            ("watchlist_items", _claim_watchlist),
            ("portfolios", _claim_portfolios),
            ("user_learn_progress", _claim_learn),
            ("research_reports", _claim_reports),
            ("chat_sessions", _claim_chats),
            ("user_investor_profile", _claim_investor_profile),
        ):
            try:
                step()
            except Exception as e:  # noqa: BLE001 — recorded and surfaced by the caller
                step_failures[name] = f"{type(e).__name__}: {e}"
                logger.error(
                    "Guest-data claim: %s step failed for user=%s bucket=%s: %s: %s",
                    name, user_id, bucket, type(e).__name__, e, exc_info=True,
                )

    try:
        await asyncio.to_thread(_claim)
    except Exception as e:
        # The per-step handler above catches everything a step can raise, so reaching here
        # means the driver itself failed (a thread-dispatch problem, not a table).
        logger.error(
            "Guest-data claim failed for user=%s bucket=%s: %s: %s",
            user_id, bucket, type(e).__name__, e, exc_info=True,
        )
        # Never fatal: the user IS signed in. Report honestly instead of 500ing.
        return {"claimed": claimed, "error": f"{type(e).__name__}"}

    if step_failures:
        logger.error(
            "Guest-data claim partially failed for user=%s: %s",
            user_id, ", ".join(sorted(step_failures)),
        )
        # `error` keeps the shape iOS already decodes (it drives the "we couldn't move your
        # saved tickers" warning); `failed` names the tables for the logs and future clients.
        return {
            "claimed": claimed,
            "error": "PartialClaim",
            "failed": sorted(step_failures),
        }

    if any(claimed.values()):
        # Report EVERY counter: a claim that moved only reports used to log nothing at all.
        logger.info(
            "Guest-data claim for user=%s: %d watchlist row(s), %d portfolio(s) moved, "
            "%d merged, %d learn-progress row(s), %d research report(s), %d chat session(s), "
            "%d investor profile(s)",
            user_id, claimed["watchlist_items"], claimed["portfolios"],
            claimed["portfolios_merged"], claimed["learn_progress"],
            claimed["research_reports"], claimed["chat_sessions"],
            claimed["investor_profile"],
        )
    return {"claimed": claimed}


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Update current user profile (display_name).

    Lengths are bounded on the schema (see `UpdateProfileRequest`); the trim happens here
    because a name that is ALL whitespace passes `min_length=1` and would blank the row.

    `avatar_url` is NOT updatable here — it is server-owned, written only by
    POST/DELETE /users/me/avatar. See `UpdateProfileRequest`'s docstring for why.
    """
    update_data = request.model_dump(exclude_none=True)

    if "display_name" in update_data:
        trimmed = update_data["display_name"].strip()
        if not trimmed:
            return make_error_response(
                ErrorCode.INVALID_INPUT,
                message="display_name was empty after trimming",
                user_message="Please enter a name.",
            )
        update_data["display_name"] = trimmed

    if not update_data:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message="no updatable fields in the request",
            user_message="Nothing to update.",
        )

    # `users.updated_at` defaults to now() on INSERT only — there is no trigger — so without
    # this the column claims the row has not changed since signup.
    update_data["updated_at"] = _now_iso()

    result = supabase.table("users").update(update_data).eq(
        "id", user["id"]
    ).execute()

    # A Supabase UPDATE that matches ZERO rows does not raise — it returns `data == []`.
    # Falling back to `user` answers 200 carrying the PRE-update values, so the client
    # adopts them and the edit silently reverts with nothing logged anywhere. Keep the
    # fallback (it is the safe render) but make the anomaly loud.
    if not result.data:
        logger.error(
            "Profile update matched no rows for user=%s — responding with pre-update values",
            user["id"],
        )
    updated = result.data[0] if result.data else user
    return UserResponse(
        id=updated["id"],
        email=updated["email"],
        display_name=updated.get("display_name"),
        avatar_url=await avatar_service.signed_avatar_url(
            user["id"], updated.get("avatar_url")
        ),
        tier=updated.get("tier", "free"),
        created_at=updated["created_at"],
        updated_at=updated.get("updated_at"),
    )


# ── Profile picture ───────────────────────────────────────────────────────────────────────
#
# `avatar_url` is SERVER-OWNED: these two routes are its only writers. The bytes go to the
# private `user-avatars` bucket (migration 152) under a content-addressed, server-derived key,
# and the read paths above hand back a short-lived signed URL.


def _avatar_response(user: dict, row: dict, signed: Optional[str]) -> UserResponse:
    return UserResponse(
        id=row.get("id", user["id"]),
        email=row.get("email", user["email"]),
        display_name=row.get("display_name"),
        avatar_url=signed,
        tier=row.get("tier", user.get("tier", "free")),
        created_at=row.get("created_at", user["created_at"]),
        updated_at=row.get("updated_at"),
    )


def _write_avatar_url(supabase: Client, user_id: str, url: Optional[str]):
    return (
        supabase.table("users")
        .update({"avatar_url": url, "updated_at": _now_iso()})
        .eq("id", user_id)
        .execute()
    )


@router.post("/me/avatar", response_model=UserResponse)
async def upload_my_avatar(
    request: UpdateAvatarRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    _rate: None = AvatarRateLimit,
):
    """Set the caller's profile picture from a base64 JPEG.

    Order is store → write → delete-the-old, and each step's failure mode is deliberate:

    * store fails  → nothing changed, AVATAR_UPLOAD_FAILED, the old picture still shows.
    * write fails  → the just-uploaded object is orphaned, so we remove it again rather than
      leave a byte nobody references. Answering 200 here would be worse than an error: the
      client adopts the response, and a response built from an unwritten row carries the OLD
      avatar_url, so the user's new picture silently vanishes.
    * delete fails → warned, non-fatal. An orphan is swept by account deletion.

    The previous object is removed only AFTER the write is confirmed. Deleting it first would
    404 any view still rendering the old URL and flash the placeholder glyph.
    """
    user_id = user["id"]
    previous_url = user.get("avatar_url")

    try:
        jpeg = avatar_service.decode_and_validate(request.image_base64)
    except AvatarTooLargeError as exc:
        logger.warning("[Avatar] rejected oversize upload user=%s: %s", user_id, exc)
        return make_error_response(ErrorCode.AVATAR_TOO_LARGE, message=str(exc))
    except AvatarInvalidImageError as exc:
        logger.warning("[Avatar] rejected invalid upload user=%s: %s", user_id, exc)
        return make_error_response(ErrorCode.AVATAR_INVALID_IMAGE, message=str(exc))

    try:
        stored_url = await avatar_service.store_avatar(user_id, jpeg)
    except AvatarError as exc:
        return make_error_response(
            ErrorCode.AVATAR_UPLOAD_FAILED,
            message=f"{type(exc).__name__}: {exc}",
            details={"step": "store"},
        )

    try:
        result = await asyncio.to_thread(_write_avatar_url, supabase, user_id, stored_url)
    except Exception as exc:  # noqa: BLE001 — surfaced immediately below
        logger.error(
            "[Avatar] profile write failed user=%s: %s",
            user_id, f"{type(exc).__name__}: {exc}",
        )
        await avatar_service.remove_object(user_id, stored_url)
        return make_error_response(
            ErrorCode.AVATAR_UPLOAD_FAILED,
            message=f"{type(exc).__name__}: {exc}",
            details={"step": "persist"},
        )

    # Zero rows is NOT an exception in the Supabase SDK. Untreated it becomes a silent
    # success: the object exists, the column does not point at it, and the client renders the
    # placeholder again with no error anywhere.
    if not result.data:
        logger.error("[Avatar] profile write matched no rows for user=%s", user_id)
        await avatar_service.remove_object(user_id, stored_url)
        return make_error_response(
            ErrorCode.AVATAR_UPLOAD_FAILED,
            message="profile update matched no rows",
            details={"step": "persist"},
        )

    if previous_url and previous_url != stored_url:
        await avatar_service.remove_object(user_id, previous_url)

    signed = await avatar_service.signed_avatar_url(user_id, stored_url)
    logger.info("[Avatar] updated for user=%s", user_id)
    return _avatar_response(user, result.data[0], signed)


@router.delete("/me/avatar", response_model=UserResponse)
async def delete_my_avatar(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    _rate: None = AvatarRateLimit,
):
    """Remove the caller's profile picture, reverting to the placeholder glyph.

    The column is cleared FIRST and the object removed after: if the remove fails the user
    still sees the change they asked for, and the orphan is swept by account deletion. The
    reverse order would leave a column pointing at a deleted object — a 404 render, which is
    indistinguishable from a broken avatar.
    """
    user_id = user["id"]
    previous_url = user.get("avatar_url")

    try:
        result = await asyncio.to_thread(_write_avatar_url, supabase, user_id, None)
    except Exception as exc:  # noqa: BLE001 — surfaced immediately below
        logger.error(
            "[Avatar] clear failed user=%s: %s", user_id, f"{type(exc).__name__}: {exc}"
        )
        return make_error_response(
            ErrorCode.AVATAR_UPLOAD_FAILED, message=f"{type(exc).__name__}: {exc}"
        )

    if not result.data:
        logger.error("[Avatar] clear matched no rows for user=%s", user_id)
        return make_error_response(
            ErrorCode.AVATAR_UPLOAD_FAILED, message="profile update matched no rows"
        )

    await avatar_service.remove_object(user_id, previous_url)
    logger.info("[Avatar] removed for user=%s", user_id)
    return _avatar_response(user, result.data[0], None)


# User-keyed tables with NO foreign key to public.users, so the auth.users cascade does
# NOT reach them. Each omits the FK deliberately (the shared guest id has to be able to
# write them), which means account deletion has to clear them explicitly.
#   user_learn_progress  — migrations/067, "no FK so the shared guest id works"
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
# `user_book_progress` (migrations/066) is deliberately ABSENT: 067 superseded it, copied its
# rows into `user_learn_progress`, and nothing has read or written it since — production held
# 0 rows. Migration 116 drops the table. Do NOT re-add it here, and do not add it to
# `claim-guest-data` either; the live book-progress path is `user_learn_progress` with
# `content_type='book_core'`, which both already cover.
_UNLINKED_USER_TABLES: tuple[str, ...] = (
    "user_learn_progress",
    "chat_usage_budget",
    "credit_transactions",
    "watchlist_items",
    "portfolios",   # migrations/108, same dropped cascade
    "push_send_log",  # migrations/109, no FK by design
    # migrations/110 dropped research_reports_user_id_fkey (ON DELETE CASCADE) so guests can be
    # partitioned per install. That cascade WAS the account-deletion path for this table, so
    # without this entry a deleted account's research reports — ticker, thesis, fair value —
    # would survive, which the privacy policy says they do not.
    "research_reports",
    # migrations/111, same story for chat. `chat_messages` needs no entry: it cascades from
    # chat_sessions(id), which this delete removes. Chat transcripts are the most sensitive
    # rows the app stores — people paste holdings into them — so an incomplete deletion here
    # is the worst version of that bug.
    "chat_sessions",
    # migrations/131. Guest-writable, so it never had an FK to cascade from. The row
    # records how this person prefers to learn and what they read about — squarely the
    # "profile data" the privacy policy promises deletion removes.
    "user_investor_profile",
)

# Same purge, different column. `analytics_events.identity_key` holds the real user id
# for a signed-in user, so their behavioural history would otherwise survive account
# deletion — which the privacy policy promises it does not.
_UNLINKED_IDENTITY_TABLES: tuple[str, ...] = (
    "analytics_events",
)

_RESEARCH_PDF_BUCKET = "research-pdfs"
_AVATAR_BUCKET = avatar_service.BUCKET


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
            # A table that does not exist cannot be holding this user's data, so a
            # "relation not found" is a completed purge, not a failed one.
            #
            # This is NOT a convenience. A failure here aborts deletion with a 500 (see the
            # caller), so without this branch, dropping a table in a migration would break
            # in-app account deletion for EVERY user until the code that stops listing it
            # deploys — an App Store 5.1.1(v) break, opened by a schema change that looks
            # entirely unrelated. It makes the deploy/migrate ordering safe in BOTH
            # directions instead of only code-first. Discovered while writing migration 116
            # (`DROP TABLE user_book_progress`).
            #
            # Deliberately narrow: PGRST205 is PostgREST's schema-cache miss, and 42P01 is
            # Postgres' own `undefined_table`. Every other error still fails the deletion.
            detail = str(e)
            if "PGRST205" in detail or "42P01" in detail:
                logger.warning(
                    "Account deletion: %s no longer exists — treating as already purged "
                    "for user=%s. Remove it from _UNLINKED_USER_TABLES.",
                    table, user_id,
                )
                continue
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
    # storage3 silently caps an unpaginated `list()` at 100 objects
    # (`DEFAULT_SEARCH_OPTIONS = {"limit": 100, "offset": 0, ...}`). A single call therefore
    # deleted the first 100 PDFs, returned success, and left the rest in Storage FOREVER
    # while the endpoint reported a complete deletion — and the privacy policy promises
    # otherwise. Not hypothetical: a Max subscriber gets 4000 credits ÷ 20 = 200 reports a
    # month, so the very users who generate the most are the ones whose data survives.
    _PAGE = 100
    _MAX_PAGES = 500          # 50k objects; a bound so a bad page-cursor cannot spin forever
    try:
        bucket = supabase.storage.from_(_RESEARCH_PDF_BUCKET)
        removed = 0
        for page in range(_MAX_PAGES):
            entries = bucket.list(
                prefix, {"limit": _PAGE, "offset": 0, "sortBy": {"column": "name", "order": "asc"}}
            ) or []
            names = [e["name"] for e in entries if isinstance(e, dict) and e.get("name")]
            if not names:
                break
            # Offset stays 0 on purpose: each pass DELETES what it listed, so the next page
            # of survivors shifts down into the same window. Paging with a growing offset
            # against a shrinking collection is how you skip objects.
            bucket.remove([f"{prefix}/{name}" for name in names])
            removed += len(names)
            if len(names) < _PAGE:
                break
        else:
            # Loop ran to _MAX_PAGES without emptying — report it rather than claim success,
            # so the caller keeps the auth row and the user can retry.
            msg = f"still not empty after {_MAX_PAGES} pages ({removed} removed)"
            logger.error(
                "Account deletion: report PDF purge incomplete for user=%s: %s", user_id, msg
            )
            return msg

        if removed:
            logger.info(
                "Account deletion: removed %d report PDF(s) for user=%s", removed, user_id
            )
        return None
    except Exception as e:  # noqa: BLE001 — recorded and surfaced below
        logger.error(
            "Account deletion: failed to purge report PDFs for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        return f"{type(e).__name__}: {e}"


def _purge_avatars(supabase: Client, user_id: str) -> Optional[str]:
    """Delete the user's profile pictures from Storage.

    Mirrors `_purge_research_pdfs` — same pagination, same offset-stays-0 reasoning, same
    Optional[str] contract (None = done, a string = the failure to report).

    ⚠️ ONE DELIBERATE DIFFERENCE: a MISSING BUCKET is treated as a completed purge.
    `research-pdfs` has existed since migration 063, but `user-avatars` arrives with 152 and
    the user applies migrations by hand — so there is a real window where the code is deployed
    and the bucket is not. Without this carve-out, `bucket.list()` raises, `failures` is set,
    and DELETE /users/me returns ACCOUNT_DELETE_INCOMPLETE for EVERY user, including the ~all
    of them who never uploaded a picture. An account the user cannot delete is an App Store
    5.1.1(v) break. `_purge_unlinked_rows` carries the same carve-out for a dropped TABLE
    (PGRST205 / 42P01) for exactly this reason.
    """
    prefix = f"{avatar_service.PREFIX}/{user_id}"
    _PAGE = 100
    _MAX_PAGES = 500
    try:
        bucket = supabase.storage.from_(_AVATAR_BUCKET)
        removed = 0
        for page in range(_MAX_PAGES):
            entries = bucket.list(
                prefix, {"limit": _PAGE, "offset": 0, "sortBy": {"column": "name", "order": "asc"}}
            ) or []
            names = [e["name"] for e in entries if isinstance(e, dict) and e.get("name")]
            if not names:
                break
            bucket.remove([f"{prefix}/{name}" for name in names])
            removed += len(names)
            if len(names) < _PAGE:
                break
        else:
            msg = f"still not empty after {_MAX_PAGES} pages ({removed} removed)"
            logger.error(
                "Account deletion: avatar purge incomplete for user=%s: %s", user_id, msg
            )
            return msg

        if removed:
            logger.info(
                "Account deletion: removed %d avatar object(s) for user=%s", removed, user_id
            )
        return None
    except Exception as e:  # noqa: BLE001 — classified, then recorded or waived below
        detail = str(e)
        if "not found" in detail.lower() or "404" in detail:
            # The bucket itself is absent. There is nothing of this user's to delete, so the
            # purge is vacuously complete — do NOT block the deletion. Loud, because the only
            # legitimate cause is a migration that has not been applied yet.
            logger.warning(
                "Account deletion: bucket %s is missing — treating the avatar purge as "
                "complete for user=%s. Apply migration 152 (%s: %s)",
                _AVATAR_BUCKET, user_id, type(e).__name__, e,
            )
            return None
        logger.error(
            "Account deletion: failed to purge avatars for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        return f"{type(e).__name__}: {e}"


@router.delete("/me")
async def delete_account(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Permanently delete the CALLER'S OWN account and all of their data.

    Three steps, in this order:

    1. Storage — remove `research-pdfs/reports/<user_id>/*`. Done FIRST because the
       object path is the only remaining handle on those files; if the auth row went
       first and step 3 then failed, the PDFs would be orphaned with no way to find them.
    2. Un-FK'd tables — `_UNLINKED_USER_TABLES`. These have no foreign key to
       `public.users`, so the cascade in step 3 does not reach them.
    3. `auth.users` — cascades to `public.users` and every FK-linked child table
       (user_credits, subscriptions,
       user_settings, device_tokens, whale_follows, …). NOT chat_sessions: migration 111
       dropped its FK, so it is purged explicitly in step 2 via _UNLINKED_USER_TABLES
       (chat_messages still cascades from chat_sessions(id)).

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
        # AUTH_FORBIDDEN, not a bare 403: the credential is fine, the caller just isn't allowed
        # to do this. Unreachable in practice (`get_current_user` 401s a guest before we get
        # here) — defense in depth, and one of the two sites the enum comment names.
        raise auth_error(
            ErrorCode.AUTH_FORBIDDEN,
            message="refusing to delete the shared guest account",
            user_message="This account can't be deleted.",
        )

    failures: dict[str, str] = {}

    # 1. Storage objects (before the identity row disappears).
    pdf_error = _purge_research_pdfs(supabase, user_id)
    if pdf_error:
        failures[f"storage:{_RESEARCH_PDF_BUCKET}"] = pdf_error

    avatar_error = _purge_avatars(supabase, user_id)
    if avatar_error:
        failures[f"storage:{_AVATAR_BUCKET}"] = avatar_error

    # 2. Tables the cascade cannot reach.
    failures.update(_purge_unlinked_rows(supabase, user_id))

    if failures:
        logger.error(
            "Account deletion aborted for user=%s — auth row kept so a retry can "
            "still reach the remaining data. Failed targets: %s",
            user_id, ", ".join(sorted(failures)),
        )
        # Structured, not a bare string: a bare `detail` renders as `{"detail": ...}`, and
        # `APIClient.validateResponse`'s 5xx arm has no `detail` fallback (only the 4xx arm
        # does). So this carefully-worded message was DISCARDED and the user saw a generic
        # transient server error for an operation that is neither transient nor complete —
        # their account still exists and they were not told.
        return make_error_response(
            ErrorCode.ACCOUNT_DELETE_INCOMPLETE,
            message=f"partial deletion; failed targets: {', '.join(sorted(failures))}",
            # Flat scalars only — iOS `AnyCodable` decodes String/Int/Double/Bool and
            # silently yields "" for anything else, so this is a joined string, not a list.
            details={"failed": ", ".join(sorted(failures))},
        )

    # 3. Identity row + every FK-linked child table.
    try:
        # Isolated auth client. `admin.*` does not emit SIGNED_IN today, so this is not the
        # demotion path — but keeping ALL `auth.*` off the service-role singleton makes the
        # invariant simple, greppable, and testable (see test_supabase_client_isolation.py).
        # Falls back to `supabase` when this handler is called directly (the suite's idiom);
        # see auth._auth_of for the same accommodation.
        client = auth_client if hasattr(auth_client, "auth") else supabase
        client.auth.admin.delete_user(user_id)
    except Exception as e:
        logger.error(
            "Account deletion failed at the auth step for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        # Same contract as the partial-failure branch above. Storage and the un-FK'd tables
        # have already been purged at this point, so the account is genuinely half-deleted —
        # "please try again" is right, and the user must know it still exists.
        return make_error_response(
            ErrorCode.ACCOUNT_DELETE_INCOMPLETE,
            message=f"auth-step deletion failed: {type(e).__name__}",
        )

    logger.info("Account deleted for user=%s (storage + unlinked rows + cascade)", user_id)
    return {"deleted": True}


# ── Notification inbox ───────────────────────────────────────────────────────
#
# `notification_events` is written by the dispatcher BEFORE delivery is attempted, so
# these endpoints see every notification the system decided to send — including ones
# that were deferred by quiet hours or that found no registered device. That is the
# point: a push that arrives while the phone is face-down is gone, and before this
# table the app had no record of what fired.
#
# Auth-only (`get_current_user`), matching /me/settings and /me/devices. Push never
# reaches a guest — `device_tokens` is FK-bound to public.users — so a guest inbox
# would be permanently empty by construction.


@router.get("/me/notifications", response_model=NotificationListResponse)
async def list_my_notifications(
    limit: int = DEFAULT_PAGE,
    before: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """Newest-first page of this user's notifications, plus the unread count.

    `before` is a KEYSET cursor (the `created_at` of the last item on the previous
    page), not an offset: rows arrive continuously at the head, so an offset page 2
    would repeat or skip whenever a notification landed between requests.

    A read failure is 503 NOTIFICATIONS_UNAVAILABLE, never an empty 200 — an empty
    inbox and a broken inbox look identical to a user, and "No notifications yet"
    rendered over a database error is a failure nobody reports.
    """
    try:
        return await asyncio.to_thread(
            get_notification_inbox_service().list_for_user,
            user["id"], limit=limit, before=before,
        )
    except NotificationInboxUnavailable as e:
        return make_error_response(
            ErrorCode.NOTIFICATIONS_UNAVAILABLE,
            message=str(e),
        )


@router.post("/me/notifications/read", response_model=MarkReadResponse)
async def mark_my_notifications_read(
    request: MarkReadRequest,
    user: dict = Depends(get_current_user),
):
    """Mark specific notifications (or all of them) as read.

    Ownership is enforced by scoping every write to `user_id` IN ADDITION to the id
    filter. Filtering on `id` alone would be an IDOR — one user clearing another's
    badge — and because the backend holds the service-role key, that in-code filter is
    the effective wall rather than RLS (SYSTEM_DESIGN_GUIDELINES §9).
    """
    service = get_notification_inbox_service()
    try:
        updated = await asyncio.to_thread(
            service.mark_read, user["id"], ids=request.ids, mark_all=request.all
        )
    except NotificationInboxUnavailable as e:
        return make_error_response(
            ErrorCode.NOTIFICATIONS_UNAVAILABLE,
            message=str(e),
        )
    unread = await asyncio.to_thread(service.unread_count, user["id"])
    return MarkReadResponse(updated=updated, unread_count=unread)
