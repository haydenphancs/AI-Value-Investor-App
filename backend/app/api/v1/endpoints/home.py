"""
Home Feed Endpoint
Frontend: GET /home/feed

Returns aggregated data for the HomeView screen in a single request:
- Market tickers with real-time prices + sparkline data (FMP)
- AI-generated market insight summary
- Daily briefing alerts (earnings calendar, whale alerts)
- User's recent research reports (if authenticated)

Auth is optional — unauthenticated users receive public market data;
authenticated users also receive personalised research reports.
"""

from fastapi import APIRouter, Depends
from typing import Optional
import logging
import re

from app.dependencies import get_current_user_id, get_watchlist_identity
from app.services.home_service import HomeService
from app.services.home_dashboard_service import get_home_dashboard_service
from app.services.signals_service import get_signals_service
from app.schemas.home import HomeFeedResponse
from app.schemas.home_dashboard import HomeDashboardResponse
from app.schemas.signals_detail import SignalTickerDetailResponse
from app.schemas.themes_detail import ThemeDetailResponse
# The schema stays at module scope (`response_model` needs it; it imports only pydantic). The
# SERVICE is imported inside `get_trillion_club_detail`: an import-time defect there must cost
# that one route a 503, not the /home router every Home request runs through.
from app.schemas.trillion_club import TrillionClubDetailResponse
from app.api.error_response import (
    error_response_from_exception,
    make_error_response,
    ErrorCode,
)
from app.services.entitlements import (
    TIER_PRO,
    required_tier_for_trillion_club_detail,
    required_tier_for_whales,
    trillion_club_detail_unlocked,
    whale_detail_unlocked,
)

# The signal cards that have a per-ticker drill-down. Earnings Shockers has none (its leaders
# open the ticker screen). iOS mirrors this set as `ExclusiveSignal.drillDownKinds`, pinned
# by tests/test_ios_signal_kinds_parity.py.
_VALID_SIGNAL_KINDS = {"whale", "congress", "ceo"}

# The `trillion_club_companies.slug` CHECK, verbatim. Used with `fullmatch`: `re.match` with a
# `$` anchor would accept a trailing newline (a `%0A` in the path).
_TRILLION_CLUB_SLUG = re.compile(r"[a-z0-9-]{1,40}")

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
# and this file alone has 4 routes — the failure mode is silent (a 200 with real prices)
# and nothing downstream would notice.
router = APIRouter(dependencies=[Depends(get_current_user_id)])


@router.get("/feed", response_model=HomeFeedResponse)
async def get_home_feed(
    # Was `get_optional_user_id`, which answered None for a signed-out caller and served the
    # feed anyway. The feed is FMP market data, so under the End-User Display licence there is
    # no signed-out case left to be optional about.
    #
    # `get_watchlist_identity`, not the token-only `get_current_user_id`, because the feed
    # serves the CALLER'S OWN research reports: a token-only dependency has no eviction —
    # a deleted account's still-valid JWT read its reports for the token's lifetime — while
    # the `users`-row read (threaded, and shared with the router-level dependency) refuses a
    # gone account, exactly as `/dashboard` below does.
    user: dict = Depends(get_watchlist_identity),
):
    """
    Aggregated home feed — single request for the entire home screen.

    Fetches market tickers, insight summary, daily briefings, and recent
    research concurrently.  Each section degrades gracefully on failure.
    """
    user_id = user["id"]
    service = HomeService()
    return await service.get_home_feed(user_id)


@router.get("/dashboard", response_model=HomeDashboardResponse)
async def get_home_dashboard(
    # The WATCHLIST identity (a real account, else a per-INSTALL guest — migration
    # 108), not the shared guest sentinel: the strip must resolve to the same
    # partition the watchlist routes write, or a guest would see another install's
    # tickers on the app's most-visited screen.
    #
    # The legacy `/home/feed` above has always taken a user; the redesigned dashboard
    # dropped it, which is why a day-1 user and a 40-ticker power user saw a
    # byte-identical Home.
    user: dict = Depends(get_watchlist_identity),
):
    """
    Aggregated Caydex Home dashboard — single request for the redesigned
    `HomeDashboardView` (distinct from the legacy `/home/feed`).

    Today this returns the market-status header and the top "Market Pulse"
    strip (major indices + Bitcoin + commodities), each with a live quote and
    a daily-close sparkline, PLUS the caller's own watchlist strip — the one
    user-scoped section on this screen. Auth-optional: an anonymous caller simply
    gets an empty `watchlist`. Degrades gracefully — a failed symbol is dropped
    rather than failing the whole strip, and a failed watchlist read costs that
    section only; only an unexpected failure surfaces a structured error.
    """
    try:
        service = get_home_dashboard_service()
        # `tier` gates the App-Exclusive Signals tickers (Pro/Max). Same dependency
        # `updates.py` reads it from: a real account carries its own tier, a
        # per-install guest is hardcoded "free" — so a signed-out caller is locked.
        return await service.get_dashboard(
            user_id=user.get("id"), tier=user.get("tier")
        )
    except Exception as e:
        logger.error(
            "Home dashboard failed: %s: %s", type(e).__name__, e, exc_info=True
        )
        return error_response_from_exception(e, step="home_dashboard")


@router.get("/signals/{kind}/{ticker}", response_model=SignalTickerDetailResponse)
async def get_signal_ticker_detail(
    kind: str,
    ticker: str,
    user: dict = Depends(get_watchlist_identity),
):
    """Per-ticker drill-down for a Home signal card — WHO bought/added `ticker`,
    WHEN, HOW MUCH. ``kind`` ∈ {whale, congress, ceo}. The service degrades to an empty
    holder list rather than failing; only an unexpected error surfaces a structured
    response.

    Paid (Pro/Max). This route used to take no auth dependency at all, which made the
    signals redaction on `/dashboard` a curtain rather than a gate: the masked ticker was
    the only thing standing between a free caller and the full holder list, and a guessed
    symbol walked straight past it. It returns the same 13F/congress position detail the
    whale profile withholds, so it takes the same gate (and the CEO list is the paid
    ranking's own evidence, so it sits behind it too).
    """
    if not whale_detail_unlocked(user.get("tier")):
        return make_error_response(
            ErrorCode.WHALE_FOLLOW_LOCKED,
            message="Signal holder detail requires a paid plan",
            details={
                "tier_required": required_tier_for_whales(user.get("tier")) or "",
                "kind": kind,
            },
        )
    if kind not in _VALID_SIGNAL_KINDS:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Unsupported signal kind: {kind!r}",
            # Joined, not a list — iOS `AnyCodable` yields "" for a non-scalar. See the same
            # fix in ticker_report.py.
            details={"kind": kind, "valid": ", ".join(sorted(_VALID_SIGNAL_KINDS))},
        )
    if not ticker or len(ticker) > 12:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid ticker symbol: {ticker!r}",
            details={"ticker": ticker},
        )
    try:
        return await get_signals_service().get_ticker_detail(kind, ticker)
    except Exception as e:
        logger.error(
            "Signal detail failed (%s/%s): %s: %s",
            kind, ticker, type(e).__name__, e, exc_info=True,
        )
        return error_response_from_exception(e, ticker=ticker, step="signal_detail")


@router.get("/themes/{slug}", response_model=ThemeDetailResponse)
async def get_theme_detail(slug: str):
    """Emerging Frontiers theme drill-down — the theme's hero (title / subtitle /
    image) + its live constituent companies (price, daily %, market cap). Public
    (no auth). Reads the `trending_themes` row (editable in Supabase → no app
    release) and resolves its tickers to live quotes; the constituent list
    degrades to empty on an FMP hiccup rather than failing the screen. A slug that
    isn't an active theme → 404 THEME_NOT_FOUND.
    """
    if not slug or len(slug) > 100:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid theme slug: {slug!r}",
            details={"slug": slug},
        )
    try:
        detail = await get_home_dashboard_service().get_theme_detail(slug)
    except Exception as e:
        logger.error(
            "Theme detail failed (%s): %s: %s", slug, type(e).__name__, e, exc_info=True
        )
        return error_response_from_exception(
            e, step="theme_detail", extra_details={"slug": slug}
        )
    if detail is None:
        return make_error_response(
            ErrorCode.THEME_NOT_FOUND,
            message=f"No active theme with slug {slug!r}",
            details={"slug": slug},
        )
    return detail


@router.get("/trillion-club/{slug}", response_model=TrillionClubDetailResponse)
async def get_trillion_club_detail(
    slug: str,
    # The users-row identity (like /dashboard and /signals): it carries `tier`, and a
    # deleted account's still-valid JWT is refused. Sign-in itself is the ROUTER's
    # dependency — the 13F rows are FMP-licensed data (auth.md §1a).
    user: dict = Depends(get_watchlist_identity),
):
    """Trillion-Dollar Club Bets drill-down for one company: its card, 13F holdings and
    quarter-over-quarter changes, every published stake, earlier quarters, and the other
    members.

    Free sees the top 3 holdings, the changed rows and the stakes, minus any 13F note naming
    a withheld holding (`is_locked`, with `locked_holdings_count`, `locked_history_count` and
    no history); Pro/Max see everything. The redaction is a
    per-request COPY of the shared cached detail. A slug that is not a published club
    member — or any slug while the feature is off or the section is hidden for stale
    membership — is 404 TRILLION_CLUB_COMPANY_NOT_FOUND.
    """
    if not isinstance(slug, str) or not _TRILLION_CLUB_SLUG.fullmatch(slug):
        shown = slug[:60] if isinstance(slug, str) else ""
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid Trillion-Dollar Club slug: {shown!r}",
            details={"slug": shown},
        )
    tier = user.get("tier")
    try:
        # Function-local on purpose (see the module imports): an ImportError lands in the
        # handler below as a typed, retryable 503 instead of failing the router's import.
        from app.services.trillion_club_service import (
            get_trillion_club_service,
            redact_trillion_club_detail,
        )

        detail = await get_trillion_club_service().get_detail(slug)
    except Exception as e:
        logger.error(
            "Trillion club detail failed (slug=%s user=%s): %s: %s",
            slug, user.get("id"), type(e).__name__, e, exc_info=True,
        )
        # Typed and retryable: the only failure here is a READ (the service never calls
        # FMP). The generic mapping would have said "The report failed to generate".
        return make_error_response(
            ErrorCode.TRILLION_CLUB_UNAVAILABLE,
            message=f"trillion club detail read failed: {type(e).__name__}",
            details={"slug": slug},
        )
    if detail is None:
        return make_error_response(
            ErrorCode.TRILLION_CLUB_COMPANY_NOT_FOUND,
            message=f"No published Trillion-Dollar Club member with slug {slug!r}",
            details={"slug": slug},
        )
    if not trillion_club_detail_unlocked(tier):
        detail = redact_trillion_club_detail(
            detail, required_tier_for_trillion_club_detail(tier) or TIER_PRO
        )
    return detail
