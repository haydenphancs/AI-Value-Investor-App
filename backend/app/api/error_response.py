"""
Structured error responses for the report pipeline.

The iOS `APIErrorResponse` schema (Core/Services/APIClient.swift) already
decodes:
    {error_code, message, user_message, action?, details?}
…on 403/422 paths. Phase 3 extends that contract to 502/503 errors from
the report-generation path so iOS can show a debuggable, actionable
message instead of "Server error (502)".

Public API:
  - `ErrorCode` — enum of every machine-readable code we emit
  - `make_error_response(...)` — build a `JSONResponse` with the
    structured body
  - `classify_exception(exc)` — inspect an exception and return
    `(ErrorCode, http_status)` based on its class + message regex
  - `error_response_from_exception(exc, ...)` — one-line wrapper that
    classifies + builds the response with `details.underlying` carrying
    the truncated underlying error text for production debugging

The classifier looks at exception class names and message keywords —
it does NOT import google.api_core or httpx at module load, so the
helper stays cheap to import and resilient to dependency changes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple

from fastapi.responses import JSONResponse


# ── Error code registry ───────────────────────────────────────────────


class ErrorCode(str, Enum):
    """Every code iOS may receive. Add new codes here so the iOS
    `userFriendlyError` switch covers them centrally."""

    # ── Input / lookup ────────────────────────────────────────────────
    TICKER_NOT_FOUND = "TICKER_NOT_FOUND"
    INVALID_PERSONA = "INVALID_PERSONA"
    INVALID_INPUT = "INVALID_INPUT"
    # An Emerging Frontiers theme slug that isn't an active `trending_themes` row
    # (e.g. a card deleted between a dashboard load and the tap).
    THEME_NOT_FOUND = "THEME_NOT_FOUND"

    # ── Upstream services ────────────────────────────────────────────
    FMP_RATE_LIMITED = "FMP_RATE_LIMITED"
    FMP_UNAVAILABLE = "FMP_UNAVAILABLE"
    GEMINI_QUOTA_EXCEEDED = "GEMINI_QUOTA_EXCEEDED"
    GEMINI_UNAVAILABLE = "GEMINI_UNAVAILABLE"

    # ── Data / pipeline ──────────────────────────────────────────────
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED"
    # The user's watchlist row set could not be READ (Supabase/PostgREST blip).
    # Load-bearing: this MUST be distinguishable from "the watchlist is empty".
    # The Assets tab purges portfolio tickers that are absent from the feed, so a
    # read failure laundered into an empty 200 makes the client delete every
    # ticker (and its hand-entered shares) from every portfolio, permanently.
    WATCHLIST_UNAVAILABLE = "WATCHLIST_UNAVAILABLE"
    # The user's synced preference blob could not be READ. Same shape of hazard as
    # WATCHLIST_UNAVAILABLE, and for the same reason it must not be an empty 200:
    # iOS gates its FULL-REPLACE push on "the server state is known", so a read
    # failure served as `{}` opens that gate on a session whose blob was never seen,
    # and the next push overwrites ~20 synced keys with whatever this device holds.
    # On a fresh install that is close to nothing — and it propagates everywhere.
    SETTINGS_UNAVAILABLE = "SETTINGS_UNAVAILABLE"

    # The notification inbox could not be READ. Same reasoning as the two above:
    # an EMPTY inbox and a BROKEN inbox look identical to a user, and "No
    # notifications yet" rendered over a database error is the kind of failure
    # nobody reports because it looks exactly like the intended empty state.
    NOTIFICATIONS_UNAVAILABLE = "NOTIFICATIONS_UNAVAILABLE"

    # Account deletion got PART WAY and stopped. The auth row is deliberately kept so a
    # retry can still reach the rest, which means the account STILL EXISTS — and that is
    # the one thing the user has to be told. Both 500 paths in `delete_account` used to
    # raise a bare-string `HTTPException`, so the carefully-worded detail never reached
    # iOS at all: `validateResponse`'s 5xx arm has no `{"detail": ...}` fallback (only the
    # 4xx arm does), so it threw a generic `.serverError`, which `AppError` renders as a
    # transient "try again" — for an operation that is neither transient nor complete.
    # Distinct from SYSTEM_BUSY because retrying is exactly right here, but the user must
    # not be left believing their data is gone when it is not.
    ACCOUNT_DELETE_INCOMPLETE = "ACCOUNT_DELETE_INCOMPLETE"

    # The caller is at their price-alert quota. Distinct from INVALID_INPUT because
    # the input was fine — the ACCOUNT is full — so the client action is "remove one",
    # not "fix what you typed". Collapsing them would surface a form-validation error
    # on a form with nothing wrong in it.
    PRICE_ALERT_LIMIT_REACHED = "PRICE_ALERT_LIMIT_REACHED"

    # The alert id is not this caller's (or is gone). 404 rather than 403 on purpose:
    # a 403 would CONFIRM the id exists and belongs to someone else, which is an
    # enumeration oracle. "Not found" is true from this caller's perspective either way.
    PRICE_ALERT_NOT_FOUND = "PRICE_ALERT_NOT_FOUND"

    # ── Research-flow specific ───────────────────────────────────────
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
    REPORT_NOT_READY = "REPORT_NOT_READY"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    # The caller's plan does not allow tracking THIS whale — a Free account outside its one
    # free slot, or a Pro account already at its limit. Deliberately NOT INSUFFICIENT_CREDITS:
    # that code means "top up", routes iOS to BuyCredits, and buying credits would not let you
    # follow one more investor. This is a PLAN gate and must reach the plan sheet.
    WHALE_FOLLOW_LOCKED = "WHALE_FOLLOW_LOCKED"
    # The whale exists in the roster but its profile could not be built (FMP outage,
    # Supabase blip). Distinct from WHALE_NOT_FOUND: retryable, and the roster row the
    # user tapped is still legitimate.
    WHALE_PROFILE_UNAVAILABLE = "WHALE_PROFILE_UNAVAILABLE"
    WHALE_NOT_FOUND = "WHALE_NOT_FOUND"
    # Terminal, NOT retryable. Ownership of an App Store transaction never moves, so this
    # condition can never clear — which is why it must not share a code with the retryable
    # billing failures. See PurchaseBoundToAnotherAccount in iap_service.py.
    PURCHASE_ALREADY_LINKED = "PURCHASE_ALREADY_LINKED"
    # Distinct from the above, and the distinction is money. PURCHASE_ALREADY_LINKED means we
    # HAVE the transaction recorded against another account — somebody was credited, and the
    # client is right to finish it. PURCHASE_ACCOUNT_MISMATCH means the transaction's
    # `appAccountToken` names a different account and we recorded NOTHING, so NOBODY has been
    # credited. Finishing that one destroys a purchase the user paid for, with no redelivery
    # left to repair it — the client must keep it unfinished until the right account signs in.
    PURCHASE_ACCOUNT_MISMATCH = "PURCHASE_ACCOUNT_MISMATCH"
    # Apple refunded or cancelled this purchase. Terminal AND finishable — the third distinct
    # answer to "this purchase isn't grantable". It is separate from PURCHASE_ALREADY_LINKED
    # (someone else was credited) and from INVALID_INPUT (which the client reads as a
    # correctable caller error and never finishes, so a revoked transaction was redelivered on
    # every launch forever). See PurchaseRevoked in iap_service.py.
    PURCHASE_REVOKED = "PURCHASE_REVOKED"
    TOO_MANY_CONCURRENT_REPORTS = "TOO_MANY_CONCURRENT_REPORTS"
    # Global overload backstop — distinct from the per-user cap above. The
    # whole service is at capacity, not just this user. 409 (not 429) so iOS
    # surfaces the user_message instead of a generic "wait 60s".
    SYSTEM_BUSY = "SYSTEM_BUSY"

    # ── Chat abuse / cost controls (OWASP LLM10 — denial-of-wallet) ──────────
    # The user message exceeded the friendly length ceiling (400).
    CHAT_MESSAGE_TOO_LONG = "CHAT_MESSAGE_TOO_LONG"
    # The per-user daily chat turn budget is exhausted. 409 (NOT 429) for the same
    # reason as TOO_MANY_CONCURRENT_REPORTS: iOS swallows 429 bodies before decode,
    # so 409 lets the structured user_message surface.
    CHAT_DAILY_LIMIT_REACHED = "CHAT_DAILY_LIMIT_REACHED"

    # ── Auth ──────────────────────────────────────────────────────────
    # Sign-in blocked because the address has not been confirmed yet. Distinct from a
    # generic 401 so the client can offer "resend confirmation" instead of implying the
    # password was wrong.
    EMAIL_NOT_CONFIRMED = "EMAIL_NOT_CONFIRMED"

    # The five below are deliberately DISTINCT rather than one AUTH_FAILED, because iOS must
    # react differently to each and only two of them may destroy a stored credential. Collapsing
    # them is how "tapped Follow while signed out" became "your session expired" — and how a
    # transient identity-store blip could sign a valid user out.
    #
    # No credential was presented at all. The caller is a guest who reached a route that needs an
    # account. NOT a session failure: the client must show "Sign In Required" and must NOT touch
    # the Keychain, because there is nothing wrong with any token it holds.
    AUTH_REQUIRED = "AUTH_REQUIRED"
    # A credential WAS presented and could not be verified (bad signature, expired, malformed,
    # or a refresh token used as an access token). The client should refresh and retry; only if
    # the refresh itself genuinely fails may it drop the credential.
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    # The token is well-formed and correctly signed but predates the account's last password
    # change (migration 105). The session is over by design — re-authentication is the only path.
    AUTH_SESSION_EXPIRED = "AUTH_SESSION_EXPIRED"
    # Valid token naming an account with no `public.users` row (deleted, or a signup whose
    # trigger never seeded it). Unrecoverable for this credential; the client drops it.
    AUTH_ACCOUNT_NOT_FOUND = "AUTH_ACCOUNT_NOT_FOUND"
    # Authenticated fine, but not permitted (admin allowlist, deleting the guest account).
    # 403, and explicitly NOT an auth error on the client — re-authenticating cannot help, so
    # nothing about the stored session should change.
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    # The identity store could not be READ (Supabase/PostgREST blip). Load-bearing that this is
    # separable from "your token is bad": it is retryable and the client MUST keep its
    # credential. Previously this surfaced as a bare 500 from `get_current_user`, which iOS does
    # not classify as an auth error — so the client kept retrying a request that never resolved.
    AUTH_UNAVAILABLE = "AUTH_UNAVAILABLE"

    # The two below describe CREDENTIALS SUBMITTED IN A REQUEST BODY, not the state of a stored
    # bearer token — which is what every code above is about. That distinction is the whole
    # reason they exist. With no code for "the password you just typed is wrong", `auth.py`
    # raised a bare-string 401, iOS failed to decode it against the contract, fell back to
    # `APIError.unauthorized`, and showed its hardcoded "Your session has expired." So a user
    # mistyping their current password was told their session had ended — and because
    # `.unauthorized` sets `triggersTokenRefresh` while `.changePassword` is excluded from
    # `isAuthEndpoint`, the client also ran a full refresh and REPLAYED the request, spending
    # two of the five per-user attempts on one typo.
    #
    # NEITHER may clear a stored credential and neither may trigger a refresh: nothing is wrong
    # with the caller's session. `triggersTokenRefresh` enumerates AUTH_TOKEN_INVALID and
    # AUTH_SESSION_EXPIRED only, so that falls out automatically — do not add these to it.
    #
    # The email/password or current-password supplied does not match. 401, but the action is
    # `fix_input`, not `sign_in`: on `/auth/login` the user is already looking at the sign-in
    # form, and on `/auth/change-password` they are signed in — "Sign In" is a circle in both.
    AUTH_CREDENTIALS_INVALID = "AUTH_CREDENTIALS_INVALID"
    # An Apple/Google identity token failed verification (bad signature, wrong audience,
    # expired, nonce mismatch). Distinct from the above so the copy can stay honest: there is no
    # password in that flow, and the old fallback told those users to check one.
    AUTH_PROVIDER_FAILED = "AUTH_PROVIDER_FAILED"


# Default user-facing copy per code. Endpoints can override per-call.
_USER_MESSAGES: Dict[ErrorCode, str] = {
    ErrorCode.EMAIL_NOT_CONFIRMED: (
        "Please confirm your email address first. Check your inbox for the "
        "confirmation link \u2014 including your spam folder."
    ),
    ErrorCode.TICKER_NOT_FOUND: (
        "We couldn't find that ticker symbol. Check the spelling and try again."
    ),
    ErrorCode.INVALID_PERSONA: (
        "That investor persona isn't supported."
    ),
    ErrorCode.INVALID_INPUT: (
        "The request was missing or malformed."
    ),
    ErrorCode.THEME_NOT_FOUND: (
        "That theme is no longer available."
    ),
    ErrorCode.FMP_RATE_LIMITED: (
        "Market data is rate-limited right now. Please try again in a minute."
    ),
    ErrorCode.FMP_UNAVAILABLE: (
        "Our market data provider is temporarily unavailable. Try again shortly."
    ),
    ErrorCode.GEMINI_QUOTA_EXCEEDED: (
        "AI analysis quota exceeded. Please try again in a few minutes."
    ),
    ErrorCode.GEMINI_UNAVAILABLE: (
        "The AI analysis engine is temporarily unavailable. Try again shortly."
    ),
    ErrorCode.DATA_INCOMPLETE: (
        "We couldn't gather enough data for this ticker to produce a full report."
    ),
    ErrorCode.REPORT_GENERATION_FAILED: (
        "The report failed to generate. Please try again."
    ),
    ErrorCode.WATCHLIST_UNAVAILABLE: (
        "We couldn't load your holdings right now. Pull to refresh in a moment."
    ),
    ErrorCode.SETTINGS_UNAVAILABLE: (
        "We couldn't load your settings right now. They'll sync automatically in a moment."
    ),
    ErrorCode.NOTIFICATIONS_UNAVAILABLE: (
        "We couldn't load your notifications right now. Pull to refresh in a moment."
    ),
    ErrorCode.ACCOUNT_DELETE_INCOMPLETE: (
        "We couldn't finish deleting your account, so it's still here. "
        "Some data may already have been removed. Please try again."
    ),
    ErrorCode.PRICE_ALERT_LIMIT_REACHED: (
        "You've reached your price-alert limit. Remove one to add another."
    ),
    ErrorCode.PRICE_ALERT_NOT_FOUND: (
        "That price alert no longer exists."
    ),
    ErrorCode.REPORT_NOT_FOUND: (
        "That report no longer exists."
    ),
    ErrorCode.REPORT_NOT_READY: (
        "The report is still generating. Try again in a few seconds."
    ),
    ErrorCode.INSUFFICIENT_CREDITS: (
        "You don't have enough credits. Upgrade your tier or wait for the monthly reset."
    ),
    # Number-free on purpose: the limit differs per plan, and hardcoding one here is how a
    # marketing string drifts from the table that actually enforces it. The endpoint sends
    # the real numbers in `details`.
    ErrorCode.WHALE_PROFILE_UNAVAILABLE: (
        "We couldn't load this investor right now. Please try again shortly."
    ),
    ErrorCode.WHALE_NOT_FOUND: (
        "We couldn't find this investor. They may no longer be tracked."
    ),
    ErrorCode.WHALE_FOLLOW_LOCKED: (
        "Your plan doesn't include tracking this investor. Upgrade to follow more."
    ),
    ErrorCode.PURCHASE_ALREADY_LINKED: (
        "This subscription is already linked to a different Caydex account. Sign in with "
        "that account, or contact support if you think this is wrong."
    ),
    ErrorCode.PURCHASE_ACCOUNT_MISMATCH: (
        "This purchase was made from a different Caydex account. Sign in with that account "
        "and it will be applied — nothing has been lost."
    ),
    ErrorCode.PURCHASE_REVOKED: (
        "This purchase was refunded, so its credits weren't added. If you think that's "
        "wrong, contact support and we'll sort it out."
    ),
    # Number-free default so the cap value never drifts here; the endpoint
    # overrides user_message with the live cap (e.g. "up to 4 at once").
    ErrorCode.TOO_MANY_CONCURRENT_REPORTS: (
        "You're already running the maximum number of analyses at once. "
        "Wait for one to finish, then try again."
    ),
    ErrorCode.SYSTEM_BUSY: (
        "Our analysis engine is at capacity right now. "
        "Please try again in a moment."
    ),
    ErrorCode.CHAT_MESSAGE_TOO_LONG: (
        "Your message is too long. Please shorten it and try again."
    ),
    ErrorCode.CHAT_DAILY_LIMIT_REACHED: (
        "You've reached today's chat limit. Please try again tomorrow."
    ),
    ErrorCode.AUTH_REQUIRED: (
        "Sign in to use this feature."
    ),
    ErrorCode.AUTH_TOKEN_INVALID: (
        "Your session needs to be refreshed. Please sign in again if this keeps happening."
    ),
    ErrorCode.AUTH_SESSION_EXPIRED: (
        "Your password was changed, so this session ended. Please sign in again."
    ),
    ErrorCode.AUTH_ACCOUNT_NOT_FOUND: (
        "We couldn't find your account. Please sign in again."
    ),
    ErrorCode.AUTH_FORBIDDEN: (
        "You don't have access to this."
    ),
    ErrorCode.AUTH_UNAVAILABLE: (
        "We couldn't verify your account just now. Please try again in a moment."
    ),
    # Deliberately does NOT say which of the two was wrong — that would be an account-existence
    # oracle on a finance app. Endpoints override this with something more specific when the
    # caller is already authenticated (change-password knows the email is right).
    ErrorCode.AUTH_CREDENTIALS_INVALID: (
        "That email or password doesn't match. Please check and try again."
    ),
    ErrorCode.AUTH_PROVIDER_FAILED: (
        "We couldn't complete that sign-in. Please try again."
    ),
}


# Suggested user action per code (optional, shown as a button label / hint).
_DEFAULT_ACTIONS: Dict[ErrorCode, str] = {
    ErrorCode.EMAIL_NOT_CONFIRMED: "confirm_email",
    ErrorCode.TICKER_NOT_FOUND: "check_symbol",
    ErrorCode.FMP_RATE_LIMITED: "retry_later",
    ErrorCode.FMP_UNAVAILABLE: "retry_later",
    ErrorCode.GEMINI_QUOTA_EXCEEDED: "retry_later",
    ErrorCode.GEMINI_UNAVAILABLE: "retry_later",
    ErrorCode.REPORT_NOT_READY: "poll_again",
    ErrorCode.INSUFFICIENT_CREDITS: "upgrade",
    ErrorCode.WHALE_PROFILE_UNAVAILABLE: "retry",
    ErrorCode.WHALE_FOLLOW_LOCKED: "upgrade",
    # NOT "retry_later": retrying can never succeed, and telling the client to wait is what
    # left StoreKit redelivering the transaction on every launch forever.
    ErrorCode.PURCHASE_ALREADY_LINKED: "contact_support",
    # `sign_in`, NOT `contact_support`: the purchase is intact and un-granted, and signing in
    # as the buying account is the action that claims it.
    ErrorCode.PURCHASE_ACCOUNT_MISMATCH: "sign_in",
    # NOT "retry_later": a refund can never become grantable, and telling the client to wait
    # is exactly what kept the transaction unfinished and redelivering.
    ErrorCode.PURCHASE_REVOKED: "contact_support",
    ErrorCode.TOO_MANY_CONCURRENT_REPORTS: "retry_later",
    ErrorCode.SYSTEM_BUSY: "retry_later",
    ErrorCode.CHAT_MESSAGE_TOO_LONG: "fix_input",
    ErrorCode.CHAT_DAILY_LIMIT_REACHED: "retry_later",
    ErrorCode.WATCHLIST_UNAVAILABLE: "retry_later",
    ErrorCode.SETTINGS_UNAVAILABLE: "retry_later",
    ErrorCode.NOTIFICATIONS_UNAVAILABLE: "retry_later",
    # Retrying is the right action AND it is safe: the purge is idempotent and the auth
    # row was kept precisely so a second attempt can reach whatever survived.
    ErrorCode.ACCOUNT_DELETE_INCOMPLETE: "retry_later",
    # NOT retry_later: retrying changes nothing until the user deletes an alert.
    ErrorCode.PRICE_ALERT_LIMIT_REACHED: "fix_input",
    # PRICE_ALERT_NOT_FOUND deliberately has NO action, matching THEME_NOT_FOUND: there
    # is nothing for the user to do, and "none" is not one of the iOS `ErrorAction`
    # cases — it would decode as an unknown action rather than as no action.
    # `sign_in` maps to iOS `ErrorAction.signIn`, whose button opens SignInView.
    ErrorCode.AUTH_REQUIRED: "sign_in",
    ErrorCode.AUTH_TOKEN_INVALID: "sign_in",
    ErrorCode.AUTH_SESSION_EXPIRED: "sign_in",
    ErrorCode.AUTH_ACCOUNT_NOT_FOUND: "sign_in",
    # Deliberately NOT sign_in: the caller is already signed in, and offering sign-in for a
    # permission wall sends them in a circle.
    ErrorCode.AUTH_FORBIDDEN: "contact_support",
    ErrorCode.AUTH_UNAVAILABLE: "retry_later",
    # Same reasoning as AUTH_FORBIDDEN, different circle: the user is looking at the form they
    # just submitted, so the useful affordance is "correct the field", not "sign in".
    ErrorCode.AUTH_CREDENTIALS_INVALID: "fix_input",
    ErrorCode.AUTH_PROVIDER_FAILED: "retry_later",
}


# Default HTTP status per code (endpoints may override).
_DEFAULT_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.EMAIL_NOT_CONFIRMED: 403,
    ErrorCode.TICKER_NOT_FOUND: 404,
    ErrorCode.INVALID_PERSONA: 400,
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.THEME_NOT_FOUND: 404,
    ErrorCode.FMP_RATE_LIMITED: 502,
    ErrorCode.FMP_UNAVAILABLE: 502,
    ErrorCode.GEMINI_QUOTA_EXCEEDED: 502,
    ErrorCode.GEMINI_UNAVAILABLE: 502,
    ErrorCode.DATA_INCOMPLETE: 502,
    ErrorCode.REPORT_GENERATION_FAILED: 502,
    ErrorCode.REPORT_NOT_FOUND: 404,
    ErrorCode.REPORT_NOT_READY: 409,
    # 402 Payment Required — the standard "you're out of credits, pay/upgrade" status.
    # Not 401/429: iOS APIClient intercepts those before decoding the body (→ generic
    # "sign in" / "wait 60s"); 402 falls through to the structured-body decode so the
    # credits user_message + action="upgrade" reach the client. Transient charge-RPC
    # failures are SYSTEM_BUSY (409, retryable), NEVER this — a DB blip must not tell a
    # paying user they're broke.
    ErrorCode.INSUFFICIENT_CREDITS: 402,
    # 403, not 402: the credential is fine and nothing needs paying off — the caller simply
    # isn't allowed this action on their plan (auth.md §2). 402 would also send iOS down the
    # top-up route, and no amount of credits unlocks a follow slot.
    ErrorCode.WHALE_PROFILE_UNAVAILABLE: 503,
    ErrorCode.WHALE_NOT_FOUND: 404,
    ErrorCode.WHALE_FOLLOW_LOCKED: 403,
    # 409 conflict — a terminal 4xx, so the client finishes the transaction instead of
    # treating it as a transient 5xx and retrying forever.
    ErrorCode.PURCHASE_ALREADY_LINKED: 409,
    # Also 409 and also terminal for THIS account — but the client must NOT finish the
    # transaction, because no account has been credited for it yet. See the ErrorCode comment.
    ErrorCode.PURCHASE_ACCOUNT_MISMATCH: 409,
    # 409 like its two siblings: terminal, and the client SHOULD finish the transaction.
    # Deliberately not 400 — `INVALID_INPUT` reads to the client as a correctable caller
    # error, so a revoked purchase was redelivered on every launch forever.
    ErrorCode.PURCHASE_REVOKED: 409,
    # 409 (NOT 429): iOS APIClient intercepts 429 before decoding the body and
    # shows a generic "wait 60s", discarding our user_message. 409 falls
    # through to the structured-body decode so the cap copy is surfaced.
    ErrorCode.TOO_MANY_CONCURRENT_REPORTS: 409,
    # Same 409 rationale — surface the SYSTEM_BUSY user_message, not a 429
    # generic. Semantically "Too Many Requests" but 429 would be swallowed.
    ErrorCode.SYSTEM_BUSY: 409,
    ErrorCode.CHAT_MESSAGE_TOO_LONG: 400,
    # 409 (NOT 429): iOS swallows 429 bodies before decode; 409 surfaces the copy.
    ErrorCode.CHAT_DAILY_LIMIT_REACHED: 409,
    # 503 Service Unavailable — the datastore is transiently unreadable. NOT 200
    # with an empty list (see the enum comment) and NOT 500: it is retryable and
    # the client must be able to tell "couldn't read" from "you have nothing".
    ErrorCode.WATCHLIST_UNAVAILABLE: 503,
    # Same contract as WATCHLIST_UNAVAILABLE: retryable, and the client must be able
    # to tell "couldn't read your settings" from "you have no settings yet" — the
    # second opens its push gate, the first must not.
    ErrorCode.SETTINGS_UNAVAILABLE: 503,
    # Retryable, and distinguishable from "you have no notifications yet".
    ErrorCode.NOTIFICATIONS_UNAVAILABLE: 503,
    # 500, not 503: this is our own partial write, not an upstream being unavailable.
    ErrorCode.ACCOUNT_DELETE_INCOMPLETE: 500,
    # 409, not 400: the request was well-formed and the state is the conflict.
    ErrorCode.PRICE_ALERT_LIMIT_REACHED: 409,
    ErrorCode.PRICE_ALERT_NOT_FOUND: 404,
    # 401 for all four credential failures — NOT 403.
    #
    # FastAPI's `HTTPBearer(auto_error=True)` answers a MISSING or malformed Authorization
    # header with 403 "Not authenticated" (fastapi/security/http.py). That is the defect this
    # block exists to correct: iOS only runs its refresh-and-retry interceptor on 401, so a 403
    # meant the client never even tried to recover — it just surfaced "Access Denied" or, at a
    # silent call site, nothing at all.
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.AUTH_TOKEN_INVALID: 401,
    ErrorCode.AUTH_SESSION_EXPIRED: 401,
    ErrorCode.AUTH_ACCOUNT_NOT_FOUND: 401,
    # 403 is correct HERE and only here: identity is established, authorization is not.
    ErrorCode.AUTH_FORBIDDEN: 403,
    # Same reasoning as WATCHLIST_UNAVAILABLE — retryable, and distinguishable from "your
    # credential is bad" so the client keeps the token instead of signing the user out.
    ErrorCode.AUTH_UNAVAILABLE: 503,
    # 401 for both: the caller is unauthenticated as far as this request is concerned. The
    # status is what iOS keys its interceptor off; the CODE is what stops it being mistaken
    # for a dead session.
    ErrorCode.AUTH_CREDENTIALS_INVALID: 401,
    ErrorCode.AUTH_PROVIDER_FAILED: 401,
}


# ── Builders ──────────────────────────────────────────────────────────


def make_error_response(
    code: ErrorCode,
    *,
    message: str,
    status_code: Optional[int] = None,
    user_message: Optional[str] = None,
    action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Return a `JSONResponse` whose body matches the iOS
    `APIErrorResponse` schema.

    `message` is the developer-facing technical detail (logged + shown
    in dev builds). `user_message` is the end-user copy; defaults
    to the registered `_USER_MESSAGES` entry for the code.
    """
    body = {
        "error_code": code.value,
        "message": (message or "")[:500],
        "user_message": (
            user_message
            if user_message is not None
            else _USER_MESSAGES.get(
                code, "Something went wrong. Please try again."
            )
        ),
        "action": action if action is not None else _DEFAULT_ACTIONS.get(code),
        "details": details or {},
    }
    return JSONResponse(
        status_code=status_code or _DEFAULT_STATUS.get(code, 500),
        content=body,
    )


def make_error_body(
    code: ErrorCode,
    *,
    message: str,
    user_message: Optional[str] = None,
    action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Same shape as `make_error_response` but returns a plain dict —
    used by background tasks that persist the error into Supabase
    (research_reports.error_message JSON-encoded) instead of returning
    an HTTP response."""
    return {
        "error_code": code.value,
        "message": (message or "")[:500],
        "user_message": (
            user_message
            if user_message is not None
            else _USER_MESSAGES.get(
                code, "Something went wrong. Please try again."
            )
        ),
        "action": action if action is not None else _DEFAULT_ACTIONS.get(code),
        "details": details or {},
    }


def auth_error(
    code: ErrorCode,
    *,
    message: str,
    user_message: Optional[str] = None,
    status_code: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
) -> "HTTPException":
    """An `HTTPException` whose `detail` IS the structured error body.

    Auth rejections have to be raised (they happen inside a dependency, before the handler
    exists to return a response), and a raise normally lands in FastAPI's built-in handler,
    which renders `{"detail": ...}` — outside the `{error_code, message, user_message, action,
    details}` contract that iOS decodes (CLAUDE.md invariant #3).

    So: put the whole body in `detail` as a dict, and let the `HTTPException` handler registered
    in `app/main.py` pass a dict detail through verbatim. Every auth raise site is then one line
    and cannot drift from the contract.

    `WWW-Authenticate: Bearer` is attached to the 401s per RFC 6750.

    Imported here rather than at module top so this module stays cheap to import and free of a
    hard FastAPI dependency for the pure-body helpers above.
    """
    from fastapi import HTTPException  # local: keep module import cost low

    resolved_status = status_code or _DEFAULT_STATUS.get(code, 401)
    headers = {"WWW-Authenticate": "Bearer"} if resolved_status == 401 else None
    return HTTPException(
        status_code=resolved_status,
        detail=make_error_body(
            code, message=message, user_message=user_message, details=details
        ),
        headers=headers,
    )


# ── Classifier ────────────────────────────────────────────────────────


def classify_exception(exc: BaseException) -> Tuple[ErrorCode, int]:
    """Inspect an exception and return (error_code, default http status).

    Detection is class-name + message-keyword based so we don't have to
    import `httpx` or `google.api_core` at module load. False positives
    (e.g. an FMP exception text mentioning "quota" mistakenly mapped to
    Gemini) are acceptable — the underlying message is still preserved
    in `details.underlying` for production debugging.
    """
    cls = type(exc).__name__.lower()
    cls_module = type(exc).__module__.lower()
    msg = str(exc).lower()

    # ── Profile-not-found from collector / service ────────────────────
    if isinstance(exc, ValueError) and "profile" in msg:
        return ErrorCode.TICKER_NOT_FOUND, _DEFAULT_STATUS[ErrorCode.TICKER_NOT_FOUND]

    # ── Watchlist datastore unreadable (tracking_service) ─────────────
    # Checked BEFORE the generic heuristics below: a PostgREST read timeout
    # carries "timeout" in its message and would otherwise be mislabelled
    # FMP_UNAVAILABLE, which points the user (and the logs) at the wrong system.
    if "watchlistunavailable" in cls:
        return (
            ErrorCode.WATCHLIST_UNAVAILABLE,
            _DEFAULT_STATUS[ErrorCode.WATCHLIST_UNAVAILABLE],
        )

    # ── Degraded report (deep path refuses to deliver a Gemini-outage shell) ──
    # Matched by name before the Gemini block below: the exception is raised by our own
    # service layer, so neither its module nor its message contains "google"/"genai", and
    # without this it would fall through to a bare 500 — which on the paid research path
    # means the user sees "Something went wrong" instead of "you have not been charged".
    if "degradedreporterror" in cls:
        return (
            ErrorCode.GEMINI_UNAVAILABLE,
            _DEFAULT_STATUS[ErrorCode.GEMINI_UNAVAILABLE],
        )

    # ── Gemini per-call timeout (app.integrations.gemini.GeminiTimeoutError) ──
    # Matched by NAME, and placed BEFORE both the google/genai module test below and
    # the generic keyword heuristics at the bottom. The class is OURS, so cls_module
    # is "app.integrations.gemini" — it contains neither "google" nor "genai" and so
    # misses the block below. It would then fall all the way to `"timeout" in cls`
    # and answer FMP_UNAVAILABLE, i.e. tell the user "Our market data provider is
    # temporarily unavailable" when it was the AI engine that stalled. Same shape and
    # same reason as the watchlistunavailable / degradedreporterror guards above.
    if "geminitimeout" in cls:
        return (
            ErrorCode.GEMINI_UNAVAILABLE,
            _DEFAULT_STATUS[ErrorCode.GEMINI_UNAVAILABLE],
        )

    # ── Gemini / Google generative AI errors ──────────────────────────
    if (
        "google" in cls_module
        or "genai" in cls_module
        or "vertexai" in cls_module
    ):
        if "resourceexhausted" in cls or "quota" in msg or "429" in msg or "rate limit" in msg:
            return ErrorCode.GEMINI_QUOTA_EXCEEDED, _DEFAULT_STATUS[ErrorCode.GEMINI_QUOTA_EXCEEDED]
        return ErrorCode.GEMINI_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.GEMINI_UNAVAILABLE]

    # ── FMP typed exceptions (from app.integrations.fmp) ──────────────
    if (
        "fmpauthexception" in cls
        or "fmpratelimitexception" in cls
        or "fmpunavailableexception" in cls
        or "fmpexception" in cls
    ):
        if "ratelimit" in cls:
            return ErrorCode.FMP_RATE_LIMITED, _DEFAULT_STATUS[ErrorCode.FMP_RATE_LIMITED]
        return ErrorCode.FMP_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.FMP_UNAVAILABLE]

    # ── httpx upstream errors (FMP) ───────────────────────────────────
    if "httpx" in cls_module or "httpstatus" in cls or "httperror" in cls:
        # FMP-specific 429 detection
        if "429" in msg or "rate limit" in msg or "too many requests" in msg:
            return ErrorCode.FMP_RATE_LIMITED, _DEFAULT_STATUS[ErrorCode.FMP_RATE_LIMITED]
        return ErrorCode.FMP_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.FMP_UNAVAILABLE]

    # ── Generic message-keyword heuristic ─────────────────────────────
    if "quota" in msg or "resource_exhausted" in msg:
        return ErrorCode.GEMINI_QUOTA_EXCEEDED, _DEFAULT_STATUS[ErrorCode.GEMINI_QUOTA_EXCEEDED]
    if "rate limit" in msg or "429" in msg:
        return ErrorCode.FMP_RATE_LIMITED, _DEFAULT_STATUS[ErrorCode.FMP_RATE_LIMITED]
    if "timeout" in cls or "timeout" in msg:
        return ErrorCode.FMP_UNAVAILABLE, _DEFAULT_STATUS[ErrorCode.FMP_UNAVAILABLE]

    return (
        ErrorCode.REPORT_GENERATION_FAILED,
        _DEFAULT_STATUS[ErrorCode.REPORT_GENERATION_FAILED],
    )


def error_response_from_exception(
    exc: BaseException,
    *,
    ticker: Optional[str] = None,
    persona: Optional[str] = None,
    step: Optional[str] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Convenience wrapper: classify, build details, return JSONResponse.

    `step` records the pipeline phase that blew up — e.g. "collector",
    "stage_a", "agentic_research", "narratives". Carried in
    `details.step` so production logs and iOS error views can show
    exactly where in the pipeline it failed.
    """
    code, status_code = classify_exception(exc)
    details: Dict[str, Any] = {
        "underlying": f"{type(exc).__name__}: {str(exc)[:200]}",
    }
    if ticker is not None:
        details["ticker"] = ticker
    if persona is not None:
        details["persona"] = persona
    if step is not None:
        details["step"] = step
    if extra_details:
        details.update(extra_details)

    return make_error_response(
        code,
        status_code=status_code,
        message=f"{type(exc).__name__}: {str(exc)[:300]}",
        details=details,
    )


# Codes that represent a KNOWN upstream / lookup failure worth surfacing to the
# user with an actionable message (as opposed to an unexpected internal error,
# which detail endpoints keep mapping to their own generic 502 so we don't
# mislabel it with report-pipeline copy).
_UPSTREAM_CODES = frozenset({
    ErrorCode.FMP_RATE_LIMITED,
    ErrorCode.FMP_UNAVAILABLE,
    ErrorCode.GEMINI_QUOTA_EXCEEDED,
    ErrorCode.GEMINI_UNAVAILABLE,
    ErrorCode.TICKER_NOT_FOUND,
})


def upstream_error_response(
    exc: BaseException,
    *,
    ticker: Optional[str] = None,
    step: Optional[str] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> Optional[JSONResponse]:
    """Return a structured `JSONResponse` IFF `exc` classifies as a known
    upstream/lookup failure (FMP rate-limit/unavailable, Gemini, ticker-not-found);
    otherwise return ``None`` so the caller keeps its own generic fallback.

    This lets the detail endpoints (stocks/etfs/crypto/indices/commodities) honor
    the iOS `APIErrorResponse` contract — surfacing e.g. `FMP_RATE_LIMITED` +
    `retry_later` with an actionable message — instead of a bare
    `HTTPException(502, {"detail": ...})` that iOS can only render as a generic
    "Server error". Importing this (from `app.api.error_response`) keeps the
    endpoint layer free of any `app.integrations` import.
    """
    code, _status = classify_exception(exc)
    if code in _UPSTREAM_CODES:
        return error_response_from_exception(
            exc, ticker=ticker, step=step, extra_details=extra_details
        )
    return None


def error_body_from_exception(
    exc: BaseException,
    *,
    ticker: Optional[str] = None,
    persona: Optional[str] = None,
    step: Optional[str] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Like `error_response_from_exception` but returns the dict
    body — used by `_run_research_task` to persist a structured
    error blob into `research_reports.error_message`."""
    code, _status = classify_exception(exc)
    details: Dict[str, Any] = {
        "underlying": f"{type(exc).__name__}: {str(exc)[:200]}",
    }
    if ticker is not None:
        details["ticker"] = ticker
    if persona is not None:
        details["persona"] = persona
    if step is not None:
        details["step"] = step
    if extra_details:
        details.update(extra_details)

    return make_error_body(
        code,
        message=f"{type(exc).__name__}: {str(exc)[:300]}",
        details=details,
    )
