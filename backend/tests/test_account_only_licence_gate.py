"""No route may serve data to a caller with no credential (FMP End-User Display licence).

WHY THIS FILE EXISTS. FMP's signed Order Form grants **End-User Display Rights** — Exhibit A's
*Access-Restricted External Display* — which permits showing their data only "through the
Licensee's authenticated platform". Public External Display was evaluated and declined on
2026-09-04. So a signed-out 200 on a market-data route is a **licence breach**, not a product
choice, and it is the reason the app became account-only.

This is the only test that checks the thing that actually matters. Everything else in the
suite is a source scan: `test_ios_auth_policy_parity.py` reads decorators, and
`test_auth_dependency_matrix.py` calls dependencies in isolation. Neither would notice that a
route is reachable, because neither sends a request. When the ~56 previously-open market-data
routes were gated, the entire 8,818-test suite stayed green — the change was invisible to it.

It is also the guard that has to survive a REFACTOR, not just a revert. The gating is declared
on the router (`APIRouter(dependencies=[Depends(get_current_user_id)])`) rather than per route,
precisely so a route added tomorrow is closed by default. That makes the failure mode of a
mistake "someone deletes one line in a file they were editing for another reason", and nothing
in a diff review looks less alarming than a shortened `APIRouter(...)` call.

HOW IT FAILS CLOSED. `_OPEN` is an ALLOW-LIST with a stated reason per entry. Any route not on
it must refuse an unauthenticated caller. Adding a new public route therefore requires editing
this file and writing down why — which is the review conversation the licence deserves.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    """The storefront catalogues are memoised process-wide for 120 s (they are public,
    unauthenticated routes that used to run a blocking SELECT per request). Tests install
    different fake tables, so the cache must not carry one test's rows into the next."""
    from app.services.subscription_service import reset_catalog_cache

    reset_catalog_cache()
    yield
    reset_catalog_cache()


# Path params substituted so a route can actually be reached. The values are irrelevant —
# the credential check runs before any handler, so nothing here touches an upstream.
_PATH_SAMPLE = {
    "ticker": "AAPL", "symbol": "AAPL", "slug": "x", "content_type": "book_core",
    "report_id": "00000000-0000-0000-0000-000000000000", "session_id": "s",
    "whale_id": "w", "group_id": "g", "alert_id": "a", "portfolio_id": "p", "kind": "k",
}

# ── THE ALLOW-LIST ───────────────────────────────────────────────────────────────────
# (METHOD, path) that may answer a caller with no credential, and why. Nothing else may.
_OPEN: dict[tuple[str, str], str] = {
    # The eight auth flows. Gating any of these is an unbreakable loop: you cannot obtain the
    # token that would let you call the thing that grants the token. Password recovery in
    # particular exists FOR people who cannot sign in.
    ("POST", "/api/v1/auth/login"): "no token exists yet",
    ("POST", "/api/v1/auth/register"): "no token exists yet",
    ("POST", "/api/v1/auth/refresh"): "the refresh token travels in the BODY, not as a bearer",
    ("POST", "/api/v1/auth/forgot-password"): "recovery for someone who cannot sign in",
    ("POST", "/api/v1/auth/reset-password"): "recovery for someone who cannot sign in",
    ("POST", "/api/v1/auth/resend-confirmation"): "pre-confirmation, so pre-session",
    ("POST", "/api/v1/auth/oauth"): "exchanges an Apple/Google id_token for our session",
    ("POST", "/api/v1/auth/session-exchange"): "completes the OAuth web redirect",
    # Price catalogues. No FMP data and no user data — and PaywallSignInGate.swift renders the
    # plan list before an account exists.
    ("GET", "/api/v1/billing/plans"): "static catalogue, no FMP data, needed by the paywall",
    ("GET", "/api/v1/billing/credit-packs"): "static catalogue, no FMP data",
    # Apple's server calls this one. It carries no bearer; the JWS signature IS the credential.
    ("POST", "/api/v1/billing/app-store-notifications"): "Apple-signed JWS, verified in-handler",
    # The one deliberate identity-only carve-out (.claude/rules/auth.md §4). Analytics promises
    # in its own docstring that it can never break the app, and the iOS client drops the batch
    # on any error. Gating it would also destroy the pre-sign-up funnel — the only instrument
    # that can measure what the sign-in wall costs in installs.
    ("POST", "/api/v1/events"): "telemetry; get_identity_only_user never raises, by design",
}

# Routers whose every route must answer exactly 401 — the strongest form of the assertion.
# `admin` is deliberately absent: its routes validate the request body before reaching
# `_authorize_admin`, so an anonymous caller gets 422. That is a 401-vs-422 contract wrinkle,
# not an access grant (a 422 means FastAPI never dispatched to the handler), and it predates
# the account-only change. The not-2xx assertion below still covers it.
_MUST_BE_401_PREFIXES = (
    "/api/v1/stocks", "/api/v1/etfs", "/api/v1/indices", "/api/v1/commodities",
    "/api/v1/crypto", "/api/v1/home", "/api/v1/updates", "/api/v1/widget",
    "/api/v1/whales", "/api/v1/watchlist", "/api/v1/tracking", "/api/v1/portfolios",
    "/api/v1/learn", "/api/v1/chat", "/api/v1/research", "/api/v1/users",
    "/api/v1/alerts",
)


@pytest.fixture(scope="module")
def client():
    # NOT `with TestClient(app)`. The context manager runs the app LIFESPAN, whose startup
    # jobs reach Supabase — and conftest blocks that. These routes are refused before any
    # handler runs, so no startup state is needed.
    logging.disable(logging.CRITICAL)  # 150 refusals log a line each; keep the output usable
    try:
        yield TestClient(app)
    finally:
        logging.disable(logging.NOTSET)


def _api_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if not path or not path.startswith("/api/v1"):
            continue
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            out.append((m, path))
    return sorted(out)


def _call(client: TestClient, method: str, path: str):
    url = path
    for key, value in _PATH_SAMPLE.items():
        url = url.replace("{" + key + "}", value)
    assert "{" not in url, f"unmapped path param in {path} — add it to _PATH_SAMPLE"
    body = {} if method in ("POST", "PUT", "PATCH") else None
    return client.request(method, url, json=body)


def test_the_route_scan_is_not_empty():
    """Anti-vacuity. Every assertion below iterates this list; if route discovery broke, they
    would all pass by iterating nothing — which is the failure mode this whole file exists to
    catch in the app itself."""
    routes = _api_routes()
    assert len(routes) >= 150, f"expected the full API surface, found {len(routes)}"


def test_no_unlisted_route_serves_an_anonymous_caller(client):
    """THE LICENCE ASSERTION. Not 2xx for anything outside the allow-list."""
    served = []
    for method, path in _api_routes():
        if (method, path) in _OPEN:
            continue
        r = _call(client, method, path)
        if 200 <= r.status_code < 300:
            served.append(f"{method} {path} -> {r.status_code}")
    assert not served, (
        "these routes answered a caller with NO credential:\n  "
        + "\n  ".join(served)
        + "\n\nFMP's End-User Display licence permits their data only through an authenticated "
        "platform. If one of these is genuinely meant to be public, add it to _OPEN with a "
        "reason — do not delete this test."
    )


def test_every_market_data_route_answers_401_specifically(client):
    """401, not 403 and not 422 — the status is a contract iOS keys off.

    `.claude/rules/auth.md` §2: 401 means "something is wrong with the credential (or there
    isn't one)", 403 means "the credential is fine, you just may not". iOS only attempts
    recovery on 401, so a 403 here would leave the client never retrying — the exact defect
    that made tapping Follow while signed out revert silently with nothing shown.
    """
    wrong = []
    for method, path in _api_routes():
        if (method, path) in _OPEN:
            continue
        if not path.startswith(_MUST_BE_401_PREFIXES):
            continue
        r = _call(client, method, path)
        if r.status_code != 401:
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert not wrong, "market-data routes must answer 401 without a credential:\n  " + "\n  ".join(wrong)


def test_the_refusal_carries_the_ios_error_contract(client):
    """A bare 401 is not enough: iOS decodes `error_code` and branches on it.

    AUTH_REQUIRED must NOT be in `triggersTokenRefresh` and must NOT clear the Keychain —
    there was no credential to invalidate. Answering AUTH_TOKEN_INVALID here would make every
    signed-out tap look like an expired session and burn a refresh round trip.
    """
    r = _call(client, "GET", "/api/v1/stocks/{ticker}/overview")
    assert r.status_code == 401
    body = r.json()
    assert body.get("error_code") == "AUTH_REQUIRED", body
    assert body.get("action") == "sign_in", body
    assert body.get("user_message"), "a refusal with no user_message reaches the UI as a blank alert"


def test_the_allow_list_has_no_stale_entries():
    """An entry naming a route that no longer exists is silent permission for a future route
    that happens to reuse the path."""
    live = set(_api_routes())
    stale = sorted(f"{m} {p}" for (m, p) in _OPEN if (m, p) not in live)
    assert not stale, f"_OPEN names routes that do not exist: {stale}"


def test_the_open_surface_stays_small():
    """A tripwire on scope creep. Twelve routes are open; if that number climbs, someone is
    re-opening the app one route at a time and each individual diff looks reasonable."""
    assert len(_OPEN) <= 12, (
        f"{len(_OPEN)} routes are now public. Every addition needs a licence-level reason — "
        "market data cannot be among them."
    )


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-07, when the ~56 open market-data routes were gated. Each mutation
# applied, the file run, then reverted (testing.md §3 rule 3).
#
#  1. `etfs.py`: router back to a bare `APIRouter()` — the realistic one-line revert.
#       -> test_no_unlisted_route_serves_an_anonymous_caller AND
#          test_every_market_data_route_answers_401_specifically both FAILED  ✅
#     Worth recording WHY both fire: the second is not redundant. Several handlers reach FMP,
#     and under the hermetic conftest an un-gated call fails to 5xx rather than 200 — so the
#     "not 2xx" assertion alone would have passed on a genuinely open route. The exact-401
#     assertion is what makes the guard non-vacuous.
#  2. `stocks.py`: same revert. -> 3 tests FAILED  ✅
#  3. Allow-list silently widened with three market-data routes, as someone "fixing" a red
#     test would do. -> test_the_open_surface_stays_small FAILED  ✅ (12 → 15)
#     ⚠️ This mutation did NOT revert with `git checkout --`, because the file was still
#     untracked; it survived into the next run and briefly left a real hole in the guard.
#     Re-check untracked files by content, never by git.
