"""
Home-screen widget endpoints, on THREE routers (one auth policy each):

  strict `router` (a real session, `get_current_user_id`):
    GET  /api/v1/widget/portfolio-mover         the caller's holdings (the APP's call)
    GET  /api/v1/widget/token                   mint the extension's market-scoped credential
    PUT  /api/v1/widget/push-token              the app uploads the widget's WidgetKit push token
  `widget_client_router` (a session OR the market widget token, `get_widget_caller`):
    GET  /api/v1/widget/market-mover            the day's most unusual tracked move
  `widget_holdings_router` (the Holdings grant key ONLY, `get_holdings_grant_caller`):
    GET  /api/v1/widget/holdings-mover          the holdings payload, for the EXTENSION
    POST /api/v1/widget/holdings-grant/revoke   the device revokes the key it presents

THE THIRD ROUTER (migration 216; owner 2026-11-27 "Own key, 30-day life (Recommended)")
-----------------------------------------------------------------------------------------
The Holdings tile used to change only while the app ran: the extension holds no session, and the
market widget token is defensible only because it reaches market-wide data (auth.md §8a). The
extension now holds a per-device Holdings GRANT — an opaque key minted only inside a
`POST /auth/refresh` rotation, stored server-side only as a SHA-256 and checked against
migration 216's revocation list on EVERY call. Its router holds exactly the two routes above;
its dependency reads no bearer and never reads the grants switch, which sits on the data route
alone (`require_holdings_grants_enabled`) so a device can always revoke its key.
`tests/test_ios_auth_policy_parity.py` and `tests/test_widget_token_auth.py` pin the route sets:
a data route added to the holdings router without a test change fails the build.

DESIGN NOTE — the read path never calls Gemini, same as `updates.py`. Everything
served here was produced by the background insight sweeper; these handlers read
caches. That matters more for a widget than for a screen: WidgetKit schedules
refreshes itself, unattended, and the client cannot be rate-limited into behaving.

WHY TWO ROUTES INSTEAD OF ONE WITH `?mode=`
--------------------------------------------
`APIEndpoint.authPolicy` on iOS is an exhaustive switch with one policy per case,
and `tests/test_ios_auth_policy_parity.py` asserts each case matches its backend
dependency. A single route whose auth requirement changed with a query parameter
cannot be expressed in that model — it would have to be special-cased in the test
that exists to stop auth drift.

It also keeps holdings out of the query string, and therefore out of Railway's
access logs.

DEGRADE, NEVER ERROR
--------------------
A widget that renders an error message is worse than one rendering yesterday's
close — the user cannot retry it, cannot see why, and it sits on their Home Screen
looking broken. So every failure path here returns a valid payload with whatever
was resolvable rather than raising. No new `ErrorCode`.

The portfolio route degrades WITHIN its own mode. It used to answer an empty group
(and, via a swallowed read failure, a database outage) with the MARKET payload, so a
"My Holdings" tile showed the market's movers as if they were the user's — and the
client could not tell "no holdings" from "the read failed". Now an empty group is an
explicit `mode="portfolio"` payload with `holdings_count=0`, and an unreadable one is
`_empty("portfolio")` (`holdings_count=None`), which the client keeps its last good
snapshot over.
"""

import asyncio
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from app.api.error_response import ErrorCode, auth_error
from app.core.client_app_version import (
    WEB_ONLY_CAUSE_MIN_APP_VERSION,
    capture_client_app_version,
    client_is_at_least,
)
from app.core.security import create_widget_token, widget_token_expires_at
from app.database import get_supabase
from app.dependencies import (
    HoldingsGrantCaller,
    UserIdRateLimitChecker,
    WidgetHoldingsRateLimit,
    WidgetRateLimit,
    get_current_user,
    get_current_user_id,
    get_holdings_grant_caller,
    get_watchlist_identity,
    get_widget_caller,
    require_holdings_grants_enabled,
)
from app.schemas.widget import (
    WidgetHoldingsGrantRevokeRequest,
    WidgetHoldingsGrantRevokeResponse,
    WidgetMoverPayload,
    WidgetPushTokenRequest,
    WidgetPushTokenResponse,
    WidgetTokenResponse,
)
from app.services import widget_holdings_grant_service as holdings_grants
from app.services.active_group_service import (
    ActiveGroupUnavailable,
    get_active_group,
)
from app.services.widget_movers_service import get_widget_movers_service

logger = logging.getLogger(__name__)

# 🔒 ACCOUNT-ONLY. Every route on this router requires a real account.
#
# FMP's signed Order Form grants End-User Display Rights — Exhibit A's *Access-Restricted
# External Display* — so their market data may be shown only "through the Licensee's
# authenticated platform". Public External Display was declined (2026-09-04), which makes a
# signed-out response on any of these routes a licence breach, not a product choice.
#
# Declared on the ROUTER rather than per-route on purpose: a route added to this file
# tomorrow is authenticated by default. The per-route form relies on the author remembering,
# and this file alone has 2 routes — the failure mode is silent (a 200 with real prices)
# and nothing downstream would notice.
router = APIRouter(dependencies=[Depends(get_current_user_id)])

# The ONE route the Home Screen extension calls for itself, on its own router.
#
# `CaydexWidgets` is a separate process holding no session: the App Group is its only channel to
# the app, and it cannot refresh a token because refresh is main-actor and lives in the app
# (auth.md §8). It authenticates with a long-lived, market-scoped widget token instead — see the
# WIDGET TOKEN block in `core/security.py`.
#
# ⚠️ A SECOND ROUTER, not a relaxed dependency on the first, and that is the whole point. If
# `get_widget_caller` were moved onto `router`, every route added to this file afterwards would
# silently accept a widget token — including one returning the caller's holdings, which the token
# is only defensible because it cannot reach. Landing on the STRICT router by default is the
# fail-closed half of auth.md §1; a route joins this one only by someone typing its name here.
#
# `tests/test_widget_token_auth.py` pins that this router holds exactly one route.
widget_client_router = APIRouter(dependencies=[Depends(get_widget_caller)])

# The THIRD router: the extension's HOLDINGS calls, authenticated by the per-device grant key
# alone (migration 216). Exactly two routes — the data route and revoke-by-key — pinned by
# `tests/test_ios_auth_policy_parity.py` and `tests/test_widget_token_auth.py`.
#
# ⚠️ The grants switch is NOT a dependency here. A router-level dependency runs for every route
# on the router, so the switch here would 503 the revoke and leave the grant of a device that
# signed out while offline alive. It sits on `GET /holdings-mover` only.
# ⚠️ Declared AFTER `router`: the auth-policy scanner anchors on the line `router = APIRouter(`.
widget_holdings_router = APIRouter(dependencies=[Depends(get_holdings_grant_caller)])

# How many of the caller's holdings are RANKED.
#
# This is a cap on the ranking input, not on what is rendered — and that distinction is
# why 30 was wrong. Holdings arrive ordered by `position` (active group) or `added_at
# desc` (watchlist), so truncating at 30 dropped tickers by RECENCY before anything was
# ranked: a user with 40 holdings was told the biggest mover among the 30 they happened
# to add most recently, and `basket.total_count` then stated a denominator that was not
# their portfolio. Neither is detectable from the tile.
#
# 500 = `settings.WATCHLIST_MAX_ITEMS`, the most holdings a user can have, so in practice
# nothing is truncated. That matters since the tile states COUNTS ("▲ 8 ▼ 5 · 13
# holdings", "N no price"): at the old 200 a 250-holding group reported its 50
# unranked names as unpriced. It costs no upstream call — `price_service.get_quotes`
# reads the one cached screener sweep — and the σ read degrading only drops the z
# tie-breaker, because portfolio mode ranks by |%| first.
_MAX_HOLDINGS = 500

_MAX_SCOPE_LEN = 32

# The portfolio tile's OWN per-account window, not `StandardRateLimit`. That one is shared
# with a dozen browsing routes, so a burst on Updates or a detail screen followed by a
# foreground inside the same minute 429'd the widget refresh — and a 429 is the one answer
# here that bypasses "degrade, never error". The app asks at most ~2 times per foreground
# behind a 60 s throttle, so 30/min is never reached by the app itself. The bucket name must
# stay distinct from every other limiter's (`widget` is `WidgetRateLimitChecker`'s key space).
# Keyed on the token's user id (`get_current_user_id`) — the route is account-only anyway.
WidgetPortfolioRateLimit = Depends(UserIdRateLimitChecker("widget_portfolio", 30, 60))

# `PUT /push-token`'s own per-account window (the app uploads at most once a day per grant, plus
# a retry). A distinct bucket, per the note above.
WidgetPushTokenRateLimit = Depends(UserIdRateLimitChecker("widget_push_token", 10, 60))

# The head-ticker memo for `note_head`: one RPC only when a grant's head ticker CHANGES, not on
# every extension build. Bounded LRU; a restart only costs one RPC per grant.
_HEAD_MEMO_MAX = 4096
_head_memo: "OrderedDict[str, Optional[str]]" = OrderedDict()


def _valid_ticker(t: str) -> bool:
    if not t or len(t) > _MAX_SCOPE_LEN:
        return False
    return all(c.isalnum() or c in ".-^=" for c in t)


def _empty(mode: str) -> WidgetMoverPayload:
    """Last-resort payload. Renders as the widget's empty state, not an error.

    In portfolio mode it is the DEGRADED answer (`holdings_count` None), which the client
    never writes over a good snapshot; the authoritative empty group is this plus
    `holdings_count=0`.
    """
    from datetime import datetime, timezone

    from app.utils.market_hours import (
        session_label,
        session_phase,
        session_trading_date,
    )

    # Even the empty payload carries an honest time anchor: a blank tile that also
    # cannot say WHEN it went blank is indistinguishable from a broken one.
    return WidgetMoverPayload(
        mode=mode,
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session=session_phase(),
        session_date=session_trading_date().isoformat(),
        session_label=session_label(),
    )


@widget_client_router.get("/market-mover", response_model=WidgetMoverPayload)
async def get_market_mover(
    _rate_limit=WidgetRateLimit,
    # Records X-App-Version for the web-only cause gate (`WEB_ONLY_CAUSE_MIN_APP_VERSION`). The
    # extension's fetcher sends it from 1.03 on; an older extension sends none and is held.
    _app_version: None = Depends(capture_client_app_version),
) -> WidgetMoverPayload:
    """The most unusual move among the tickers Caydex tracks, and why.

    ⚠️ This docstring used to open "Public on purpose". It was, and that is exactly why the
    extension could refresh itself. It stopped being true on 2026-09-07: End-User Display Rights
    permit FMP data only "through the Licensee's authenticated platform", so it now takes either
    a session bearer (the app's own call, via `APIClient`) or a widget token (the extension's).
    What has NOT changed is that the payload is market-wide — a shared universe, nothing about
    the caller — which is the entire reason a long-lived token is defensible here and nowhere
    else.

    `WidgetRateLimit`, not `StandardRateLimit`: the latter would bucket every widget on Earth
    under one shared guest key. See `WidgetRateLimitChecker`.

    The current Market tile renders `market_assets` (the Home Market Pulse: S&P 500, Nasdaq,
    Dow, Russell 2000, Gold ETFs and Bitcoin), the session-gated brief and sector breadth. The
    legacy `headline_mover` / `runners_up` are still sent for installed builds, ranked by
    volatility-relative z, not raw percent — so a 3% day on a normally calm name outranks 3%
    on one that moves that much routinely.
    """
    try:
        return await get_widget_movers_service().get_market_mover(
            links_web_only=client_is_at_least(WEB_ONLY_CAUSE_MIN_APP_VERSION),
        )
    except Exception as e:
        logger.error(
            "widget market-mover failed: %s: %s", type(e).__name__, e, exc_info=True
        )
        return _empty("market")


@router.get("/token", response_model=WidgetTokenResponse)
async def issue_widget_token(user: dict = Depends(get_current_user)) -> WidgetTokenResponse:
    """Mint the Home Screen extension's market-data credential for the signed-in caller.

    On the STRICT router: you must already hold a real session to be issued one. The token that
    comes back is not a session and can never become one — `_decode_access_token` allow-lists
    `type == "access"`, so presenting this as a bearer is a 401 on every authenticated route
    (`tests/test_widget_token_auth.py` proves it rather than asserting it).

    ⚠️ `get_current_user`, NOT the router's token-only `get_current_user_id`. That one checks
    signature and expiry alone, so an access token still inside its life after the victim's
    PASSWORD RESET, or one naming a DELETED account, used to mint here — and the widget token
    deliberately does not participate in password-change eviction (`core/security.py`) and is
    never re-checked against `public.users`, so that caller kept FMP market data for 90 days
    with no account behind it (auth.md §1a). `get_current_user` refuses both
    (AUTH_SESSION_EXPIRED / AUTH_ACCOUNT_NOT_FOUND). This is the ONE place a widget token's
    account is ever checked, so the check has to be the strict one.

    Not rate-limited beyond the router's own gate: the client asks at most once per sign-in and
    once per renewal window (30 days), and a caller that passes `get_current_user` holds a
    live session on a live account — the strictly more powerful credential.
    """
    user_id = user["id"]
    token = create_widget_token(user_id)
    expires = widget_token_expires_at(token)
    # `widget_token_expires_at` re-decodes what we just minted, so None here means the token we
    # are about to hand out is unreadable by our own verifier — worth an error, not a shrug.
    if expires is None:
        logger.error("widget token minted but its exp is unreadable (user=%s)", user_id)
        raise HTTPException(
            status_code=500, detail="Could not issue a widget token. Please try again."
        )
    return WidgetTokenResponse(
        token=token, expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


@router.get("/portfolio-mover", response_model=WidgetMoverPayload)
async def get_portfolio_mover(
    # ACCOUNT-ONLY: the strict router's `get_current_user_id`, then this — which delegates
    # to `get_current_user` (a real `public.users` row) and never resolves a guest. A
    # signed-out caller never reaches here; iOS refuses the call before it leaves the device
    # (`.signInRequired`).
    user: dict = Depends(get_watchlist_identity),
    _rate_limit=WidgetPortfolioRateLimit,
    # Records X-App-Version (the app's `APIClient` sends it on every request) for the web-only
    # cause gate: a web-only card backs a cause only for a build that links it (fail closed).
    _app_version: None = Depends(capture_client_app_version),
) -> WidgetMoverPayload:
    """The caller's biggest movers — ranked by absolute % move — plus a combined reason when
    holdings moved together.

    Follows the ACTIVE GROUP (migration 126) rather than the master watchlist, so the
    widget, the Updates pills, Home and Tracking all describe the same set of tickers. A
    user with no group at all falls back to the master watchlist.

    Every branch answers in PORTFOLIO mode (module docstring, DEGRADE):

    * a non-empty group → the service build, with the group's name and the real holdings
      count applied AFTER the 60 s cache, on a copy — the cache key carries neither the
      name nor the group id, so a rename shows at once, and the cached object (shared by
      every caller in the window) is never mutated;
    * an empty group → `holdings_count=0` and the name ("No holdings in <name> yet");
    * an unreadable group or watchlist → `_empty("portfolio")`, `holdings_count=None`.
    """
    return await _portfolio_payload(user["id"])


async def _portfolio_payload(user_id: str, *, for_extension: bool = False) -> WidgetMoverPayload:
    """The holdings payload for `user_id` — the body of BOTH holdings routes.

    `/portfolio-mover` (the app, a session) and `/holdings-mover` (the extension, a grant) build
    exactly this, so the two can never disagree; `for_extension` only selects the extension's
    upstream budget (the crypto board, the tiered TTL). DEGRADE, never error (module docstring).
    """
    try:
        group_name: Optional[str] = None
        try:
            group = await get_active_group(user_id)
        except ActiveGroupUnavailable as e:
            # NOT the master watchlist: a "Tech" tile rebuilt from every holding the user
            # owns, labelled as their holdings, is a silent scope swap. Degrade instead.
            logger.warning(
                "widget: active group unreadable for user=%s (%s) — serving the degraded "
                "portfolio payload; the client keeps its last good snapshot",
                user_id, e,
            )
            return _empty("portfolio")

        if group is not None:
            # "" (or whitespace) is no name: the client renders "My Holdings" for None.
            group_name = (group.name or "").strip() or None
            tickers = [t.upper() for t in group.tickers if _valid_ticker(t.upper())]
        else:
            # RAISES on a read failure — caught below as degraded, never as "no holdings".
            tickers = await _watchlist_tickers(user_id)

        tickers = list(dict.fromkeys(tickers))
        # Counted BEFORE the cap: the header says how many holdings the user has, not how
        # many were ranked.
        precap_n = len(tickers)
        if not tickers:
            # An AUTHORITATIVE empty group — a state to show ("No holdings in Tech yet"),
            # never the market's movers wearing the "My Holdings" label.
            return _empty("portfolio").model_copy(
                update={"group_name": group_name, "holdings_count": 0}
            )
        if precap_n > _MAX_HOLDINGS:
            logger.info(
                "widget: user=%s has %d holdings — ranking the first %d",
                user_id, precap_n, _MAX_HOLDINGS,
            )

        service_kw: Dict[str, Any] = {
            "links_web_only": client_is_at_least(WEB_ONLY_CAUSE_MIN_APP_VERSION),
        }
        if for_extension:
            # The extension's build: the shared crypto board (coins off it are unpriced) and the
            # tiered off-universe TTL. Passed ONLY when set, so the app's call is unchanged.
            service_kw["for_extension"] = True
        payload = await get_widget_movers_service().get_portfolio_mover(
            user_id, tickers[:_MAX_HOLDINGS], **service_kw,
        )
        # A COPY. `payload` may be the cached object every caller in the TTL shares.
        return payload.model_copy(
            update={
                "group_name": group_name,
                # The service withholds the count on a DEGRADED build; keep it withheld.
                "holdings_count": precap_n if payload.holdings_count is not None else None,
            }
        )
    except Exception as e:
        logger.error(
            "widget portfolio payload failed for user=%s (extension=%s): %s: %s",
            user_id, for_extension, type(e).__name__, e, exc_info=True,
        )
        return _empty("portfolio")


async def _watchlist_tickers(user_id: str) -> List[str]:
    """The master watchlist's valid tickers, newest first.

    RAISES on a read failure. It used to log and return `[]`, which the route then served as
    "no holdings" — the outage-equals-empty conflation `active_group_service` says must
    never happen. The caller turns a raise into the degraded payload.
    """
    import asyncio

    from app.database import get_supabase

    def _read() -> List[Dict[str, Any]]:
        res = (
            get_supabase()
            .table("watchlist_items")
            .select("ticker")
            .eq("user_id", user_id)
            .order("added_at", desc=True)
            .limit(_MAX_HOLDINGS)
            .execute()
        )
        return res.data or []

    try:
        rows = await asyncio.to_thread(_read)
    except Exception as e:
        # Context here; the route's catch-all logs the stack and degrades.
        logger.warning(
            "widget: watchlist read failed for user=%s: %s: %s — re-raising as degraded",
            user_id, type(e).__name__, e,
        )
        raise
    return [
        str(r["ticker"]).upper()
        for r in rows
        if r.get("ticker") and _valid_ticker(str(r["ticker"]).upper())
    ]


# ── The Holdings grant: the extension's own calls (migration 216) ────────────────────────


def _remember_head(grant_id: str, ticker: Optional[str]) -> bool:
    """True when `ticker` is NEW for this grant (the RPC is then worth a round trip).

    A bounded LRU: at most `_HEAD_MEMO_MAX` grants, least-recently-built first out. Losing an
    entry only costs one idempotent RPC.
    """
    if grant_id in _head_memo and _head_memo[grant_id] == ticker:
        _head_memo.move_to_end(grant_id)
        return False
    _head_memo[grant_id] = ticker
    _head_memo.move_to_end(grant_id)
    while len(_head_memo) > _HEAD_MEMO_MAX:
        _head_memo.popitem(last=False)
    return True


def _forget_head(grant_id: str) -> None:
    _head_memo.pop(grant_id, None)


async def _note_head(supabase: Any, caller: HoldingsGrantCaller, payload: WidgetMoverPayload) -> None:
    """Best-effort: record the ticker the tile now leads with, for push targeting. Never raises.

    A DEGRADED build (`holdings_count` None) says nothing about the head: the client keeps its
    last snapshot, so the last noted head stays too. An authoritative empty group clears it.
    """
    if payload.holdings_count is None:
        return
    head = payload.headline_mover.ticker.upper() if payload.headline_mover else None
    if head is not None and not _valid_ticker(head):
        head = None
    if not _remember_head(caller.grant_id, head):
        return
    try:
        await asyncio.to_thread(holdings_grants.note_head, supabase, caller.grant_id, head)
    except Exception as e:  # noqa: BLE001 — push targeting is best-effort; the payload is served
        _forget_head(caller.grant_id)
        logger.warning(
            "widget holdings: note_head failed grant=%s user=%s (%s: %s) — push targeting uses "
            "the previous head until the next build",
            caller.grant_id, caller.user_id, type(e).__name__, e,
        )


@widget_holdings_router.get("/holdings-mover", response_model=WidgetMoverPayload)
async def get_holdings_mover(
    response: Response,
    # The router's dependency already resolved this; FastAPI caches it per request.
    caller: HoldingsGrantCaller = Depends(get_holdings_grant_caller),
    # The kill switch — on THIS route only, never on the router (the revoke must keep working).
    _enabled: None = Depends(require_holdings_grants_enabled),
    _rate_limit=WidgetHoldingsRateLimit,
    # The extension sends X-App-Version (WidgetMarketFetcher.appVersion): the web-only cause gate.
    _app_version: None = Depends(capture_client_app_version),
    supabase: Any = Depends(get_supabase),
) -> WidgetMoverPayload:
    """The Holdings tile's payload for the EXTENSION, authenticated by its grant key alone.

    Exactly `/portfolio-mover`'s build (`_portfolio_payload`) for the grant's own account —
    the user comes from the grant row, never from a header — with the extension's upstream
    budget (`for_extension=True`). `Cache-Control: no-store`: holdings must never sit in an
    intermediary or the extension's URL cache. DEGRADE, never error, past the grant check.
    """
    response.headers["Cache-Control"] = "no-store"
    payload = await _portfolio_payload(caller.user_id, for_extension=True)
    await _note_head(supabase, caller, payload)
    return payload


@widget_holdings_router.post(
    "/holdings-grant/revoke", response_model=WidgetHoldingsGrantRevokeResponse,
)
async def revoke_holdings_grant(
    response: Response,
    body: Optional[WidgetHoldingsGrantRevokeRequest] = Body(default=None),
    caller: HoldingsGrantCaller = Depends(get_holdings_grant_caller),
    supabase: Any = Depends(get_supabase),
) -> WidgetHoldingsGrantRevokeResponse:
    """The device revokes the key it presents: at session end (`app_revoked`), after a renewal
    (the old key), or when the last Movers widget is removed (`widget_removed`).

    NOT gated by `WIDGET_HOLDINGS_GRANTS_ENABLED`: revocation must always work. A dead key never
    reaches here — the dependency answers 401, which the client reads as "already dead". A store
    failure is a 503 AUTH_UNAVAILABLE so the client keeps the key and retries.
    """
    response.headers["Cache-Control"] = "no-store"
    reason = body.reason if body is not None else "app_revoked"
    try:
        outcome = await asyncio.to_thread(
            holdings_grants.revoke, supabase, caller.grant_id, caller.user_id, reason,
        )
    except Exception as e:  # noqa: BLE001 — surfaced as a retryable 503, never a fake success
        logger.error(
            "widget holdings: revoke failed grant=%s user=%s reason=%s (%s: %s)",
            caller.grant_id, caller.user_id, reason, type(e).__name__, e, exc_info=True,
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"Holdings grant revoke unavailable: {type(e).__name__}",
        )
    _forget_head(caller.grant_id)
    logger.info(
        "widget holdings: device revoked grant=%s user=%s reason=%s outcome=%s",
        caller.grant_id, caller.user_id, reason, outcome,
    )
    return WidgetHoldingsGrantRevokeResponse(revoked=outcome == "revoked")


@router.put("/push-token", response_model=WidgetPushTokenResponse)
async def register_widget_push_token(
    body: WidgetPushTokenRequest,
    # STRICT: a real users row — an evicted session or a deleted account must not bind a token.
    user: dict = Depends(get_current_user),
    _rate_limit=WidgetPushTokenRateLimit,
    supabase: Any = Depends(get_supabase),
) -> WidgetPushTokenResponse:
    """The APP uploads the widget's WidgetKit push token (iOS 26+) onto the device's grant.

    On the STRICT router, never on either widget router: it carries no market data and never
    accepts a widget or grant credential. The token rides on the grant row, so every revocation
    of the grant stops its pushes with no extra hook.

      * stored → `{stored: true, grant_usable: true}`;
      * an unknown, foreign, dead or superseded grant → `{false, false}` (one answer, so nothing
        is enumerable) — the app re-mints;
      * a database error → `{false, true}` + ERROR (retry later; never a 5xx the app would treat
        as a reason to drop the grant). DEGRADE, never error — no new ErrorCode.
    """
    user_id = user["id"]
    grant_id = str(body.grant_id)
    try:
        stored, usable = await asyncio.to_thread(
            holdings_grants.register_push_token,
            supabase, user_id, grant_id, body.token, body.environment,
        )
    except Exception as e:  # noqa: BLE001 — degrade (docstring); the token is never logged
        logger.error(
            "widget push: token registration failed user=%s grant=%s env=%s (%s: %s)",
            user_id, grant_id, body.environment, type(e).__name__, e, exc_info=True,
        )
        return WidgetPushTokenResponse(stored=False, grant_usable=True)
    if not usable:
        logger.info(
            "widget push: registration refused user=%s grant=%s (unknown, foreign, dead or "
            "superseded grant)", user_id, grant_id,
        )
    return WidgetPushTokenResponse(stored=stored, grant_usable=usable)
