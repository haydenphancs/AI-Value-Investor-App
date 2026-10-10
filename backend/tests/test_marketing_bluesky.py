"""The Bluesky thin client (app/integrations/bluesky.py) — design doc §12.10, rules/marketing.md §2.

What these pin, in order of how much a regression would cost:

1. THE OUTCOME SPLIT. A request that provably never left is `NotSent` (safe to retry); one that may
   have reached the platform is `Ambiguous` (reconcile first); a definite 4xx is `Refused`. A
   transport error filed on the wrong side is a double post or a lost post.
2. `"swapRecord":null` reaches the PDS literally — it is what makes a Bluesky create idempotent.
3. No credential (app password, access or refresh JWT) in any exception text, attribute or log.
4. ExpiredToken is told apart from a refused credential (refresh vs. re-login).

Hermetic: every request goes to an `httpx.MockTransport` (backend/conftest.py blocks sockets).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Callable, List, Optional, Tuple, Union

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.integrations import bluesky

HANDLE = "caydex.bsky.social"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"
ACCESS = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLmFjY2VzcyJ9"
          ".c2lnbmF0dXJlLWFjY2Vzcy10b2tlbi12YWx1ZQ")
REFRESH = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLnJlZnJlc2gifQ"
           ".c2lnbmF0dXJlLXJlZnJlc2gtdG9rZW4tdmFsdWU")
NEW_REFRESH = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLnJlZnJlc2gyIn0"
               ".bmV3LXJvdGF0ZWQtcmVmcmVzaC10b2tlbg")
#: Not JWT-shaped: only the per-call literal replacement can hide this one.
OPAQUE = "opaqueAccessTokenValue0123456789"
SECRETS = (APP_PASSWORD, ACCESS, REFRESH, NEW_REFRESH, OPAQUE)

DID = "did:plc:abc123xyz789"
SERVICE = "https://bsky.social"
PDS = "https://morel.us-east.host.bsky.network"
RKEY = "3lb2c4d5e6f7g"
URI = f"at://{DID}/app.bsky.feed.post/{RKEY}"
CID = "bafyreib2rxk3rh6kzwq"
RECORD = {"$type": "app.bsky.feed.post", "text": "Hello", "createdAt": "2026-09-30T12:00:00.000Z"}

Answer = Union[Tuple[int, Any], Tuple[int, Any, dict], Callable[[httpx.Request], httpx.Response]]


class FakeBluesky:
    """A scripted XRPC host. `answers` is consumed in order; an unscripted call is a 599."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.answers: List[Answer] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.answers:
            return httpx.Response(599, json={"error": "Unscripted"})
        answer = self.answers.pop(0)
        if callable(answer):
            return answer(request)
        status, body, *rest = answer
        headers = rest[0] if rest else {}
        if isinstance(body, (bytes, str)):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@pytest.fixture
def bsky(monkeypatch):
    fake = FakeBluesky()
    monkeypatch.setattr(bluesky, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_HANDLE", HANDLE)
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_APP_PASSWORD", APP_PASSWORD)
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_SERVICE", SERVICE)


def _session_body(*, refresh: str = REFRESH, did_doc: Any = "default") -> dict:
    body = {"accessJwt": ACCESS, "refreshJwt": refresh, "did": DID, "handle": HANDLE,
            "email": "owner@example.com", "active": True}
    if did_doc == "default":
        did_doc = {"id": DID, "service": [
            {"id": "#bsky_notif", "type": "BskyNotificationService", "serviceEndpoint": "https://x.test"},
            {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": PDS + "/"},
        ]}
    if did_doc is not None:
        body["didDoc"] = did_doc
    return body


def _json(request: httpx.Request) -> Any:
    return json.loads(request.content.decode("utf-8"))


async def _put(pds: str = PDS, token: str = ACCESS, **over):
    kw = dict(repo=DID, collection=bluesky.POST_COLLECTION, rkey=RKEY, record=RECORD)
    kw.update(over)
    return await bluesky.put_record(pds, token, **kw)


# ── settings ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("handle, password, expected", [
    (HANDLE, APP_PASSWORD, True),
    (None, APP_PASSWORD, False),
    (HANDLE, None, False),
    ("", APP_PASSWORD, False),
    (HANDLE, "   ", False),
    (None, None, False),
])
def test_configured_needs_both(monkeypatch, handle, password, expected):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_HANDLE", handle)
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_APP_PASSWORD", password)
    assert bluesky.configured() is expected


@pytest.mark.parametrize("raw, expected", [
    ("https://bsky.social", "https://bsky.social"),
    ("https://bsky.social/", "https://bsky.social"),
    ("https://pds.example.com//", "https://pds.example.com"),
])
def test_service_url_strips_the_trailing_slash(monkeypatch, raw, expected):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_SERVICE", raw)
    assert bluesky.service_url() == expected


def test_settings_default_bluesky_off():
    from app.config import Settings
    fields = Settings.model_fields
    assert fields["MARKETING_BLUESKY_HANDLE"].default is None
    assert fields["MARKETING_BLUESKY_APP_PASSWORD"].default is None
    assert fields["MARKETING_BLUESKY_SERVICE"].default == "https://bsky.social"


# ── sessions ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_sends_the_app_password_and_reads_the_pds(bsky, configured):
    bsky.answers = [(200, _session_body())]
    session = await bluesky.create_session()

    assert session == {"access_jwt": ACCESS, "refresh_jwt": REFRESH, "did": DID, "handle": HANDLE,
                       "pds": PDS}
    req = bsky.last
    assert req.method == "POST"
    assert str(req.url) == f"{SERVICE}/xrpc/com.atproto.server.createSession"
    assert _json(req) == {"identifier": HANDLE, "password": APP_PASSWORD}
    assert req.headers["content-type"] == "application/json"
    assert "authorization" not in req.headers


@pytest.mark.asyncio
async def test_create_session_strips_a_leading_at_from_the_handle(bsky, configured, monkeypatch):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_HANDLE", f" @{HANDLE} ")
    bsky.answers = [(200, _session_body())]
    await bluesky.create_session()
    assert _json(bsky.last)["identifier"] == HANDLE


@pytest.mark.asyncio
async def test_create_session_uses_the_configured_service(bsky, configured, monkeypatch):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_SERVICE", "https://entry.example.com/")
    bsky.answers = [(200, _session_body(did_doc=None))]
    session = await bluesky.create_session()
    assert str(bsky.last.url) == "https://entry.example.com/xrpc/com.atproto.server.createSession"
    assert session["pds"] == "https://entry.example.com"


@pytest.mark.parametrize("did_doc", [
    None,                                                     # no didDoc at all
    {"id": DID},                                              # no service list
    {"id": DID, "service": "not-a-list"},
    {"id": DID, "service": [{"id": "#bsky_notif", "serviceEndpoint": "https://x.test"}]},
    {"id": DID, "service": ["junk", {"id": "#atproto_pds", "serviceEndpoint": 42}]},
    {"id": DID, "service": [{"id": "#atproto_pds", "serviceEndpoint": "http://plain.example"}]},
    {"id": DID, "service": [{"id": "#atproto_pds", "serviceEndpoint": "https://"}]},
    "not-a-dict",
])
@pytest.mark.asyncio
async def test_create_session_falls_back_to_the_service_url(bsky, configured, did_doc):
    bsky.answers = [(200, _session_body(did_doc=did_doc))]
    session = await bluesky.create_session()
    assert session["pds"] == SERVICE


@pytest.mark.asyncio
async def test_a_full_did_service_id_is_accepted(bsky, configured):
    doc = {"id": DID, "service": [{"id": f"{DID}#atproto_pds", "serviceEndpoint": "https://pds.example.org"}]}
    bsky.answers = [(200, _session_body(did_doc=doc))]
    assert (await bluesky.create_session())["pds"] == "https://pds.example.org"


@pytest.mark.parametrize("body", [
    {"accessJwt": ACCESS, "did": DID},                                # no refreshJwt
    {"refreshJwt": REFRESH, "did": DID},                              # no accessJwt
    {"accessJwt": ACCESS, "refreshJwt": REFRESH},                     # no did
    {"accessJwt": ACCESS, "refreshJwt": REFRESH, "did": "plc:abc"},   # not a DID
    {"accessJwt": "", "refreshJwt": REFRESH, "did": DID},
    {"accessJwt": 1, "refreshJwt": REFRESH, "did": DID},
])
@pytest.mark.asyncio
async def test_an_unusable_session_answer_is_ambiguous(bsky, configured, body):
    bsky.answers = [(200, body)]
    with pytest.raises(bluesky.BlueskyAmbiguousError) as info:
        await bluesky.create_session()
    assert info.value.status == 200


@pytest.mark.asyncio
async def test_a_missing_handle_in_the_answer_is_an_empty_string(bsky, configured):
    body = _session_body()
    del body["handle"]
    bsky.answers = [(200, body)]
    assert (await bluesky.create_session())["handle"] == ""


@pytest.mark.parametrize("status, error, exc", [
    (401, "AuthenticationRequired", bluesky.BlueskyAuthError),        # wrong app password
    (401, "AccountTakedown", bluesky.BlueskyAuthError),
    (401, "AuthFactorTokenRequired", bluesky.BlueskyAuthError),
    (401, "InvalidRequest", bluesky.BlueskyAuthError),
    (401, None, bluesky.BlueskyAuthError),
    (400, "AccountTakedown", bluesky.BlueskyAuthError),
    (400, "InvalidRequest", bluesky.BlueskyRefusedError),             # malformed identifier
])
@pytest.mark.asyncio
async def test_create_session_refusals(bsky, configured, status, error, exc):
    body = {"error": error, "message": "Invalid identifier or password"} if error else b""
    bsky.answers = [(status, body)]
    with pytest.raises(exc) as info:
        await bluesky.create_session()
    assert type(info.value) is exc
    assert info.value.status == status and info.value.error == error
    assert info.value.method == "com.atproto.server.createSession"


@pytest.mark.asyncio
async def test_refresh_sends_the_refresh_token_and_returns_the_rotated_one(bsky, configured):
    bsky.answers = [(200, _session_body(refresh=NEW_REFRESH))]
    session = await bluesky.refresh_session(REFRESH)

    assert session["refresh_jwt"] == NEW_REFRESH and session["access_jwt"] == ACCESS
    assert session["pds"] == PDS and session["did"] == DID
    req = bsky.last
    assert req.method == "POST"
    assert str(req.url) == f"{SERVICE}/xrpc/com.atproto.server.refreshSession"
    assert req.headers["authorization"] == f"Bearer {REFRESH}"
    assert req.content == b""


@pytest.mark.parametrize("status", [400, 401])
@pytest.mark.asyncio
async def test_refresh_with_an_expired_token(bsky, configured, status):
    bsky.answers = [(status, {"error": "ExpiredToken", "message": "Token has expired"})]
    with pytest.raises(bluesky.BlueskyExpiredTokenError) as info:
        await bluesky.refresh_session(REFRESH)
    assert not isinstance(info.value, bluesky.BlueskyAuthError)


@pytest.mark.asyncio
async def test_refresh_without_a_token_sends_nothing(bsky, configured):
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await bluesky.refresh_session("")
    assert bsky.requests == []


# ── putRecord ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_record_sends_swap_record_null_literally(bsky):
    bsky.answers = [(200, {"uri": URI, "cid": CID, "commit": {"cid": "c", "rev": "r"},
                           "validationStatus": "valid"})]
    out = await _put(PDS + "/")

    assert out == {"uri": URI, "cid": CID}
    req = bsky.last
    assert req.method == "POST"
    assert str(req.url) == f"{PDS}/xrpc/com.atproto.repo.putRecord"
    assert req.headers["authorization"] == f"Bearer {ACCESS}"
    assert req.headers["content-type"] == "application/json"
    # The BYTES, not a re-parse: an omitted key and an explicit null parse the same in Python
    # (`.get` → None), and only the explicit null means "create only if absent".
    assert b'"swapRecord":null' in req.content
    body = _json(req)
    assert body == {"repo": DID, "collection": "app.bsky.feed.post", "rkey": RKEY,
                    "record": RECORD, "swapRecord": None}
    assert "validate" not in body


@pytest.mark.asyncio
async def test_an_identical_retry_without_commit_is_success(bsky):
    bsky.answers = [(200, {"uri": URI, "cid": CID})]
    assert await _put() == {"uri": URI, "cid": CID}


@pytest.mark.asyncio
async def test_a_record_that_is_not_json_is_refused_before_sending(bsky):
    with pytest.raises(bluesky.BlueskyRefusedError) as info:
        await _put(record={**RECORD, "score": float("nan")})
    assert type(info.value) is bluesky.BlueskyRefusedError
    with pytest.raises(bluesky.BlueskyRefusedError):
        await _put(record={**RECORD, "when": datetime.now(timezone.utc)})
    assert bsky.requests == []


@pytest.mark.parametrize("pds", ["http://pds.example.com", "pds.example.com", "", "https://", "ftp://x"])
@pytest.mark.asyncio
async def test_a_non_https_host_never_gets_the_token(bsky, pds):
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await _put(pds)
    assert bsky.requests == []


@pytest.mark.asyncio
async def test_an_empty_access_token_sends_nothing(bsky):
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await _put(token="")
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await bluesky.delete_record(PDS, "", repo=DID, collection=bluesky.POST_COLLECTION, rkey=RKEY)
    assert bsky.requests == []


@pytest.mark.parametrize("answer, exc, status, error", [
    ((400, {"error": "ExpiredToken", "message": "Token has expired"}),
     bluesky.BlueskyExpiredTokenError, 400, "ExpiredToken"),
    ((401, {"error": "ExpiredToken", "message": "Token has expired"}),
     bluesky.BlueskyExpiredTokenError, 401, "ExpiredToken"),
    ((400, {"error": "InvalidSwap", "message": "Record was at bafy..."}),
     bluesky.BlueskyInvalidSwapError, 400, "InvalidSwap"),
    ((400, {"error": "InvalidRequest", "message": "Invalid record key"}),
     bluesky.BlueskyRefusedError, 400, "InvalidRequest"),
    ((400, {"error": "InvalidRecord", "message": "Record/text must not be longer than 300 graphemes"}),
     bluesky.BlueskyRefusedError, 400, "InvalidRecord"),
    ((413, {"error": "PayloadTooLarge"}), bluesky.BlueskyRefusedError, 413, "PayloadTooLarge"),
    ((403, b"<html>forbidden</html>"), bluesky.BlueskyRefusedError, 403, None),
    ((401, {"error": "AuthenticationRequired", "message": "Authentication Required"}),
     bluesky.BlueskyAuthError, 401, "AuthenticationRequired"),
    ((400, {"error": "InvalidToken", "message": "Token could not be verified"}),
     bluesky.BlueskyAuthError, 400, "InvalidToken"),
    ((401, {"error": "InvalidSwap"}), bluesky.BlueskyAuthError, 401, "InvalidSwap"),
    # 408 is NOT "never received" (a proxy can answer it after the PDS processed the request).
    ((408, b""), bluesky.BlueskyAmbiguousError, 408, None),
    ((500, {"error": "InternalServerError", "message": "Internal Server Error"}),
     bluesky.BlueskyAmbiguousError, 500, "InternalServerError"),
    ((502, b"<html>bad gateway</html>"), bluesky.BlueskyAmbiguousError, 502, None),
    ((503, b""), bluesky.BlueskyAmbiguousError, 503, None),
    ((302, b"", {"location": "https://elsewhere.example"}), bluesky.BlueskyAmbiguousError, 302, None),
    ((200, b"not json"), bluesky.BlueskyAmbiguousError, 200, None),
    ((200, b"\xff\xfe\x00garbage"), bluesky.BlueskyAmbiguousError, 200, None),
    ((200, b""), bluesky.BlueskyAmbiguousError, 200, None),
    ((200, [1, 2]), bluesky.BlueskyAmbiguousError, 200, None),
    ((200, {"uri": URI}), bluesky.BlueskyAmbiguousError, 200, None),           # no cid
    ((200, {"cid": CID}), bluesky.BlueskyAmbiguousError, 200, None),           # no uri
    ((200, {"uri": URI, "cid": None}), bluesky.BlueskyAmbiguousError, 200, None),
    ((200, {}), bluesky.BlueskyAmbiguousError, 200, None),
])
@pytest.mark.asyncio
async def test_put_record_maps_every_answer(bsky, answer, exc, status, error):
    bsky.answers = [answer]
    with pytest.raises(exc) as info:
        await _put()
    assert type(info.value) is exc
    assert info.value.status == status
    assert info.value.error == error
    assert info.value.method == "com.atproto.repo.putRecord"
    assert len(bsky.requests) == 1  # never followed a redirect, never retried
    if error:
        assert error in str(info.value)


@pytest.mark.asyncio
async def test_the_provider_message_is_capped(bsky):
    bsky.answers = [(400, {"error": "InvalidRequest", "message": "x" * 5000})]
    with pytest.raises(bluesky.BlueskyRefusedError) as info:
        await _put()
    assert len(info.value.detail) == 300
    assert len(str(info.value)) < 500


# ── 429 ───────────────────────────────────────────────────────────────────────


def _rate_limited(headers: dict) -> tuple:
    return (429, {"error": "RateLimitExceeded", "message": "Rate Limit Exceeded"}, headers)


@pytest.mark.asyncio
async def test_429_reads_a_lowercase_ratelimit_reset(bsky):
    reset = int(time.time()) + 120
    bsky.answers = [_rate_limited({"ratelimit-limit": "30", "ratelimit-remaining": "0",
                                   "ratelimit-reset": str(reset)})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    e = info.value
    assert e.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)
    assert e.retry_at.tzinfo is not None and e.retry_at.utcoffset() == timedelta(0)
    assert e.status == 429 and e.error == "RateLimitExceeded"
    assert not isinstance(e, bluesky.BlueskyRefusedError)


@pytest.mark.asyncio
async def test_429_reads_a_mixed_case_ratelimit_reset(bsky):
    reset = int(time.time()) + 300
    bsky.answers = [_rate_limited({"RateLimit-Reset": str(reset)})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    assert info.value.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)


@pytest.mark.asyncio
async def test_429_falls_back_to_retry_after_seconds(bsky):
    before = datetime.now(timezone.utc)
    bsky.answers = [_rate_limited({"Retry-After": "60"})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    after = datetime.now(timezone.utc)
    assert before + timedelta(seconds=60) <= info.value.retry_at <= after + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_429_falls_back_to_retry_after_http_date(bsky):
    when = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0)
    bsky.answers = [_rate_limited({"Retry-After": format_datetime(when, usegmt=True)})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    assert info.value.retry_at == when


@pytest.mark.parametrize("headers", [
    {},
    {"ratelimit-reset": "soon"},
    {"ratelimit-reset": "nan"},
    {"ratelimit-reset": "inf"},
    {"ratelimit-reset": "-5"},
    {"ratelimit-reset": str((int(time.time()) + 120) * 1000)},   # milliseconds sent as seconds
    {"ratelimit-reset": str(int(time.time()) + 30 * 86400)},     # a month ahead
    {"Retry-After": "-1"},
    {"Retry-After": "999999999"},
    {"Retry-After": "not a date"},
])
@pytest.mark.asyncio
async def test_429_with_an_unreadable_reset_has_no_retry_at(bsky, headers):
    bsky.answers = [_rate_limited(headers)]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    assert info.value.retry_at is None


@pytest.mark.asyncio
async def test_429_with_a_past_reset_means_now(bsky):
    before = datetime.now(timezone.utc)
    bsky.answers = [_rate_limited({"ratelimit-reset": str(int(time.time()) - 100)})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    assert before <= info.value.retry_at <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_a_bad_reset_falls_through_to_retry_after(bsky):
    before = datetime.now(timezone.utc)
    bsky.answers = [_rate_limited({"ratelimit-reset": "garbage", "Retry-After": "30"})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await _put()
    assert info.value.retry_at >= before + timedelta(seconds=30)


# ── the transport split ───────────────────────────────────────────────────────


def _raiser(error: type) -> Callable[[httpx.Request], httpx.Response]:
    def raise_it(request: httpx.Request) -> httpx.Response:
        # The message embeds the URL and the bearer — exactly what must not escape.
        auth = request.headers.get("authorization") or ""
        text = f"boom calling {request.url} with {auth} (token {auth.removeprefix('Bearer ')})"
        if error is httpx.InvalidURL:
            raise error(text)
        raise error(text, request=request)
    return raise_it


_NOT_SENT = [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
             httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.InvalidURL]
_AMBIGUOUS = [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError,
              httpx.WriteError, httpx.ProxyError, httpx.CloseError, httpx.DecodingError,
              httpx.TimeoutException, httpx.NetworkError, httpx.TransportError]


@pytest.mark.parametrize("error", _NOT_SENT)
@pytest.mark.asyncio
async def test_a_request_that_never_left_is_not_sent(bsky, error):
    bsky.answers = [_raiser(error)]
    with pytest.raises(bluesky.BlueskyNotSentError) as info:
        await _put(token=OPAQUE)
    e = info.value
    assert not isinstance(e, bluesky.BlueskyAmbiguousError)
    assert error.__name__ in str(e)
    assert e.status is None and e.method == "com.atproto.repo.putRecord"
    # Raised OUTSIDE the except block: no cause and no context (the httpx exception holds the
    # request — the Authorization header).
    assert e.__cause__ is None and e.__context__ is None
    for secret in (OPAQUE, ACCESS):
        assert secret not in str(e)


@pytest.mark.parametrize("error", _AMBIGUOUS)
@pytest.mark.asyncio
async def test_a_request_that_may_have_landed_is_ambiguous(bsky, error):
    bsky.answers = [_raiser(error)]
    with pytest.raises(bluesky.BlueskyAmbiguousError) as info:
        await _put(token=OPAQUE)
    e = info.value
    assert not isinstance(e, bluesky.BlueskyNotSentError)
    assert error.__name__ in str(e)
    # Raised OUTSIDE the except block: no cause and no context (the httpx exception holds the
    # request — the Authorization header).
    assert e.__cause__ is None and e.__context__ is None
    assert OPAQUE not in str(e)


def test_the_not_sent_classes_are_caught_before_their_parents():
    """ConnectTimeout is a TimeoutException and ConnectError a NetworkError: an `except
    httpx.TimeoutException` first would file a never-sent request as ambiguous."""
    assert issubclass(httpx.ConnectTimeout, httpx.TimeoutException)
    for cls in _NOT_SENT:
        assert issubclass(cls, bluesky._NOT_SENT_ERRORS)
    for cls in _AMBIGUOUS:
        assert not issubclass(cls, bluesky._NOT_SENT_ERRORS)


# ── getRecord ─────────────────────────────────────────────────────────────────


async def _get(host: str = PDS):
    return await bluesky.get_record(host, repo=DID, collection=bluesky.POST_COLLECTION, rkey=RKEY)


@pytest.mark.asyncio
async def test_get_record_found_sends_no_authorization(bsky, configured):
    bsky.answers = [(200, {"uri": URI, "cid": CID, "value": RECORD})]
    assert await _get() == {"uri": URI, "cid": CID, "value": RECORD}
    req = bsky.last
    assert req.method == "GET"
    assert req.url.path == "/xrpc/com.atproto.repo.getRecord"
    assert req.url.host == "morel.us-east.host.bsky.network"
    assert dict(req.url.params) == {"repo": DID, "collection": "app.bsky.feed.post", "rkey": RKEY}
    assert "authorization" not in req.headers
    assert req.content == b""


@pytest.mark.asyncio
async def test_get_record_without_a_cid_is_still_found(bsky):
    bsky.answers = [(200, {"uri": URI, "value": RECORD})]
    assert await _get() == {"uri": URI, "cid": None, "value": RECORD}


@pytest.mark.asyncio
async def test_get_record_not_found_is_none(bsky):
    bsky.answers = [(400, {"error": "RecordNotFound", "message": f"Could not locate record: {URI}"})]
    assert await _get() is None


@pytest.mark.asyncio
async def test_a_404_record_not_found_is_not_trusted_as_absent(bsky):
    # Only the documented 400 RecordNotFound means absent; "absent" licenses a resend, so anything
    # less certain raises.
    bsky.answers = [(404, {"error": "RecordNotFound", "message": "Could not locate record"})]
    with pytest.raises(bluesky.BlueskyRefusedError):
        await _get()


@pytest.mark.asyncio
async def test_an_absurdly_nested_answer_is_ambiguous_not_a_raw_recursion_error(bsky):
    bsky.answers = [(200, b"[" * 5000 + b"]" * 5000)]
    with pytest.raises(bluesky.BlueskyAmbiguousError):
        await _get()


@pytest.mark.parametrize("answer, exc", [
    ((400, {"error": "InvalidRequest", "message": "Invalid rkey"}), bluesky.BlueskyRefusedError),
    ((400, {"error": "RepoNotFound"}), bluesky.BlueskyRefusedError),
    ((400, b""), bluesky.BlueskyRefusedError),            # a 400 that does not SAY "not found"
    ((404, b"<html>not found</html>"), bluesky.BlueskyRefusedError),
    ((401, {"error": "RecordNotFound"}), bluesky.BlueskyAuthError),
    ((503, b""), bluesky.BlueskyAmbiguousError),
    ((500, {"error": "RecordNotFound"}), bluesky.BlueskyAmbiguousError),
    ((200, b"garbage"), bluesky.BlueskyAmbiguousError),
    ((200, {"uri": URI}), bluesky.BlueskyAmbiguousError),  # no value
    ((200, {"uri": URI, "value": "text"}), bluesky.BlueskyAmbiguousError),
    (_rate_limited({}), bluesky.BlueskyRateLimitError),
    (_raiser(httpx.ReadTimeout), bluesky.BlueskyAmbiguousError),
    (_raiser(httpx.ConnectError), bluesky.BlueskyNotSentError),
])
@pytest.mark.asyncio
async def test_get_record_any_other_failure_raises(bsky, answer, exc):
    bsky.answers = [answer]
    with pytest.raises(exc) as info:
        await _get()
    assert type(info.value) is exc


# ── deleteRecord ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("answer", [
    (200, {}),
    (200, b""),
    (200, {"commit": {"cid": "bafycommit", "rev": "3lb2"}}),
])
@pytest.mark.asyncio
async def test_delete_record_is_idempotent(bsky, answer):
    bsky.answers = [answer]
    out = await bluesky.delete_record(PDS, ACCESS, repo=DID, collection=bluesky.POST_COLLECTION, rkey=RKEY)
    assert out == {"deleted": True}
    req = bsky.last
    assert req.method == "POST"
    assert str(req.url) == f"{PDS}/xrpc/com.atproto.repo.deleteRecord"
    assert req.headers["authorization"] == f"Bearer {ACCESS}"
    assert _json(req) == {"repo": DID, "collection": "app.bsky.feed.post", "rkey": RKEY}


@pytest.mark.parametrize("answer, exc", [
    ((500, b""), bluesky.BlueskyAmbiguousError),
    ((200, b"not json"), bluesky.BlueskyAmbiguousError),
    ((200, [1]), bluesky.BlueskyAmbiguousError),
    ((400, {"error": "ExpiredToken"}), bluesky.BlueskyExpiredTokenError),
    ((400, {"error": "InvalidRequest"}), bluesky.BlueskyRefusedError),
])
@pytest.mark.asyncio
async def test_delete_record_failures(bsky, answer, exc):
    bsky.answers = [answer]
    with pytest.raises(exc):
        await bluesky.delete_record(PDS, ACCESS, repo=DID, collection=bluesky.POST_COLLECTION, rkey=RKEY)


# ── the public AppView: getPosts / getProfile (the measure step) ──────────────


URI2 = f"at://{DID}/app.bsky.feed.post/3lb2c4d5e6f7h"
INDEXED = "2026-10-03T20:15:03.000Z"


def _view(uri: str = URI, **counts: Any) -> dict:
    """A post view as the AppView sends it — author, record (the TEXT) and viewer state included."""
    return {"uri": uri, "cid": CID, "author": {"did": DID, "handle": HANDLE, "displayName": "Caydex"},
            "record": dict(RECORD), "indexedAt": INDEXED, "labels": [], "viewer": {}, **counts}


def _uris(n: int) -> List[str]:
    return [f"at://{DID}/app.bsky.feed.post/3lb2c4d5e{i:04d}" for i in range(n)]


@pytest.mark.asyncio
async def test_get_posts_asks_the_public_appview_without_a_credential(bsky, configured):
    bsky.answers = [(200, {"posts": [
        _view(URI, likeCount=4, repostCount=1, replyCount=0, quoteCount=0, bookmarkCount=2),
        _view(URI2, likeCount=0),
    ]})]
    out = await bluesky.get_posts([URI, URI2])

    assert out == [
        {"uri": URI, "cid": CID, "likeCount": 4, "repostCount": 1, "replyCount": 0, "quoteCount": 0,
         "bookmarkCount": 2, "indexedAt": INDEXED},
        {"uri": URI2, "cid": CID, "likeCount": 0, "indexedAt": INDEXED},
    ]
    req = bsky.last
    assert req.method == "GET"
    assert req.url.scheme == "https" and req.url.host == "public.api.bsky.app"
    assert req.url.path == "/xrpc/app.bsky.feed.getPosts"
    # A query ARRAY repeats the parameter name.
    assert req.url.params.multi_items() == [("uris", URI), ("uris", URI2)]
    assert "authorization" not in req.headers
    assert req.content == b""
    # Neither the post text nor the author leaves the client.
    assert "Hello" not in repr(out) and HANDLE not in repr(out)


def test_the_appview_is_the_documented_public_host():
    assert bluesky.APPVIEW_URL == "https://public.api.bsky.app"


@pytest.mark.asyncio
async def test_get_posts_returns_counts_exactly_as_sent(bsky):
    # Counts the lexicon calls integers but a hostile or buggy answer sends otherwise: untouched.
    odd = {"likeCount": "12", "repostCount": None, "replyCount": -3, "quoteCount": True, "bookmarkCount": 1.5}
    bsky.answers = [(200, {"posts": [_view(URI, **odd)]})]
    (view,) = await bluesky.get_posts([URI])
    assert {k: view[k] for k in odd} == odd
    assert set(view) == {"uri", "cid", "indexedAt", *odd}


@pytest.mark.asyncio
async def test_get_posts_a_view_without_counts_has_no_count_keys_not_zeros(bsky):
    bsky.answers = [(200, {"posts": [{"uri": URI}]})]
    assert await bluesky.get_posts([URI]) == [{"uri": URI}]


@pytest.mark.asyncio
async def test_get_posts_a_deleted_post_is_simply_absent(bsky):
    bsky.answers = [(200, {"posts": [_view(URI2, likeCount=1)]}), (200, {"posts": []})]
    assert [v["uri"] for v in await bluesky.get_posts([URI, URI2])] == [URI2]
    assert await bluesky.get_posts([URI]) == []


@pytest.mark.parametrize("empty", [[], ()])
@pytest.mark.asyncio
async def test_get_posts_with_no_uris_makes_no_call(bsky, empty):
    assert await bluesky.get_posts(empty) == []
    assert bsky.requests == []


@pytest.mark.asyncio
async def test_get_posts_takes_25_and_refuses_26_before_sending(bsky):
    bsky.answers = [(200, {"posts": []})]
    assert await bluesky.get_posts(_uris(25)) == []
    assert bsky.last.url.params.get_list("uris") == _uris(25)
    with pytest.raises(bluesky.BlueskyRefusedError) as info:
        await bluesky.get_posts(_uris(26))
    assert type(info.value) is bluesky.BlueskyRefusedError and info.value.status is None
    assert "26" in str(info.value) and len(bsky.requests) == 1


@pytest.mark.asyncio
async def test_get_posts_asks_for_a_repeated_uri_once(bsky):
    bsky.answers = [(200, {"posts": [_view(URI)]})]
    await bluesky.get_posts((URI, URI2, URI))
    assert bsky.last.url.params.get_list("uris") == [URI, URI2]


_BAD_URIS = [
    "http://bsky.app/profile/caydex.bsky.social/post/3lb2c4d5e6f7g",
    f"https://bsky.app/profile/{DID}/post/{RKEY}",
    f"at://{HANDLE}/app.bsky.feed.post/{RKEY}",                 # a handle can change hands
    f"at://{DID}/app.bsky.feed.like/{RKEY}",                     # wrong collection
    f"at://{DID}/app.bsky.feed.repost/{RKEY}",
    f"at://{DID}/app.bsky.feed.post/",                            # empty record key
    f"at://{DID}/app.bsky.feed.post",
    f"at://{DID}",
    f"at://{DID}/app.bsky.feed.post/{RKEY}/extra",
    f"at://{DID}/app.bsky.feed.post/{RKEY}\n",                    # a trailing newline ($ would pass it)
    f"at://{DID}/app.bsky.feed.post/{RKEY}\nX-Evil: 1",
    f" {URI}", f"{URI} ",
    f"at://{DID}/app.bsky.feed.post/.", f"at://{DID}/app.bsky.feed.post/..",
    f"at://{DID}/app.bsky.feed.post/a b",
    f"at://{DID}/app.bsky.feed.post/{RKEY}?x=1",
    f"at://{DID}/app.bsky.feed.post/{RKEY}#frag",
    f"at://{DID}/app.bsky.feed.post/{'r' * 513}",                 # record key over 512
    f"at://did:plc:{'a' * 3000}/app.bsky.feed.post/{RKEY}",       # DID over 2 KB
    "at://" + "x" * 10_000,
    "at://did:PLC:abc123xyz789/app.bsky.feed.post/3lb",          # the method is lowercase
    "at://did:plc:/app.bsky.feed.post/3lb",                      # empty identifier
    "at://did:plc:abc:/app.bsky.feed.post/3lb",                  # ends in ":"
    "at://did:plc:abc%/app.bsky.feed.post/3lb",                  # ends in "%"
    "at:///app.bsky.feed.post/3lb",
    "AT://did:plc:abc/app.bsky.feed.post/3lb",
    "",
    None, 123, b"at://did:plc:abc/app.bsky.feed.post/3lb", {"uri": URI},
]


@pytest.mark.parametrize("bad", _BAD_URIS)
@pytest.mark.asyncio
async def test_get_posts_refuses_a_malformed_uri_before_sending(bsky, bad):
    with pytest.raises(bluesky.BlueskyRefusedError) as info:
        await bluesky.get_posts([URI, bad])
    e = info.value
    assert type(e) is bluesky.BlueskyRefusedError and e.status is None
    assert e.method == "app.bsky.feed.getPosts" and "uris[1]" in str(e) and "nothing was sent" in str(e)
    assert len(str(e)) < 400 and "\n" not in str(e)
    assert bsky.requests == []


@pytest.mark.parametrize("uris", [URI, None, 5, {URI}, {URI: 1}, (u for u in [URI]), URI.encode()])
@pytest.mark.asyncio
async def test_get_posts_needs_a_list_or_tuple(bsky, uris):
    with pytest.raises(bluesky.BlueskyRefusedError) as info:
        await bluesky.get_posts(uris)
    assert info.value.status is None
    assert bsky.requests == []


@pytest.mark.parametrize("good", [
    URI, URI2, "at://did:web:example.com/app.bsky.feed.post/3lb2c4d5e6f7g",
    "at://did:web:localhost%3A8080/app.bsky.feed.post/self", f"at://{DID}/app.bsky.feed.post/a.b_c:d~e-f",
    f"at://{DID}/app.bsky.feed.post/{'r' * 512}",
])
def test_is_post_uri_accepts_the_spec_shapes(good):
    assert bluesky.is_post_uri(good) is True


@pytest.mark.parametrize("bad", _BAD_URIS)
def test_is_post_uri_refuses_everything_else(bad):
    assert bluesky.is_post_uri(bad) is False


@pytest.mark.parametrize("answer", [
    (200, {}),                                          # no posts list: NOT "every post deleted"
    (200, {"posts": None}),
    (200, {"posts": {}}),
    (200, {"posts": "x"}),
    (200, {"feed": [{"uri": URI}]}),
    (200, {"posts": ["x"]}),
    (200, {"posts": [None]}),
    (200, {"posts": [{"cid": CID, "likeCount": 3}]}),   # a view without a uri
    (200, {"posts": [{"uri": 5}]}),
    (200, {"posts": [{"uri": ""}]}),
    (200, {"posts": [{"uri": URI}, {"likeCount": 3}]}),
    (200, b"not json"),
    (200, b"[1, 2]"),
    (200, b""),
])
@pytest.mark.asyncio
async def test_get_posts_an_unreadable_answer_is_ambiguous(bsky, answer):
    bsky.answers = [answer]
    with pytest.raises(bluesky.BlueskyAmbiguousError) as info:
        await bluesky.get_posts([URI])
    assert type(info.value) is bluesky.BlueskyAmbiguousError and info.value.status == 200
    assert info.value.method == "app.bsky.feed.getPosts"


@pytest.mark.asyncio
async def test_get_profile_by_did_reads_the_counts_without_a_credential(bsky, configured):
    bsky.answers = [(200, {"did": DID, "handle": HANDLE, "displayName": "Caydex", "description": "bio text",
                           "followersCount": 12, "followsCount": 3, "postsCount": 40, "indexedAt": INDEXED})]
    out = await bluesky.get_profile(DID)

    assert out == {"did": DID, "handle": HANDLE, "followersCount": 12, "followsCount": 3, "postsCount": 40}
    req = bsky.last
    assert req.method == "GET" and req.url.host == "public.api.bsky.app"
    assert req.url.path == "/xrpc/app.bsky.actor.getProfile"
    assert req.url.params.multi_items() == [("actor", DID)]
    assert "authorization" not in req.headers and req.content == b""


@pytest.mark.parametrize("given, sent", [
    (HANDLE, HANDLE), (f"@{HANDLE}", HANDLE), (f"  {HANDLE}\n", HANDLE), ("Caydex.Bsky.Social", "Caydex.Bsky.Social"),
])
@pytest.mark.asyncio
async def test_get_profile_by_handle(bsky, given, sent):
    bsky.answers = [(200, {"did": DID, "handle": HANDLE, "followersCount": 1})]
    out = await bluesky.get_profile(given)
    assert bsky.last.url.params.multi_items() == [("actor", sent)]
    # Absent counts are None — never 0.
    assert out == {"did": DID, "handle": HANDLE, "followersCount": 1, "followsCount": None, "postsCount": None}


@pytest.mark.asyncio
async def test_get_profile_returns_counts_exactly_as_sent(bsky):
    body = {"did": DID, "handle": HANDLE, "followersCount": "12", "followsCount": -1, "postsCount": True}
    bsky.answers = [(200, body)]
    assert await bluesky.get_profile(DID) == body


@pytest.mark.parametrize("actor", [
    "", "   ", "@", None, 42, b"did:plc:abc", ["did:plc:abc"],
    "did:", "did:plc:", "did:PLC:abc", "did:plc:abc:", f"did:plc:{'a' * 3000}",
    "not a handle", "caydex", "caydex.", ".caydex.bsky.social", "caydex..bsky.social",
    "-caydex.bsky.social", "caydex.bsky.123", f"{'a' * 64}.bsky.social", "a." * 130 + "social",
    f"{HANDLE}\nX-Evil: 1", "https://bsky.app/profile/caydex.bsky.social", "@@caydex.bsky.social",
])
@pytest.mark.asyncio
async def test_get_profile_refuses_an_actor_that_is_neither_a_did_nor_a_handle(bsky, actor):
    with pytest.raises(bluesky.BlueskyRefusedError) as info:
        await bluesky.get_profile(actor)
    e = info.value
    assert type(e) is bluesky.BlueskyRefusedError and e.status is None and "nothing was sent" in str(e)
    assert bsky.requests == []


@pytest.mark.parametrize("actor, answer", [
    (DID, (200, {})),                                                       # no did
    (DID, (200, {"handle": HANDLE, "followersCount": 99999})),
    (DID, (200, {"did": None, "handle": HANDLE})),
    (DID, (200, {"did": "plc:abc123xyz789", "handle": HANDLE})),           # not a DID
    (DID, (200, {"did": 5})),
    (DID, (200, {"did": "did:plc:someoneelse0000", "handle": HANDLE, "followersCount": 99999})),
    (HANDLE, (200, {"did": DID, "handle": "someone.else.social", "followersCount": 99999})),
    (HANDLE, (200, {"did": DID, "handle": "handle.invalid", "followersCount": 99999})),
    (HANDLE, (200, {"did": DID, "followersCount": 99999})),                # nothing to compare with
    (HANDLE, (200, {"did": DID, "handle": 5})),
    (DID, (200, b"not json")),
    (DID, (200, [DID])),
])
@pytest.mark.asyncio
async def test_get_profile_about_another_account_or_unreadable_is_ambiguous(bsky, actor, answer):
    bsky.answers = [answer]
    with pytest.raises(bluesky.BlueskyAmbiguousError) as info:
        await bluesky.get_profile(actor)
    e = info.value
    assert type(e) is bluesky.BlueskyAmbiguousError and e.status == 200
    assert e.method == "app.bsky.actor.getProfile"
    assert "99999" not in str(e)   # another account's numbers never travel


@pytest.mark.asyncio
async def test_get_profile_another_account_message_cannot_forge_a_log_line(bsky):
    bsky.answers = [(200, {"did": DID, "handle": "evil.example\nERROR forged: line " + "z" * 500})]
    with pytest.raises(bluesky.BlueskyAmbiguousError) as info:
        await bluesky.get_profile(HANDLE)
    text = str(info.value)
    assert "another account" in text and "\n" not in text and len(text) < 400


_APPVIEW_READS = {
    "posts": lambda: bluesky.get_posts([URI]),
    "profile": lambda: bluesky.get_profile(DID),
}


@pytest.mark.parametrize("read", sorted(_APPVIEW_READS))
@pytest.mark.parametrize("answer, exc, status", [
    ((400, {"error": "InvalidRequest", "message": "uris/0 must be a valid at-uri"}), bluesky.BlueskyRefusedError, 400),
    ((400, {"error": "AccountDeactivated", "message": "Account is deactivated"}), bluesky.BlueskyRefusedError, 400),
    ((401, {"error": "AuthenticationRequired"}), bluesky.BlueskyAuthError, 401),
    ((404, b"<html>not found</html>"), bluesky.BlueskyRefusedError, 404),
    ((408, b""), bluesky.BlueskyAmbiguousError, 408),
    ((500, {"error": "InternalServerError"}), bluesky.BlueskyAmbiguousError, 500),
    ((502, b"<html>bad gateway</html>"), bluesky.BlueskyAmbiguousError, 502),
    ((302, b"", {"location": "https://elsewhere.example"}), bluesky.BlueskyAmbiguousError, 302),
    (_rate_limited({}), bluesky.BlueskyRateLimitError, 429),
    (_raiser(httpx.ConnectError), bluesky.BlueskyNotSentError, None),
    (_raiser(httpx.ReadTimeout), bluesky.BlueskyAmbiguousError, None),
])
@pytest.mark.asyncio
async def test_appview_reads_map_every_answer(bsky, read, answer, exc, status):
    bsky.answers = [answer]
    with pytest.raises(exc) as info:
        await _APPVIEW_READS[read]()
    e = info.value
    assert type(e) is exc and e.status == status
    assert len(bsky.requests) == 1   # never retried, never followed a redirect
    assert e.__cause__ is None and e.__context__ is None


@pytest.mark.asyncio
async def test_appview_429_carries_retry_at(bsky):
    reset = int(time.time()) + 120
    bsky.answers = [_rate_limited({"ratelimit-reset": str(reset)})]
    with pytest.raises(bluesky.BlueskyRateLimitError) as info:
        await bluesky.get_posts([URI])
    assert info.value.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)


@pytest.mark.asyncio
async def test_appview_reads_take_another_https_host(bsky):
    bsky.answers = [(200, {"posts": []}), (200, {"did": DID, "handle": HANDLE})]
    await bluesky.get_posts([URI], host="https://api.bsky.app/")
    await bluesky.get_profile(DID, host="https://api.bsky.app")
    assert [(r.url.host, r.url.path) for r in bsky.requests] == [
        ("api.bsky.app", "/xrpc/app.bsky.feed.getPosts"), ("api.bsky.app", "/xrpc/app.bsky.actor.getProfile")]


@pytest.mark.parametrize("host", ["http://public.api.bsky.app", "public.api.bsky.app", "", "https://", None])
@pytest.mark.asyncio
async def test_a_non_https_appview_is_refused_before_sending(bsky, host):
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await bluesky.get_posts([URI], host=host)
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await bluesky.get_profile(DID, host=host)
    assert bsky.requests == []


# ── not configured ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("handle, password", [
    (None, APP_PASSWORD), (HANDLE, None), ("", ""), ("  ", APP_PASSWORD), (HANDLE, " "),
])
@pytest.mark.asyncio
async def test_not_configured_never_calls_the_platform(bsky, monkeypatch, handle, password):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_HANDLE", handle)
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_APP_PASSWORD", password)
    with pytest.raises(bluesky.BlueskyNotConfiguredError) as info:
        await bluesky.create_session()
    assert bsky.requests == []
    assert info.value.status is None


@pytest.mark.asyncio
async def test_a_plain_http_service_url_is_not_configured(bsky, configured, monkeypatch):
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_SERVICE", "http://bsky.social")
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await bluesky.create_session()
    with pytest.raises(bluesky.BlueskyNotConfiguredError):
        await bluesky.refresh_session(REFRESH)
    assert bsky.requests == []


# ── secrets ───────────────────────────────────────────────────────────────────


#: Secrets with a recognisable SHAPE (an app password, a JWT): `redact_secrets` hides them even in
#: a call that never carried them. OPAQUE has no shape, so only a call that sent it can scrub it.
_SHAPED = (APP_PASSWORD, ACCESS, REFRESH, NEW_REFRESH)


def _echo(status: int, error: str, extra: Optional[dict] = None) -> Callable[[httpx.Request], httpx.Response]:
    """An error answer whose `message` echoes back everything the request carried — its body and
    its Authorization header — plus every shaped secret (the worst a platform could do)."""
    def answer(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization") or ""
        # The bare token too: only the per-call literal replacement hides a shapeless one.
        msg = (f"rejected {request.content.decode('utf-8', 'replace')} "
               f"auth={auth} token {auth.removeprefix('Bearer ')} " + " ".join(_SHAPED))
        return httpx.Response(status, json={"error": error, "message": msg}, headers=extra or {})
    return answer


_SECRET_CASES = [
    ("create", _echo(400, "InvalidRequest")),
    ("create", _echo(401, "AuthenticationRequired")),
    ("create", _echo(429, "RateLimitExceeded", {"ratelimit-reset": "x"})),
    ("create", _raiser(httpx.ConnectError)),
    ("create", _raiser(httpx.ReadTimeout)),
    ("refresh", _echo(400, "ExpiredToken")),
    ("refresh", _echo(502, "UpstreamFailure")),
    ("put", _echo(401, "InvalidToken")),
    ("put", _echo(400, "InvalidSwap")),
    ("put", _echo(500, "InternalServerError")),
    ("put", _raiser(httpx.WriteTimeout)),
    ("put", _raiser(httpx.PoolTimeout)),
    ("delete", _echo(400, "InvalidRequest")),
    ("delete", _raiser(httpx.RemoteProtocolError)),
    ("get", _echo(400, "InvalidRequest")),
    ("posts", _echo(400, "InvalidRequest")),
    ("posts", _echo(503, "UpstreamFailure")),
    ("posts", _echo(429, "RateLimitExceeded", {"ratelimit-reset": "x"})),
    ("posts", _raiser(httpx.ConnectError)),
    ("posts", _raiser(httpx.ReadTimeout)),
    ("profile", _echo(400, "InvalidRequest")),
    ("profile", _echo(401, "AuthenticationRequired")),
    ("profile", _raiser(httpx.PoolTimeout)),
]


@pytest.mark.parametrize("call, answer", _SECRET_CASES)
@pytest.mark.asyncio
async def test_no_secret_in_any_exception_or_log(bsky, configured, caplog, call, answer):
    caplog.set_level(logging.DEBUG)
    bsky.answers = [answer]
    with pytest.raises(bluesky.BlueskyException) as info:
        if call == "create":
            await bluesky.create_session()
        elif call == "refresh":
            await bluesky.refresh_session(REFRESH)
        elif call == "put":
            await _put(token=OPAQUE)
        elif call == "delete":
            await bluesky.delete_record(PDS, ACCESS, repo=DID, collection=bluesky.POST_COLLECTION, rkey=RKEY)
        elif call == "posts":
            await bluesky.get_posts([URI])
        elif call == "profile":
            await bluesky.get_profile(DID)
        else:
            await _get()
    e = info.value
    surfaces = [str(e), repr(e), str(e.args), str(e.detail), str(e.error), caplog.text]
    for surface in surfaces:
        for secret in SECRETS:
            assert secret not in surface, (call, secret, surface[:200])
    assert e.__cause__ is None
    if call in ("posts", "profile"):
        # The AppView never receives a credential to echo in the first place.
        assert all("authorization" not in r.headers for r in bsky.requests)


@pytest.mark.asyncio
async def test_a_successful_session_logs_no_secret(bsky, configured, caplog):
    caplog.set_level(logging.DEBUG)
    bsky.answers = [(200, _session_body(did_doc=None)), (200, _session_body(refresh=NEW_REFRESH)),
                    (200, {"uri": URI, "cid": CID})]
    await bluesky.create_session()
    await bluesky.refresh_session(REFRESH)
    await _put()
    for secret in SECRETS:
        assert secret not in caplog.text


# ── lifecycle ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_is_idempotent(monkeypatch):
    monkeypatch.setattr(bluesky, "_client", None)
    client = bluesky._get_client()
    assert bluesky._get_client() is client
    assert client.follow_redirects is False
    await bluesky.close_bluesky_client()
    assert bluesky._client is None and client.is_closed
    await bluesky.close_bluesky_client()
    assert bluesky._client is None


# ── the exception family ──────────────────────────────────────────────────────


#: Every class and the status `classify_exception` must give it: 502 = permanent for that request
#: (refused / auth / not configured / invalid swap / expired token), 503 = transient or unknown.
_EXPECTED_STATUS = {
    "BlueskyException": 503,
    "BlueskyNotConfiguredError": 502,
    "BlueskyNotSentError": 503,
    "BlueskyAmbiguousError": 503,
    "BlueskyRefusedError": 502,
    "BlueskyAuthError": 502,
    "BlueskyExpiredTokenError": 502,
    "BlueskyInvalidSwapError": 502,
    "BlueskyRateLimitError": 503,
}


def _exception_classes() -> list:
    return [obj for obj in vars(bluesky).values()
            if isinstance(obj, type) and issubclass(obj, bluesky.BlueskyException)
            and obj.__module__ == bluesky.__name__]


def test_every_exception_class_is_listed():
    names = {cls.__name__ for cls in _exception_classes()}
    assert names == set(_EXPECTED_STATUS), names ^ set(_EXPECTED_STATUS)


@pytest.mark.parametrize("name, status", sorted(_EXPECTED_STATUS.items()))
def test_every_exception_classifies_as_publisher_unavailable(name, status):
    cls = getattr(bluesky, name)
    exc = cls("x")  # every class constructs from a bare message
    # A message that would trip the generic heuristics ("429", "timed out") must not matter.
    noisy = cls("bluesky com.atproto.repo.putRecord: HTTP 429 timed out rate limit", method="m", status=429)
    for e in (exc, noisy):
        assert classify_exception(e) == (ErrorCode.MARKETING_PUBLISHER_UNAVAILABLE, status)


def test_exception_hierarchy():
    for sub in (bluesky.BlueskyAuthError, bluesky.BlueskyExpiredTokenError, bluesky.BlueskyInvalidSwapError):
        assert issubclass(sub, bluesky.BlueskyRefusedError)
    for other in (bluesky.BlueskyNotSentError, bluesky.BlueskyAmbiguousError,
                  bluesky.BlueskyRateLimitError, bluesky.BlueskyNotConfiguredError):
        assert not issubclass(other, bluesky.BlueskyRefusedError)
    assert not issubclass(bluesky.BlueskyNotSentError, bluesky.BlueskyAmbiguousError)
    assert not issubclass(bluesky.BlueskyAmbiguousError, bluesky.BlueskyNotSentError)
    assert not issubclass(bluesky.BlueskyExpiredTokenError, bluesky.BlueskyAuthError)
    e = bluesky.BlueskyRateLimitError("x")
    assert e.retry_at is None and e.method == "" and e.status is None and e.error is None and e.detail is None


# ── the image post: blob CIDs and uploadBlob (drop 1, contract C9) ──────────────────────────────

IMG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8 + b"\xff\xd9"
IMG_CID = bluesky.raw_cid_for_sha256(hashlib.sha256(IMG).hexdigest())


def _blob_answer(cid: str = IMG_CID, size: int = len(IMG), mime: str = "image/jpeg") -> tuple:
    return 200, {"blob": {"$type": "blob", "ref": {"$link": cid}, "mimeType": mime, "size": size}}


@pytest.mark.parametrize("data, cid", [
    # The well-known CIDv1 (raw, sha2-256) of the empty input — computed elsewhere, not by this module.
    (b"", "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"),
    (b"hello world", "bafkreifzjut3te2nhyekklss27nh3k72ysco7y32koao5eei66wof36n5e"),
])
def test_raw_cid_for_sha256_matches_known_vectors(data, cid):
    digest = hashlib.sha256(data).hexdigest()
    assert bluesky.raw_cid_for_sha256(digest) == cid
    assert bluesky.raw_cid_for_sha256(digest.upper()) == cid      # hex case does not matter
    assert bluesky.raw_cid_for_sha256(f"  {digest}\n") == cid


@pytest.mark.parametrize("digest", ["", "abc", "g" * 64, "0" * 63, "0" * 65, None, 12, b"0" * 64])
def test_raw_cid_for_sha256_refuses_a_malformed_digest(digest):
    with pytest.raises(ValueError, match="64 hex"):
        bluesky.raw_cid_for_sha256(digest)


def test_blob_ref_is_the_json_shape_a_record_names():
    assert bluesky.blob_ref(IMG_CID, "image/jpeg", 12) == {
        "$type": "blob", "ref": {"$link": IMG_CID}, "mimeType": "image/jpeg", "size": 12}


@pytest.mark.asyncio
async def test_upload_blob_sends_the_raw_bytes_with_their_type(bsky):
    bsky.answers = [_blob_answer()]
    out = await bluesky.upload_blob(PDS, ACCESS, data=IMG, mime_type="image/jpeg")
    assert out == {"cid": IMG_CID, "mime_type": "image/jpeg", "size": len(IMG)}
    req = bsky.last
    assert req.method == "POST" and str(req.url) == f"{PDS}/xrpc/com.atproto.repo.uploadBlob"
    assert req.headers["content-type"] == "image/jpeg" and req.content == IMG
    assert req.headers["authorization"] == f"Bearer {ACCESS}"


@pytest.mark.asyncio
@pytest.mark.parametrize("kw, exc, why", [
    ({"data": b""}, bluesky.BlueskyRefusedError, "blob must be"),
    ({"data": None}, bluesky.BlueskyRefusedError, "blob must be"),
    ({"data": "text"}, bluesky.BlueskyRefusedError, "blob must be"),
    ({"data": b"x" * 1_000_001}, bluesky.BlueskyRefusedError, "blob must be"),
    ({"mime_type": "image/jpeg\r\nX-Evil: 1"}, bluesky.BlueskyRefusedError, "blob type"),
    ({"mime_type": "text/html"}, bluesky.BlueskyRefusedError, "blob type"),
    ({"token": ""}, bluesky.BlueskyNotConfiguredError, "no access token"),
])
async def test_upload_blob_refuses_before_sending(bsky, kw, exc, why):
    args = {"data": IMG, "mime_type": "image/jpeg", "token": ACCESS, **kw}
    with pytest.raises(exc, match=why) as ei:
        await bluesky.upload_blob(PDS, args["token"], data=args["data"], mime_type=args["mime_type"])
    assert type(ei.value) is exc and bsky.requests == []


@pytest.mark.asyncio
async def test_upload_blob_accepts_exactly_the_lexicon_maximum(bsky):
    data = b"x" * 1_000_000
    bsky.answers = [_blob_answer(bluesky.raw_cid_for_sha256(hashlib.sha256(data).hexdigest()), len(data))]
    assert (await bluesky.upload_blob(PDS, ACCESS, data=data, mime_type="image/jpeg"))["size"] == 1_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"blob": None}, {"blob": {"ref": {"$link": IMG_CID}, "mimeType": "image/jpeg"}},
    {"blob": {"ref": {"$link": "not a cid"}, "mimeType": "image/jpeg", "size": 5}},
    {"blob": {"ref": IMG_CID, "mimeType": "image/jpeg", "size": 5}},
    {"blob": {"ref": {"$link": IMG_CID}, "mimeType": "image/jpeg", "size": True}},
    {"blob": {"ref": {"$link": IMG_CID}, "mimeType": "image/jpeg", "size": -1}},
    {"blob": {"ref": {"$link": IMG_CID}, "size": 5}},
])
async def test_upload_blob_without_a_readable_blob_is_ambiguous(bsky, body):
    bsky.answers = [(200, body)]
    with pytest.raises(bluesky.BlueskyAmbiguousError, match="without a readable blob"):
        await bluesky.upload_blob(PDS, ACCESS, data=IMG, mime_type="image/jpeg")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, exc", [
    ((400, {"error": "ExpiredToken", "message": "Token has expired"}), bluesky.BlueskyExpiredTokenError),
    ((401, {"error": "AuthenticationRequired"}), bluesky.BlueskyAuthError),
    ((400, {"error": "BlobTooLarge", "message": "too big"}), bluesky.BlueskyRefusedError),
    ((429, {"error": "RateLimitExceeded"}, {"ratelimit-reset": str(int(time.time()) + 60)}),
     bluesky.BlueskyRateLimitError),
    ((502, {"error": "UpstreamFailure"}), bluesky.BlueskyAmbiguousError),
    (_raiser(httpx.ConnectError), bluesky.BlueskyNotSentError),
    (_raiser(httpx.ReadTimeout), bluesky.BlueskyAmbiguousError),
])
async def test_upload_blob_keeps_the_outcome_split_and_no_secret(bsky, answer, exc):
    bsky.answers = [answer]
    with pytest.raises(exc) as ei:
        await bluesky.upload_blob(PDS, ACCESS, data=IMG, mime_type="image/jpeg")
    assert type(ei.value) is exc and ei.value.__context__ is None
    for secret in SECRETS:
        assert secret not in str(ei.value)


@pytest.mark.asyncio
async def test_a_json_and_a_binary_body_at_once_is_refused_unsent(bsky):
    with pytest.raises(bluesky.BlueskyRefusedError, match="nothing was sent"):
        await bluesky._xrpc("POST", PDS, "com.atproto.repo.uploadBlob", bearer=ACCESS, payload={"a": 1},
                            raw=(IMG, "image/jpeg"))
    assert bsky.requests == []
