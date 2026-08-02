"""`auth.*` never runs on the service-role client.

THE BUG. supabase-py registers an auth-state listener on every client it builds. After a
successful `sign_in_with_password` / `sign_in_with_id_token` / `verify_otp`, that listener
REWRITES `options.headers["Authorization"]` with the signing-in USER's JWT and nulls
`_postgrest`, so the next `.table()` call rebuilds postgrest carrying that user's token.

Every sign-in path used the ONE process-wide service-role client, so a single sign-in demoted
the whole process from `service_role` to that user, permanently, and nothing restored it.

Observed against the installed SDK:

    before      : Bearer SERVICE_ROLE_FAKE
    after       : Bearer USER_A_JWT
    _postgrest reset to None: True

What it cost, in the user's own words ("at the profile, it doesn't show any name or email"):
user A signs in; user B — valid token, did nothing — makes any request; `get_current_user`
reads `public.users` as A under RLS (`users_select_own` is `auth.uid() = id`), gets ZERO rows
for B, and 401s. The app treats that as a dead session and renders the Guest branch: "Guest",
a Sign In button, no name, no email.

The fix is a second, isolated client used only for `auth.*`. This file pins that at the source
level, so it holds regardless of how handlers are invoked.

No network — the SDK behaviour is exercised against a throwaway client.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_APP = _BACKEND / "app"


def _sources():
    for p in _APP.rglob("*.py"):
        yield p, p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. The SDK really does what we think (regression canary on the dependency)
# ---------------------------------------------------------------------------


def test_sdk_still_rewrites_the_auth_header_on_sign_in():
    """If a future supabase-py stops doing this, the isolation is no longer load-bearing and
    this test tells us why the rest of the file exists."""
    from supabase import create_client

    c = create_client("https://example.supabase.co", "SERVICE_ROLE_FAKE")
    assert c.options.headers.get("Authorization") == "Bearer SERVICE_ROLE_FAKE"

    class _Session:
        access_token = "USER_A_JWT"

    c._listen_to_auth_events("SIGNED_IN", _Session())

    assert c.options.headers.get("Authorization") == "Bearer USER_A_JWT", (
        "the SDK no longer rewrites the header — re-check whether get_auth_client is still needed"
    )
    assert c._postgrest is None, "the SDK no longer resets postgrest on sign-in"


def test_the_isolated_client_is_a_different_object():
    """Isolation is the SEPARATE INSTANCE. If both names returned one client, signing in would
    demote the very client every `.table()` call uses."""
    import app.database as db

    db._supabase_client = object()
    db._auth_client = object()
    assert db.get_supabase() is not db.get_auth_client()


def test_auth_client_is_memoized():
    """A new client per request would leak sockets and a refresh thread each time."""
    import app.database as db

    sentinel = object()
    db._auth_client = sentinel
    assert db.get_auth_client() is sentinel


# ---------------------------------------------------------------------------
# 2. No production code calls auth.* on the service-role client
# ---------------------------------------------------------------------------


def test_no_auth_call_on_the_service_role_client():
    """THE invariant. `supabase.auth.*` anywhere in app/ re-arms the bug."""
    offenders = []
    for path, src in _sources():
        if path.name == "database.py":
            continue  # its docstring names the pattern it exists to prevent
        for i, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r'\bsupabase\.auth\.', line):
                offenders.append(f"{path.relative_to(_BACKEND)}:{i}: {line.strip()}")
    assert not offenders, (
        "auth.* called on the service-role client — one sign-in demotes the whole process "
        "to that user under RLS:\n" + "\n".join(offenders)
    )


def test_every_handler_that_runs_auth_has_the_isolated_dependency():
    src = (_APP / "api" / "v1" / "endpoints" / "auth.py").read_text(encoding="utf-8")
    assert "from app.database import get_auth_client" in src
    # Every auth.* call goes through the resolver, which prefers the injected isolated client.
    assert "_auth_of(auth_client, supabase).auth." in src
    assert src.count("auth_client: Client = Depends(get_auth_client)") >= 8, (
        "a handler that performs auth.* is missing the isolated-client dependency"
    )


def test_the_auth_client_never_reads_tables():
    """It is auth-only by contract; a `.table()` on it would run under whatever session the
    last sign-in left behind."""
    offenders = []
    for path, src in _sources():
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r'\bauth_client\.table\(', line):
                offenders.append(f"{path.relative_to(_BACKEND)}:{i}")
    assert not offenders, f"auth_client used for a table read: {offenders}"


@pytest.mark.parametrize("session_flag", ["persist_session", "auto_refresh_token"])
def test_the_auth_client_does_not_carry_sessions_between_requests(session_flag):
    """Without these, the SDK keeps one caller's session (and a background refresh thread)
    alive on a process-wide client that serves every user."""
    src = (_APP / "database.py").read_text(encoding="utf-8")
    assert f"{session_flag}=False" in src
