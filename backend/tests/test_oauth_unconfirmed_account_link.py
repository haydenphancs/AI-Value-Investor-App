"""SOC-08 — a Google/Apple sign-in that links to an account somebody registered earlier and never
confirmed (sign-in audit, 2026-11-26).

What GoTrue does (verified in supabase/auth's source, master, 2026-11-26 — not reproducible on the
local stack, which cannot mint a Google or Apple identity token): the id_token grant LINKS a
verified provider email to the existing email/password user, and because that user is
unconfirmed, `RemoveUnconfirmedIdentities` NULLs its password, deletes its email identity and
REPLACES its user_metadata with the provider's identity data before `Confirm()`. That guard is
GoTrue's and is right. What it leaves behind is ours: `public.users.display_name`, copied from
the unverified registration by the signup trigger at INSERT and never synced again. A squatter
who registered the victim's address first chose the name the victim's account then wore.

`/auth/oauth` now recognises that sign-in from three signals in GoTrue's answer and resets the
name. Each signal is pinned against the legitimate shape it rules out — a wrong reset would wipe
a real user's chosen name, which is worse than the cosmetic bug — and the reset never fails a
sign-in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints import auth as auth_ep
from app.core.security import rate_limiter
from app.schemas.auth import OAuthSignInRequest

_USER_ID = "11111111-2222-4333-8444-555555555555"
_EMAIL = "victim@gmail.com"
_NOW = datetime(2026, 11, 26, 18, 0, 0, tzinfo=timezone.utc)


class _User:
    def __init__(self, *, created, confirmed, metadata):
        self.id = _USER_ID
        self.email = _EMAIL
        self.created_at = created
        self.email_confirmed_at = confirmed
        self.user_metadata = metadata


def _linked(**overrides):
    """GoTrue's answer for the SOC-08 case: registered days ago, confirmed now, metadata replaced
    by the provider's identity data (no `display_name`)."""
    shape = dict(created=_NOW - timedelta(days=3), confirmed=_NOW - timedelta(seconds=1),
                 metadata={"full_name": "Real Owner", "name": "Real Owner", "email": _EMAIL,
                           "email_verified": True, "sub": "1234567890"})
    shape.update(overrides)
    return _User(**shape)


# ── The detector ──────────────────────────────────────────────────────────────

def test_the_soc08_shape_is_recognised():
    assert auth_ep._confirmed_an_earlier_unconfirmed_account(_linked(), now=_NOW)


@pytest.mark.parametrize("why,user", [
    # A brand-new provider account: created and confirmed in one transaction.
    ("new provider account", _linked(created=_NOW - timedelta(seconds=1, milliseconds=40))),
    # An account confirmed long ago (by email, or by an earlier provider sign-in), now linking.
    ("confirmed earlier", _linked(confirmed=_NOW - timedelta(days=2))),
    # Confirmed by its emailed code a minute ago, then Google: GoTrue MERGED metadata, so the
    # chosen display_name is still there — that name is the user's own and must survive.
    ("confirmed by code, then linked", _linked(metadata={"display_name": "Chosen", "full_name": "G"})),
    # Unreadable signals: never reset on a guess.
    ("no confirmation time", _linked(confirmed=None)),
    ("no creation time", _linked(created=None)),
    ("garbage time", _linked(confirmed="not a time")),
    ("metadata not a dict", _linked(metadata=None)),
])
def test_every_legitimate_shape_is_left_alone(why, user):
    assert not auth_ep._confirmed_an_earlier_unconfirmed_account(user, now=_NOW), why


def test_string_timestamps_and_small_clock_skew_are_read():
    user = _linked(created="2026-11-23T18:00:00Z",
                   confirmed=(_NOW + timedelta(seconds=20)).isoformat().replace("+00:00", "Z"))
    assert auth_ep._confirmed_an_earlier_unconfirmed_account(user, now=_NOW)
    naive = _linked(confirmed=(_NOW - timedelta(seconds=5)).replace(tzinfo=None))
    assert auth_ep._confirmed_an_earlier_unconfirmed_account(naive, now=_NOW), "naive = UTC (GoTrue)"


@pytest.mark.parametrize("seconds,expected", [(119, True), (121, False), (-29, True), (-31, False)])
def test_the_confirmation_window_edges(seconds, expected):
    user = _linked(confirmed=_NOW - timedelta(seconds=seconds))
    assert auth_ep._confirmed_an_earlier_unconfirmed_account(user, now=_NOW) is expected


@pytest.mark.parametrize("older_by,expected", [(10, False), (11, True)])
def test_the_account_age_edge(older_by, expected):
    confirmed = _NOW - timedelta(seconds=1)
    user = _linked(confirmed=confirmed, created=confirmed - timedelta(seconds=older_by))
    assert auth_ep._confirmed_an_earlier_unconfirmed_account(user, now=_NOW) is expected


@pytest.mark.parametrize("sent,metadata,expected", [
    ("Ada Lovelace", {"full_name": "Google Name"}, "Ada Lovelace"),   # the app's (Apple's) name first
    (None, {"full_name": "Google Name", "name": "G"}, "Google Name"),
    ("   ", {"name": "Only Name"}, "Only Name"),
    (None, {}, None),
    (None, None, None),
    ("x" * 200, {}, "x" * 64),                                          # the column's cap
])
def test_the_reset_name(sent, metadata, expected):
    assert auth_ep._provider_name_for_reset(sent, metadata) == expected


# ── Wiring through /auth/oauth ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    rate_limiter.clear()
    yield


class _Req:
    def __init__(self):
        self.client = type("C", (), {"host": "203.0.113.10"})()
        self.headers = {}


class _Supabase:
    def __init__(self, user, *, current_name="Squatter Chosen Name", fail_update=False):
        self.log: list[tuple] = []
        outer = self

        class _Auth:
            def sign_in_with_id_token(self, creds):
                return type("R", (), {"user": user})()
        self.auth = _Auth()
        self._current = current_name
        self._fail_update = fail_update

        class _Q:
            def __init__(self):
                self._update = None

            def select(self, *_a, **_k): return self
            def eq(self, *_a, **_k): return self
            def limit(self, *_a, **_k): return self
            def single(self): return self

            def update(self, values):
                self._update = values
                return self

            def execute(self):
                if self._update is not None:
                    if outer._fail_update:
                        raise RuntimeError("write failed")
                    outer.log.append(("update", dict(self._update)))
                    return type("R", (), {"data": [self._update]})()
                return type("R", (), {"data": {"id": _USER_ID, "email": _EMAIL,
                                                "display_name": outer._current}})()
        self._q = _Q

    def table(self, _name):
        return self._q()


async def _oauth(sb, *, provider="google", display_name=None, monkeypatch=None):
    return await auth_ep.oauth_sign_in(
        OAuthSignInRequest(provider=provider, id_token="x" * 40, display_name=display_name),
        _Req(), sb,
    )


def _live(**overrides):
    """`_linked`, but relative to the REAL clock: the endpoint reads `datetime.now()` itself."""
    now = datetime.now(timezone.utc)
    shape = dict(created=now - timedelta(days=3), confirmed=now - timedelta(seconds=1))
    shape.update(overrides)
    return _linked(**shape)


@pytest.mark.asyncio
async def test_the_squatters_name_is_replaced_by_the_providers(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    sb = _Supabase(_live())
    out = await _oauth(sb, monkeypatch=monkeypatch)
    assert out.access_token
    assert ("update", {"display_name": "Real Owner"}) in sb.log
    assert any("never confirmed" in r.getMessage() and "reset" in r.getMessage() for r in caplog.records)
    assert "Squatter Chosen Name" not in caplog.text and "Real Owner" not in caplog.text, (
        "names are personal data — the log says what happened, not who")


@pytest.mark.asyncio
async def test_apples_own_first_time_name_wins(monkeypatch):
    sb = _Supabase(_live(metadata={"email": _EMAIL, "sub": "001.apple.1"}))
    await _oauth(sb, provider="apple", display_name="Ada Lovelace", monkeypatch=monkeypatch)
    assert sb.log[0] == ("update", {"display_name": "Ada Lovelace"})


@pytest.mark.asyncio
async def test_no_provider_name_falls_back_to_the_placeholder(monkeypatch):
    """The signup trigger's own placeholder — which `_ensure_display_name` reads as unset."""
    sb = _Supabase(_live(metadata={"email": _EMAIL}))
    await _oauth(sb, monkeypatch=monkeypatch)
    assert sb.log[0] == ("update", {"display_name": "victim"})


@pytest.mark.asyncio
async def test_a_legitimate_sign_in_never_touches_the_name(monkeypatch):
    sb = _Supabase(_live(metadata={"display_name": "Chosen", "full_name": "G"}), current_name="Chosen")
    await _oauth(sb, monkeypatch=monkeypatch)
    assert sb.log == []


@pytest.mark.asyncio
async def test_a_failed_reset_never_fails_the_sign_in(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    out = await _oauth(_Supabase(_live(), fail_update=True), monkeypatch=monkeypatch)
    assert out.access_token
    assert any("NOT reset" in r.getMessage() for r in caplog.records)
