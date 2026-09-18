"""The request-body cap covers EVERY write, and chunked writes are refused.

`cap_json_body` was scoped to four `/users/me*` suffixes on the premise that "chat streams
SSE and the report path proxies a PDF" — those are RESPONSE streams, and a cap on the
request's Content-Length never touches them. The scoping left every unauthenticated JSON
route open: `POST /auth/login` took a multi-megabyte password, buffered it, `json.loads`'d
it on the single worker's loop and forwarded it to GoTrue synchronously before the per-IP
limiter ever ran (1.76 s measured at 2 MB live); `POST /events` took a 40 MB batch of 200k
events and validated every one before the 50-cap sliced it.

A second hole sat inside the cap itself: it read a MISSING Content-Length as 0, so
`Transfer-Encoding: chunked` bypassed it on every capped route. A BaseHTTPMiddleware cannot
wrap the downstream `receive`, so chunked writes are refused outright (411) — every client
this API has sends a Content-Length.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.main import _MAX_JSON_BODY_BYTES, _write_body_verdict


def _client() -> TestClient:
    return TestClient(main_mod.app)   # no context manager: the lifespan must not run


_BIG = b'{"padding": "' + b"x" * (_MAX_JSON_BODY_BYTES + 1) + b'"}'


@pytest.mark.parametrize("path", [
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/events",
    "/api/v1/users/me/settings",
    "/api/v1/chat/sessions",
    "/api/v1/research/generate",
])
def test_an_oversized_write_is_a_413_on_every_route(path):
    r = _client().post(path, content=_BIG, headers={"content-type": "application/json"})
    assert r.status_code == 413, (path, r.status_code, r.text[:200])
    body = r.json()
    assert body["error_code"] == "INVALID_INPUT"
    # The refusal carries the security headers even though it never reaches the
    # downstream middleware.
    assert r.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/events"])
def test_a_chunked_write_is_refused_before_it_is_read(path):
    def _gen():
        yield b'{"email": "a@b.co", "password": "'
        yield b"x" * 1024
        yield b'"}'
    r = _client().post(path, content=_gen(), headers={"content-type": "application/json"})
    assert r.status_code == 411, (path, r.status_code, r.text[:200])
    assert r.json()["error_code"] == "INVALID_INPUT"


def test_the_verdict_helper_reads_the_headers_the_way_uvicorn_presents_them():
    assert _write_body_verdict({"content-length": str(_MAX_JSON_BODY_BYTES)}) == (
        _MAX_JSON_BODY_BYTES, None)
    assert _write_body_verdict({"content-length": str(_MAX_JSON_BODY_BYTES + 1)})[1] == "oversized"
    assert _write_body_verdict({"transfer-encoding": "chunked"})[1] == "chunked"
    assert _write_body_verdict({"transfer-encoding": "gzip, Chunked"})[1] == "chunked"
    # An absent body (a bare POST with no payload) is NOT chunked and must pass.
    assert _write_body_verdict({}) == (0, None)
    # A garbage Content-Length is read as 0 rather than crashing the middleware.
    assert _write_body_verdict({"content-length": "lots"}) == (0, None)


def test_a_bodiless_post_still_reaches_the_route():
    """Control: the chunked refusal must not catch a write that simply has no body."""
    r = _client().post("/api/v1/auth/logout")
    assert r.status_code != 411 and r.status_code != 413, r.status_code


def test_a_normal_sized_write_is_not_capped():
    r = _client().post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x" * 64})
    assert r.status_code not in (411, 413), r.status_code


# ── the schema-level bounds behind the cap ───────────────────────────────────────


def test_sign_in_secrets_are_bounded_but_far_above_the_bcrypt_limit():
    """A LENGTH ceiling at sign-in is not the strength rule (which must never apply there):
    72 bytes is all bcrypt reads, so 1,024 locks nobody out and turns a 2 MB password into a
    422 with no upstream call."""
    from pydantic import ValidationError
    from app.schemas.auth import (
        ChangePasswordRequest, SignInRequest, SIGN_IN_SECRET_MAX_LENGTH, PASSWORD_MAX_LENGTH,
    )

    assert SIGN_IN_SECRET_MAX_LENGTH > PASSWORD_MAX_LENGTH > 72
    ok = SignInRequest(email="a@b.co", password="x" * SIGN_IN_SECRET_MAX_LENGTH)
    assert len(ok.password) == SIGN_IN_SECRET_MAX_LENGTH
    with pytest.raises(ValidationError):
        SignInRequest(email="a@b.co", password="x" * (SIGN_IN_SECRET_MAX_LENGTH + 1))
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="x" * (SIGN_IN_SECRET_MAX_LENGTH + 1),
                              new_password="Abcdefg1!")
    # And a legacy short/weak password still signs in (the strength rule stays OFF here).
    assert SignInRequest(email="a@b.co", password="abc").password == "abc"


def test_tokens_and_the_oauth_display_name_are_bounded():
    from pydantic import ValidationError
    from app.schemas.auth import (
        OAuthSignInRequest, RefreshTokenRequest, SessionExchangeRequest, TOKEN_MAX_LENGTH,
    )
    from app.schemas.user import DISPLAY_NAME_MAX_LENGTH

    with pytest.raises(ValidationError):
        RefreshTokenRequest(refresh_token="r" * (TOKEN_MAX_LENGTH + 1))
    with pytest.raises(ValidationError):
        SessionExchangeRequest(supabase_access_token="t" * (TOKEN_MAX_LENGTH + 1))
    with pytest.raises(ValidationError):
        OAuthSignInRequest(provider="apple", id_token="t" * (TOKEN_MAX_LENGTH + 1))
    with pytest.raises(ValidationError):
        OAuthSignInRequest(provider="apple", id_token="t" * 32,
                           display_name="n" * (DISPLAY_NAME_MAX_LENGTH + 1))
    assert OAuthSignInRequest(provider="apple", id_token="t" * 32,
                              display_name="Ada Lovelace").display_name == "Ada Lovelace"


def test_the_events_batch_is_sliced_before_any_item_is_validated():
    """The after-mode slice ran once every item had been validated: a 200k-event batch cost
    ~1 s of loop time before 199,950 of them were thrown away.

    Oracle without a spy: items PAST the cap are not dicts at all. A before-mode slice
    discards them unseen; the old after-mode slice validated them first and raised."""
    from pydantic import ValidationError
    from app.schemas import analytics as an

    cap = an.MAX_EVENTS_PER_BATCH
    raw = [{"event": "app_open"}] * cap + [12345] * (cap * 40)
    batch = an.AnalyticsBatchRequest(events=raw)
    assert len(batch.events) == cap
    # The cap is a slice, not a licence: an invalid item INSIDE the window still fails.
    with pytest.raises(ValidationError):
        an.AnalyticsBatchRequest(events=[{"event": "app_open"}] * (cap - 1) + [12345])
    # A non-list still fails type validation rather than being sliced.
    with pytest.raises(ValidationError):
        an.AnalyticsBatchRequest(events="not a list")
