"""`app.integrations.brave_search` — the thin Brave Search client behind report chat's web search.

Pins the request shape, the documented-fields-only parse, the `not_run` flag on every failure path
(the service refunds a budget unit on exactly that flag), that no exception text ever carries the
key, the URL or the query, the constant-repr key wrapper (Sentry frame variables), the lifespan
closer, and `classify_exception`'s by-name branch.

Hermetic: the module-level client is replaced with an `httpx.MockTransport`; nothing reaches the
network. Settings are set explicitly per test (`Settings` reads `backend/.env`).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest

from app.integrations import brave_search as bs
from app.log_redaction import scrub_sentry_event

KEY = "sk-test-SECRET-brave-0123456789"
QUERY = "Apple DOJ antitrust case 2026"


class _Fake:
    def __init__(self, answers: List[Any]):
        self.answers = list(answers)
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else (599, {"error": "unscripted"})
        if callable(answer):
            return answer(request)
        status, body, *rest = answer
        headers = rest[0] if rest else {}
        if isinstance(body, (bytes, str)):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(bs.settings, "BRAVE_SEARCH_API_KEY", KEY)
    monkeypatch.setattr(bs.settings, "BRAVE_SEARCH_BASE_URL", "https://api.search.brave.com/res/v1")
    monkeypatch.setattr(bs.settings, "BRAVE_SEARCH_TIMEOUT_SECONDS", 4.0)


def _install(monkeypatch, answers) -> _Fake:
    fake = _Fake(answers)
    monkeypatch.setattr(bs, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def _body(rows: List[Any], altered: Any = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "search", "web": {"type": "search", "results": rows}}
    if altered is not None:
        out["query"] = {"original": QUERY, "altered": altered}
    return out


_ROW = {
    "title": "Apple <strong>antitrust</strong> case", "url": "https://www.reuters.com/legal/apple-doj/",
    "description": "The DOJ case…", "age": "2 days ago", "page_age": "2026-09-30T10:00:00",
    "meta_url": {"hostname": "www.reuters.com"}, "profile": {"name": "Reuters"},
    "extra_snippets": ["one", "two"], "family_friendly": True, "thumbnail": {"src": "x"},
}


# ── request shape ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [False, True])
async def test_request_shape(monkeypatch, configured, extra):
    fake = _install(monkeypatch, [(200, _body([_ROW]))])
    await bs.web_search(QUERY, freshness="pw", extra_snippets=extra)
    req = fake.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/res/v1/web/search"
    assert req.url.scheme == "https"
    assert req.headers["x-subscription-token"] == KEY
    assert req.headers["accept"] == "application/json"
    p = dict(req.url.params)
    assert p["q"] == QUERY and p["count"] == "10"
    assert p["country"] == "US" and p["search_lang"] == "en" and p["ui_lang"] == "en-US"
    assert p["safesearch"] == "strict" and p["text_decorations"] == "false"
    assert p["result_filter"] == "web" and p["freshness"] == "pw"
    assert ("extra_snippets" in p) is extra
    # The key rides in the header only — never the URL.
    assert KEY not in str(req.url)


@pytest.mark.asyncio
@pytest.mark.parametrize("freshness", ["bogus", "", None, "PW", 7])
async def test_an_invalid_freshness_is_not_sent(monkeypatch, configured, freshness):
    fake = _install(monkeypatch, [(200, _body([]))])
    await bs.web_search(QUERY, freshness=freshness)
    assert "freshness" not in dict(fake.requests[0].url.params)


@pytest.mark.asyncio
@pytest.mark.parametrize("count,sent", [(0, "1"), (-5, "1"), (10, "10"), (500, "20"), ("x", "10")])
async def test_count_is_clamped(monkeypatch, configured, count, sent):
    fake = _install(monkeypatch, [(200, _body([]))])
    await bs.web_search(QUERY, count=count)
    assert dict(fake.requests[0].url.params)["count"] == sent


@pytest.mark.asyncio
async def test_redirects_are_never_followed(monkeypatch, configured):
    """A 3xx would carry the key header to another host if followed."""
    fake = _install(monkeypatch, [(302, b"", {"location": "https://evil.example/steal"})])
    with pytest.raises(bs.BraveSearchUnavailableException) as ei:
        await bs.web_search(QUERY)
    assert len(fake.requests) == 1
    assert ei.value.not_run is False


# ── parse ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parses_only_documented_fields(monkeypatch, configured):
    rows = [_ROW, "not a dict", None, {"title": "no url"}, {"url": 42}, {"url": "  "},
            {"url": "https://ap.example/x", "title": None, "profile": "junk", "meta_url": [1]}]
    _install(monkeypatch, [(200, _body(rows, altered="apple doj case"))])
    out = await bs.web_search(QUERY)
    assert out["altered_query"] == "apple doj case"
    assert len(out["results"]) == 2
    r = out["results"][0]
    assert set(r) == {"title", "url", "description", "age", "page_age", "hostname", "publisher",
                      "extra_snippets"}
    assert r["hostname"] == "www.reuters.com" and r["publisher"] == "Reuters"
    assert r["extra_snippets"] == ["one", "two"]
    assert "thumbnail" not in r and "family_friendly" not in r
    junk = out["results"][1]
    assert junk["publisher"] is None and junk["hostname"] is None and junk["title"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"web": None}, {"web": {"results": "x"}}, {"web": []},
                                  {"query": "x"}])
async def test_a_missing_or_malformed_web_block_is_an_empty_list(monkeypatch, configured, body):
    _install(monkeypatch, [(200, body)])
    out = await bs.web_search(QUERY)
    assert out == {"results": [], "altered_query": None}


# ── configuration ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", None])
async def test_unconfigured_raises_before_io(monkeypatch, key):
    monkeypatch.setattr(bs.settings, "BRAVE_SEARCH_API_KEY", key)
    fake = _install(monkeypatch, [(200, _body([]))])
    assert bs.is_configured() is False
    with pytest.raises(bs.BraveSearchNotConfiguredException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.not_run is True
    assert fake.requests == []


@pytest.mark.asyncio
async def test_a_key_with_a_line_break_is_unusable_and_never_echoed(monkeypatch):
    monkeypatch.setattr(bs.settings, "BRAVE_SEARCH_API_KEY", "abc\ndef-SECRETPART")
    fake = _install(monkeypatch, [(200, _body([]))])
    with pytest.raises(bs.BraveSearchNotConfiguredException) as ei:
        await bs.web_search(QUERY)
    assert "SECRETPART" not in str(ei.value) and fake.requests == []


@pytest.mark.asyncio
async def test_a_plain_http_base_url_is_refused_before_io(monkeypatch, configured):
    monkeypatch.setattr(bs.settings, "BRAVE_SEARCH_BASE_URL", "http://api.search.brave.com/res/v1")
    fake = _install(monkeypatch, [(200, _body([]))])
    with pytest.raises(bs.BraveSearchNotConfiguredException):
        await bs.web_search(QUERY)
    assert fake.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["", "   ", None, 12])
async def test_an_empty_query_is_refused_before_io(monkeypatch, configured, q):
    fake = _install(monkeypatch, [(200, _body([]))])
    with pytest.raises(bs.BraveSearchRequestException) as ei:
        await bs.web_search(q)
    assert ei.value.not_run is True and fake.requests == []


# ── status + transport mapping ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status,cls,not_run", [
    (401, bs.BraveSearchAuthException, True),
    (403, bs.BraveSearchAuthException, True),
    (429, bs.BraveSearchRateLimitException, True),
    (400, bs.BraveSearchRequestException, True),
    (404, bs.BraveSearchRequestException, True),
    (422, bs.BraveSearchRequestException, True),
    (500, bs.BraveSearchUnavailableException, False),
    (502, bs.BraveSearchUnavailableException, False),
    (503, bs.BraveSearchUnavailableException, False),
])
async def test_status_mapping(monkeypatch, configured, status, cls, not_run):
    err = {"type": "ErrorResponse", "error": {"code": "VALIDATION", "detail": f"bad q={QUERY}",
                                              "status": status}}
    _install(monkeypatch, [(status, err)])
    with pytest.raises(cls) as ei:
        await bs.web_search(QUERY)
    assert ei.value.not_run is not_run
    assert ei.value.status == status
    msg = str(ei.value)
    assert f"HTTP {status}" in msg
    # Brave's detail may quote the request back: never echoed.
    assert QUERY not in msg and "bad q" not in msg


@pytest.mark.asyncio
async def test_a_rate_limit_carries_retry_after(monkeypatch, configured):
    _install(monkeypatch, [(429, {"error": {"code": "RATE_LIMITED"}}, {"retry-after": "3"})])
    with pytest.raises(bs.BraveSearchRateLimitException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.retry_after == 3.0 and "RATE_LIMITED" in str(ei.value)


@pytest.mark.asyncio
async def test_a_rate_limit_falls_back_to_the_reset_window(monkeypatch, configured):
    _install(monkeypatch, [(429, {}, {"x-ratelimit-reset": "1, 2592000"})])
    with pytest.raises(bs.BraveSearchRateLimitException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.retry_after == 1.0


@pytest.mark.asyncio
async def test_a_rate_limit_with_junk_headers_has_no_retry_after(monkeypatch, configured):
    _install(monkeypatch, [(429, b"<html>", {"retry-after": "soon", "x-ratelimit-reset": "nan, x"})])
    with pytest.raises(bs.BraveSearchRateLimitException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.retry_after is None


@pytest.mark.asyncio
async def test_an_error_code_that_is_not_an_identifier_is_not_echoed(monkeypatch, configured):
    _install(monkeypatch, [(422, {"error": {"code": f"<script>{QUERY}"}})])
    with pytest.raises(bs.BraveSearchRequestException) as ei:
        await bs.web_search(QUERY)
    assert "script" not in str(ei.value) and QUERY not in str(ei.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"<html>proxy error</html>", b"", b"not json {"])
async def test_a_non_json_200_is_unavailable_and_may_have_been_billed(monkeypatch, configured, body):
    _install(monkeypatch, [(200, body)])
    with pytest.raises(bs.BraveSearchUnavailableException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.not_run is False


@pytest.mark.asyncio
async def test_a_json_200_that_is_not_an_object_is_unavailable(monkeypatch, configured):
    _install(monkeypatch, [(200, [1, 2, 3])])
    with pytest.raises(bs.BraveSearchUnavailableException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.not_run is False


def _raiser(exc: Exception):
    def _h(request: httpx.Request) -> httpx.Response:
        raise exc
    return _h


@pytest.mark.asyncio
@pytest.mark.parametrize("exc,not_run", [
    (httpx.ConnectError("connection refused to api.search.brave.com"), True),
    (httpx.ConnectTimeout("connect timed out"), True),
    (httpx.PoolTimeout("pool timed out"), True),
    (httpx.ReadTimeout("read timed out"), False),
    (httpx.WriteTimeout("write timed out"), False),
    (httpx.ReadError("connection reset"), False),
    (httpx.RemoteProtocolError("server disconnected"), False),
])
async def test_transport_errors(monkeypatch, configured, exc, not_run):
    _install(monkeypatch, [_raiser(exc)])
    with pytest.raises(bs.BraveSearchUnavailableException) as ei:
        await bs.web_search(QUERY)
    assert ei.value.not_run is not_run
    # Never chained: an httpx exception holds the request, and the request holds the key header.
    assert ei.value.__cause__ is None and ei.value.__suppress_context__ is True
    assert "brave.com" not in str(ei.value)


# ── secrets ───────────────────────────────────────────────────────────────────


def _every_failure_answer():
    return [
        (401, {"error": {"code": "SUBSCRIPTION_TOKEN_INVALID", "detail": KEY}}),
        (403, {"error": {"code": "X", "detail": KEY}}),
        (429, {"error": {"detail": KEY}}, {"retry-after": "1"}),
        (422, {"error": {"code": "VALIDATION", "detail": KEY}}),
        (500, KEY.encode()),
        (200, KEY.encode()),
        _raiser(httpx.ConnectError(f"boom {KEY}")),
        _raiser(httpx.ReadTimeout(f"boom {KEY}")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", _every_failure_answer())
async def test_no_secret_in_exception_text_or_attributes(monkeypatch, configured, answer):
    _install(monkeypatch, [answer])
    with pytest.raises(bs.BraveSearchException) as ei:
        await bs.web_search(QUERY)
    e = ei.value
    blob = " ".join([str(e), repr(e), json.dumps(vars(e), default=str)])
    assert KEY not in blob and QUERY not in blob


def test_the_key_wrapper_has_a_constant_repr_and_survives_a_sentry_frame_dump():
    """Sentry's `include_local_variables` serialises a frame local through `repr`. A plain str
    local named `key` is NOT a credential-shaped name, so the frame scrub would keep it — the
    wrapper is what keeps the key out (review 2026-10-02 #2)."""
    wrapped = bs._ApiKey(KEY)
    assert KEY not in repr(wrapped) and KEY not in str(wrapped)
    event = {"exception": {"values": [{"value": "x", "stacktrace": {"frames": [
        {"function": "web_search", "vars": {"key": repr(wrapped), "url": "https://api/x",
                                            "headers": {"X-Subscription-Token": KEY}}},
    ]}}]}}
    scrubbed = scrub_sentry_event(event)
    assert KEY not in json.dumps(scrubbed)


def test_no_module_function_keeps_the_bare_key_in_a_local():
    """`_api_key` wraps on the first read; the request reads `key.value` inline. A plain local
    holding the setting would be serialised into a Sentry frame dump."""
    import inspect
    src = inspect.getsource(bs)
    assert "= (settings.BRAVE_SEARCH_API_KEY" not in src.replace("_ApiKey((settings", "")
    assert "_ApiKey((settings.BRAVE_SEARCH_API_KEY" in src


# ── client lifecycle ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_is_idempotent_and_reopens(monkeypatch):
    monkeypatch.setattr(bs, "_client", None)
    c1 = bs._get_client()
    assert bs._get_client() is c1
    await bs.close_brave_search_client()
    assert bs._client is None
    await bs.close_brave_search_client()   # idempotent
    c2 = bs._get_client()
    assert c2 is not c1 and c2.follow_redirects is False
    await bs.close_brave_search_client()


@pytest.mark.asyncio
async def test_low_monthly_quota_is_warned(monkeypatch, configured, caplog):
    _install(monkeypatch, [(200, _body([]), {"x-ratelimit-remaining": "1, 12"})])
    with caplog.at_level("WARNING", logger=bs.__name__):
        await bs.web_search(QUERY)
    assert any("quota nearly spent" in r.getMessage() for r in caplog.records)


# ── classify_exception ────────────────────────────────────────────────────────


@pytest.mark.parametrize("exc", [
    bs.BraveSearchException("x"),
    bs.BraveSearchNotConfiguredException("x", not_run=True),
    bs.BraveSearchAuthException("brave search refused the key: HTTP 401", not_run=True),
    bs.BraveSearchRateLimitException("brave search rate limited: HTTP 429 rate limit"),
    bs.BraveSearchRequestException("HTTP 422", not_run=True),
    bs.BraveSearchUnavailableException("brave search transport failure: timed out timeout"),
])
def test_classify_exception_branch(exc):
    from app.api.error_response import ErrorCode, classify_exception
    code, status = classify_exception(exc)
    assert code == ErrorCode.GEMINI_UNAVAILABLE
    assert code not in (ErrorCode.FMP_RATE_LIMITED, ErrorCode.FMP_UNAVAILABLE)
