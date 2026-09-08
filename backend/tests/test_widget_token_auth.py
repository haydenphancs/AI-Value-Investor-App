"""The Home Screen widget's market-data credential.

WHY IT EXISTS AT ALL
--------------------
`CaydexWidgets` is a separate process. It holds no session, the App Group is its only channel to
the app, and it cannot refresh anything (refresh is main-actor and lives in the app —
`.claude/rules/auth.md` §8). Until 2026-09-07 that was fine: `/widget/market-mover` was public,
so the extension just called it.

Then the account-only redesign closed it, because FMP's Order Form grants End-User Display Rights
only — data may be shown "through the Licensee's **authenticated** platform" — and Public
External Display was declined on 2026-09-04. Every extension fetch started answering 401. There
is no error state on a Home Screen tile, so the only symptom was the widget quietly freezing on
whatever the app last wrote: verbatim the TestFlight 1.0(3) report the self-refresh was built to
fix.

The widget token is the resolution: long-lived (the extension cannot renew one), signed with the
same `SECRET_KEY`, and scoped to a single route that returns MARKET-WIDE data — no watchlist, no
portfolio, no PII. It is a licence gate, not an identity.

THE ONE THING THAT MAKES IT SAFE
--------------------------------
`_decode_access_token` allow-lists `type == "access"`, so this token can never be replayed as a
session bearer. That check used to be a DENY-list on `"refresh"`, and under it a `type="widget"`
token would have been accepted on every authenticated route in the app — a 90-day session,
handed out by an endpoint whose whole justification is that it reaches nothing sensitive. The
escalation tests below are the point of this file; everything else is supporting detail.

No network, no Supabase, no Gemini.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.config import settings
from app.core.security import (
    WIDGET_TOKEN_SCOPE,
    WIDGET_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    create_widget_token,
    decode_widget_token,
    widget_token_expires_at,
)
from app.dependencies import WIDGET_TOKEN_HEADER
from app.main import app

_UID = "11111111-2222-3333-4444-555555555555"
_MARKET = "/api/v1/widget/market-mover"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _forge(**claims) -> str:
    """A widget token with individual claims overridden — for the negative cases."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": _UID,
        "type": WIDGET_TOKEN_TYPE,
        "scope": WIDGET_TOKEN_SCOPE,
        "iat": now,
        "exp": now + timedelta(days=90),
    }
    payload.update(claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ── 1. Mint and verify ──────────────────────────────────────────────────────

def test_a_freshly_minted_token_round_trips():
    assert decode_widget_token(create_widget_token(_UID)) == _UID


def test_the_expiry_is_readable_and_long():
    """The client renews on this value. Unreadable ⇒ it can only learn the token died from a
    401 inside a process with no way to report one."""
    exp = widget_token_expires_at(create_widget_token(_UID))
    assert exp is not None
    days = (exp - datetime.now(timezone.utc)).days
    assert 85 <= days <= 90, f"expiry is {days} days — the renewal window assumes ~90"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "nonsense", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.e30.", "\x00"],
)
def test_garbage_is_refused_without_raising(bad):
    """The caller is a FastAPI dependency that must answer 401 identically for every failure
    shape — a malformed header must not be distinguishable from an expired token."""
    assert decode_widget_token(bad) is None


def test_a_tampered_signature_is_refused():
    token = create_widget_token(_UID)
    head, payload, sig = token.split(".")
    flipped = sig[:-4] + ("aaaa" if not sig.endswith("aaaa") else "bbbb")
    assert decode_widget_token(f"{head}.{payload}.{flipped}") is None


def test_a_token_signed_with_a_different_secret_is_refused():
    now = datetime.now(timezone.utc)
    foreign = jwt.encode(
        {"sub": _UID, "type": WIDGET_TOKEN_TYPE, "scope": WIDGET_TOKEN_SCOPE,
         "iat": now, "exp": now + timedelta(days=90)},
        settings.SECRET_KEY + "-not-ours",
        algorithm=settings.ALGORITHM,
    )
    assert decode_widget_token(foreign) is None


def test_an_expired_token_is_refused():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert decode_widget_token(_forge(iat=past - timedelta(days=91), exp=past)) is None


def test_the_wrong_type_is_refused():
    """An access or refresh token must not double as a widget credential. The reverse direction
    is the dangerous one and is tested in §2, but this side keeps the two mints distinct."""
    assert decode_widget_token(_forge(type="access")) is None
    assert decode_widget_token(_forge(type="refresh")) is None
    assert decode_widget_token(create_access_token({"sub": _UID})) is None
    assert decode_widget_token(create_refresh_token({"sub": _UID})) is None


def test_the_wrong_scope_is_refused():
    """Checking `scope` as well as `type` is not redundant — it is what stops a future, broader
    widget-family token silently inheriting this route by carrying the same `type`."""
    assert decode_widget_token(_forge(scope="widget:portfolio")) is None
    assert decode_widget_token(_forge(scope=None)) is None


def test_a_missing_or_empty_subject_is_refused():
    assert decode_widget_token(_forge(sub="")) is None
    assert decode_widget_token(_forge(sub=None)) is None


# ── 2. 🔴 The escalation. This is why `_decode_access_token` is an allow-list ────

def test_a_widget_token_is_not_a_session_credential():
    """Presented as a Bearer, it must fail exactly as an unverifiable token does.

    Under the previous deny-list (`type == "refresh"` refused, everything else accepted) this
    would have resolved to a real user id on every authenticated route in the app.
    """
    from jose import JWTError

    from app.dependencies import _decode_access_token

    with pytest.raises(JWTError):
        _decode_access_token(create_widget_token(_UID))


def test_a_refresh_token_is_still_not_a_session_credential():
    """The original defect this check was written for. Inverting to an allow-list must not have
    dropped it on the way past."""
    from jose import JWTError

    from app.dependencies import _decode_access_token

    with pytest.raises(JWTError):
        _decode_access_token(create_refresh_token({"sub": _UID}))


def test_a_real_access_token_still_works():
    """Anti-vacuity: an allow-list that refused everything would satisfy both tests above."""
    from app.dependencies import _decode_access_token

    assert _decode_access_token(create_access_token({"sub": _UID}))["sub"] == _UID


@pytest.mark.parametrize(
    "path",
    ["/api/v1/widget/portfolio-mover", "/api/v1/widget/token", "/api/v1/users/me/credits"],
)
def test_a_widget_token_opens_no_session_route(client, path):
    """End to end, through the real app, on the route that mints it and on one that holds the
    caller's own data."""
    r = client.get(path, headers={"Authorization": f"Bearer {create_widget_token(_UID)}"})
    assert r.status_code == 401, f"{path} accepted a widget token as a session"


# ── 3. Routing: who may call the market route ───────────────────────────────

def test_no_credential_is_refused(client):
    """The licence gate. `tests/test_account_only_licence_gate.py` pins the same thing across
    every prefix; repeated here because this route is the one with an exception."""
    r = client.get(_MARKET)
    assert r.status_code == 401
    assert r.json()["error_code"] == "AUTH_REQUIRED"


def test_a_widget_token_is_accepted(client):
    r = client.get(_MARKET, headers={WIDGET_TOKEN_HEADER: create_widget_token(_UID)})
    assert r.status_code == 200


def test_a_session_bearer_is_accepted(client):
    """The APP calls this route too, through `APIClient` with a real session — the widget token
    is an addition, not a replacement."""
    r = client.get(
        _MARKET, headers={"Authorization": f"Bearer {create_access_token({'sub': _UID})}"}
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "header_value",
    ["", "   ", "nonsense", "not.a.token"],
)
def test_a_bad_widget_token_is_refused(client, header_value):
    r = client.get(_MARKET, headers={WIDGET_TOKEN_HEADER: header_value})
    assert r.status_code == 401


def test_an_access_token_in_the_widget_header_is_refused(client):
    """Wrong slot. The two credentials are not interchangeable in either direction."""
    r = client.get(_MARKET, headers={WIDGET_TOKEN_HEADER: create_access_token({"sub": _UID})})
    assert r.status_code == 401


def test_a_bad_bearer_is_not_rescued_by_a_valid_widget_token(client):
    """auth.md §4: a present-but-invalid bearer must NEVER be silently downgraded.

    Falling through to the widget header here would serve a signed-in user whose token died
    under the widget's identity, with nothing telling the client to refresh — the single largest
    source of the app-wide auth unreliability the redesign existed to fix.
    """
    r = client.get(
        _MARKET,
        headers={
            "Authorization": "Bearer garbage",
            WIDGET_TOKEN_HEADER: create_widget_token(_UID),
        },
    )
    assert r.status_code == 401
    assert r.json()["error_code"] in {"AUTH_TOKEN_INVALID", "AUTH_UNAVAILABLE"}


# ── 4. The allow-list: exactly one route may take a widget token ────────────

def _routes_using(dep_name: str) -> set[str]:
    """Paths whose resolved dependency tree contains `dep_name`, read off the live app."""
    found = set()
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        stack, seen = [dependant], set()
        while stack:
            d = stack.pop()
            if id(d) in seen:
                continue
            seen.add(id(d))
            if getattr(d.call, "__name__", "") == dep_name:
                found.add(route.path)
            stack.extend(d.dependencies)
    return found


def test_only_the_market_route_accepts_a_widget_token():
    """⚠️ THE GUARD THAT KEEPS THE TOKEN DEFENSIBLE.

    A long-lived credential in a shared container is only acceptable because it reaches a
    market-wide payload and nothing else. The day it reaches a route carrying the caller's own
    data, that reasoning collapses — and it would do so silently, because nothing else in the
    suite looks at which routes resolve `get_widget_caller`.

    `widget.py` keeps TWO routers for this reason: a route added to the file lands on the strict
    one unless someone deliberately types the permissive router's name.
    """
    accepting = _routes_using("get_widget_caller")
    assert accepting == {_MARKET}

    # ⚠️ The set above is NOT sufficient on its own, and finding that out is why this test has
    # two halves. `WidgetRateLimit` buckets per caller, so it depends on `get_widget_caller` —
    # which means moving `/market-mover` back onto the STRICT router leaves the name in the
    # dependency tree and the first assertion still passes, while the route now demands a
    # session and the extension is 401'd again. Exactly the regression this whole change exists
    # to fix, invisible to a scan that only asks "is the permissive dep present?".
    #
    # A route holding both is contradictory in any case: the strict dependency runs and raises,
    # so the widget token can never be the thing that admits the caller. Same invariant
    # `test_ios_auth_policy_parity.py::test_no_route_takes_both_a_guest_and_a_strict_dependency`
    # pins one layer up.
    both = accepting & _routes_using("get_current_user_id")
    assert not both, (
        f"{sorted(both)} require a session AND appear to accept a widget token — the strict "
        "dependency wins, so the widget is refused while the wiring looks correct"
    )


def test_the_strict_widget_routes_are_still_strict():
    """Anti-vacuity for the assertion above: if the scanner silently found nothing (a renamed
    dependency, a changed FastAPI internal), the empty set would still have to differ from
    `{_MARKET}` — but this pins the other side too."""
    strict = _routes_using("get_current_user_id")
    assert "/api/v1/widget/portfolio-mover" in strict
    assert "/api/v1/widget/token" in strict
    assert _MARKET not in strict, (
        "the market route is on the strict router again — the extension cannot call it"
    )


# ── 5. The mint ─────────────────────────────────────────────────────────────

def test_the_mint_requires_a_session(client):
    assert client.get("/api/v1/widget/token").status_code == 401


def test_the_mint_returns_a_usable_token(client):
    r = client.get(
        "/api/v1/widget/token",
        headers={"Authorization": f"Bearer {create_access_token({'sub': _UID})}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert decode_widget_token(body["token"]) == _UID
    # ISO-8601 with no fractional seconds: `ISO8601DateFormatter` on iOS rejects them, and a
    # nil expiry there means the app renews on every refresh instead of every 60 days.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", body["expires_at"]), body


def test_the_minted_token_is_for_the_CALLER(client):
    """A correct-looking token for somebody else's subject would be worse than no token."""
    other = "99999999-8888-7777-6666-555555555555"
    r = client.get(
        "/api/v1/widget/token",
        headers={"Authorization": f"Bearer {create_access_token({'sub': other})}"},
    )
    assert decode_widget_token(r.json()["token"]) == other


# ── 6. iOS side: the extension sends it, and skips the call without one ─────

_REPO = Path(__file__).resolve().parents[2]
_FETCHER = _REPO / "frontend" / "ios" / "Shared" / "WidgetMarketFetcher.swift"
_CONFIG = _REPO / "frontend" / "ios" / "Shared" / "WidgetAPIConfig.swift"


def _strip_comments(src: str) -> str:
    """Comments here narrate the fix in full and contain every token below (testing.md §3)."""
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            out.append("")
        else:
            out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of the declaration whose signature contains `header`."""
    i = src.index(header)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


def test_the_header_name_matches_the_backend():
    """Two copies of a header name is exactly the kind of drift that fails silently — the route
    would answer 401 and the tile would just look stale."""
    assert f'"{WIDGET_TOKEN_HEADER}"' in _CONFIG.read_text()


def test_the_fetcher_sends_the_token():
    body = _decl_block(_strip_comments(_FETCHER.read_text()), "func fetchMarket()")
    assert "WidgetAPIConfig.tokenHeader" in body, "the request goes out unauthenticated"
    assert "forHTTPHeaderField: WidgetAPIConfig.tokenHeader" in body


def test_the_fetcher_skips_the_call_when_there_is_no_token():
    """WidgetKit grants only a few dozen refreshes a day and adapts to how often the tile is
    viewed. Spending one on a request that can only 401 is a refresh the tile never gets back —
    and signed out, the correct render is the placeholder, not a stale price."""
    body = _decl_block(_strip_comments(_FETCHER.read_text()), "func fetchMarket()")
    guard = body.index("guard let token = WidgetAPIConfig.widgetToken")
    call = body.index("URLSession.shared.data")
    assert guard < call, "the token check must come BEFORE the request is issued"
    assert "return nil" in body[guard:call]


def test_only_the_app_writes_the_token():
    """The extension reads. If it could write, a compromised extension could plant a credential
    the app would then keep renewing."""
    src = _strip_comments(_CONFIG.read_text())
    fetcher = _strip_comments(_FETCHER.read_text())
    assert "publishWidgetToken" not in fetcher and "clearWidgetToken" not in fetcher
    for fn in ("publishWidgetToken", "clearWidgetToken"):
        assert f"func {fn}(" in src or f"func {fn}()" in src


# ── MUTATION_LOG ────────────────────────────────────────────────────────────
#
# Every guard here was broken by hand and observed to fail, per `.claude/rules/testing.md` §3.
# Run 2026-09-07; 16 mutations killed, 1 control survived.
#
#  1. `WidgetMarketFetcher` drops `setValue(token, forHTTPHeaderField:)`
#       -> test_the_fetcher_sends_the_token FAILED ✅
#          (and test_ios_widget_self_refresh.py::test_the_extension_fetch_is_authenticated ✅)
#  2. The no-token `guard` replaced with `if false`, so the request goes out anyway
#       -> test_the_fetcher_skips_the_call_when_there_is_no_token FAILED ✅
#  3. `tokenHeader` renamed to "X-Caydex-Widget" — the silent-drift case, where the route
#     answers 401 and the tile merely looks stale
#       -> test_the_header_name_matches_the_backend FAILED ✅
#  4. `@widget_client_router.get("/market-mover")` -> `@router.get(...)`, i.e. the route put
#     back behind a session so the extension is 401'd again
#       -> ⚠️ SURVIVED the first draft of test_only_the_market_route_accepts_a_widget_token,
#          which asserted only that `get_widget_caller` appeared in the dependency tree.
#          `WidgetRateLimit` buckets per caller and therefore DEPENDS on `get_widget_caller`,
#          so the name stayed in the tree while the route demanded a session. The test now
#          also asserts no route holds both dependencies -> FAILED ✅
#  5. `/portfolio-mover` moved ONTO the permissive router — the leak the allow-list exists for
#       -> test_only_the_market_route_accepts_a_widget_token FAILED ✅
#  6. `_decode_access_token` reverted to the deny-list (`if kind == "refresh"`)
#       -> test_a_widget_token_is_not_a_session_credential FAILED ✅  (the escalation)
#  7. `decode_widget_token` stops checking `scope`
#       -> test_the_wrong_scope_is_refused FAILED ✅
#  8. Router dependency AND `WidgetRateLimit` both removed — the only mutation that genuinely
#     opens the route to an anonymous caller
#       -> test_account_only_licence_gate.py FAILED ✅ and this file FAILED ✅
#     Removing the router dependency ALONE leaves it 401 (measured), because the rate limiter
#     resolves `get_widget_caller` too. Defence in depth, not a gap — recorded so the next
#     reader does not mistake that for an untested path.
#
# CONTROL: appending "groundingLines tokenHeader clearAll" to a COMMENT in the fetcher left
# every scan green ✅ — the comment strippers are doing their job.
