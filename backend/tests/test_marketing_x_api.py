"""The X API v2 client (`app/integrations/x_api.py`) — signing, the outcome split, secrets.

Hermetic: X is an `httpx.MockTransport` installed on the module's `_client`; credentials are set
with monkeypatch on `settings`. Transport failures are simulated by raising the httpx exception
inside the handler, exactly where a real transport would raise it.

What matters most is the OUTCOME SPLIT the publisher codes against: POST /2/tweets has no
idempotency key, so a failure that provably never left (`XApiNotSentError`) may be resent, while
one that may have reached X (`XApiAmbiguousError`, incl. the duplicate-content 403) must be
reconciled — confusing the two either double-posts or strands a post.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, unquote

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.integrations import x_api

CK = "ckTESTconsumerKEY0001aaaaaaaa"
CS = "csTESTconsumerSECRET0002bbbbbbbbbbbbbbbbbbbbbb"
AT = "1234567890-TESTaccessTOKEN0003cccccccccccccccccc"
ATS = "atsTESTaccessSECRET0004dddddddddddddddddddddd"
_CREDS = {
    "MARKETING_X_CONSUMER_KEY": CK,
    "MARKETING_X_CONSUMER_SECRET": CS,
    "MARKETING_X_ACCESS_TOKEN": AT,
    "MARKETING_X_ACCESS_TOKEN_SECRET": ATS,
}

# The OAuth 1.0a worked example (Twitter's original page; docs.x.com shows the same inputs on
# api.x.com — both pinned below).
V_CONSUMER_KEY = "xvz1evFS4wEEPTGEFPHBog"
V_CONSUMER_SECRET = "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw"
V_TOKEN = "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"
V_TOKEN_SECRET = "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"
V_NONCE = "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"
V_TIMESTAMP = 1318622958
V_QUERY = {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"}
V_PARAMS = {
    **V_QUERY,
    "oauth_consumer_key": V_CONSUMER_KEY,
    "oauth_nonce": V_NONCE,
    "oauth_signature_method": "HMAC-SHA1",
    "oauth_timestamp": str(V_TIMESTAMP),
    "oauth_token": V_TOKEN,
    "oauth_version": "1.0",
}
V_BASE_STRING = (
    "POST&https%3A%2F%2Fapi.twitter.com%2F1.1%2Fstatuses%2Fupdate.json&include_entities%3Dtrue"
    "%26oauth_consumer_key%3Dxvz1evFS4wEEPTGEFPHBog%26oauth_nonce%3DkYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTg"
    "mZeNu2VS4cg%26oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D1318622958%26oauth_token"
    "%3D370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb%26oauth_version%3D1.0%26status%3DHello"
    "%2520Ladies%2520%252B%2520Gentlemen%252C%2520a%2520signed%2520OAuth%2520request%2521"
)


# ── harness ────────────────────────────────────────────────────────────


class FakeX:
    """Records every request; answers with `respond(request)`."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def _install(monkeypatch, respond: Callable[[httpx.Request], httpx.Response]) -> FakeX:
    fake = FakeX(respond)
    monkeypatch.setattr(x_api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
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
        raise exc_type(f"simulated {exc_type.__name__} on {AT}", request=request)
    return respond


@pytest.fixture
def creds(monkeypatch):
    for name, value in _CREDS.items():
        monkeypatch.setattr(x_api.settings, name, value)


def _oauth_params(header: str) -> Dict[str, str]:
    assert header.startswith("OAuth ")
    out = {}
    for part in header[len("OAuth "):].split(", "):
        key, _, quoted = part.partition("=")
        assert quoted.startswith('"') and quoted.endswith('"'), part
        out[unquote(key)] = unquote(quoted[1:-1])
    return out


# ── signing ────────────────────────────────────────────────────────────


def test_signature_base_string_and_hmac_match_the_oauth_worked_example():
    base = x_api.signature_base_string("post", "https://api.twitter.com/1.1/statuses/update.json", V_PARAMS)
    assert base == V_BASE_STRING
    assert x_api.hmac_sha1_signature(base, V_CONSUMER_SECRET, V_TOKEN_SECRET) == "hCtSmYh+iHYCEqBWrE7C7hYmtUk="


def test_signature_matches_the_docs_x_com_example_on_api_x_com():
    # docs.x.com "Creating a signature" (read 2026-09-30) runs the same inputs on api.x.com.
    base = x_api.signature_base_string("POST", "https://api.x.com/1.1/statuses/update.json", V_PARAMS)
    assert x_api.hmac_sha1_signature(base, V_CONSUMER_SECRET, V_TOKEN_SECRET) == "Ls93hJiZbQ3akF3HF3x1Bz8/zU4="


def test_oauth1_authorization_header_is_exact():
    header = x_api.oauth1_authorization(
        "POST", "https://api.twitter.com/1.1/statuses/update.json", V_QUERY,
        consumer_key=V_CONSUMER_KEY, consumer_secret=V_CONSUMER_SECRET,
        token=V_TOKEN, token_secret=V_TOKEN_SECRET, nonce=V_NONCE, timestamp=V_TIMESTAMP,
    )
    assert header == (
        'OAuth oauth_consumer_key="xvz1evFS4wEEPTGEFPHBog", '
        'oauth_nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", '
        'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D", '
        'oauth_signature_method="HMAC-SHA1", oauth_timestamp="1318622958", '
        'oauth_token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", oauth_version="1.0"'
    )
    # The secrets key the HMAC; they never appear in the header.
    assert V_CONSUMER_SECRET not in header and V_TOKEN_SECRET not in header


def test_fresh_nonce_and_timestamp_per_header():
    kw = dict(consumer_key="k", consumer_secret="s", token="1-t", token_secret="ts")
    a = _oauth_params(x_api.oauth1_authorization("POST", "https://api.x.com/2/tweets", **kw))
    b = _oauth_params(x_api.oauth1_authorization("POST", "https://api.x.com/2/tweets", **kw))
    assert a["oauth_nonce"] != b["oauth_nonce"] and len(a["oauth_nonce"]) == 32
    assert abs(int(a["oauth_timestamp"]) - int(datetime.now(timezone.utc).timestamp())) < 60


@pytest.mark.parametrize("raw,encoded", [
    ("a b", "a%20b"), ("+", "%2B"), ("~-._", "~-._"), ("*", "%2A"), (":", "%3A"),
    ("/", "%2F"), ("é", "%C3%A9"), ("!", "%21"), (5, "5"),
])
def test_percent_encode_is_rfc3986(raw, encoded):
    assert x_api.percent_encode(raw) == encoded


def test_base_string_folds_a_url_query_and_drops_the_default_port():
    with_query = x_api.signature_base_string("GET", "HTTPS://API.X.COM:443/2/x?b=2", {"a": "1"})
    explicit = x_api.signature_base_string("GET", "https://api.x.com/2/x", {"a": "1", "b": "2"})
    assert with_query == explicit


@pytest.mark.asyncio
async def test_list_query_is_signed_and_sent_identically_encoded(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    start = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    await x_api.list_user_posts("1234567890", start_time=start, max_results=5)

    (req,) = fake.requests
    assert req.method == "GET"
    assert req.url.path == "/2/users/1234567890/tweets"
    # On the wire: RFC 3986, sorted — the colons of start_time are %3A, exactly as signed.
    assert req.url.query == b"max_results=5&start_time=2026-09-30T12%3A00%3A00Z&tweet.fields=created_at"
    sent = dict(parse_qsl(req.url.query.decode()))
    assert sent["start_time"] == "2026-09-30T12:00:00Z"

    oauth = _oauth_params(req.headers["Authorization"])
    assert oauth["oauth_consumer_key"] == CK and oauth["oauth_token"] == AT
    assert oauth["oauth_signature_method"] == "HMAC-SHA1" and oauth["oauth_version"] == "1.0"
    signature = oauth.pop("oauth_signature")
    base = x_api.signature_base_string("GET", "https://api.x.com/2/users/1234567890/tweets", {**sent, **oauth})
    assert x_api.hmac_sha1_signature(base, CS, ATS) == signature


@pytest.mark.asyncio
async def test_create_post_json_body_is_not_signed(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1850000000000000001", "text": "hi"}}))
    await x_api.create_post("hi")
    (req,) = fake.requests
    oauth = _oauth_params(req.headers["Authorization"])
    signature = oauth.pop("oauth_signature")
    base = x_api.signature_base_string("POST", "https://api.x.com/2/tweets", oauth)
    assert x_api.hmac_sha1_signature(base, CS, ATS) == signature
    assert req.url.query == b""


# ── create_post ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_201_returns_id_text_and_no_errors(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1850000000000000001", "text": "Hello",
                                                         "edit_history_post_ids": ["1850000000000000001"]}}))
    out = await x_api.create_post("Hello")
    assert out == {"id": "1850000000000000001", "text": "Hello", "errors": []}
    (req,) = fake.requests
    assert req.method == "POST" and str(req.url) == "https://api.x.com/2/tweets"
    assert json.loads(req.content) == {"text": "Hello"}  # no made_with_ai unless asked
    assert req.headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_made_with_ai_is_sent_only_when_true(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1", "text": "a"}}))
    await x_api.create_post("a", made_with_ai=True)
    await x_api.create_post("a", made_with_ai=False)
    assert json.loads(fake.requests[0].content) == {"text": "a", "made_with_ai": True}
    assert json.loads(fake.requests[1].content) == {"text": "a"}


@pytest.mark.asyncio
async def test_create_201_with_errors_returns_them_and_warns(monkeypatch, creds, caplog):
    errors = [{"title": "Partial", "detail": "something about a field", "type": "about:blank"}]
    _install(monkeypatch, _answer(201, {"data": {"id": "77", "text": "t"}, "errors": errors}))
    caplog.set_level(logging.WARNING, logger=x_api.__name__)
    out = await x_api.create_post("t")
    assert out == {"id": "77", "text": "t", "errors": errors}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == x_api.__name__]
    assert warnings and "77" in warnings[0].getMessage() and "Partial" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_create_without_returned_text_gives_empty_text(monkeypatch, creds):
    _install(monkeypatch, _answer(201, {"data": {"id": "77"}}))
    assert await x_api.create_post("t") == {"id": "77", "text": "", "errors": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (201, {}),
    (201, {"data": {}}),
    (201, {"data": {"id": ""}}),
    (201, {"data": {"id": None}}),
    (201, {"data": {"id": True}}),
    (201, {"data": {"id": "abc"}}),
    (201, {"data": [{"id": "1"}]}),
    (201, {"errors": [{"title": "x", "detail": "y"}]}),
    (201, b"not json"),
    (201, b"[1, 2]"),
    (200, {"data": {"text": "no id"}}),
    (204, None),
])
async def test_create_2xx_without_a_post_id_is_ambiguous(monkeypatch, creds, status, body):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await x_api.create_post("t")
    assert type(ei.value) is x_api.XApiAmbiguousError
    assert ei.value.status == status and ei.value.method == "create_post"


DUP_DETAIL = "You are not allowed to create a Tweet with duplicate content."
GENERIC_403 = "You are not permitted to perform this action."
CREDITS_TYPE = "https://api.x.com/2/problems/credits-depleted"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (400, {"title": "Invalid Request", "detail": "One or more parameters are invalid.",
           "type": "https://api.x.com/2/problems/invalid-request"}, x_api.XApiRefusedError),
    (404, {"title": "Not Found Error", "detail": "nope", "type": "https://api.x.com/2/problems/resource-not-found"},
     x_api.XApiRefusedError),
    (401, {"title": "Unauthorized", "detail": "Unauthorized", "type": "about:blank", "status": 401},
     x_api.XApiAuthError),
    (402, {"title": "Payment Required", "detail": "Your account has no credits.", "type": CREDITS_TYPE},
     x_api.XApiCreditsDepletedError),
    (402, None, x_api.XApiCreditsDepletedError),
    (403, {"title": "Forbidden", "detail": "Credits depleted", "type": CREDITS_TYPE}, x_api.XApiCreditsDepletedError),
    (400, {"errors": [{"type": CREDITS_TYPE, "title": "CreditsDepleted"}]}, x_api.XApiCreditsDepletedError),
    (403, {"title": "Forbidden", "detail": DUP_DETAIL, "type": "about:blank", "status": 403},
     x_api.XApiDuplicateContentError),
    (403, {"errors": [{"code": 187, "message": "Status is a duplicate. " + DUP_DETAIL}]},
     x_api.XApiDuplicateContentError),
    (403, {"title": "Forbidden", "detail": GENERIC_403, "type": "about:blank", "status": 403},
     x_api.XApiForbiddenError),
    (403, b"<html>blocked</html>", x_api.XApiForbiddenError),
    (500, {"title": "Internal Error"}, x_api.XApiAmbiguousError),
    (502, b"Bad Gateway", x_api.XApiAmbiguousError),
    (503, {"title": "Service Unavailable", "detail": "Over capacity"}, x_api.XApiAmbiguousError),
    (302, None, x_api.XApiAmbiguousError),
])
async def test_create_status_mapping(monkeypatch, creds, status, body, expected):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(x_api.XApiException) as ei:
        await x_api.create_post("t")
    e = ei.value
    assert type(e) is expected, (status, body, type(e))
    assert e.status == status and e.method == "create_post"
    assert f"HTTP {status}" in str(e)
    if isinstance(body, dict) and body.get("detail"):
        assert e.detail == body["detail"]
    if isinstance(body, dict) and body.get("type"):
        assert e.problem_type == body["type"]


def test_the_outcome_families_are_disjoint():
    # The publisher's except-order relies on this shape.
    assert issubclass(x_api.XApiDuplicateContentError, x_api.XApiAmbiguousError)
    assert not issubclass(x_api.XApiDuplicateContentError, x_api.XApiRefusedError)
    for cls in (x_api.XApiAuthError, x_api.XApiForbiddenError, x_api.XApiCreditsDepletedError):
        assert issubclass(cls, x_api.XApiRefusedError)
    for cls in (x_api.XApiNotSentError, x_api.XApiRateLimitError, x_api.XApiNotConfiguredError):
        assert not issubclass(cls, (x_api.XApiAmbiguousError, x_api.XApiRefusedError))


@pytest.mark.asyncio
async def test_429_retry_at_from_x_rate_limit_reset(monkeypatch, creds):
    reset = int((datetime.now(timezone.utc) + timedelta(minutes=12)).timestamp())
    _install(monkeypatch, _answer(429, {"title": "Too Many Requests", "detail": "Too Many Requests"},
                                  {"x-rate-limit-reset": str(reset), "x-rate-limit-remaining": "0",
                                   "retry-after": "5"}))
    with pytest.raises(x_api.XApiRateLimitError) as ei:
        await x_api.create_post("t")
    assert ei.value.status == 429
    assert ei.value.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)
    assert ei.value.retry_at.tzinfo is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,expect_seconds", [
    ({"retry-after": "120"}, 120),
    ({"x-rate-limit-reset": "garbage", "retry-after": "30"}, 30),
    ({"x-rate-limit-reset": "99999999999"}, None),   # far beyond any window: junk
    ({"x-rate-limit-reset": "-5"}, None),
    ({}, None),
])
async def test_429_retry_at_fallbacks(monkeypatch, creds, headers, expect_seconds):
    _install(monkeypatch, _answer(429, {"title": "Too Many Requests"}, headers))
    before = datetime.now(timezone.utc)
    with pytest.raises(x_api.XApiRateLimitError) as ei:
        await x_api.create_post("t")
    if expect_seconds is None:
        assert ei.value.retry_at is None
    else:
        delta = (ei.value.retry_at - before).total_seconds()
        assert expect_seconds - 2 <= delta <= expect_seconds + 2


@pytest.mark.asyncio
async def test_429_retry_after_http_date(monkeypatch, creds):
    _install(monkeypatch, _answer(429, None, {"retry-after": "Wed, 30 Sep 2026 23:00:00 GMT"}))
    with pytest.raises(x_api.XApiRateLimitError) as ei:
        await x_api.create_post("t")
    assert ei.value.retry_at == datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)


# ── the transport split ────────────────────────────────────────────────


NOT_SENT = [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
            httpx.UnsupportedProtocol, httpx.LocalProtocolError]
MAYBE_SENT = [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError,
              httpx.WriteError, httpx.DecodingError, httpx.ProxyError]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", NOT_SENT)
async def test_transport_errors_that_never_left_are_not_sent(monkeypatch, creds, exc_type):
    _install(monkeypatch, _raising(exc_type))
    with pytest.raises(x_api.XApiNotSentError) as ei:
        await x_api.create_post("t")
    assert exc_type.__name__ in str(ei.value)
    # Raised OUTSIDE the except block: no cause AND no context (the httpx exception carries the
    # request, Authorization header included — `from None` alone only hid it).
    assert ei.value.__cause__ is None and ei.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", MAYBE_SENT)
async def test_transport_errors_after_sending_are_ambiguous(monkeypatch, creds, exc_type):
    _install(monkeypatch, _raising(exc_type))
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await x_api.create_post("t")
    assert type(ei.value) is x_api.XApiAmbiguousError
    assert ei.value.status is None
    # Raised OUTSIDE the except block: no cause AND no context (the httpx exception carries the
    # request, Authorization header included — `from None` alone only hid it).
    assert ei.value.__cause__ is None and ei.value.__context__ is None


def test_not_sent_list_is_caught_before_its_httpx_parents():
    # ConnectTimeout / PoolTimeout ARE TimeoutExceptions and every class is an HTTPError: a
    # broader clause first would turn a never-sent request into an ambiguous one.
    for exc_type in NOT_SENT:
        assert exc_type in x_api._NOT_SENT_ERRORS
    for exc_type in MAYBE_SENT:
        assert not issubclass(exc_type, x_api._NOT_SENT_ERRORS), exc_type


# ── delete_post ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (200, {"data": {"deleted": True}}, {"deleted": True, "already_gone": False}),
    (204, None, {"deleted": True, "already_gone": False}),
    (404, {"title": "Not Found Error", "detail": "Could not find tweet"}, {"deleted": True, "already_gone": True}),
])
async def test_delete_success_shapes(monkeypatch, creds, status, body, expected):
    fake = _install(monkeypatch, _answer(status, body))
    assert await x_api.delete_post("1850000000000000001") == expected
    (req,) = fake.requests
    assert req.method == "DELETE" and str(req.url) == "https://api.x.com/2/tweets/1850000000000000001"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (200, {"data": {"deleted": False}}, x_api.XApiAmbiguousError),
    (200, {"data": {}}, x_api.XApiAmbiguousError),
    (200, {"data": {"deleted": "true"}}, x_api.XApiAmbiguousError),
    (200, b"oops", x_api.XApiAmbiguousError),
    (503, {"title": "Service Unavailable"}, x_api.XApiAmbiguousError),
    (401, {"title": "Unauthorized"}, x_api.XApiAuthError),
    (403, {"title": "Forbidden", "detail": GENERIC_403}, x_api.XApiForbiddenError),
])
async def test_delete_failures(monkeypatch, creds, status, body, expected):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(x_api.XApiException) as ei:
        await x_api.delete_post("42")
    assert type(ei.value) is expected and ei.value.method == "delete_post"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "12a", "../2/users", "1 2", None, True, "١٢٣"])
async def test_delete_refuses_a_non_numeric_id_without_sending(monkeypatch, creds, bad):
    fake = _install(monkeypatch, _answer(200, {"data": {"deleted": True}}))
    with pytest.raises(x_api.XApiRefusedError) as ei:
        await x_api.delete_post(bad)
    assert ei.value.status is None
    assert fake.requests == []


# ── list_user_posts ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_parses_posts_and_count(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {
        "data": [
            {"id": "2", "text": "second", "created_at": "2026-09-30T12:01:00.000Z", "edit_history_tweet_ids": ["2"]},
            {"id": "1", "text": "first"},
        ],
        "meta": {"result_count": 2, "newest_id": "2", "oldest_id": "1"},
    }))
    out = await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert out == {
        "posts": [{"id": "2", "text": "second", "created_at": "2026-09-30T12:01:00.000Z"},
                  {"id": "1", "text": "first", "created_at": None}],
        "result_count": 2,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"meta": {"result_count": 0}}, {}, {"meta": {}}])
async def test_list_empty_has_no_data(monkeypatch, creds, body):
    _install(monkeypatch, _answer(200, body))
    out = await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert out == {"posts": [], "result_count": 0}


@pytest.mark.asyncio
async def test_list_without_meta_counts_the_posts(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"data": [{"id": "5", "text": "x"}]}))
    out = await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert out["result_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"meta": {"result_count": 3}},                       # says 3, shows none
    {"data": {"id": "1", "text": "x"}},                  # not a list
    {"data": [{"id": "1"}]},                             # no text
    {"data": [{"text": "x"}]},                           # no id
    {"data": [{"id": 1, "text": "x"}]},                  # id not a string
    {"data": ["1"]},
    b"not json",
])
async def test_list_malformed_is_ambiguous_not_silently_skipped(monkeypatch, creds, body):
    # A skipped item could be the very post a reconcile is looking for.
    _install(monkeypatch, _answer(200, body))
    with pytest.raises(x_api.XApiAmbiguousError):
        await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))


@pytest.mark.asyncio
@pytest.mark.parametrize("asked,sent", [(1, "5"), (-3, "5"), (5, "5"), (50, "50"), (100, "100"), (1000, "100")])
async def test_list_max_results_is_clamped(monkeypatch, creds, asked, sent):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc), max_results=asked)
    assert dict(parse_qsl(fake.requests[0].url.query.decode()))["max_results"] == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("start,rendered", [
    (datetime(2026, 9, 30, 8, 5, 9, 123456, tzinfo=timezone(timedelta(hours=-4))), "2026-09-30T12:05:09Z"),
    (datetime(2026, 9, 30, 12, 5, 9), "2026-09-30T12:05:09Z"),  # naive = UTC
])
async def test_list_start_time_is_rendered_in_utc(monkeypatch, creds, start, rendered):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    await x_api.list_user_posts("99", start_time=start)
    assert dict(parse_qsl(fake.requests[0].url.query.decode()))["start_time"] == rendered


@pytest.mark.asyncio
async def test_list_refusals(monkeypatch, creds):
    _install(monkeypatch, _answer(401, {"title": "Unauthorized"}))
    with pytest.raises(x_api.XApiAuthError):
        await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))
    _install(monkeypatch, _raising(httpx.ConnectError))
    with pytest.raises(x_api.XApiNotSentError):
        await x_api.list_user_posts("99", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))


# ── list_user_posts_metrics + get_me (the measure step's paid reads) ───


M_START = datetime(2026, 10, 3, 20, 5, 0, tzinfo=timezone.utc)
M_END = datetime(2026, 10, 3, 20, 25, 0, tzinfo=timezone.utc)
_ABSENT = object()


async def _metrics(user_id: Any = "1234567890", **over: Any) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"start_time": M_START, "end_time": M_END}
    kw.update(over)
    return await x_api.list_user_posts_metrics(user_id, **kw)


_READS = {
    "list_user_posts_metrics": lambda: _metrics(),
    "get_me": lambda: x_api.get_me(),
}


@pytest.mark.asyncio
async def test_metrics_read_request_is_exact_and_signed(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    await _metrics()

    (req,) = fake.requests
    assert req.method == "GET" and req.url.host == "api.x.com"
    assert req.url.path == "/2/users/1234567890/tweets"
    # On the wire: RFC 3986, sorted — the commas and colons percent-encoded exactly as signed.
    assert req.url.query == (b"end_time=2026-10-03T20%3A25%3A00Z&exclude=replies%2Cretweets&max_results=5"
                             b"&start_time=2026-10-03T20%3A05%3A00Z&tweet.fields=created_at%2Cpublic_metrics")
    sent = dict(parse_qsl(req.url.query.decode()))
    assert sent == {"start_time": "2026-10-03T20:05:00Z", "end_time": "2026-10-03T20:25:00Z",
                    "max_results": "5", "tweet.fields": "created_at,public_metrics",
                    "exclude": "replies,retweets"}
    assert req.content == b""

    oauth = _oauth_params(req.headers["Authorization"])
    assert oauth["oauth_consumer_key"] == CK and oauth["oauth_token"] == AT
    signature = oauth.pop("oauth_signature")
    base = x_api.signature_base_string("GET", "https://api.x.com/2/users/1234567890/tweets", {**sent, **oauth})
    assert x_api.hmac_sha1_signature(base, CS, ATS) == signature


@pytest.mark.asyncio
async def test_metrics_read_returns_public_metrics_exactly_as_sent(monkeypatch, creds):
    # Both repost spellings the docs show, and counts X should never send (strings, null, negative,
    # bool): the client hands them over untouched — normalising (and omitting) is the service's job.
    documented = {"retweet_count": 1, "reply_count": 0, "like_count": 4, "quote_count": 0,
                  "bookmark_count": 2, "impression_count": 310}
    odd = {"repost_count": 3, "like_count": "7", "impression_count": -1, "reply_count": None,
           "quote_count": True, "bookmark_count": 1.5}
    _install(monkeypatch, _answer(200, {
        "data": [
            {"id": "1850000000000000002", "text": "the second caption", "created_at": "2026-10-03T20:15:02.000Z",
             "edit_history_tweet_ids": ["1850000000000000002"], "public_metrics": documented},
            {"id": "1850000000000000001", "text": "the first caption", "public_metrics": odd},
        ],
        "meta": {"result_count": 2, "newest_id": "1850000000000000002", "oldest_id": "1850000000000000001",
                 "next_token": "7140dibdnow9c7btw423x9h8b8ay4b2v6xrg4n4fwakv5"},
    }))
    out = await _metrics()
    assert out == {
        "posts": [
            {"id": "1850000000000000002", "created_at": "2026-10-03T20:15:02.000Z", "public_metrics": documented},
            {"id": "1850000000000000001", "created_at": None, "public_metrics": odd},
        ],
        "result_count": 2,
        "next_token": "7140dibdnow9c7btw423x9h8b8ay4b2v6xrg4n4fwakv5",
    }
    # The post text never leaves the client on the metrics path.
    assert "caption" not in repr(out)


@pytest.mark.asyncio
@pytest.mark.parametrize("metrics", [_ABSENT, None, "lots", ["like_count", 3], 5, True])
async def test_metrics_read_unreadable_public_metrics_are_empty_not_zero(monkeypatch, creds, metrics):
    item: Dict[str, Any] = {"id": "9", "text": "t"}
    if metrics is not _ABSENT:
        item["public_metrics"] = metrics
    _install(monkeypatch, _answer(200, {"data": [item], "meta": {"result_count": 1}}))
    out = await _metrics()
    assert out["posts"] == [{"id": "9", "created_at": None, "public_metrics": {}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"meta": {"result_count": 0}}, {}, {"meta": {}}, {"meta": "junk"}, {"data": []}])
async def test_metrics_read_empty_window(monkeypatch, creds, body):
    _install(monkeypatch, _answer(200, body))
    assert await _metrics() == {"posts": [], "result_count": 0, "next_token": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("token, expected", [
    ("b26v89c19zqg8o3fo7gesq314yb9l2l4ptqy", "b26v89c19zqg8o3fo7gesq314yb9l2l4ptqy"),
    (None, None), (5, None), ("", None), ("has space", None), ("tab\there", None),
    ("x" * 257, None), ("ünïcode", None), (["a"], None),
])
async def test_metrics_read_next_token_is_kept_only_when_it_looks_like_one(monkeypatch, creds, token, expected):
    _install(monkeypatch, _answer(200, {"data": [{"id": "9", "text": "t"}],
                                        "meta": {"result_count": 1, "next_token": token}}))
    assert (await _metrics())["next_token"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"meta": {"result_count": 3}},                       # says 3, shows none
    {"data": {"id": "1", "text": "x"}},                  # not a list
    {"data": "1"},
    {"data": [{"id": "1"}]},                             # no text
    {"data": [{"text": "x"}]},                           # no id
    {"data": [{"id": 1, "text": "x"}]},                  # id not a string
    {"data": [{"id": "1a", "text": "x"}]},
    {"data": [{"id": "", "text": "x"}]},
    {"data": [{"id": "1" * 26, "text": "x"}]},
    {"data": [{"id": "1", "text": "x"}, None]},          # one good item does not excuse a bad one
    {"data": ["1"]},
    b"not json",
    b"[1, 2]",
    b"null",
    b"",
])
async def test_metrics_read_malformed_is_ambiguous_not_silently_skipped(monkeypatch, creds, body):
    _install(monkeypatch, _answer(200, body))
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await _metrics()
    assert type(ei.value) is x_api.XApiAmbiguousError
    assert ei.value.status == 200 and ei.value.method == "list_user_posts_metrics"


@pytest.mark.asyncio
@pytest.mark.parametrize("asked, sent", [
    (1, "5"), (-3, "5"), (0, "5"), (5, "5"), (50, "50"), (100, "100"), (1000, "100"), (10 ** 30, "100"),
    (True, "5"), (7.9, "7"), ("50", "50"), ("5.5", "5"), (None, "5"), ("lots", "5"), ("9" * 5000, "5"),
    (float("nan"), "5"), (float("inf"), "5"), (float("-inf"), "5"),
])
async def test_metrics_read_max_results_is_clamped_and_never_crashes(monkeypatch, creds, asked, sent):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    await _metrics(max_results=asked)
    assert dict(parse_qsl(fake.requests[0].url.query.decode()))["max_results"] == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("start, end, sent_start, sent_end", [
    (datetime(2026, 10, 3, 16, 5, 9, 123456, tzinfo=timezone(timedelta(hours=-4))),
     datetime(2026, 10, 3, 16, 25, 9, 999999, tzinfo=timezone(timedelta(hours=-4))),
     "2026-10-03T20:05:09Z", "2026-10-03T20:25:09Z"),
    (datetime(2026, 10, 3, 20, 5, 9), datetime(2026, 10, 3, 20, 25, 9),          # naive = UTC
     "2026-10-03T20:05:09Z", "2026-10-03T20:25:09Z"),
    (datetime(2026, 10, 4, 5, 5, tzinfo=timezone(timedelta(hours=9))), datetime(2026, 10, 3, 20, 6),
     "2026-10-03T20:05:00Z", "2026-10-03T20:06:00Z"),                            # mixed zones
])
async def test_metrics_read_window_is_sent_in_utc_to_the_second(monkeypatch, creds, start, end, sent_start,
                                                               sent_end):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    await _metrics(start_time=start, end_time=end)
    sent = dict(parse_qsl(fake.requests[0].url.query.decode()))
    assert (sent["start_time"], sent["end_time"]) == (sent_start, sent_end)


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [
    {"end_time": M_START},                                                        # an empty window
    {"end_time": M_START + timedelta(milliseconds=900)},                          # the same second
    {"end_time": M_START - timedelta(minutes=1)},                                 # backwards
    {"start_time": "2026-10-03T20:05:00Z"},                                       # a string
    {"start_time": None},
    {"end_time": None},
    {"end_time": date(2026, 10, 3)},                                              # a date, not a datetime
    {"start_time": 1_759_521_900},                                                # an epoch number
    {"start_time": datetime(2010, 11, 5, 23, 59, 59, tzinfo=timezone.utc)},      # before X's first day
    {"start_time": datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))},      # not expressible in UTC
    {"end_time": datetime(9999, 12, 31, 23, 0, tzinfo=timezone(timedelta(hours=-5)))},
    {"user_id": ""}, {"user_id": "12a"}, {"user_id": "../2/users"}, {"user_id": None}, {"user_id": True},
    {"user_id": "١٢٣"}, {"user_id": -5},
])
async def test_metrics_read_refuses_bad_arguments_without_sending(monkeypatch, creds, over):
    fake = _install(monkeypatch, _answer(200, {"meta": {"result_count": 0}}))
    user_id = over.pop("user_id", "1234567890")
    with pytest.raises(x_api.XApiRefusedError) as ei:
        await _metrics(user_id, **over)
    e = ei.value
    assert type(e) is x_api.XApiRefusedError and e.status is None and "not sent" in str(e)
    assert e.__context__ is None and e.__cause__ is None
    assert fake.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("read", sorted(_READS))
@pytest.mark.parametrize("status, body, expected", [
    (400, {"title": "Invalid Request", "detail": "One or more parameters to your request was invalid.",
           "type": "https://api.x.com/2/problems/invalid-request"}, x_api.XApiRefusedError),
    (401, {"title": "Unauthorized", "detail": "Unauthorized", "type": "about:blank"}, x_api.XApiAuthError),
    (402, {"title": "Payment Required", "type": CREDITS_TYPE}, x_api.XApiCreditsDepletedError),
    (402, None, x_api.XApiCreditsDepletedError),
    (403, {"title": "Forbidden", "detail": GENERIC_403}, x_api.XApiForbiddenError),
    (404, {"title": "Not Found Error", "detail": "Could not find user"}, x_api.XApiRefusedError),
    (408, None, x_api.XApiAmbiguousError),
    (500, {"title": "Internal Error"}, x_api.XApiAmbiguousError),
    (503, b"Service Unavailable", x_api.XApiAmbiguousError),
    (302, None, x_api.XApiAmbiguousError),
])
async def test_reads_status_mapping(monkeypatch, creds, read, status, body, expected):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(x_api.XApiException) as ei:
        await _READS[read]()
    e = ei.value
    assert type(e) is expected, (read, status, type(e))
    assert e.status == status and e.method == read and f"HTTP {status}" in str(e)
    assert e.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("read", sorted(_READS))
async def test_reads_429_carry_retry_at(monkeypatch, creds, read):
    reset = int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp())
    _install(monkeypatch, _answer(429, {"title": "Too Many Requests"}, {"x-rate-limit-reset": str(reset)}))
    with pytest.raises(x_api.XApiRateLimitError) as ei:
        await _READS[read]()
    assert ei.value.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)
    assert ei.value.status == 429 and ei.value.method == read


@pytest.mark.asyncio
@pytest.mark.parametrize("read", sorted(_READS))
@pytest.mark.parametrize("exc_type, expected", [
    *[(t, x_api.XApiNotSentError) for t in NOT_SENT],
    *[(t, x_api.XApiAmbiguousError) for t in MAYBE_SENT],
])
async def test_reads_transport_split(monkeypatch, creds, read, exc_type, expected):
    _install(monkeypatch, _raising(exc_type))
    with pytest.raises(x_api.XApiException) as ei:
        await _READS[read]()
    e = ei.value
    assert type(e) is expected and e.status is None and e.method == read
    assert exc_type.__name__ in str(e)
    # Raised OUTSIDE the except block (the httpx exception holds the Authorization header).
    assert e.__cause__ is None and e.__context__ is None
    assert AT not in str(e)


@pytest.mark.asyncio
async def test_get_me_request_is_exact_and_signed(monkeypatch, creds):
    metrics = {"followers_count": 1, "following_count": 0, "tweet_count": 3, "listed_count": 0,
               "like_count": 0, "media_count": 0}
    fake = _install(monkeypatch, _answer(200, {"data": {"id": "1234567890", "name": "Caydex",
                                                        "username": "caydexapp", "public_metrics": metrics}}))
    assert await x_api.get_me() == {"id": "1234567890", "username": "caydexapp", "public_metrics": metrics}

    (req,) = fake.requests
    assert req.method == "GET" and str(req.url) == "https://api.x.com/2/users/me?user.fields=public_metrics"
    assert req.content == b""
    oauth = _oauth_params(req.headers["Authorization"])
    signature = oauth.pop("oauth_signature")
    base = x_api.signature_base_string("GET", "https://api.x.com/2/users/me",
                                       {"user.fields": "public_metrics", **oauth})
    assert x_api.hmac_sha1_signature(base, CS, ATS) == signature


@pytest.mark.asyncio
@pytest.mark.parametrize("data, expected", [
    # Raw counts, the reference page's renamed key included — the caller normalises.
    ({"id": "42", "username": "caydex", "public_metrics": {"followers_count": "12", "post_count": -1}},
     {"id": "42", "username": "caydex", "public_metrics": {"followers_count": "12", "post_count": -1}}),
    ({"id": 42, "username": "caydex"}, {"id": "42", "username": "caydex", "public_metrics": {}}),
    ({"id": "42", "public_metrics": None}, {"id": "42", "username": None, "public_metrics": {}}),
    ({"id": "42", "public_metrics": [1, 2]}, {"id": "42", "username": None, "public_metrics": {}}),
    ({"id": "42", "username": ""}, {"id": "42", "username": None, "public_metrics": {}}),
    ({"id": "42", "username": "has space"}, {"id": "42", "username": None, "public_metrics": {}}),
    ({"id": "42", "username": "evil\nline"}, {"id": "42", "username": None, "public_metrics": {}}),
    ({"id": "42", "username": "x" * 51}, {"id": "42", "username": None, "public_metrics": {}}),
    ({"id": "42", "username": 7}, {"id": "42", "username": None, "public_metrics": {}}),
])
async def test_get_me_answers_degrade_field_by_field(monkeypatch, creds, data, expected):
    _install(monkeypatch, _answer(200, {"data": data}))
    assert await x_api.get_me() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"data": None}, {"data": []}, {"data": "42"}, {"data": {}}, {"data": {"id": None}},
    {"data": {"id": True}}, {"data": {"id": "abc"}}, {"data": {"id": -5}}, {"data": {"id": ""}},
    {"data": {"id": "1" * 26}}, {"data": {"id": 1.5}}, {"errors": [{"title": "Not Found Error"}]},
    b"not json", b"[1]", b"",
])
async def test_get_me_without_a_readable_id_is_ambiguous(monkeypatch, creds, body):
    _install(monkeypatch, _answer(200, body))
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await x_api.get_me()
    assert type(ei.value) is x_api.XApiAmbiguousError
    assert ei.value.status == 200 and ei.value.method == "get_me"


# ── configuration ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", sorted(_CREDS))
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_not_configured_never_sends(monkeypatch, creds, missing, blank):
    monkeypatch.setattr(x_api.settings, missing, blank)
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1", "text": "t"}}))
    assert x_api.configured() is False
    calls = [
        x_api.create_post("t"),
        x_api.delete_post("1"),
        x_api.list_user_posts("1", start_time=datetime(2026, 9, 30, tzinfo=timezone.utc)),
        x_api.list_user_posts_metrics("1", start_time=M_START, end_time=M_END),
        x_api.get_me(),
    ]
    for coro in calls:
        with pytest.raises(x_api.XApiNotConfiguredError) as ei:
            await coro
        assert missing in str(ei.value)
    assert fake.requests == []


def test_configured_true_with_all_four(creds):
    assert x_api.configured() is True


@pytest.mark.asyncio
async def test_empty_text_is_refused_without_sending(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1", "text": "t"}}))
    for bad in ("", "  \n", None):
        with pytest.raises(x_api.XApiRefusedError):
            await x_api.create_post(bad)  # type: ignore[arg-type]
    assert fake.requests == []


@pytest.mark.parametrize("token,expected", [
    ("1234567890-abcDEF", "1234567890"),
    ("42-a-b-c", "42"),
    ("abc-123", None),
    ("1234567890", None),
    ("-abc", None),
    ("", None),
    (None, None),
    ("١٢٣-abc", None),   # non-ASCII digits are not a user id
    ("12 3-abc", None),
])
def test_user_id_from_access_token(monkeypatch, token, expected):
    monkeypatch.setattr(x_api.settings, "MARKETING_X_ACCESS_TOKEN", token)
    assert x_api.user_id_from_access_token() == expected


# ── secrets never leak ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_secret_in_any_exception_or_log(monkeypatch, creds, caplog):
    caplog.set_level(logging.DEBUG)
    seen_signatures: List[str] = []

    def echo(status: int, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
        def respond(request: httpx.Request) -> httpx.Response:
            seen_signatures.append(_oauth_params(request.headers["Authorization"])["oauth_signature"])
            # A hostile/buggy upstream echoing every credential and the header back.
            leak = f"{CK} {CS} {AT} {ATS} {request.headers['Authorization']}"
            filled = json.loads(json.dumps(body).replace("LEAK", leak.replace('"', "'")))
            return httpx.Response(status, json=filled, headers=headers)
        return respond

    def raise_with_leak(exc_type: type):
        def respond(request: httpx.Request) -> httpx.Response:
            seen_signatures.append(_oauth_params(request.headers["Authorization"])["oauth_signature"])
            raise exc_type(f"{CK} {CS} {AT} {ATS} {request.headers['Authorization']}", request=request)
        return respond

    scenarios = [
        echo(401, {"title": "Unauthorized LEAK", "detail": "bad token LEAK", "type": "about:blank LEAK"}),
        echo(403, {"title": "Forbidden", "detail": "duplicate content LEAK"}),
        echo(403, {"errors": [{"message": "LEAK"}]}),
        echo(402, {"title": "LEAK", "type": CREDITS_TYPE}),
        echo(429, {"detail": "LEAK"}, {"x-rate-limit-reset": "garbage LEAK"}),
        echo(500, {"detail": "LEAK"}),
        echo(201, {"data": {"id": "9", "text": "ok"}, "errors": [{"title": "LEAK", "detail": "LEAK"}]}),
        raise_with_leak(httpx.ConnectError),
        raise_with_leak(httpx.ReadTimeout),
    ]
    raised: List[BaseException] = []
    for respond in scenarios:
        _install(monkeypatch, respond)
        for call in (lambda: x_api.create_post("t"), lambda: x_api.delete_post("9"),
                     lambda: x_api.list_user_posts_metrics("9", start_time=M_START, end_time=M_END),
                     x_api.get_me):
            try:
                await call()
            except x_api.XApiException as e:
                raised.append(e)

    # 9 scenarios × 4 calls; only create_post and get_me succeed on the 201 scenario.
    assert len(raised) == 34
    assert len(seen_signatures) == 36
    texts = [caplog.text]
    for e in raised:
        texts += [str(e), repr(e), repr(vars(e)), repr(e.args)]
    blob = "\n".join(texts)
    for secret in (CK, CS, AT, ATS, *seen_signatures):
        assert secret not in blob, secret[:6]
    # The redaction marker proves the scenarios really echoed something to redact.
    assert "<redacted>" in blob


# ── lifecycle + error mapping ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_x_client_is_idempotent(monkeypatch):
    monkeypatch.setattr(x_api, "_client", None)
    client = x_api._get_client()
    assert x_api._get_client() is client
    await x_api.close_x_client()
    assert x_api._client is None and client.is_closed
    await x_api.close_x_client()
    assert x_api._client is None


def _exception_classes() -> List[type]:
    return [obj for _, obj in inspect.getmembers(x_api, inspect.isclass)
            if issubclass(obj, x_api.XApiException) and obj.__module__ == x_api.__name__]


_EXPECTED_STATUS = {
    "XApiException": 503,
    "XApiNotSentError": 503,
    "XApiAmbiguousError": 503,
    "XApiRateLimitError": 503,
    "XApiNotConfiguredError": 502,
    "XApiDuplicateContentError": 502,
    "XApiRefusedError": 502,
    "XApiAuthError": 502,
    "XApiForbiddenError": 502,
    "XApiCreditsDepletedError": 502,
}


def test_every_exception_class_classifies_as_publisher_unavailable():
    classes = _exception_classes()
    assert {c.__name__ for c in classes} == set(_EXPECTED_STATUS)
    for cls in classes:
        for exc in (cls("x"), cls(), cls("x create_post: HTTP 429 timed out", method="create_post", status=429)):
            code, status = classify_exception(exc)
            assert code == ErrorCode.MARKETING_PUBLISHER_UNAVAILABLE, cls.__name__
            assert status == _EXPECTED_STATUS[cls.__name__], cls.__name__
        e = cls("m", method="create_post", status=400, problem_type="t", detail="d")
        assert (e.method, e.status, e.problem_type, e.detail) == ("create_post", 400, "t", "d")
    assert x_api.XApiRateLimitError("x").retry_at is None


# ── review 2026-09-30 follow-ups ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_legacy_duplicate_status_is_ambiguous_not_forbidden(monkeypatch, creds):
    # The pre-RFC 7807 shape: {"errors":[{"code":187,"message":"Status is a duplicate."}]}. Read as
    # a plain 403 it would mark a post FAILED that may well exist on X.
    _install(monkeypatch, _answer(403, {"errors": [{"code": 187, "message": "Status is a duplicate."}]}))
    with pytest.raises(x_api.XApiDuplicateContentError):
        await x_api.create_post("t")


@pytest.mark.asyncio
async def test_a_body_that_cannot_be_encoded_is_typed_not_sent_and_leaks_nothing(monkeypatch, creds, caplog):
    # A lone surrogate cannot be UTF-8 encoded: httpx raises a plain UnicodeEncodeError while
    # building the request, which used to escape untyped (and a frame dump of _call held the raw
    # consumer and token secrets as locals).
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1", "text": "t"}}))
    with pytest.raises(x_api.XApiNotSentError) as ei:
        await x_api.create_post("hi \ud83d")
    assert not fake.requests
    assert ei.value.__context__ is None and ei.value.__cause__ is None
    for secret in (CS, ATS, AT, CK):
        assert secret not in str(ei.value) and secret not in repr(ei.value) and secret not in caplog.text


@pytest.mark.asyncio
async def test_a_non_httpx_error_after_sending_began_is_ambiguous(monkeypatch, creds):
    def boom(_request):
        raise RuntimeError("transport exploded mid-response")
    _install(monkeypatch, boom)
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await x_api.create_post("t")
    assert type(ei.value) is x_api.XApiAmbiguousError and ei.value.__context__ is None


def test_credentials_never_render_in_a_frame_dump(creds):
    creds_obj = x_api._credentials("create_post")
    hidden = x_api._Redacted((CK, CS, AT, ATS))
    for rendered in (repr(creds_obj), str(creds_obj), repr(hidden), str(hidden)):
        assert all(s not in rendered for s in (CK, CS, AT, ATS))


# ── review 2026-10-01 follow-ups ──────────────────────────────────────────────────


#: Valid JSON nested far past the interpreter's recursion limit: `resp.json()` raises RecursionError
#: (a RuntimeError, not a ValueError), which used to escape `_call` untyped (finding #9).
DEEP_ARRAY = b"[" * 100_000 + b"]" * 100_000
DEEP_OBJECT = b'{"a":' * 100_000 + b"1" + b"}" * 100_000


@pytest.mark.asyncio
@pytest.mark.parametrize("deep", [DEEP_ARRAY, DEEP_OBJECT], ids=["array", "object"])
@pytest.mark.parametrize("name, status, call, why", [
    ("list_user_posts_metrics", 200, lambda: _metrics(), "with an unreadable body"),
    ("get_me", 200, lambda: x_api.get_me(), "without a readable data.id"),
    ("create_post", 201, lambda: x_api.create_post("t"), "without a readable data.id"),
    ("list_user_posts", 200, lambda: x_api.list_user_posts("99", start_time=M_START), "with an unreadable body"),
    ("delete_post", 200, lambda: x_api.delete_post("42"), "without data.deleted == true"),
])
async def test_a_deeply_nested_2xx_body_is_the_documented_ambiguous_error(monkeypatch, creds, deep, name, status,
                                                                         call, why):
    _install(monkeypatch, _answer(status, deep))
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await call()
    e = ei.value
    assert type(e) is x_api.XApiAmbiguousError
    assert (e.method, e.status) == (name, status) and why in str(e)
    # Raised after the parse failure was handled — nothing chained, nothing holding the request.
    assert e.__context__ is None and e.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [
    (401, x_api.XApiAuthError), (403, x_api.XApiForbiddenError), (402, x_api.XApiCreditsDepletedError),
    (404, x_api.XApiRefusedError), (429, x_api.XApiRateLimitError), (503, x_api.XApiAmbiguousError),
])
async def test_a_deeply_nested_error_body_still_maps_by_status(monkeypatch, creds, status, expected):
    # The parse runs before the status is read, so a deep 4xx/5xx body escaped untyped too.
    _install(monkeypatch, _answer(status, DEEP_ARRAY))
    for call in (lambda: _metrics(), x_api.get_me, lambda: x_api.create_post("t")):
        with pytest.raises(x_api.XApiException) as ei:
            await call()
        assert type(ei.value) is expected and ei.value.status == status
        assert ei.value.problem_type is None and ei.value.detail is None


#: X's partial-error 200 for a resource it could not return (tweepy's `Response.errors` shape).
SUSPENDED = {"value": "1234567890", "detail": "User has been suspended: [1234567890].", "title": "Forbidden",
             "resource_type": "user", "parameter": "id",
             "type": "https://api.twitter.com/2/problems/resource-not-found"}
_SHAPE = {"posts", "result_count", "next_token"}


@pytest.mark.asyncio
@pytest.mark.parametrize("body, described", [
    ({"errors": [SUSPENDED]}, ("Forbidden", "User has been suspended: [1234567890].", "resource-not-found")),
    ({"errors": [SUSPENDED], "data": None, "meta": {"result_count": 0}}, ("Forbidden", "suspended", "problems")),
    # A next_token beside no data paginates nothing.
    ({"errors": [SUSPENDED], "meta": {"result_count": 0, "next_token": "b26v89c19zqg8o3fo7gesq314yb9l2l4ptqy"}},
     ("Forbidden",)),
    # The FIRST problem object is described; the count says there were more.
    ({"errors": [SUSPENDED, {"title": "Not Found Error", "detail": "second"}]}, ("Forbidden", "2 error object(s)")),
    ({"errors": [None, "junk", SUSPENDED]}, ("Forbidden", "3 error object(s)")),
    # The legacy shape: the message is the detail.
    ({"errors": [{"code": 34, "message": "Sorry, that page does not exist."}]}, ("Sorry, that page does not exist.",)),
    # Nothing readable inside — still a problem, never an empty window.
    ({"errors": ["oops", 5, None]}, ("HTTP 200", "3 error object(s)")),
    ({"errors": [{}]}, ("HTTP 200", "1 error object(s)")),
])
async def test_metrics_read_an_errors_only_200_is_a_problem_not_an_empty_window(monkeypatch, creds, caplog, body,
                                                                                 described):
    caplog.set_level(logging.WARNING, logger=x_api.__name__)
    _install(monkeypatch, _answer(200, body))
    out = await _metrics()
    assert set(out) == _SHAPE | {"problem"}
    assert (out["posts"], out["result_count"], out["next_token"]) == ([], 0, None)
    problem = out["problem"]
    assert isinstance(problem, str) and 0 < len(problem) <= 300
    assert problem.startswith("x list_user_posts_metrics: HTTP 200")
    for text in described:
        assert text in problem, (text, problem)
    logged = [r.getMessage() for r in caplog.records if r.name == x_api.__name__ and r.levelno == logging.WARNING]
    assert any(problem in line for line in logged)


@pytest.mark.asyncio
async def test_metrics_read_problem_is_scrubbed_and_capped(monkeypatch, creds, caplog):
    caplog.set_level(logging.DEBUG)
    signatures: List[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        # A hostile/buggy upstream echoing every credential and the signed header into the problem.
        header = request.headers["Authorization"]
        signatures.append(_oauth_params(header)["oauth_signature"])
        leak = f"{CK} {CS} {AT} {ATS} {header}"
        return httpx.Response(200, json={"errors": [{"title": f"Forbidden {leak}", "detail": leak + " z" * 3000,
                                                     "type": f"about:blank {leak}"}]})

    _install(monkeypatch, respond)
    problem = (await _metrics())["problem"]
    assert len(problem) == 300 and problem.startswith("x list_user_posts_metrics: HTTP 200 Forbidden <redacted>")
    for secret in (CK, CS, AT, ATS, *signatures):
        assert secret not in problem and secret not in caplog.text, secret[:6]


@pytest.mark.asyncio
async def test_metrics_read_problem_is_one_line(monkeypatch, creds, caplog):
    # X's title / detail / type are upstream-controlled: a line break or control character in them
    # would forge a log line (the problem is logged) and break the one-line note the caller stores.
    caplog.set_level(logging.WARNING, logger=x_api.__name__)
    hostile = {"title": "Forbidden\nINFO app.main: forged line", "detail": "User\r\nsuspended\x1b[31m ‮\x00\tend",
               "type": "https://api.x.com/2/problems/x\x85y"}
    _install(monkeypatch, _answer(200, {"errors": [hostile]}))
    problem = (await _metrics())["problem"]
    assert problem == ("x list_user_posts_metrics: HTTP 200 Forbidden INFO app.main: forged line - User suspended [31m "
                       "end [https://api.x.com/2/problems/x y] (no data; 1 error object(s))")
    assert problem.isprintable() and "  " not in problem
    logged = [r.getMessage() for r in caplog.records if r.name == x_api.__name__]
    assert logged and all(m.isprintable() for m in logged)


@pytest.mark.asyncio
@pytest.mark.parametrize("body, expected", [
    # Data beside errors is a normal answer: the posts are what X returned.
    ({"data": [{"id": "9", "text": "t", "public_metrics": {"like_count": 2}}], "errors": [SUSPENDED],
      "meta": {"result_count": 1}},
     {"posts": [{"id": "9", "created_at": None, "public_metrics": {"like_count": 2}}], "result_count": 1,
      "next_token": None}),
    # X omits `data` when it has no posts, so an explicit empty list is an (empty) answer, not its
    # partial-error shape.
    ({"data": [], "errors": [SUSPENDED]}, {"posts": [], "result_count": 0, "next_token": None}),
    # An empty window, and `errors` that hold nothing or are not X's list: read as before.
    ({}, {"posts": [], "result_count": 0, "next_token": None}),
    ({"meta": {"result_count": 0}}, {"posts": [], "result_count": 0, "next_token": None}),
    ({"errors": []}, {"posts": [], "result_count": 0, "next_token": None}),
    ({"errors": None}, {"posts": [], "result_count": 0, "next_token": None}),
    ({"errors": {"title": "Forbidden"}}, {"posts": [], "result_count": 0, "next_token": None}),
    ({"errors": "Forbidden"}, {"posts": [], "result_count": 0, "next_token": None}),
])
async def test_metrics_read_only_the_errors_only_answer_carries_a_problem(monkeypatch, creds, body, expected):
    _install(monkeypatch, _answer(200, body))
    out = await _metrics()
    assert out == expected and "problem" not in out


@pytest.mark.asyncio
async def test_metrics_read_a_count_without_data_stays_ambiguous_even_beside_errors(monkeypatch, creds):
    # "Says 3, shows none" is malformed whatever rides along: the read may have been billed for 3.
    _install(monkeypatch, _answer(200, {"errors": [SUSPENDED], "meta": {"result_count": 3}}))
    with pytest.raises(x_api.XApiAmbiguousError) as ei:
        await _metrics()
    assert "result_count=3 and no data" in str(ei.value)


# ── the media writes of an IMAGE post (drop 1, contract C9) ─────────────────────────────────────

#: A stand-in picture: bytes only (the client never decodes an image).
IMG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8 + b"\xff\xd9"
MEDIA_ID = "1880000000000000001"


@pytest.mark.asyncio
async def test_upload_media_sends_only_base64_media_and_the_category_signed_without_the_body(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, {"data": {
        "id": MEDIA_ID, "media_key": f"3_{MEDIA_ID}", "size": len(IMG), "expires_after_secs": 86400,
        "image": {"w": 1080, "h": 1350, "image_type": "image/jpeg"}}}))
    out = await x_api.upload_media(IMG)
    assert out == {"id": MEDIA_ID, "media_key": f"3_{MEDIA_ID}", "size": len(IMG), "state": None}
    (request,) = fake.requests
    assert request.method == "POST" and str(request.url) == "https://api.x.com/2/media/upload"
    body = json.loads(request.content)
    # MediaUploadRequest allows nothing else (additionalProperties: false): no media_type.
    assert set(body) == {"media", "media_category"} and body["media_category"] == "tweet_image"
    assert base64.b64decode(body["media"], validate=True) == IMG
    params = _oauth_params(request.headers["Authorization"])
    assert params["oauth_consumer_key"] == CK and params["oauth_token"] == AT
    # A JSON body is not an OAuth parameter: the signature covers no body field (as for a create).
    base = x_api.signature_base_string("POST", "https://api.x.com/2/media/upload",
                                       {k: v for k, v in params.items() if k != "oauth_signature"})
    assert "media_category" not in base and "tweet_image" not in base
    assert x_api.hmac_sha1_signature(base, CS, ATS) == params["oauth_signature"]


@pytest.mark.asyncio
@pytest.mark.parametrize("data, category, why", [
    (b"", "tweet_image", "no media bytes"),
    (None, "tweet_image", "no media bytes"),
    ("text", "tweet_image", "no media bytes"),
    (b"x" * (5 * 1024 * 1024 + 1), "tweet_image", "bytes >"),
    (IMG, "tweet_video", "only 'tweet_image'"),
])
async def test_upload_media_refuses_before_sending(monkeypatch, creds, data, category, why):
    fake = _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID}}))
    with pytest.raises(x_api.XApiRefusedError, match=why) as ei:
        await x_api.upload_media(data, media_category=category)
    assert ei.value.status is None and fake.requests == []


@pytest.mark.asyncio
async def test_upload_media_accepts_exactly_five_megabytes(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID}}))
    assert (await x_api.upload_media(b"x" * (5 * 1024 * 1024)))["id"] == MEDIA_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {}, {"id": ""}, {"id": "12a"}, {"id": True}, {"id": "1" * 20}, {"id": None}, "not an object",
])
async def test_upload_media_without_a_readable_id_is_ambiguous(monkeypatch, creds, data):
    _install(monkeypatch, _answer(200, {"data": data}))
    with pytest.raises(x_api.XApiAmbiguousError, match="without a readable data.id"):
        await x_api.upload_media(IMG)


@pytest.mark.asyncio
async def test_upload_media_reads_an_integer_id_and_drops_odd_fields(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"data": {"id": int(MEDIA_ID), "media_key": "evil\nkey", "size": -3,
                                                 "processing_info": {"state": "Weird"}}}))
    assert await x_api.upload_media(IMG) == {"id": MEDIA_ID, "media_key": None, "size": None, "state": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("state, expected", [("succeeded", "succeeded"), ("pending", "pending"),
                                             ("IN_PROGRESS", "in_progress")])
async def test_upload_media_reports_the_processing_state(monkeypatch, creds, state, expected):
    _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID, "processing_info": {"state": state}}}))
    assert (await x_api.upload_media(IMG))["state"] == expected


@pytest.mark.asyncio
async def test_upload_media_whose_processing_failed_is_refused(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID, "processing_info": {"state": "failed"}}}))
    with pytest.raises(x_api.XApiRefusedError, match="processing failed") as ei:
        await x_api.upload_media(IMG)
    assert ei.value.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body, exc", [
    (400, {"title": "Invalid Request", "detail": "media is not an image"}, x_api.XApiRefusedError),
    (401, {"title": "Unauthorized"}, x_api.XApiAuthError),
    (402, {"title": "Payment Required"}, x_api.XApiCreditsDepletedError),
    (403, {"title": "Forbidden", "detail": "You are not permitted to perform this action."}, x_api.XApiForbiddenError),
    (429, {"title": "Too Many Requests"}, x_api.XApiRateLimitError),
    (503, {"title": "Service Unavailable"}, x_api.XApiAmbiguousError),
])
async def test_upload_media_errors_keep_the_outcome_split(monkeypatch, creds, status, body, exc):
    _install(monkeypatch, _answer(status, body))
    with pytest.raises(exc) as ei:
        await x_api.upload_media(IMG)
    assert type(ei.value) is exc and ei.value.method == "upload_media"
    for secret in (CK, CS, AT, ATS):
        assert secret not in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error, exc", [(httpx.ConnectError, x_api.XApiNotSentError),
                                        (httpx.ReadTimeout, x_api.XApiAmbiguousError)])
async def test_upload_media_transport_failures_split_and_hide_the_request(monkeypatch, creds, error, exc):
    _install(monkeypatch, _raising(error))
    with pytest.raises(exc) as ei:
        await x_api.upload_media(IMG)
    assert ei.value.__context__ is None and ei.value.__cause__ is None
    assert AT not in str(ei.value)


@pytest.mark.asyncio
async def test_create_media_metadata_sets_the_alt_text(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID, "associated_metadata": {}}}))
    assert await x_api.create_media_metadata(MEDIA_ID, alt_text="Title\n\nParagraph one.") == {"id": MEDIA_ID}
    (request,) = fake.requests
    assert request.method == "POST" and str(request.url) == "https://api.x.com/2/media/metadata"
    assert json.loads(request.content) == {"id": MEDIA_ID, "metadata": {"alt_text": {"text": "Title\n\nParagraph one."}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("media_id, alt, why", [
    (MEDIA_ID, "", "alt text"), (MEDIA_ID, "   ", "alt text"), (MEDIA_ID, None, "alt text"),
    (MEDIA_ID, "a" * 1001, "alt text"), ("12a", "alt", "numeric media id"), (12, "alt", "numeric media id"),
    ("1" * 20, "alt", "numeric media id"),
])
async def test_create_media_metadata_refuses_before_sending(monkeypatch, creds, media_id, alt, why):
    fake = _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID}}))
    with pytest.raises(x_api.XApiRefusedError, match=why):
        await x_api.create_media_metadata(media_id, alt_text=alt)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_create_media_metadata_accepts_exactly_1000_characters(monkeypatch, creds):
    _install(monkeypatch, _answer(200, {"data": {"id": MEDIA_ID}}))
    assert await x_api.create_media_metadata(MEDIA_ID, alt_text="a" * 1000) == {"id": MEDIA_ID}


@pytest.mark.asyncio
@pytest.mark.parametrize("body, why", [({}, "without data.id"), ({"data": {"id": "1880000000000000002"}},
                                                                  "for another media id")])
async def test_create_media_metadata_answer_must_name_our_media(monkeypatch, creds, body, why):
    _install(monkeypatch, _answer(200, body))
    with pytest.raises(x_api.XApiAmbiguousError, match=why):
        await x_api.create_media_metadata(MEDIA_ID, alt_text="alt")


@pytest.mark.asyncio
async def test_create_post_attaches_media_ids(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1790000000000000001",
                                                        "text": "Caption https://t.co/AbC123"}}))
    out = await x_api.create_post("Caption", made_with_ai=True, media_ids=[MEDIA_ID])
    assert out["id"] == "1790000000000000001" and out["text"].endswith("https://t.co/AbC123")
    assert json.loads(fake.requests[0].content) == {"text": "Caption", "made_with_ai": True,
                                                     "media": {"media_ids": [MEDIA_ID]}}


@pytest.mark.asyncio
async def test_create_post_without_media_ids_sends_no_media_key(monkeypatch, creds):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1790000000000000001", "text": "Caption"}}))
    await x_api.create_post("Caption")
    assert "media" not in json.loads(fake.requests[0].content)


@pytest.mark.asyncio
@pytest.mark.parametrize("media_ids", [[], [MEDIA_ID] * 5, MEDIA_ID, ["12a"], [int(MEDIA_ID)], [None]])
async def test_create_post_refuses_unusable_media_ids_before_sending(monkeypatch, creds, media_ids):
    fake = _install(monkeypatch, _answer(201, {"data": {"id": "1"}}))
    with pytest.raises(x_api.XApiRefusedError, match="media"):
        await x_api.create_post("Caption", media_ids=media_ids)
    assert fake.requests == []
