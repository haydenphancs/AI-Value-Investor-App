"""The Upload-Post thin client (`app/integrations/upload_post.py`) — design doc §12.10, Stage 2.

What these pin, in order of how much a regression would cost:

1. THE OUTCOME SPLIT. A request that provably never left is `NotSent` (safe to resend); one that may
   have reached Upload-Post is `Ambiguous` (poll the job first); a definite 4xx is `Refused`, split
   into the owner-action refusals (key, plan, monthly quota, platform not connected, reconnect) the
   outlet alerts on; a 429 without `usage` or a parked account is `RateLimit` with `retry_at`.
2. The request: multipart, one platform, `async_upload=true`, `request_id` both as a field and as
   the `Idempotency-Key` header — the header is what makes a resend return the existing job.
3. No API key in any exception text, attribute, repr or log line; no `__context__` on a transport
   failure (the httpx exception holds the request and its Authorization header).
4. A 404 means "no such job" only when Upload-Post SAYS `not_found` — "absent" licenses a resend.

Hermetic: Upload-Post is an `httpx.MockTransport` installed on the module's `_client`; settings are
set with monkeypatch. Transport failures are raised inside the handler, where a real transport
would raise them.
"""

from __future__ import annotations

import email
import email.policy
import inspect
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.integrations import upload_post

#: Opaque (not JWT-, not app-password-shaped): only the per-call literal replacement hides it.
API_KEY = "UPkeyTEST0123456789abcdefghijKLMNOPqrstuv"
PROFILE = "caydex"
REQUEST_ID = "2026-10-01:tiktok:video:a1"
EXTERNAL_ID = "5f0c2b1e-9a77-4c1e-8f43-0d8f1a2b3c4d"
VIDEO_URL = "https://xyz.supabase.co/storage/v1/object/public/marketing-media/2026-10-01/video-abc.mp4"
BASE = "https://api.upload-post.com/api"


# ── harness ────────────────────────────────────────────────────────────


class FakeUploadPost:
    """Records every request; answers with `respond(request)`."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _install(monkeypatch, respond: Callable[[httpx.Request], httpx.Response]) -> FakeUploadPost:
    fake = FakeUploadPost(respond)
    monkeypatch.setattr(upload_post, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def _answer(status: int, body: Any = None, headers: Optional[Dict[str, str]] = None):
    def respond(_request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, headers=headers)
        if isinstance(body, (bytes, str)):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)
    return respond


def _raising(exc_type: type):
    def respond(request: httpx.Request) -> httpx.Response:
        message = f"simulated {exc_type.__name__} {API_KEY} {request.headers['Authorization']}"
        if exc_type is httpx.InvalidURL:  # not a RequestError: it takes no request
            raise exc_type(message)
        raise exc_type(message, request=request)
    return respond


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_API_KEY", API_KEY)
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_USER", PROFILE)


def _parts(request: httpx.Request) -> List[Tuple[str, str]]:
    """The multipart form as (name, value) pairs, in order; asserts no part is a file."""
    ctype = request.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary="), ctype
    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + request.content,
                                   policy=email.policy.HTTP)
    out = []
    for part in msg.iter_parts():
        assert part.get_filename() is None
        name = part.get_param("name", header="content-disposition")
        out.append((name, part.get_payload(decode=True).decode("utf-8")))
    return out


ASYNC_ACK = {"success": True, "message": "Upload initiated successfully in background.",
             "request_id": REQUEST_ID, "total_platforms": 1}


async def _text(**over):
    kw = dict(platform="facebook", text="Hello world", fields={"facebook_page_id": "123"},
              request_id=REQUEST_ID, external_id=EXTERNAL_ID)
    kw.update(over)
    return await upload_post.upload_text(**kw)


async def _video(**over):
    kw = dict(platform="tiktok", video_url=VIDEO_URL, fields={"title": "t", "is_aigc": True},
              request_id=REQUEST_ID, external_id=EXTERNAL_ID)
    kw.update(over)
    return await upload_post.upload_video(**kw)


# ── settings ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key, profile, expected", [
    (API_KEY, PROFILE, True),
    (None, PROFILE, False),
    (API_KEY, None, False),
    ("", PROFILE, False),
    (API_KEY, "   ", False),
    ("  ", "  ", False),
])
def test_configured_needs_both(monkeypatch, key, profile, expected):
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_API_KEY", key)
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_USER", profile)
    assert upload_post.configured() is expected


def test_user_is_stripped(monkeypatch):
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_USER", f"  {PROFILE}\n")
    assert upload_post.user() == PROFILE
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_USER", None)
    assert upload_post.user() == ""


def test_settings_default_upload_post_off():
    from app.config import Settings
    fields = Settings.model_fields
    assert fields["MARKETING_UPLOAD_POST_API_KEY"].default is None
    assert fields["MARKETING_UPLOAD_POST_USER"].default is None


# ── request shape ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_text_request_shape(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, ASYNC_ACK))
    res = await _text(fields={"facebook_page_id": "123", "threads_long_text_as_post": True,
                              "link_preview": False, "max_posts": 3, "ratio": 1.5, "skip_me": None,
                              "tags[]": ["a", "b"]})
    assert res == {"mode": "async", "request_id": REQUEST_ID}

    req = fake.last
    assert req.method == "POST"
    assert str(req.url) == f"{BASE}/upload_text"
    assert req.headers["authorization"] == f"Apikey {API_KEY}"
    assert req.headers["idempotency-key"] == REQUEST_ID
    assert req.headers["accept"] == "application/json"
    assert _parts(req) == [
        ("user", PROFILE),
        ("platform[]", "facebook"),
        ("title", "Hello world"),
        ("async_upload", "true"),
        ("request_id", REQUEST_ID),
        ("external_id", EXTERNAL_ID),
        ("facebook_page_id", "123"),
        ("threads_long_text_as_post", "true"),
        ("link_preview", "false"),
        ("max_posts", "3"),
        ("ratio", "1.5"),
        ("tags[]", "a"),
        ("tags[]", "b"),
    ]


@pytest.mark.asyncio
async def test_upload_video_request_shape(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, ASYNC_ACK))
    fields = {"title": "Caption", "tiktok_title": "Caption", "privacy_level": "PUBLIC_TO_EVERYONE",
              "post_mode": "DIRECT_POST", "disable_inbox_fallback": True, "brand_organic_toggle": True,
              "is_aigc": True}
    res = await _video(fields=fields)
    assert res == {"mode": "async", "request_id": REQUEST_ID}

    req = fake.last
    assert str(req.url) == f"{BASE}/upload"
    assert req.headers["authorization"] == f"Apikey {API_KEY}"
    assert req.headers["idempotency-key"] == REQUEST_ID
    parts = _parts(req)
    assert parts[:6] == [
        ("user", PROFILE),
        ("platform[]", "tiktok"),
        ("video", VIDEO_URL),
        ("async_upload", "true"),
        ("request_id", REQUEST_ID),
        ("external_id", EXTERNAL_ID),
    ]
    assert dict(parts[6:]) == {"title": "Caption", "tiktok_title": "Caption",
                               "privacy_level": "PUBLIC_TO_EVERYONE", "post_mode": "DIRECT_POST",
                               "disable_inbox_fallback": "true", "brand_organic_toggle": "true",
                               "is_aigc": "true"}
    # One platform per request, always.
    assert [n for n, _ in parts].count("platform[]") == 1


@pytest.mark.asyncio
async def test_multipart_is_forced_even_without_files_and_text_is_utf8(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, ASYNC_ACK))
    await _text(text="Ünïcode — 💡 line one\nline two", fields={})
    assert ("title", "Ünïcode — 💡 line one\nline two") in _parts(fake.last)
    assert b"filename" not in fake.last.content


@pytest.mark.asyncio
async def test_a_blank_external_id_is_omitted(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, ASYNC_ACK))
    await _text(external_id="   ")
    assert "external_id" not in [n for n, _ in _parts(fake.last)]


# ── 2xx answers ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_answer_is_normalised(monkeypatch, creds):
    usage = {"count": 3, "limit": 10, "last_reset": "2026-10-01T00:00:00Z"}
    result = {"success": True, "url": "https://www.tiktok.com/@caydex/video/1", "post_id": "1"}
    _install(monkeypatch, _answer(200, {"success": True, "results": {"tiktok": result}, "usage": usage}))
    res = await _video()
    assert res == {"mode": "sync", "request_id": None, "results": {"tiktok": result}, "usage": usage}


@pytest.mark.asyncio
async def test_sync_answer_with_a_failed_platform_is_still_sync(monkeypatch, creds):
    body = {"success": False, "request_id": REQUEST_ID,
            "results": {"tiktok": {"success": False, "error": "spam_risk_too_many_posts"}}}
    _install(monkeypatch, _answer(200, body))
    res = await _video()
    assert res["mode"] == "sync" and res["request_id"] == REQUEST_ID and res["usage"] is None
    assert res["results"]["tiktok"]["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("results", [
    {"instagram": {"success": True}},      # no entry for the requested platform
    {"tiktok": "ok"},                      # entry is not an object
    ["tiktok"],                            # not an object at all
])
async def test_sync_results_without_our_platform_are_ambiguous(monkeypatch, creds, results):
    _install(monkeypatch, _answer(200, {"success": True, "results": results}))
    with pytest.raises(upload_post.UploadPostAmbiguousError) as ei:
        await _video()
    assert type(ei.value) is upload_post.UploadPostAmbiguousError and ei.value.status == 200


@pytest.mark.asyncio
async def test_202_scheduled_answer(monkeypatch, creds):
    _install(monkeypatch, _answer(202, {"success": True, "job_id": "scheduler_job_456",
                                        "scheduled_date": "2026-10-02T10:00:00Z"}))
    assert await _video() == {"mode": "scheduled", "job_id": "scheduler_job_456"}


@pytest.mark.asyncio
async def test_async_ack_with_another_request_id_returns_theirs_and_warns(monkeypatch, creds, caplog):
    caplog.set_level(logging.WARNING, logger=upload_post.__name__)
    _install(monkeypatch, _answer(200, {**ASYNC_ACK, "request_id": "server-chosen-1"}))
    assert await _text() == {"mode": "async", "request_id": "server-chosen-1"}
    assert any("server-chosen-1" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body", [
    (200, b""),
    (200, b"<html>ok</html>"),
    (200, ["not", "an", "object"]),
    (200, {"success": False, "message": "hmm"}),
    (200, {"success": True}),                                  # no request_id / results / job_id
    (200, {"success": True, "request_id": ""}),
    (201, {"message": "created?"}),
    (202, {"success": True}),                                  # scheduled without a job_id
    (204, None),
])
async def test_unreadable_or_unexpected_2xx_is_ambiguous(monkeypatch, creds, status, body):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(upload_post.UploadPostAmbiguousError) as ei:
        await _text()
    assert type(ei.value) is upload_post.UploadPostAmbiguousError
    assert ei.value.status == status


# ── error answers ─────────────────────────────────────────────────────


_NOT_CONNECTED = {"success": False,
                  "message": "None of the requested platforms are valid for profile \"caydex\".",
                  "invalid_platforms": {"linkedin": "Profile caydex has no Linkedin account configured"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body, expected", [
    (400, _NOT_CONNECTED, upload_post.UploadPostNotConnectedError),
    (400, {"success": False, "message": "Missing user", "invalid_platforms": {}}, upload_post.UploadPostRefusedError),
    (400, {"success": False, "message": "Missing user"}, upload_post.UploadPostRefusedError),
    (401, {"success": False, "message": "Invalid or expired token"}, upload_post.UploadPostAuthError),
    (403, {"success": False, "message": "TikTok uploads are not available on the Free plan."},
     upload_post.UploadPostPlanError),
    (404, {"success": False, "message": "User not found"}, upload_post.UploadPostRefusedError),
    (422, None, upload_post.UploadPostRefusedError),
    (429, {"success": False, "message": "This upload would exceed your monthly limit.",
           "usage": {"count": 10, "limit": 10, "last_reset": "2026-09-01T00:00:00Z"}},
     upload_post.UploadPostQuotaError),
    (429, {"success": False, "message": "Post verification failed", "violations": [
        {"platform": "tiktok", "type": "hard_cap", "message": "Daily cap reached for tiktok: 15/15 in last 24h",
         "used_last_24h": 15, "cap": 15}]}, upload_post.UploadPostRateLimitError),
    (429, {"success": False, "message": "Too many requests"}, upload_post.UploadPostRateLimitError),
    (429, None, upload_post.UploadPostRateLimitError),
    (400, {"success": False, "error_code": "account_reauth_required", "failure_stage": "precheck",
           "message": "Reconnect"}, upload_post.UploadPostReauthError),
    (403, {"success": False, "error_code": "account_checkpoint_required"}, upload_post.UploadPostReauthError),
    (500, {"success": False, "error_code": "ACCOUNT_REAUTH_REQUIRED"}, upload_post.UploadPostReauthError),
    (401, {"success": False, "reauth_required": True,
           "message": "Your TikTok session has expired."}, upload_post.UploadPostReauthError),
    (400, {"success": False, "error_code": "tiktok_reconnect_required"}, upload_post.UploadPostReauthError),
    (400, {"success": False, "error_code": "account_restricted", "restriction_reason": "spam_risk_too_many_posts",
           "retry_after_seconds": 3600}, upload_post.UploadPostRateLimitError),
    (500, {"success": False, "error": "Detailed error message"}, upload_post.UploadPostAmbiguousError),
    (502, b"<html>bad gateway</html>", upload_post.UploadPostAmbiguousError),
    (503, {"success": False, "error_code": "reddit_unavailable"}, upload_post.UploadPostAmbiguousError),
    (408, None, upload_post.UploadPostAmbiguousError),
    (301, None, upload_post.UploadPostAmbiguousError),
])
async def test_status_mapping(monkeypatch, creds, status, body, expected):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(upload_post.UploadPostException) as ei:
        await _video()
    assert type(ei.value) is expected, (status, body, type(ei.value).__name__)
    assert ei.value.status == status and ei.value.method == "upload_video"
    assert f"HTTP {status}" in str(ei.value)


@pytest.mark.asyncio
async def test_the_detail_and_error_code_ride_on_the_exception(monkeypatch, creds):
    _install(monkeypatch, _answer(400, {"success": False, "error_code": "profile_platform_mapping_invalid",
                                        "message": "Profile caydex has no TikTok account configured"}))
    with pytest.raises(upload_post.UploadPostRefusedError) as ei:
        await _video()
    e = ei.value
    assert e.error_code == "profile_platform_mapping_invalid"
    assert e.detail == "Profile caydex has no TikTok account configured"
    assert "profile_platform_mapping_invalid" in str(e) and "no TikTok account" in str(e)


@pytest.mark.asyncio
async def test_not_connected_names_the_platform(monkeypatch, creds):
    _install(monkeypatch, _answer(400, _NOT_CONNECTED))
    with pytest.raises(upload_post.UploadPostNotConnectedError) as ei:
        await _text(platform="linkedin", fields={"target_linkedin_page_id": "1"})
    assert "linkedin: Profile caydex has no Linkedin account configured" in str(ei.value)


@pytest.mark.asyncio
async def test_quota_carries_the_usage(monkeypatch, creds):
    _install(monkeypatch, _answer(429, {"success": False, "message": "This upload would exceed your monthly limit.",
                                        "usage": {"count": 10, "limit": 10, "last_reset": "x"}},
                                  {"X-RateLimit-Reset": str(int(time.time()) + 30)}))
    with pytest.raises(upload_post.UploadPostQuotaError) as ei:
        await _text()
    assert ei.value.usage == {"count": 10, "limit": 10}
    assert "usage 10/10" in str(ei.value)
    assert isinstance(ei.value, upload_post.UploadPostRefusedError)


@pytest.mark.asyncio
async def test_429_rate_limit_reads_x_ratelimit_reset_as_a_unix_timestamp(monkeypatch, creds):
    reset = int(time.time()) + 45
    _install(monkeypatch, _answer(429, {"success": False}, {"X-RateLimit-Limit": "60",
                                                           "X-RateLimit-Remaining": "0",
                                                           "X-RateLimit-Reset": str(reset)}))
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _text()
    assert ei.value.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers, expect_seconds", [
    ({"X-RateLimit-Reset": "42"}, 42),                                   # a delta, not an epoch
    ("EPOCH_MS_PLUS_90", 90),                                            # epoch milliseconds (built at run time)
    ({"X-RateLimit-Reset": "1"}, 1),
    ({"X-RateLimit-Reset": "garbage"}, None),
    ({"X-RateLimit-Reset": "-5"}, None),
    ({"X-RateLimit-Reset": str(int(time.time()) + 90 * 86400)}, None),   # > 31 days out: junk
    ({"X-RateLimit-Reset": "garbage", "Retry-After": "120"}, 120),
    ({"Retry-After": "300"}, 300),
    ({"Retry-After": str(10 ** 12)}, None),
    ({}, None),
])
async def test_429_retry_at_fallbacks(monkeypatch, creds, headers, expect_seconds):
    if headers == "EPOCH_MS_PLUS_90":   # an absolute time must be computed when the test RUNS
        headers = {"X-RateLimit-Reset": str((int(time.time()) + 90) * 1000)}
    _install(monkeypatch, _answer(429, {"success": False}, headers))
    before = datetime.now(timezone.utc)
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _text()
    if expect_seconds is None:
        assert ei.value.retry_at is None
    else:
        delta = (ei.value.retry_at - before).total_seconds()
        assert expect_seconds - 2 <= delta <= expect_seconds + 2, delta


@pytest.mark.asyncio
async def test_a_past_reset_means_now(monkeypatch, creds):
    _install(monkeypatch, _answer(429, {"success": False}, {"X-RateLimit-Reset": str(int(time.time()) - 600)}))
    before = datetime.now(timezone.utc)
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _text()
    assert before <= ei.value.retry_at <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_429_retry_after_http_date(monkeypatch, creds):
    when = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0)
    _install(monkeypatch, _answer(429, None, {"Retry-After": format_datetime(when, usegmt=True)}))
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _text()
    assert ei.value.retry_at == when


@pytest.mark.asyncio
async def test_a_daily_cap_ignores_the_per_minute_reset(monkeypatch, creds):
    # A rolling 24 h cap: the per-minute X-RateLimit-Reset would send the post straight back into it.
    _install(monkeypatch, _answer(429, {"success": False, "message": "Post verification failed",
                                        "violations": [{"platform": "x", "type": "hard_cap",
                                                        "message": "Daily cap reached for x: 10/10 in last 24h"}]},
                                  {"X-RateLimit-Reset": str(int(time.time()) + 30)}))
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _text()
    assert ei.value.retry_at is None
    assert "Daily cap reached for x: 10/10" in str(ei.value)


@pytest.mark.asyncio
async def test_account_restricted_retry_after_seconds(monkeypatch, creds):
    _install(monkeypatch, _answer(400, {"success": False, "error_code": "account_restricted",
                                        "failure_stage": "precheck", "restriction_reason": "publish_limit",
                                        "restricted_until": "2099-01-01T00:00:00Z", "retry_after_seconds": 1800},
                                  {"X-RateLimit-Reset": str(int(time.time()) + 30)}))
    before = datetime.now(timezone.utc)
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _video(platform="instagram")
    delta = (ei.value.retry_at - before).total_seconds()
    assert 1798 <= delta <= 1802
    assert ei.value.error_code == "account_restricted" and "publish_limit" in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{iso}", "{iso_naive}", "{epoch}"])
async def test_account_restricted_restricted_until(monkeypatch, creds, raw):
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    value: Any = raw.format(iso=until.isoformat().replace("+00:00", "Z"),
                            iso_naive=until.replace(tzinfo=None).isoformat(),
                            epoch=int(until.timestamp()))
    if raw == "{epoch}":
        value = int(value)
    _install(monkeypatch, _answer(403, {"success": False, "error_code": "account_restricted",
                                        "restricted_until": value}))
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _video(platform="instagram")
    assert ei.value.retry_at == until


@pytest.mark.asyncio
@pytest.mark.parametrize("until", ["not a date", "", 5, "2999-01-01T00:00:00Z"])
async def test_account_restricted_with_an_unusable_until_has_no_retry_at(monkeypatch, creds, until):
    # And never borrows the per-minute window reset for a parked account.
    _install(monkeypatch, _answer(400, {"success": False, "error_code": "account_restricted",
                                        "restricted_until": until},
                                  {"X-RateLimit-Reset": str(int(time.time()) + 30)}))
    with pytest.raises(upload_post.UploadPostRateLimitError) as ei:
        await _video()
    assert ei.value.retry_at is None


def test_the_outcome_families_are_disjoint():
    refused = upload_post.UploadPostRefusedError
    for cls in (upload_post.UploadPostAuthError, upload_post.UploadPostPlanError, upload_post.UploadPostQuotaError,
                upload_post.UploadPostNotConnectedError, upload_post.UploadPostReauthError):
        assert issubclass(cls, refused)
    unknown = (upload_post.UploadPostNotSentError, upload_post.UploadPostAmbiguousError,
               upload_post.UploadPostRateLimitError, upload_post.UploadPostNotConfiguredError)
    for cls in unknown:
        assert not issubclass(cls, refused) and not issubclass(refused, cls)
    for a in unknown:
        for b in unknown:
            assert a is b or not issubclass(a, b)


# ── transport ─────────────────────────────────────────────────────────


NOT_SENT = [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.InvalidURL,
            httpx.UnsupportedProtocol, httpx.LocalProtocolError]
MAYBE_SENT = [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError,
              httpx.WriteError, httpx.DecodingError, httpx.ProxyError]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", NOT_SENT)
async def test_transport_errors_that_never_left_are_not_sent(monkeypatch, creds, exc_type):
    _install(monkeypatch, _raising(exc_type))
    with pytest.raises(upload_post.UploadPostNotSentError) as ei:
        await _video()
    assert exc_type.__name__ in str(ei.value)
    # Raised OUTSIDE the except block: no cause AND no context (the httpx exception holds the
    # request, Authorization header included).
    assert ei.value.__cause__ is None and ei.value.__context__ is None
    assert API_KEY not in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", MAYBE_SENT)
async def test_transport_errors_after_sending_are_ambiguous(monkeypatch, creds, exc_type):
    _install(monkeypatch, _raising(exc_type))
    with pytest.raises(upload_post.UploadPostAmbiguousError) as ei:
        await _video()
    assert type(ei.value) is upload_post.UploadPostAmbiguousError and ei.value.status is None
    assert ei.value.__cause__ is None and ei.value.__context__ is None
    assert API_KEY not in str(ei.value)


def test_not_sent_list_is_caught_before_its_httpx_parents():
    # ConnectTimeout / PoolTimeout ARE TimeoutExceptions and every class is an HTTPError: a broader
    # clause first would turn a never-sent request into an ambiguous one.
    for exc_type in NOT_SENT:
        assert exc_type in upload_post._NOT_SENT_ERRORS
    for exc_type in MAYBE_SENT:
        assert not issubclass(exc_type, upload_post._NOT_SENT_ERRORS), exc_type


@pytest.mark.asyncio
async def test_a_non_httpx_error_after_sending_began_is_ambiguous(monkeypatch, creds):
    def boom(_request):
        raise RuntimeError("transport exploded mid-response")
    _install(monkeypatch, boom)
    with pytest.raises(upload_post.UploadPostAmbiguousError) as ei:
        await _text()
    assert type(ei.value) is upload_post.UploadPostAmbiguousError and ei.value.__context__ is None


@pytest.mark.asyncio
async def test_a_closed_client_is_not_sent(monkeypatch, creds):
    fake = FakeUploadPost(_answer(200, ASYNC_ACK))
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    await client.aclose()
    monkeypatch.setattr(upload_post, "_client", client)
    with pytest.raises(upload_post.UploadPostNotSentError) as ei:
        await _text()
    assert not fake.requests and ei.value.__context__ is None


# ── get_status ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_200_is_normalised(monkeypatch, creds):
    body = {"request_id": REQUEST_ID, "external_id": EXTERNAL_ID, "status": "In_Progress", "completed": 1,
            "total": 2, "results": [{"platform": "tiktok", "success": True, "message": "Queued",
                                     "upload_timestamp": "2026-10-01T12:00:00Z", "status": "processing"}],
            "last_update": "2026-10-01T12:00:01Z"}
    fake = _install(monkeypatch, _answer(200, body))
    res = await upload_post.get_status(REQUEST_ID)
    assert res == {"request_id": REQUEST_ID, "external_id": EXTERNAL_ID, "status": "in_progress",
                   "completed": 1, "total": 2, "results": body["results"],
                   "last_update": "2026-10-01T12:00:01Z", "message": None}
    req = fake.last
    assert req.method == "GET" and req.url.path == "/api/uploadposts/status"
    assert dict(req.url.params) == {"request_id": REQUEST_ID}
    assert req.headers["authorization"] == f"Apikey {API_KEY}"
    assert "idempotency-key" not in req.headers


@pytest.mark.asyncio
async def test_status_failed_without_results(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"request_id": REQUEST_ID, "status": "failed", "completed": 0, "total": 1,
                                        "message": "Upload appears to have failed (no activity for over 1 hour)"}))
    res = await upload_post.get_status(REQUEST_ID)
    assert res["status"] == "failed" and res["results"] == [] and res["external_id"] is None
    assert res["message"].startswith("Upload appears to have failed")


@pytest.mark.asyncio
async def test_status_404_not_found_is_an_answer_not_an_exception(monkeypatch, creds):
    _install(monkeypatch, _answer(404, {"request_id": REQUEST_ID, "status": "not_found",
                                        "message": "No upload request found with this ID"}))
    assert await upload_post.get_status(REQUEST_ID) == {"status": "not_found", "request_id": REQUEST_ID}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, b"<html>Not Found</html>", {"success": False, "message": "Route not found"},
                                  {"status": "pending"}])
async def test_status_404_without_not_found_is_not_absent(monkeypatch, creds, body):
    # "absent" licenses a resend: a proxy 404 or a wrong path must not read as "no such job".
    _install(monkeypatch, _answer(404, body))
    with pytest.raises(upload_post.UploadPostRefusedError) as ei:
        await upload_post.get_status(REQUEST_ID)
    assert ei.value.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body", [
    (500, {"error": "boom"}),
    (503, None),
    (200, b"not json"),
    (200, {"request_id": REQUEST_ID}),                       # no status
    (200, {"status": "completed", "results": "nope"}),
    (200, {"status": "completed", "results": ["nope"]}),
])
async def test_status_failures_are_ambiguous(monkeypatch, creds, status, body):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(upload_post.UploadPostAmbiguousError):
        await upload_post.get_status(REQUEST_ID)


# ── get_history ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_is_parsed_and_the_account_email_dropped(monkeypatch, creds):
    item = {"user_email": "owner@example.com", "profile_username": PROFILE, "platform": "tiktok",
            "success": True, "platform_post_id": ["7311"], "post_url": "https://www.tiktok.com/@caydex/video/7311",
            "error_message": None, "fallback_to_inbox": False, "request_id": REQUEST_ID, "external_id": EXTERNAL_ID}
    fake = _install(monkeypatch, _answer(200, {"history": [item], "in_progress": [], "total": 1, "page": 1,
                                               "limit": 10}))
    res = await upload_post.get_history(REQUEST_ID)
    expected = {k: v for k, v in item.items() if k != "user_email"}
    assert res == {"history": [expected], "in_progress": []}
    req = fake.last
    assert req.method == "GET" and req.url.path == "/api/uploadposts/history"
    assert dict(req.url.params) == {"request_id": REQUEST_ID}


@pytest.mark.asyncio
async def test_history_drops_rows_that_name_another_request(monkeypatch, creds, caplog):
    # The OpenAPI spec lists only page/limit for this endpoint: if the request_id filter were ever
    # ignored, the account's latest rows (other posts on the same platform) must not pass for ours.
    caplog.set_level(logging.WARNING, logger=upload_post.__name__)
    ours = {"platform": "tiktok", "success": True, "request_id": REQUEST_ID, "post_url": "u1"}
    unlabelled = {"platform": "tiktok", "success": True, "post_url": "u2"}
    theirs = {"platform": "tiktok", "success": True, "request_id": "2026-09-30:tiktok:video:a1", "post_url": "u3"}
    _install(monkeypatch, _answer(200, {"history": [theirs, ours, unlabelled], "in_progress": [theirs]}))
    res = await upload_post.get_history(REQUEST_ID)
    # An UNLABELLED row is dropped too (review 2026-10-01): it could be another post on the same
    # platform, whose id a later retract would unpublish.
    assert res == {"history": [ours], "in_progress": []}
    assert any("filter looks ignored" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"history": None}, {"total": 0, "page": 1, "limit": 10}])
async def test_history_missing_lists_are_empty(monkeypatch, creds, body):
    _install(monkeypatch, _answer(200, body))
    assert await upload_post.get_history(REQUEST_ID) == {"history": [], "in_progress": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body", [
    (200, {"history": "nope"}),
    (200, {"history": [{"platform": "x"}, "junk"]}),
    (200, {"history": [], "in_progress": {"platform": "x"}}),
    (200, b""),
    (500, {"error": "Failed to retrieve upload history"}),
])
async def test_history_malformed_is_ambiguous_not_silently_skipped(monkeypatch, creds, status, body):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(upload_post.UploadPostAmbiguousError):
        await upload_post.get_history(REQUEST_ID)


# ── unpublish ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unpublish_sends_json_and_reads_success(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, {"success": True, "message": "Post deleted successfully."}))
    res = await upload_post.unpublish(platform="youtube", post_id="dQw4w9WgXcQ")
    assert res == {"unpublished": True, "message": "Post deleted successfully."}
    req = fake.last
    assert req.method == "POST" and str(req.url) == f"{BASE}/uploadposts/posts/unpublish"
    assert req.headers["content-type"] == "application/json"
    assert json.loads(req.content) == {"platform": "youtube", "user": PROFILE, "post_id": "dQw4w9WgXcQ"}
    assert req.headers["authorization"] == f"Apikey {API_KEY}"


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body, expected", [
    (400, {"success": False, "error_code": "platform_not_supported",
           "message": "'tiktok' does not support deletion"}, upload_post.UploadPostRefusedError),
    (404, {"success": False, "message": "No post found for the given post_id."}, upload_post.UploadPostRefusedError),
    (403, {"success": False, "message": "Not authorized to delete the post."}, upload_post.UploadPostPlanError),
    (500, {"success": False, "error": "x"}, upload_post.UploadPostAmbiguousError),
    (200, {"success": False, "message": "?"}, upload_post.UploadPostAmbiguousError),
    (200, b"", upload_post.UploadPostAmbiguousError),
])
async def test_unpublish_failures(monkeypatch, creds, status, body, expected):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(upload_post.UploadPostException) as ei:
        await upload_post.unpublish(platform="tiktok", post_id="7311")
    assert type(ei.value) is expected
    if status == 400:
        assert ei.value.error_code == "platform_not_supported"


# ── get_usage ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("body, expected", [
    ({"success": True, "email": "owner@example.com", "plan": "Default",
      "api_usage": {"count": 3, "limit": 10}}, {"count": 3, "limit": 10}),
    ({"success": True, "api_usage": {"count": "7", "limit": "10"}}, {"count": 7, "limit": 10}),
    ({"success": True, "api_usage": {"count": 0}}, {"count": 0, "limit": None}),
    ({"success": True, "usage": {"count": 4, "limit": 100, "last_reset": "x"}}, {"count": 4, "limit": 100}),
    ({"success": True, "api_usage": {"count": 2, "limit": "unlimited"}}, {"count": 2, "limit": None}),
    ({"success": True, "email": "owner@example.com", "plan": "Default"}, None),
    ({"success": True, "api_usage": None}, None),
    ({"success": True, "api_usage": {"count": True}}, None),
    ({"success": True, "api_usage": {"count": -1}}, None),
    ({"success": True, "api_usage": {"count": "many"}}, None),
    ({"success": True, "api_usage": [3, 10]}, None),
])
async def test_get_usage_is_tolerant(monkeypatch, creds, body, expected):
    fake = _install(monkeypatch, _answer(200, body))
    assert await upload_post.get_usage() == expected
    assert fake.last.method == "GET" and fake.last.url.path == "/api/uploadposts/me"


@pytest.mark.asyncio
async def test_get_usage_request_failures_still_raise(monkeypatch, creds):
    _install(monkeypatch, _answer(401, {"success": False, "message": "Invalid or expired token"}))
    with pytest.raises(upload_post.UploadPostAuthError):
        await upload_post.get_usage()
    _install(monkeypatch, _answer(200, b"[1, 2]"))
    with pytest.raises(upload_post.UploadPostAmbiguousError):
        await upload_post.get_usage()


# ── refusals before anything is sent ──────────────────────────────────


_CALLS = {
    "upload_video": lambda: _video(),
    "upload_text": lambda: _text(),
    "get_status": lambda: upload_post.get_status(REQUEST_ID),
    "get_history": lambda: upload_post.get_history(REQUEST_ID),
    "unpublish": lambda: upload_post.unpublish(platform="youtube", post_id="abc"),
    "get_usage": lambda: upload_post.get_usage(),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("call", sorted(_CALLS))
@pytest.mark.parametrize("key, profile, missing", [
    (None, PROFILE, "MARKETING_UPLOAD_POST_API_KEY"),
    ("   ", PROFILE, "MARKETING_UPLOAD_POST_API_KEY"),
    (API_KEY, None, "MARKETING_UPLOAD_POST_USER"),
    (API_KEY, "", "MARKETING_UPLOAD_POST_USER"),
])
async def test_not_configured_never_sends(monkeypatch, call, key, profile, missing):
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_API_KEY", key)
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_USER", profile)
    fake = _install(monkeypatch, _answer(200, ASYNC_ACK))
    with pytest.raises(upload_post.UploadPostNotConfiguredError) as ei:
        await _CALLS[call]()
    assert not fake.requests
    assert missing in str(ei.value) and API_KEY not in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda: _text(text=""),
    lambda: _text(text="   \n"),
    lambda: _text(text=None),
    lambda: _text(text="hi \ud83d"),                                     # lone surrogate: not UTF-8
    lambda: _text(platform=""),
    lambda: _text(platform="Face Book"),
    lambda: _text(platform="tiktok\n"),
    lambda: _text(request_id=""),
    lambda: _text(request_id=" " + REQUEST_ID),                          # never silently trimmed
    lambda: _text(request_id="a" * 201),
    lambda: _text(request_id="id\r\nX-Evil: 1"),
    lambda: _text(external_id="e" * 201),
    lambda: _text(external_id="bad\x00id"),
    lambda: _text(fields={"user": "someone-else"}),
    lambda: _text(fields={"title": "a second text"}),
    lambda: _text(fields={"platform[]": "x"}),
    lambda: _text(fields={"async_upload": False}),
    lambda: _text(fields={"request_id": "other"}),
    lambda: _text(fields={"nested": {"a": 1}}),
    lambda: _text(fields={"lst": [["a"]]}),
    lambda: _text(fields={"n": float("nan")}),
    lambda: _text(fields={"bad name": "v"}),
    lambda: _text(fields={'q"uote': "v"}),
    lambda: _text(fields=[("a", "b")]),
    lambda: _video(fields={"video": "https://elsewhere.example/v.mp4"}),
    lambda: _video(video_url="http://insecure.example/v.mp4"),
    lambda: _video(video_url="https://"),
    lambda: _video(video_url="https://a.example/v .mp4"),
    lambda: _video(video_url=None),
    lambda: upload_post.get_status(""),
    lambda: upload_post.get_history("x" * 201),
    lambda: upload_post.unpublish(platform="youtube", post_id=""),
    lambda: upload_post.unpublish(platform="youtube", post_id="a\nb"),
    lambda: upload_post.unpublish(platform="", post_id="abc"),
])
async def test_bad_arguments_are_refused_without_sending(monkeypatch, creds, call):
    fake = _install(monkeypatch, _answer(200, ASYNC_ACK))
    with pytest.raises(upload_post.UploadPostRefusedError) as ei:
        await call()
    assert type(ei.value) is upload_post.UploadPostRefusedError
    assert ei.value.status is None and "not sent" in str(ei.value)
    assert ei.value.__context__ is None and ei.value.__cause__ is None
    assert not fake.requests


# ── secrets ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_api_key_in_any_exception_or_log(monkeypatch, creds, caplog):
    caplog.set_level(logging.DEBUG)
    seen_headers: List[str] = []

    def echo(status: int, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
        def respond(request: httpx.Request) -> httpx.Response:
            seen_headers.append(request.headers["Authorization"])
            # A hostile/buggy upstream echoing the key and the header back everywhere.
            leak = f"{API_KEY} {request.headers['Authorization']}"
            filled = json.loads(json.dumps(body).replace("LEAK", leak))
            return httpx.Response(status, json=filled, headers=headers)
        return respond

    def raise_with_leak(exc_type: type):
        def respond(request: httpx.Request) -> httpx.Response:
            seen_headers.append(request.headers["Authorization"])
            raise exc_type(f"{API_KEY} {request.headers['Authorization']}", request=request)
        return respond

    scenarios = [
        echo(400, {"success": False, "message": "bad LEAK", "error_code": "LEAK"}),
        echo(400, {"success": False, "message": "LEAK", "invalid_platforms": {"LEAK": "LEAK"}}),
        echo(401, {"success": False, "message": "Invalid or expired token LEAK"}),
        echo(403, {"success": False, "error": "LEAK"}),
        echo(404, {"success": False, "message": "LEAK"}),
        echo(429, {"success": False, "message": "LEAK", "usage": {"count": 1, "limit": 1}}),
        echo(429, {"success": False, "violations": [{"message": "LEAK"}]}, {"X-RateLimit-Reset": "LEAK"}),
        echo(400, {"success": False, "error_code": "account_restricted", "restriction_reason": "LEAK",
                   "restricted_until": "LEAK", "message": "LEAK"}),
        echo(400, {"success": False, "error_code": "account_reauth_required", "message": "LEAK"}),
        echo(500, {"success": False, "error": "LEAK"}),
        echo(200, {"success": True, "results": {"LEAK": {}}}),
        raise_with_leak(httpx.ConnectError),
        raise_with_leak(httpx.ReadTimeout),
        raise_with_leak(httpx.RemoteProtocolError),
    ]
    raised: List[BaseException] = []
    for respond in scenarios:
        _install(monkeypatch, respond)
        for call in (_video, _text, lambda: upload_post.get_status(REQUEST_ID),
                     lambda: upload_post.get_history(REQUEST_ID),
                     lambda: upload_post.unpublish(platform="youtube", post_id="abc"),
                     upload_post.get_usage):
            try:
                await call()
            except upload_post.UploadPostException as e:
                raised.append(e)

    assert len(raised) >= 60
    assert len(seen_headers) >= 60
    texts = [caplog.text]
    for e in raised:
        texts += [str(e), repr(e), repr(vars(e)), repr(e.args)]
    blob = "\n".join(texts)
    assert API_KEY not in blob
    for header in set(seen_headers):
        assert header not in blob
    # The redaction marker proves the scenarios really echoed something to redact.
    assert "<redacted>" in blob


def test_the_key_never_renders_in_a_frame_dump(creds):
    key, _profile = upload_post._credentials("upload_text")
    assert API_KEY not in repr(key) and API_KEY not in str(key)
    assert key.value == API_KEY


# ── lifecycle + error mapping ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_upload_post_client_is_idempotent(monkeypatch):
    monkeypatch.setattr(upload_post, "_client", None)
    client = upload_post._get_client()
    assert upload_post._get_client() is client
    assert client.follow_redirects is False
    await upload_post.close_upload_post_client()
    assert upload_post._client is None and client.is_closed
    await upload_post.close_upload_post_client()
    assert upload_post._client is None


def _exception_classes() -> List[type]:
    return [obj for _, obj in inspect.getmembers(upload_post, inspect.isclass)
            if issubclass(obj, upload_post.UploadPostException) and obj.__module__ == upload_post.__name__]


_EXPECTED_STATUS = {
    "UploadPostException": 503,
    "UploadPostNotSentError": 503,
    "UploadPostAmbiguousError": 503,
    "UploadPostRateLimitError": 503,
    "UploadPostNotConfiguredError": 502,
    "UploadPostRefusedError": 502,
    "UploadPostAuthError": 502,
    "UploadPostPlanError": 502,
    "UploadPostQuotaError": 502,
    "UploadPostNotConnectedError": 502,
    "UploadPostReauthError": 502,
}


def test_every_exception_class_classifies_as_publisher_unavailable():
    classes = _exception_classes()
    assert {c.__name__ for c in classes} == set(_EXPECTED_STATUS)
    for cls in classes:
        # A 429 / timeout message must not be read as the market-data provider.
        for exc in (cls("x"), cls(), cls("upload-post upload_video: HTTP 429 timed out", method="upload_video",
                                         status=429)):
            code, status = classify_exception(exc)
            assert code == ErrorCode.MARKETING_PUBLISHER_UNAVAILABLE, cls.__name__
            assert status == _EXPECTED_STATUS[cls.__name__], cls.__name__
        e = cls("m", method="upload_text", status=400, error_code="c", detail="d")
        assert (e.method, e.status, e.error_code, e.detail) == ("upload_text", 400, "c", "d")
    assert upload_post.UploadPostRateLimitError("x").retry_at is None
    assert upload_post.UploadPostQuotaError("x").usage is None



# ── review 2026-10-01 follow-ups ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"message": "Post not found"}, {"success": "false"}, {"success": None}])
async def test_unpublish_needs_an_explicit_success(monkeypatch, creds, body):
    _install(monkeypatch, _answer(200, body))
    with pytest.raises(upload_post.UploadPostAmbiguousError):
        await upload_post.unpublish(platform="youtube", post_id="vid123")


@pytest.mark.asyncio
async def test_a_409_is_ambiguous_not_refused(monkeypatch, creds):
    _install(monkeypatch, _answer(409, {"success": False, "message": "A request with this key is in progress"}))
    with pytest.raises(upload_post.UploadPostAmbiguousError):
        await upload_post.upload_text(platform="threads", text="t", fields={}, request_id=REQUEST_ID,
                                      external_id="ext-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_key", ["UPkeyAAAA1111\nSECRETTAILzzzz9999", "UPkey AAAA", "UPkey\x00AAAA"])
async def test_a_key_with_whitespace_or_control_characters_is_not_configured(monkeypatch, creds, bad_key):
    seen = []
    _install(monkeypatch, lambda request: seen.append(request) or httpx.Response(200, json={}))
    monkeypatch.setattr(upload_post.settings, "MARKETING_UPLOAD_POST_API_KEY", bad_key)
    with pytest.raises(upload_post.UploadPostNotConfiguredError) as ei:
        await upload_post.upload_text(platform="threads", text="t", fields={}, request_id=REQUEST_ID,
                                      external_id="ext-1")
    assert seen == [] and "SECRETTAIL" not in str(ei.value) and "AAAA" not in str(ei.value)


@pytest.mark.asyncio
async def test_an_answered_request_id_that_is_not_an_id_is_ambiguous(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"success": True, "request_id": "bad id\ninjected log line"}))
    with pytest.raises(upload_post.UploadPostAmbiguousError):
        await upload_post.upload_text(platform="threads", text="t", fields={}, request_id=REQUEST_ID,
                                      external_id="ext-1")


@pytest.mark.asyncio
async def test_status_results_never_carry_an_email(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"request_id": REQUEST_ID, "status": "completed",
                                        "results": [{"platform": "tiktok", "success": True,
                                                     "user_email": "owner@example.com"}]}))
    res = await upload_post.get_status(REQUEST_ID)
    assert all("user_email" not in r for r in res["results"])
