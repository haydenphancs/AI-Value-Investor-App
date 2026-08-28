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

THE SECOND HALF (see tests/test_admin_client_not_demoted.py for the behavioural proof). That
second client was still shared between SIGN-INS and `auth.admin.*`, and the SDK hands ONE
headers dict to `auth`, `auth.admin` and postgrest alike — so a sign-in re-authenticated the
admin API as that user and GoTrue refused `/admin/*` with `User not allowed`. Account deletion
(Guideline 5.1.1(v)), change-password and reset-password all failed. A THIRD client,
`get_admin_client()`, is admin-only and is never signed in on; the guards below keep it that
way.

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


# ⚠️ Both tests below use `monkeypatch`, NOT raw assignment.
#
# They used to do `db._supabase_client = object()` directly, with no restore. 58 test
# files sort after this one, and for every one of them `app.database.get_supabase()`
# then returned a bare `object()` for the rest of the session. Nothing depends on it
# today, so the suite stayed green — but it silently voids any later test that means to
# exercise real client wiring, and it makes a single-file run behave differently from a
# full-suite run. The sibling file `test_admin_client_not_demoted.py` already resets
# these same three globals via monkeypatch; this now matches it.


def test_the_isolated_client_is_a_different_object(monkeypatch):
    """Isolation is the SEPARATE INSTANCE. If both names returned one client, signing in would
    demote the very client every `.table()` call uses."""
    import app.database as db

    monkeypatch.setattr(db, "_supabase_client", object())
    monkeypatch.setattr(db, "_auth_client", object())
    assert db.get_supabase() is not db.get_auth_client()


def test_auth_client_is_memoized(monkeypatch):
    """A new client per request would leak sockets and a refresh thread each time."""
    import app.database as db

    sentinel = object()
    monkeypatch.setattr(db, "_auth_client", sentinel)
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
    assert "get_auth_client" in src
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


# ---------------------------------------------------------------------------
# 3. `auth.admin.*` only ever runs on the admin client
# ---------------------------------------------------------------------------

# Every gotrue call that emits SIGNED_IN / TOKEN_REFRESHED, i.e. every call that rewrites
# `Authorization` on the client it runs on. `sign_up` is in here because it demotes too
# whenever the project returns a session — the original docstrings named only three of these.
_SIGN_IN_VERBS = (
    "sign_in_with_password", "sign_in_with_id_token", "sign_up", "verify_otp",
    "exchange_code_for_session", "sign_in_anonymously", "set_session",
)


def _code_lines(src: str):
    """Numbered lines with whole-line comments dropped.

    A comment next to a fix usually contains every token the scan greps for, so an
    un-stripped scan keeps passing on prose after the code is reverted.
    """
    for i, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        yield i, line


def _signature_of(src: str, handler: str) -> str:
    """The parameter list of ONE handler.

    Bounded deliberately: asserting a dependency against a whole FILE passes as soon as ANY
    handler in it declares the dependency, which is how a fix to one route can look like a fix
    to its neighbour.
    """
    start = src.index(f"async def {handler}(")
    return src[start:src.index("):", start)]


def test_admin_calls_only_ever_run_on_the_admin_client():
    """THE invariant. `auth.admin.*` authenticates with the shared headers dict, so it must be
    resolved through `resolve_admin_client` — whose first candidate is the client nothing
    signs in on."""
    offenders = []
    for path, src in _sources():
        if path.name == "database.py":
            continue  # its docstrings name the pattern they exist to prevent
        for i, line in _code_lines(src):
            if ".auth.admin." in line and not line.lstrip().startswith("resolve_admin_client("):
                offenders.append(f"{path.relative_to(_BACKEND)}:{i}: {line.strip()}")
    assert not offenders, (
        "auth.admin.* not resolved through resolve_admin_client — one sign-in anywhere in the "
        "process makes GoTrue answer these with 'User not allowed':\n" + "\n".join(offenders)
    )


def test_nothing_signs_in_on_the_admin_client():
    """A sign-in here re-arms the whole bug: it rewrites the header the admin API reads."""
    offenders = []
    for path, src in _sources():
        for i, line in _code_lines(src):
            if "admin_client" not in line and "resolve_admin_client(" not in line:
                continue
            for verb in _SIGN_IN_VERBS:
                if f".{verb}(" in line:
                    offenders.append(f"{path.relative_to(_BACKEND)}:{i}: {line.strip()}")
    assert not offenders, f"a sign-in verb on the admin client: {offenders}"


def test_the_admin_client_never_reads_tables():
    """Admin-only by contract, exactly like the auth client."""
    offenders = []
    for path, src in _sources():
        for i, line in _code_lines(src):
            if "admin_client.table(" in line:
                offenders.append(f"{path.relative_to(_BACKEND)}:{i}")
    assert not offenders, f"admin_client used for a table read: {offenders}"


@pytest.mark.parametrize(
    "module, handler",
    [
        ("auth.py", "reset_password"),
        ("auth.py", "change_password"),
        ("users.py", "delete_account"),
    ],
)
def test_each_admin_handler_declares_the_admin_dependency(module, handler):
    """Brace-bounded to the handler's own signature — see `_signature_of`."""
    src = (_APP / "api" / "v1" / "endpoints" / module).read_text(encoding="utf-8")
    assert "admin_client: Client = Depends(get_admin_client)" in _signature_of(src, handler), (
        f"{module}::{handler} would fall back to the sign-in client for its admin call"
    )


def test_sdk_still_aliases_the_admin_headers_onto_options():
    """The mechanism, pinned on the dependency. `_reset_to_service_role` writes
    `options.headers`; that is only the right place while the SDK keeps handing the same dict
    to the admin API. A bump that de-aliases them makes the re-assert silently inert."""
    from supabase import create_client

    c = create_client("https://example.supabase.co", "SERVICE_ROLE_FAKE")
    assert c.options.headers is c.auth._headers
    assert c.auth._headers is c.auth.admin._headers
