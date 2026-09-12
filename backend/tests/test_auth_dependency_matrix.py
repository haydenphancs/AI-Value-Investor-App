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


# ── misconfiguration must not read as a bad credential ───────────────────────

@pytest.mark.asyncio
async def test_unset_supabase_secret_reports_UNAVAILABLE_not_INVALID(monkeypatch):
    """`verify_supabase_token` returns None when SUPABASE_JWT_SECRET is unset. Reporting that
    as a bad credential would send every Supabase-issued user into a re-sign-in loop while the
    real fault sat in the environment — so it answers AUTH_UNAVAILABLE (retryable, credential
    preserved) instead."""
    monkeypatch.setattr(deps.settings, "SUPABASE_JWT_SECRET", None)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_or_guest(authorization=_bearer("not.a.jwt"), supabase=_SB(_row()))
    assert exc.value.status_code == 503
    assert _code(exc.value) == "AUTH_UNAVAILABLE"


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
