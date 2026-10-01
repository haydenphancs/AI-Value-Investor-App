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

import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
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
        try:
            await x_api.create_post("t")
        except x_api.XApiException as e:
            raised.append(e)
        try:
            await x_api.delete_post("9")
        except x_api.XApiException as e:
            raised.append(e)

    assert len(raised) >= 15
    assert len(seen_signatures) >= 18
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
