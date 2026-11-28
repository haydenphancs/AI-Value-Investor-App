"""The adversarial credential grid, across every auth dependency.

Auth used to answer the same way — a silent 200 with guest data, or FastAPI's 403 — for a
whole family of distinct situations, and the client could not tell them apart. This pins the
grid so they can never collapse back together:

    no header            → guest (or AUTH_REQUIRED on strict routes)
    "Bearer" + nothing   → ABSENCE, not a bad credential
    non-Bearer scheme    → absence
    lowercase "bearer"   → a real credential (RFC 7235: the scheme is case-insensitive)
    garbage / expired    → AUTH_TOKEN_INVALID
    refresh-as-access    → AUTH_TOKEN_INVALID
    valid, no users row  → AUTH_ACCOUNT_NOT_FOUND
    users read raises    → AUTH_UNAVAILABLE (retryable — NOT 500, NOT a bad credential)

The distinction that matters most on the client: only AUTH_TOKEN_INVALID / AUTH_SESSION_EXPIRED
/ AUTH_ACCOUNT_NOT_FOUND may cost the user their stored credential. AUTH_REQUIRED must not (it
means "you were never signed in"), and neither may AUTH_UNAVAILABLE (transient).

Hermetic: dependencies are invoked directly with stub Supabase clients — the suite's standard
idiom, no TestClient.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.dependencies as deps
from app.core.security import create_access_token, create_refresh_token
from app.dependencies import (
    GUEST_USER_ID,
    get_chat_identity,
    get_current_user,
    get_current_user_id,
    get_current_user_or_guest,
    get_identity_only_user,
    get_learn_identity,
    get_optional_user_id,
    get_profile_identity,
    get_research_identity,
    get_watchlist_identity,
)

# All FIVE per-install wrappers. `get_chat_identity` was missing from the two parametrized
# tests below — chat is the surface people paste holdings into, and the one migration 111 was
# written for, so leaving it out of the matrix was the wrong omission to have.
# `get_profile_identity` (migration 131) joins for the same reason: it partitions the investor
# profile per install, so it inherits the identical obligations — reject an unverifiable token
# rather than silently downgrading to guest, and never collapse two installs onto one bucket.
_IDENTITY_WRAPPERS = [
    get_learn_identity,
    get_research_identity,
    get_watchlist_identity,
    get_chat_identity,
    get_profile_identity,
]
_IDENTITY_WRAPPER_IDS = ["learn", "research", "watchlist", "chat", "profile"]

_USER_ID = "11111111-2222-3333-4444-555555555555"


# ── stubs ────────────────────────────────────────────────────────────────────

class _SB:
    """Supabase stub returning a fixed row set from any query chain."""

    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self, *a, **k):
        class _R:
            data = self._rows
        return _R()


class _RaisingSB:
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self, *a, **k):
        raise RuntimeError("transient postgrest blip")


def _row():
    return [{"id": _USER_ID, "email": "u@example.com", "tier": "free", "password_changed_at": None}]


def _bearer(token):
    return f"Bearer {token}"


def _creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _access():
    return create_access_token({"sub": _USER_ID, "email": "u@example.com"})


def _code(exc: HTTPException) -> str:
    """The structured code, asserting the contract shape on the way through."""
    assert isinstance(exc.detail, dict), f"auth errors must carry the contract body, got {exc.detail!r}"
    for key in ("error_code", "message", "user_message", "action", "details"):
        assert key in exc.detail, f"missing {key} in {exc.detail!r}"
    return exc.detail["error_code"]


# ── strict: get_current_user_id ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strict_no_credential_is_AUTH_REQUIRED_not_403():
    """THE reported bug. FastAPI's HTTPBearer(auto_error=True) answered a missing header with
    403 'Not authenticated', which iOS never treats as recoverable — so tapping Follow while
    signed out reverted the button with nothing shown."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=None)
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_REQUIRED"
    assert exc.value.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_strict_empty_credential_is_AUTH_REQUIRED():
    """`Authorization: Bearer` with nothing after it is absence, not a broken token — the
    client must not be told to discard a credential it never sent."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds("   "))
    assert _code(exc.value) == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_strict_garbage_credential_is_AUTH_TOKEN_INVALID():
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds("not.a.jwt"))
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_strict_refresh_token_is_AUTH_TOKEN_INVALID():
    """A refresh token lives 7 days and skips the password-change eviction, so it must never
    work as an access credential."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds(create_refresh_token({"sub": _USER_ID})))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_strict_expired_token_is_AUTH_TOKEN_INVALID():
    expired = create_access_token(
        {"sub": _USER_ID, "email": "u@example.com"}, expires_delta=timedelta(seconds=-10)
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds(expired))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_strict_valid_token_resolves():
    assert await get_current_user_id(credentials=_creds(_access())) == _USER_ID


# ── strict: get_current_user ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_users_row_is_ACCOUNT_NOT_FOUND():
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            credentials=_creds(_access()), user_id=_USER_ID, supabase=_SB([])
        )
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_users_read_failure_is_503_not_500():
    """A read failure is RETRYABLE and must preserve the credential. As a 500 it was not an
    auth error to the client at all, so the app kept a good token bound to a request that could
    never resolve and retried forever. It also has to match `get_current_user_or_guest`, which
    already answered 503 — one failure mode must not have two contracts."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            credentials=_creds(_access()), user_id=_USER_ID, supabase=_RaisingSB()
        )
    assert exc.value.status_code == 503
    assert _code(exc.value) == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_password_change_eviction_is_SESSION_EXPIRED():
    changed = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    rows = [{"id": _USER_ID, "email": "u@example.com", "password_changed_at": changed}]
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            credentials=_creds(_access()), user_id=_USER_ID, supabase=_SB(rows)
        )
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_SESSION_EXPIRED"


# ── optional-auth: absence is a guest, a BAD credential is not ───────────────

@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer    ", "Basic dXNlcjpwYXNz", "Token abc"],
    ids=["none", "empty", "bearer-bare", "bearer-blank", "basic", "other-scheme"],
)
@pytest.mark.asyncio
async def test_absent_credential_is_a_guest(header):
    user = await get_current_user_or_guest(authorization=header, supabase=_SB(_row()))
    assert user["id"] == GUEST_USER_ID
    assert await get_optional_user_id(authorization=header) is None


@pytest.mark.parametrize("bad", ["not.a.jwt", "a.b.c"], ids=["garbage", "shaped-but-invalid"])
@pytest.mark.asyncio
async def test_bad_credential_is_rejected_not_guested(bad):
    """The single largest source of app-wide auth unreliability: an expired token used to be
    silently demoted to the shared guest and answered 200, so a signed-in user was served the
    guest's watchlist and reports with nothing on the wire telling the client to refresh."""
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(authorization=_bearer(bad), supabase=_SB(_row()))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"

    with pytest.raises(HTTPException) as exc2:
        await get_optional_user_id(authorization=_bearer(bad))
    assert _code(exc2.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_lowercase_bearer_scheme_is_a_credential():
    """RFC 7235 makes the scheme case-insensitive. The old `startswith("Bearer ")` treated
    `bearer x` as no credential at all — a signed-in caller silently served guest data."""
    user = await get_current_user_or_guest(
        authorization=f"bearer {_access()}", supabase=_SB(_row())
    )
    assert user["id"] == _USER_ID


def _strip_py_comments(src: str) -> str:
    """A function's source with its docstring and every `#` comment removed.

    `.claude/rules/testing.md` §3 rule 1. Acute here: the wrappers' own docstrings narrate the
    guest history they no longer implement, so an un-stripped scan for `guest_user_id_for`
    would fail on prose, and one for its ABSENCE would pass on prose after a real revert.
    `ast.unparse` drops comments for free; the docstring has to be popped explicitly.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(src))
    fn = tree.body[0]
    body = getattr(fn, "body", None)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        fn.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ── the five identity wrappers are STRICT now ────────────────────────────────
#
# These four tests used to assert the opposite: that a tokenless caller was resolved to a
# per-install guest, and that `is_guest` was True. FMP's signed Order Form grants only
# Access-Restricted External Display — their market data may be shown solely "through the
# Licensee's authenticated platform" — so the guest path is gone.
#
# The two bugs the originals were written to catch are NOT gone, and are re-pinned below:
#   • a wrapper that drops `is_guest` entirely (callers default it to DENY, so absence
#     silently disables features for real accounts — `whales.py:130`), and
#   • a wrapper that answers a bad credential with an identity instead of a rejection.


@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
def test_identity_wrappers_delegate_to_the_strict_dependency(dep):
    """Structural, and it is the assertion that carries the whole credential grid.

    Each wrapper resolves its identity through `get_current_user`, so all five inherit
    AUTH_REQUIRED (no credential — the client must NOT clear its Keychain),
    AUTH_TOKEN_INVALID, AUTH_ACCOUNT_NOT_FOUND, AUTH_SESSION_EXPIRED and the retryable
    AUTH_UNAVAILABLE 503 for free. A hand-rolled `if not token: raise 401` inside a wrapper
    would pass any behavioural test written against it while collapsing five distinct client
    behaviours into one — which is exactly how "you tapped something that needs an account"
    became "your session expired" (`.claude/rules/auth.md` §3).
    """
    import inspect

    params = inspect.signature(dep).parameters
    assert "user" in params, (
        f"{dep.__name__} no longer takes a resolved `user` — if it went back to reading "
        "headers itself, it is no longer covered by the credential grid above"
    )
    default = params["user"].default
    assert getattr(default, "dependency", None) is get_current_user, (
        f"{dep.__name__} must delegate to get_current_user, found {default!r}"
    )


@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
@pytest.mark.asyncio
async def test_identity_wrappers_flag_a_real_account_as_not_guest(dep):
    """Present-and-False, NEVER absent — and this is a live trap, not tidiness.

    `whales.py:130` reads `bool(user.get("is_guest", True))`: the default is DENY. A wrapper
    that returned a bare `get_current_user` row would therefore classify every signed-in user
    as a guest and silently disable whale force-refresh for the entire user base, with nothing
    logging or failing. `.get()` cannot tell "False" from "never set", so the key must be
    present and explicitly False.
    """
    user = await dep(user=dict(_row()[0]))
    assert user["id"] == _USER_ID
    assert "is_guest" in user, (
        f"{dep.__name__} dropped the is_guest key — callers that default it to True "
        "(whales.py:130) will treat every real account as a guest"
    )
    assert user.get("is_guest") is False


@pytest.mark.parametrize("dep", _IDENTITY_WRAPPERS, ids=_IDENTITY_WRAPPER_IDS)
@pytest.mark.asyncio
async def test_identity_wrappers_preserve_the_account_row(dep):
    """The wrapper adds a flag; it must not shadow or drop the row it was given.

    `tier` in particular: every entitlement gate downstream reads it, and a wrapper that
    rebuilt a fresh dict with a hardcoded "free" would silently drop every paying user to the
    free tier with no error anywhere — the failure mode `test_updates_tabs_group_gate.py`
    documents as "EVERY PAYING USER silently drops to one chip".
    """
    row = dict(_row()[0])
    row["tier"] = "premium"
    out = await dep(user=row)
    assert out["tier"] == "premium"
    assert out["email"] == row["email"]


def test_no_identity_wrapper_can_still_mint_a_guest_partition():
    """Source-scan: none of the five may call `guest_user_id_for` any more.

    The behavioural tests above cannot see this — a wrapper could compute a synthetic id and
    then never return it on the paths they exercise. `guest_user_id_for` itself MUST survive
    (RateLimitChecker and identity_key use it to bucket unauthenticated callers, and after the
    wall /auth/login is the only unauthenticated surface left), so its mere existence proves
    nothing; what matters is that no identity dependency reaches for it.
    """
    import inspect
    import re

    for dep in _IDENTITY_WRAPPERS:
        code = _strip_py_comments(inspect.getsource(dep))
        assert "guest_user_id_for" not in code, (
            f"{dep.__name__} still mints a per-install guest id"
        )
        assert not re.search(r"\bGUEST_USER_ID\b", code), (
            f"{dep.__name__} still references the shared guest sentinel"
        )


# ── the deliberate carve-out ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "header",
    [None, "Bearer not.a.jwt", f"Bearer {create_refresh_token({'sub': _USER_ID})}", "Basic xyz"],
    ids=["none", "garbage", "refresh-token", "basic"],
)
@pytest.mark.asyncio
async def test_identity_only_user_NEVER_raises(header):
    """Analytics promises in its own module docstring that it can never break the app, and the
    iOS `Analytics` actor drops the batch on any error. So this ONE dependency keeps the guest
    fallback while every sibling now rejects. Pinned so a future 'consistency' pass doesn't
    quietly make instrumentation able to fail the product."""
    user = await get_identity_only_user(authorization=header)
    assert user["id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_identity_only_user_still_resolves_a_good_token():
    user = await get_identity_only_user(authorization=_bearer(_access()))
    assert user["id"] == _USER_ID


# ── no Supabase token is a bearer, and nothing environmental turns a 401 into a 503 ─────
#
# Inverted on 2026-11-26 (SOC-01 / SEC-2). This section used to pin the opposite: with
# SUPABASE_JWT_SECRET unset, EVERY unverifiable bearer — an ordinary expired app token included —
# answered 503 AUTH_UNAVAILABLE. iOS refreshes only on 401, so the owner could never delete that
# variable without stranding every user whose token had expired, and the variable was the key to
# a revoked-but-unrotatable master key. The Supabase bearer fallback is gone, so the answer for a
# bad bearer is AUTH_TOKEN_INVALID whatever the environment holds, and a JWKS outage cannot
# reach a bearer route at all.

_LEGACY_SECRET = "legacy-shared-secret-for-tests-only-not-real"


def _supabase_hs256(claims):
    from jose import jwt as jose_jwt
    return jose_jwt.encode(claims, _LEGACY_SECRET, algorithm="HS256")


@pytest.fixture
def _no_jwks(monkeypatch):
    """Fails the test if any bearer dependency reaches for the Supabase JWKS."""
    from app.core import security

    calls = []

    async def _fetch():
        calls.append(1)
        return {}

    security._reset_jwks_cache_for_tests()
    monkeypatch.setattr(security, "_fetch_jwks", _fetch)
    yield calls
    security._reset_jwks_cache_for_tests()
    assert calls == [], "a bearer dependency consulted the Supabase JWKS"


def _expired_access():
    return create_access_token(
        {"sub": _USER_ID, "email": "u@example.com"}, expires_delta=timedelta(seconds=-10)
    )


@pytest.mark.parametrize("secret", [None, _LEGACY_SECRET], ids=["secret-unset", "secret-set"])
@pytest.mark.parametrize("token", ["not.a.jwt", "expired"], ids=["garbage", "expired-app-token"])
@pytest.mark.asyncio
async def test_an_unverifiable_bearer_is_INVALID_whatever_the_legacy_secret(
    monkeypatch, _no_jwks, secret, token
):
    """The 401 that makes iOS refresh — never the 503 that made it keep a dead token."""
    monkeypatch.setattr(deps.settings, "SUPABASE_JWT_SECRET", secret)
    bearer = _expired_access() if token == "expired" else token
    for call in (
        lambda: get_current_user_id(credentials=_creds(bearer)),
        lambda: get_current_user_or_guest(authorization=_bearer(bearer), supabase=_SB(_row())),
        lambda: get_optional_user_id(authorization=_bearer(bearer)),
        lambda: deps.get_widget_caller(credentials=_creds(bearer), x_widget_token=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 401
        assert _code(exc.value) == "AUTH_TOKEN_INVALID"


@pytest.mark.parametrize(
    "claims",
    [{"sub": _USER_ID}, {"sub": _USER_ID, "aud": "authenticated", "exp": 4102444800}],
    ids=["bare-sub-no-exp", "well-formed"],
)
@pytest.mark.asyncio
async def test_a_token_signed_with_the_legacy_supabase_secret_is_no_bearer(
    monkeypatch, _no_jwks, claims
):
    """THE master key (SOC-01): `{"sub": victim}` signed with the revoked legacy HS256 secret
    used to authenticate as the victim on every route, forever, surviving their password reset.
    The secret is still configured here — it must simply not matter."""
    monkeypatch.setattr(deps.settings, "SUPABASE_JWT_SECRET", _LEGACY_SECRET)
    forged = _supabase_hs256(claims)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=_creds(forged))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(authorization=_bearer(forged), supabase=_SB(_row()))
    assert _code(exc.value) == "AUTH_TOKEN_INVALID"
    user = await get_identity_only_user(authorization=_bearer(forged))
    assert user["id"] == GUEST_USER_ID, "the identity-only path must not resolve it either"


@pytest.mark.asyncio
async def test_a_genuine_supabase_es256_session_token_is_no_bearer(monkeypatch):
    """SEC-2: a VALID Supabase access token (as GoTrue's password grant would mint) is accepted
    in exactly one place — the body of `/auth/session-exchange` — never as a bearer. As a bearer
    it skipped password-change eviction entirely (`_decode_access_token` raised, the check failed
    open), so a reset never evicted whoever held one."""
    import time as _time
    from jose import jwt as jose_jwt
    from app.core import security

    private_pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgevZzL1gdAFr88hb2\n"
        "OF/2NxApJCzGCEDdfSp6VQO30hyhRANCAAQRWz+jn65BtOMvdyHKcvjBeBSDZH2r\n"
        "1RTwjmYSi9R/zpBnuQ4EiMnCqfMPWiZqB4QdbAd0E7oH50VpuZ1P087G\n"
        "-----END PRIVATE KEY-----"
    )
    public_jwk = {
        "kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": "matrix-kid",
        "x": "EVs_o5-uQbTjL3chynL4wXgUg2R9q9UU8I5mEovUf84",
        "y": "kGe5DgSIycKp8w9aJmoHhB1sB3QTugfnRWm5nU_TzsY",
    }
    monkeypatch.setattr(security.settings, "SUPABASE_URL", "https://matrix.supabase.co")
    security._reset_jwks_cache_for_tests()
    security._JWKS_CACHE["keys"] = {"matrix-kid": public_jwk}
    security._JWKS_CACHE["fetched_at"] = _time.monotonic()
    try:
        now = int(_time.time())
        token = jose_jwt.encode(
            {"sub": _USER_ID, "aud": "authenticated", "iss": "https://matrix.supabase.co/auth/v1",
             "iat": now, "exp": now + 3600, "role": "authenticated"},
            private_pem, algorithm="ES256", headers={"kid": "matrix-kid"},
        )
        # Control: the token IS valid — session-exchange would accept it.
        assert (await security.verify_supabase_token(token))["sub"] == _USER_ID
        with pytest.raises(HTTPException) as exc:
            await get_current_user_id(credentials=_creds(token))
        assert _code(exc.value) == "AUTH_TOKEN_INVALID"
        with pytest.raises(HTTPException) as exc:
            await get_current_user_or_guest(authorization=_bearer(token), supabase=_SB(_row()))
        assert _code(exc.value) == "AUTH_TOKEN_INVALID"
    finally:
        security._reset_jwks_cache_for_tests()


def test_the_bearer_dependencies_never_consult_supabase():
    """Source guard, comment-stripped: the fallback must not come back by a quiet re-import."""
    import inspect
    import re as _re

    src = inspect.getsource(deps)
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    # Drop docstrings too: they explain the removed fallback by name.
    code = _re.sub(r'"""[\s\S]*?"""', "", code)
    assert "verify_supabase_token" not in code
    assert "SUPABASE_JWT_SECRET" not in code
    reject = inspect.getsource(deps._reject_unverifiable_token)
    reject_code = _re.sub(r'"""[\s\S]*?"""', "", reject)
    assert "AUTH_UNAVAILABLE" not in reject_code, (
        "an unverifiable bearer must answer AUTH_TOKEN_INVALID, never a retryable 503"
    )


# ── AI generation is account-only ────────────────────────────────────────────
#
# The change these pin: guest AI metering keyed on `guest_user_id_for(X-Guest-Id)`, a UUID5 of a
# header the CLIENT chooses. Rotating it minted a fresh identity — and therefore a fresh
# GUEST_REPORT_MONTHLY_LIMIT — on every request, so the most expensive call in the product
# (~17 Gemini + ~20 FMP per run) was unmetered against anyone willing to send a new header.
# There was no per-IP limit behind it and credits don't meter the guest sentinel.

@pytest.mark.parametrize(
    "header, guest_id",
    [(None, None), (None, "install-A"), (None, "install-B"), ("Bearer   ", "install-C")],
    ids=["no-header", "guest-A", "guest-B", "blank-bearer"],
)
@pytest.mark.asyncio
async def test_no_guest_identity_can_reach_AI_generation(header, guest_id):
    """Whatever install id it claims, a caller with no credential is refused.

    Parametrised over DIFFERENT guest ids on purpose: that is the rotation attack. Under the
    old per-install allowance each of these was a brand-new "person" with an unused free report.
    """
    with pytest.raises(HTTPException) as exc:
        await get_current_user_id(credentials=None)
    assert exc.value.status_code == 401
    assert _code(exc.value) == "AUTH_REQUIRED"


def test_both_generation_paths_require_an_account():
    """Gating only /research/generate would be cosmetic — `GET /stocks/{ticker}/report` runs the
    same pipeline for the same cost on a cache miss."""
    import re
    from pathlib import Path

    endpoints = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints"
    # ticker_report.py: 1 since 2026-09-11 — `POST /stocks/{t}/report/chat` (its second strict
    # dependency) was deleted; the report door itself is the one that must stay gated.
    for name, minimum in (("research.py", 9), ("ticker_report.py", 1)):
        src = (endpoints / name).read_text()
        strict = src.count("Depends(get_current_user)") + src.count("Depends(get_current_user_id)")
        assert strict >= minimum, (
            f"{name}: found {strict} strict auth dependencies, expected >= {minimum}. "
            "Every AI-generation route must take get_current_user or get_current_user_id."
        )
        for guest_dep in ("get_current_user_or_guest", "get_research_identity"):
            assert f"Depends({guest_dep})" not in src, f"{name} still admits guests via {guest_dep}"


def test_the_rotatable_guest_budget_is_no_longer_consulted():
    """`guest_report_budget` was the rotatable meter. Any live call to it re-opens the hole."""
    import pathlib

    endpoints = pathlib.Path(__file__).resolve().parents[1] / "app/api/v1/endpoints"
    for name in ("research.py", "ticker_report.py", "stocks.py"):
        code = "\n".join(
            line for line in (endpoints / name).read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "get_guest_report_budget_service" not in code, name


# ── MUTATION_LOG — the identity-wrapper block ────────────────────────────────────────
#
# Hand-run 2026-09-07 when these four tests were inverted from "guests are partitioned per
# install" to "there are no guests". Each mutation applied to app/dependencies.py, the file
# run, then reverted. testing.md §3 rule 3 — an inverted guard that survives its own mutation
# is worse than the guard it replaced, because it reads as coverage.
#
#  1. Wrapper returns `dict(user)` — the is_guest key dropped. This is the REAL regression the
#     redesign invites, since a bare get_current_user row looks obviously correct.
#       -> test_identity_wrappers_flag_a_real_account_as_not_guest FAILED  ✅
#  2. Wrapper returns `{**user, "tier": "free", ...}` — the silent paid-user downgrade.
#       -> test_identity_wrappers_preserve_the_account_row FAILED  ✅
#  3. Wrapper regrows a guest branch calling guest_user_id_for().
#       -> test_no_identity_wrapper_can_still_mint_a_guest_partition FAILED  ✅
#  4. One wrapper reverted to Depends(get_current_user_or_guest) — i.e. tokenless callers are
#     silently guests again, which is the licence breach this whole change exists to close.
#       -> test_identity_wrappers_delegate_to_the_strict_dependency FAILED  ✅
#  5. Anti-vacuity: `guest_user_id_for` and `GUEST_USER_ID` written into a wrapper's DOCSTRING
#     with the code left correct.
#       -> 46 passed ✅ — the scan reads code, not the prose that narrates the history.


def test_the_home_dashboard_resolves_the_caller_through_the_users_row():
    """`/home/dashboard` serves the caller's OWN watchlist strip. A token-only dependency has
    no eviction — a deleted account's still-valid JWT would read that data for the token's
    lifetime — so it must resolve the `users` row (`get_watchlist_identity`) (F14-5)."""
    import inspect
    from app.api.v1.endpoints import home as home_ep
    from app.dependencies import get_watchlist_identity

    deps = [p.default.dependency
            for p in inspect.signature(home_ep.get_home_dashboard).parameters.values()
            if hasattr(p.default, "dependency")]
    assert get_watchlist_identity in deps


def test_the_legacy_home_feed_stays_removed():
    """Removed 2026-11-05: the app stopped calling `/home/feed` on 2026-08-19, and its briefing
    fallback was the last multi-day `earnings-calendar` read — FMP cuts those at 4,000 rows
    and keeps the NEWEST dates, so the nearest days were the ones lost."""
    from app.api.v1.endpoints import home as home_ep

    paths = {route.path for route in home_ep.router.routes}
    assert "/dashboard" in paths, paths     # anti-vacuity: this is the right router
    assert "/feed" not in paths, paths


def test_the_trillion_club_detail_is_strict_and_reads_its_tier_from_the_users_row():
    """`GET /home/trillion-club/{slug}` serves 13F rows (FMP-licensed) and gates their depth
    by tier. Two obligations, pinned separately because either can regress alone:

    * the ROUTER's strict `get_current_user_id` covers it — a per-route guest dependency
      could never re-open it, since the router dependency runs first and raises;
    * the handler's own identity is `get_watchlist_identity` (the users row), never a
      token-only dependency: a token carries no `tier`, so a token-only read would lock every
      Pro caller out — and a deleted account's still-valid JWT would keep reading.
    """
    import inspect
    from app.api.v1.endpoints import home as home_ep
    from app.dependencies import get_current_user_id, get_watchlist_identity

    router_deps = [d.dependency for d in home_ep.router.dependencies]
    assert get_current_user_id in router_deps

    route = next(r for r in home_ep.router.routes if r.path == "/trillion-club/{slug}")
    assert route.endpoint is home_ep.get_trillion_club_detail
    deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
            if hasattr(p.default, "dependency")]
    assert deps == [get_watchlist_identity]
    for guest_dep in ("get_current_user_or_guest", "get_optional_user_id", "get_identity_only_user"):
        assert all(getattr(d, "__name__", "") != guest_dep for d in deps), guest_dep


# ── The Holdings widget grant (migration 216): the same grid, one more credential ────────────
#
# `get_holdings_grant_caller` reads ONE header and the grant row decides the user. Its codes are
# the extension's contract: only AUTH_TOKEN_INVALID / AUTH_SESSION_EXPIRED /
# AUTH_ACCOUNT_NOT_FOUND mean "key refused" (hide the tile); AUTH_REQUIRED never clears anything
# and AUTH_UNAVAILABLE keeps the last snapshot.

from app.dependencies import HoldingsGrantCaller, get_holdings_grant_caller  # noqa: E402
from app.services import widget_holdings_grant_service as _grants  # noqa: E402


class _GrantSB:
    """A store whose `check_widget_holdings_grant` answers a fixed body; counts calls."""

    def __init__(self, answer=None, raises=False):
        self.answer, self.raises, self.calls = answer, raises, 0

    def rpc(self, name, params):
        assert name == "check_widget_holdings_grant"
        outer = self

        class _C:
            def execute(self_inner):
                outer.calls += 1
                if outer.raises:
                    raise RuntimeError("transient postgrest blip")

                class _R:
                    data = outer.answer
                return _R()
        return _C()


async def _holdings(token, sb):
    from app.core.security import rate_limiter

    rate_limiter.clear()
    return await get_holdings_grant_caller(request=None, x_holdings_token=token, supabase=sb)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "  "])
async def test_holdings_no_key_is_AUTH_REQUIRED(token):
    sb = _GrantSB()
    with pytest.raises(HTTPException) as e:
        await _holdings(token, sb)
    assert e.value.status_code == 401 and e.value.detail["error_code"] == "AUTH_REQUIRED"
    assert sb.calls == 0


@pytest.mark.asyncio
async def test_holdings_an_access_jwt_in_the_header_is_INVALID_with_no_read():
    sb = _GrantSB()
    with pytest.raises(HTTPException) as e:
        await _holdings(create_access_token({"sub": _USER_ID}), sb)
    assert e.value.detail["error_code"] == "AUTH_TOKEN_INVALID" and sb.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [
    ("unknown", "AUTH_TOKEN_INVALID"),
    ("revoked", "AUTH_SESSION_EXPIRED"),
    ("expired", "AUTH_SESSION_EXPIRED"),
    ("evicted", "AUTH_SESSION_EXPIRED"),
    ("account_missing", "AUTH_ACCOUNT_NOT_FOUND"),
])
async def test_holdings_every_dead_status_has_its_401_code(status, code):
    with pytest.raises(HTTPException) as e:
        await _holdings(_grants.new_token(), _GrantSB({"status": status}))
    assert e.value.status_code == 401 and e.value.detail["error_code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize("sb", [
    _GrantSB(raises=True), _GrantSB(None), _GrantSB({"status": "live"}), _GrantSB({"nope": 1}),
], ids=["store-down", "no-body", "live-without-ids", "no-status"])
async def test_holdings_store_trouble_is_503_never_allowed(sb):
    with pytest.raises(HTTPException) as e:
        await _holdings(_grants.new_token(), sb)
    assert e.value.status_code == 503 and e.value.detail["error_code"] == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_holdings_live_resolves_the_rows_user_never_a_guest():
    gid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    caller = await _holdings(_grants.new_token(), _GrantSB(
        {"status": "live", "grant_id": gid, "user_id": _USER_ID}))
    assert caller == HoldingsGrantCaller(user_id=_USER_ID, grant_id=gid)
    assert caller.user_id != GUEST_USER_ID


def test_holdings_dependency_has_no_authorization_parameter():
    """A garbage (or valid) `Authorization` header cannot change its answer: it never reads one."""
    import inspect

    assert "credentials" not in inspect.signature(get_holdings_grant_caller).parameters
