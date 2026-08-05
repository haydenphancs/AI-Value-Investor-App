"""Password recovery: enumeration resistance, brute-force limits, session eviction.

Before this, a user who forgot their password was permanently locked out — there was no
recovery path at all (`auth.py` had only login/register/refresh/logout).

The three properties worth pinning, because each is easy to get subtly wrong:

1. **No account enumeration.** `/forgot-password` must answer identically for a registered
   and an unregistered address. A 404 for unknown emails turns it into an oracle for which
   addresses have accounts on a finance app — exactly the list a credential-stuffer wants.
2. **Brute-force resistance.** A 6-digit code is ~1M possibilities. Rate limits are applied
   per-IP AND per-email independently: per-IP alone lets a distributed caller hammer one
   victim; per-email alone lets one host enumerate many addresses.
3. **Session eviction.** The app mints its OWN JWTs and validates them on signature +
   expiry alone, so a reset must stamp `password_changed_at` or a thief keeps access for
   the full 7-day refresh lifetime — defeating the point of resetting (migration 105).

No network / Supabase: the client is a fake, and the shared in-process rate limiter is
reset between tests so limits from one test can't leak into another.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import auth as auth_ep
from app.core.security import create_access_token, create_refresh_token, rate_limiter
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    PASSWORD_MIN_LENGTH,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SignInRequest,
)

_USER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_EMAIL = "someone@example.com"
_GOOD_PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """The limiter is process-wide; without this, tests poison each other.

    Uses `clear()` rather than reaching for `_requests`: credential limiters live in a
    separate pool, and a name-guessing loop silently stopped covering them.
    """
    rate_limiter.clear()
    yield


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeRequest:
    def __init__(self, ip="203.0.113.10"):
        self.client = type("C", (), {"host": ip})()


class _FakeAdmin:
    def __init__(self, log, fail):
        self._log, self._fail = log, fail

    def update_user_by_id(self, uid, attrs):
        if "update" in self._fail:
            raise RuntimeError("update failed")
        self._log.append(("admin.update_user_by_id", uid, sorted(attrs)))


class _FakeAuth:
    def __init__(self, log, fail, otp_user_id):
        self._log, self._fail = log, fail
        self._otp_user_id = otp_user_id
        self.admin = _FakeAdmin(log, fail)

    def reset_password_for_email(self, email):
        if "send" in self._fail:
            raise RuntimeError("smtp down")
        self._log.append(("reset_password_for_email", email))

    def verify_otp(self, params):
        if "otp" in self._fail:
            raise RuntimeError("invalid otp")
        self._log.append(("verify_otp", params.get("email"), params.get("type")))
        if self._otp_user_id is None:
            return type("R", (), {"user": None})()
        return type("R", (), {"user": type("U", (), {"id": self._otp_user_id})()})()

    def sign_in_with_password(self, creds):
        if "signin" in self._fail:
            raise RuntimeError("bad credentials")
        # A failure that is OURS. Worded so it does NOT match `_is_rejected_credential` —
        # that classifier is the only thing separating a wrong password from an outage.
        if "outage" in self._fail:
            raise RuntimeError("upstream gateway timeout contacting the identity provider")
        self._log.append(("sign_in_with_password", creds["email"]))


class _FakeQuery:
    def __init__(self, log, table, row, fail):
        self._log, self._table, self._row, self._fail = log, table, row, fail
        self._update = None
        # PostgREST returns a bare OBJECT for single() and a LIST for limit()/plain. Modelling
        # that matters: a fake that always returned the object let `rows[0]` pass here and
        # KeyError in production. Mirrors the same handling in
        # tests/test_auth_confirmation_and_oauth.py.
        self._shape = "single"

    def select(self, *_a, **_k):
        return self

    def update(self, values):
        self._update = values
        return self

    def eq(self, *_a, **_k):
        return self

    def single(self):
        self._shape = "single"
        return self

    def limit(self, *_a, **_k):
        # Needed by `_app_user_row_exists` and the change-password email lookup. Without it the
        # call raised AttributeError, which the helper deliberately treats as a transport blip
        # and FAILS OPEN — so the account-existence check looked covered while never running.
        self._shape = "list"
        return self

    def execute(self):
        if self._update is not None:
            if "stamp" in self._fail:
                raise RuntimeError("stamp failed")
            self._log.append(("update", self._table, sorted(self._update)))
            return type("R", (), {"data": [self._update]})()
        if "lookup" in self._fail:
            raise RuntimeError("lookup failed")
        data = self._row if self._shape == "single" else ([self._row] if self._row else [])
        return type("R", (), {"data": data})()


class FakeSupabase:
    def __init__(self, row=None, fail=(), otp_user_id=_USER_ID):
        self.log: list[tuple] = []
        self._row = row if row is not None else {"id": _USER_ID, "email": _EMAIL}
        self._fail = set(fail)
        self.auth = _FakeAuth(self.log, self._fail, otp_user_id)

    def table(self, name):
        return _FakeQuery(self.log, name, self._row, self._fail)


# ── 1. No account enumeration ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forgot_password_response_is_identical_for_unknown_email():
    known = await auth_ep.forgot_password(
        ForgotPasswordRequest(email=_EMAIL), _FakeRequest("198.51.100.1"), FakeSupabase()
    )
    # An unknown address makes Supabase raise; the response must not differ.
    unknown = await auth_ep.forgot_password(
        ForgotPasswordRequest(email="nobody@example.com"),
        _FakeRequest("198.51.100.2"),
        FakeSupabase(fail=("send",)),
    )
    assert known.message == unknown.message


@pytest.mark.asyncio
async def test_forgot_password_never_raises_on_provider_failure():
    """A provider outage must look exactly like success, or it becomes an oracle."""
    resp = await auth_ep.forgot_password(
        ForgotPasswordRequest(email=_EMAIL), _FakeRequest(), FakeSupabase(fail=("send",))
    )
    assert "if an account exists" in resp.message.lower()


@pytest.mark.asyncio
async def test_forgot_password_lowercases_the_email():
    sb = FakeSupabase()
    await auth_ep.forgot_password(
        ForgotPasswordRequest(email="MiXeD@Example.COM"), _FakeRequest(), sb
    )
    sent = [e for e in sb.log if e[0] == "reset_password_for_email"]
    assert sent and sent[0][1] == "mixed@example.com"


# ── 2. Brute-force / abuse limits ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forgot_password_is_rate_limited_per_email_across_ips():
    """Per-IP alone is not enough: a distributed caller could email-bomb one victim."""
    for i in range(3):
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest(f"198.51.100.{i}"), FakeSupabase()
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=_EMAIL), _FakeRequest("198.51.100.99"), FakeSupabase()
        )
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_forgot_password_is_rate_limited_per_ip_across_emails():
    """Per-email alone is not enough: one host could enumerate many addresses."""
    for i in range(5):
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email=f"u{i}@example.com"), _FakeRequest("203.0.113.7"),
            FakeSupabase(),
        )
    with pytest.raises(HTTPException) as ei:
        await auth_ep.forgot_password(
            ForgotPasswordRequest(email="another@example.com"), _FakeRequest("203.0.113.7"),
            FakeSupabase(),
        )
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_reset_password_is_rate_limited():
    req = ResetPasswordRequest(email=_EMAIL, code="000000", new_password=_GOOD_PASSWORD)
    for _ in range(10):
        with pytest.raises(HTTPException):
            await auth_ep.reset_password(req, _FakeRequest(), FakeSupabase(fail=("otp",)))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(req, _FakeRequest(), FakeSupabase(fail=("otp",)))
    assert ei.value.status_code == 429, "a 6-digit code must not be brute-forceable"


# ── 3. Reset correctness ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_password_verifies_otp_then_sets_password_then_stamps():
    sb = FakeSupabase()
    await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
        _FakeRequest(), sb,
    )
    kinds = [e[0] for e in sb.log]
    assert kinds.index("verify_otp") < kinds.index("admin.update_user_by_id"), (
        "the OTP must be verified BEFORE the password is changed"
    )
    assert ("update", "users", ["password_changed_at"]) in sb.log, (
        "password_changed_at must be stamped so pre-reset tokens stop working"
    )


@pytest.mark.asyncio
async def test_reset_password_uses_the_recovery_otp_type():
    sb = FakeSupabase()
    await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
        _FakeRequest(), sb,
    )
    otp = [e for e in sb.log if e[0] == "verify_otp"][0]
    assert otp[2] == "recovery"


@pytest.mark.asyncio
async def test_reset_password_rejects_a_bad_code_without_touching_the_password():
    sb = FakeSupabase(fail=("otp",))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="999999", new_password=_GOOD_PASSWORD),
            _FakeRequest(), sb,
        )
    assert ei.value.status_code == 400
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_reset_password_rejects_a_verified_response_with_no_user():
    sb = FakeSupabase(otp_user_id=None)
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
            _FakeRequest(), sb,
        )
    assert ei.value.status_code == 400
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_reset_password_surfaces_a_failed_update_as_500():
    with pytest.raises(HTTPException) as ei:
        await auth_ep.reset_password(
            ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
            _FakeRequest(), FakeSupabase(fail=("update",)),
        )
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_reset_succeeds_even_if_the_stamp_write_fails():
    """The password IS already changed by then. Failing the request would tell the user
    their reset didn't work when it did — the stamp failure is logged instead."""
    resp = await auth_ep.reset_password(
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password=_GOOD_PASSWORD),
        _FakeRequest(), FakeSupabase(fail=("stamp",)),
    )
    assert "reset" in resp.message.lower()


# ── 4. Change password (authenticated) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_change_password_requires_the_current_password():
    """A stolen access token alone must not be enough to seize the account."""
    sb = FakeSupabase(fail=("signin",))
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="wrong", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=sb,
        )
    assert ei.value.status_code == 401
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_change_password_verifies_before_updating_and_stamps():
    sb = FakeSupabase()
    await auth_ep.change_password(
        ChangePasswordRequest(current_password="old-password", new_password=_GOOD_PASSWORD),
        _FakeRequest(), user_id=_USER_ID, supabase=sb,
    )
    kinds = [e[0] for e in sb.log]
    assert kinds.index("sign_in_with_password") < kinds.index("admin.update_user_by_id")
    assert ("update", "users", ["password_changed_at"]) in sb.log


@pytest.mark.asyncio
async def test_change_password_rejects_reusing_the_same_password():
    sb = FakeSupabase()
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password=_GOOD_PASSWORD, new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=sb,
        )
    assert ei.value.status_code == 400
    assert not [e for e in sb.log if e[0] == "admin.update_user_by_id"]


@pytest.mark.asyncio
async def test_refresh_is_rejected_when_the_account_row_is_gone():
    """Otherwise the client loops forever: profile 401s, refresh SUCCEEDS, repeat.

    `is_token_stale_after_password_change` fails open when its read finds no row, so nothing
    else on this path notices a missing `public.users` row. iOS ends up holding a credential it
    can never validate and never clears — permanently `.restoring`, re-hitting the backend.
    AUTH_ACCOUNT_NOT_FOUND is one of the three codes allowed to clear a credential, so it ends
    the loop rather than feeding it.
    """
    fresh = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    with pytest.raises(HTTPException) as ei:
        await auth_ep.refresh_token(
            RefreshTokenRequest(refresh_token=fresh), _FakeRequest(), FakeSupabase(row={}),
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_refresh_still_works_when_the_existence_check_is_unavailable():
    """Fail OPEN on a transport error — a Supabase blip must not lock every user out."""
    fresh = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": past}, fail=("lookup",))
    out = await auth_ep.refresh_token(RefreshTokenRequest(refresh_token=fresh), _FakeRequest(), sb)
    assert out.access_token


@pytest.mark.asyncio
async def test_change_password_is_rate_limited_per_user_across_ips():
    """This route runs a real `sign_in_with_password` per call, so it is a password oracle
    for anyone holding a stolen access token — the exact threat the current-password
    requirement exists to stop. It was the only credential route with no limiter at all, and
    Supabase's own limiting is not a backstop because every call leaves from one egress IP.

    Keyed per USER, so rotating source addresses does not buy more guesses.
    """
    for i in range(5):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.change_password(
                ChangePasswordRequest(current_password=f"guess-{i}", new_password=_GOOD_PASSWORD),
                _FakeRequest(f"198.51.100.{i}"),
                user_id=_USER_ID, supabase=FakeSupabase(fail=("signin",)),
            )
        assert ei.value.status_code == 401  # wrong password — and the budget is consumed

    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="guess-6", new_password=_GOOD_PASSWORD),
            _FakeRequest("198.51.100.200"),
            user_id=_USER_ID, supabase=FakeSupabase(fail=("signin",)),
        )
    assert ei.value.status_code == 429


@pytest.mark.asyncio
async def test_change_password_upstream_failure_is_503_not_a_wrong_password(caplog):
    """An identity-provider failure must not be reported as "your password is incorrect".

    This block used to map EVERY exception to that message. A GoTrue 429 — which this endpoint
    invites, since every call leaves from the one Railway egress IP — a Supabase 5xx, or a
    connect timeout all told the user something false about their password and sent them to a
    reset they did not need. Worse, the per-user limiter consumes its slot at check time and
    nothing refunds it, so five outage-induced failures locked the real owner out for 15
    minutes. Same classification /login already does.
    """
    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.auth"):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.change_password(
                ChangePasswordRequest(
                    current_password="the-correct-one", new_password=_GOOD_PASSWORD
                ),
                _FakeRequest(),
                user_id=_USER_ID,
                supabase=FakeSupabase(fail=("outage",)),
            )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "an upstream failure must reach Sentry — the old handler bound no exception and "
        "logged at INFO with no type, message or stack"
    )


@pytest.mark.asyncio
async def test_a_denied_ip_never_reaches_the_per_email_limiter():
    """The short-circuit. Without it the protected pool is still evictable.

    `is_allowed` inserts its key before deciding, so evaluating the email limiter after the IP
    limiter already refused created a `login:email:<attacker-chosen>` entry per rejected
    request. ~20k cheap POSTs from ONE address — all 429'd, none reaching Supabase — filled the
    pool and evicted the victim's bucket, handing back a fresh 10-guess window. That defeated
    the whole point of the separate credential pool.

    Here: burn the per-IP budget (10/60s), then send 50 more with distinct emails. None of
    those emails may appear in the pool.
    """
    from app.core.security import rate_limiter

    for i in range(10):
        # Consume the IP budget directly; the handler path is exercised in the loop below.
        rate_limiter.is_allowed(
            "login:ip:203.0.113.77", max_requests=10, window_seconds=60, protected=True
        )

    before = len(rate_limiter._protected)
    for i in range(50):
        with pytest.raises(HTTPException) as ei:
            await auth_ep.sign_in(
                SignInRequest(email=f"flood{i}@example.com", password=_GOOD_PASSWORD),
                _FakeRequest("203.0.113.77"), FakeSupabase(),
            )
        assert ei.value.status_code == 429

    leaked = [k for k in rate_limiter._protected if k.startswith("login:email:flood")]
    assert not leaked, f"{len(leaked)} email keys inserted by requests the IP limiter refused"
    assert len(rate_limiter._protected) == before


@pytest.mark.asyncio
async def test_change_password_reports_a_missing_account_on_the_error_contract():
    """Was a bare-string 404. Now AUTH_ACCOUNT_NOT_FOUND, matching what /auth/refresh already
    returns for the identical condition.

    The string body mattered: iOS cannot decode it as `APIErrorResponse`, so it surfaced as a
    generic `.serverError`/`.unknown` — and `.serverError` used to be auto-retried twice, so a
    single tap burned three of the five per-user attempts without a password ever being checked.
    """
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="x", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=FakeSupabase(row={"id": _USER_ID}),
        )
    assert ei.value.status_code == 401
    assert ei.value.detail["error_code"] == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_change_password_lookup_blip_is_retryable_not_a_500():
    """A transport fault on the email lookup must be distinguishable from "your account is
    gone", and must not present as an undecodable 500 that the client then retries."""
    with pytest.raises(HTTPException) as ei:
        await auth_ep.change_password(
            ChangePasswordRequest(current_password="x", new_password=_GOOD_PASSWORD),
            _FakeRequest(), user_id=_USER_ID, supabase=FakeSupabase(fail=("lookup",)),
        )
    assert ei.value.status_code == 503
    assert ei.value.detail["error_code"] == "AUTH_UNAVAILABLE"


# ── 5. Session eviction on refresh ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_is_rejected_for_a_token_issued_before_the_password_change():
    """The check that actually caps a thief's window: without it a stolen refresh token
    keeps minting access tokens for 7 days after the victim resets."""
    stale = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": future})
    from app.schemas.auth import RefreshTokenRequest

    with pytest.raises(HTTPException) as ei:
        await auth_ep.refresh_token(RefreshTokenRequest(refresh_token=stale), _FakeRequest(), sb)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_still_works_when_the_password_predates_the_token():
    fresh = create_refresh_token({"sub": _USER_ID, "email": _EMAIL})
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    sb = FakeSupabase(row={"id": _USER_ID, "password_changed_at": past})
    from app.schemas.auth import RefreshTokenRequest

    out = await auth_ep.refresh_token(RefreshTokenRequest(refresh_token=fresh), _FakeRequest(), sb)
    assert out.access_token and out.refresh_token


def test_staleness_check_fails_open_on_a_db_error():
    """A Supabase blip must not log out every user."""
    tok = create_access_token({"sub": _USER_ID, "email": _EMAIL})
    payload = {"iat": datetime.now(timezone.utc).timestamp()}
    assert auth_ep.is_token_stale_after_password_change(
        payload, _USER_ID, FakeSupabase(fail=("lookup",))
    ) is False
    assert tok  # token creation itself is unaffected


def test_staleness_check_ignores_tokens_without_iat():
    assert auth_ep.is_token_stale_after_password_change(
        {}, _USER_ID, FakeSupabase(row={"id": _USER_ID, "password_changed_at": "2030-01-01T00:00:00Z"})
    ) is False


# ── 6. Password policy ────────────────────────────────────────────────────────

def test_short_passwords_are_rejected():
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password="a" * (PASSWORD_MIN_LENGTH - 1))


def test_absurdly_long_passwords_are_rejected():
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password="a" * 500)


def test_padded_passwords_are_rejected():
    """A leading/trailing space is almost always a paste accident, and the user will not
    be able to reproduce it at sign-in."""
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="123456", new_password="  spaced out  ")


def test_code_accepts_human_formatting_but_not_letters():
    assert ResetPasswordRequest(
        email=_EMAIL, code="123 456", new_password=_GOOD_PASSWORD
    ).code == "123456"
    assert ResetPasswordRequest(
        email=_EMAIL, code="123-456", new_password=_GOOD_PASSWORD
    ).code == "123456"
    with pytest.raises(ValueError):
        ResetPasswordRequest(email=_EMAIL, code="abc123", new_password=_GOOD_PASSWORD)
