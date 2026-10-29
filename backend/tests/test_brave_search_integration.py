"""`app.integrations.brave_search` — the thin Brave Search client behind Ask Cay AI's web search and
the Updates Insights jump search.

Pins the request shape, the documented-fields-only parse, the `not_run` flag on every failure path
(each caller refunds a budget unit on exactly that flag), that no exception text ever carries the
key, the URL or the query, the constant-repr key wrapper (Sentry frame variables), the lifespan
closer, `classify_exception`'s by-name branch, and that the monthly-quota warning fires only
on a real quota (a monthly limit of 0 is unlimited).

Hermetic: the module-level client is replaced with an `httpx.MockTransport`; nothing reaches the
network. Settings are set explicitly per test (`Settings` reads `backend/.env`).
"""

from __future__ import annotations

import json
import random
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


# ── the monthly-quota warning ─────────────────────────────────────────────────
# Brave's rate-limiting guide: each X-RateLimit-* header carries one comma-separated value per
# window, in the order the policy names them, and a monthly limit of 0 means UNLIMITED. The owner's
# metered plan reads "remaining 0" for that window on every answer, and the old reader (the last
# remaining value, alone) logged "monthly request quota nearly spent" on every search (2026-10-29).

_DOC = {"x-ratelimit-limit": "1, 15000", "x-ratelimit-policy": "1;w=1, 15000;w=2592000"}
_METERED = {"x-ratelimit-limit": "50, 0", "x-ratelimit-policy": "50;w=1, 0;w=2592000"}
_REVERSED = {"x-ratelimit-limit": "15000, 1", "x-ratelimit-policy": "15000;w=2592000, 1;w=1"}
_UNLIMITED_LINE = ("brave search: the plan has no monthly request quota (0 = unlimited); "
                   "its spend limit is set in Brave's dashboard")


@pytest.fixture(autouse=True)
def _plan_not_yet_logged(monkeypatch):
    # Both INFO lines are logged once per PROCESS: every test starts before either.
    monkeypatch.setattr(bs, "_quota_logged", False)
    monkeypatch.setattr(bs, "_unreadable_logged", False)


def _quota_records(caplog) -> List[Any]:
    return [r for r in caplog.records if r.name == bs.__name__]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers, warned", [
    pytest.param({**_DOC, "x-ratelimit-remaining": "0, 12"}, "remaining=12 of 15000", id="doc-low"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "0, 50"}, "remaining=50 of 15000", id="at-threshold"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "0, 0"}, "remaining=0 of 15000", id="spent"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "0, 51"}, None, id="above-threshold"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "0, 14523"}, None, id="doc-healthy"),
    # The production shape that warned on every search: no monthly quota at all.
    pytest.param({**_METERED, "x-ratelimit-remaining": "49, 0"}, None, id="metered-unlimited"),
    pytest.param({"x-ratelimit-limit": "50, 0", "x-ratelimit-remaining": "49, 0"}, None,
                 id="metered-without-policy"),
    pytest.param({"x-ratelimit-limit": "50", "x-ratelimit-policy": "50;w=1",
                  "x-ratelimit-remaining": "0"}, None, id="burst-window-only"),
    pytest.param({"x-ratelimit-remaining": "1, 12"}, None, id="remaining-without-a-limit"),
    pytest.param({}, None, id="no-headers"),
    # The policy decides which value is the month, not the position.
    pytest.param({**_REVERSED, "x-ratelimit-remaining": "12, 0"}, "remaining=12 of 15000",
                 id="policy-reversed-low"),
    pytest.param({**_REVERSED, "x-ratelimit-remaining": "14000, 0"}, None, id="policy-reversed-healthy"),
    pytest.param({"x-ratelimit-limit": "1, 500", "x-ratelimit-policy": "1;w=1, 500;w=86400",
                  "x-ratelimit-remaining": "0, 12"}, None, id="a-daily-window-is-not-monthly"),
    pytest.param({"x-ratelimit-limit": "1, 15000, 100000",
                  "x-ratelimit-policy": "1;w=1, 15000;w=2592000, 100000;w=31536000",
                  "x-ratelimit-remaining": "0, 12, 90000"}, "remaining=12 of 15000",
                 id="the-month-beside-a-year"),
    # The window is the parameter NAMED w, wherever it sits: a reader that takes the first or the
    # last parameter, or refuses the others, finds no month here.
    pytest.param({"x-ratelimit-limit": "15000, 1",
                  "x-ratelimit-policy": "15000;q=15000;w=2592000;x=7, 1;q=1;w=1;x=7",
                  "x-ratelimit-remaining": "12, 0"}, "remaining=12 of 15000",
                 id="the-window-is-the-w-parameter"),
    # Without a usable policy: Brave's documented order, two values only.
    pytest.param({"x-ratelimit-limit": "1, 2000", "x-ratelimit-remaining": "0, 12"},
                 "remaining=12 of 2000", id="no-policy-documented-order"),
    pytest.param({"x-ratelimit-limit": "1, 15000", "x-ratelimit-policy": "1;w=1, 15000",
                  "x-ratelimit-remaining": "0, 12"}, "remaining=12 of 15000",
                 id="unreadable-policy-documented-order"),
    pytest.param({"x-ratelimit-limit": "1, 15000", "x-ratelimit-policy": "1;w=1, 15000;w=x",
                  "x-ratelimit-remaining": "0, 12"}, "remaining=12 of 15000",
                 id="an-unreadable-w-falls-back-to-the-documented-order"),
    pytest.param({"x-ratelimit-limit": "1, 15000", "x-ratelimit-remaining": "0, 12",
                  "x-ratelimit-policy": "1;w=1, 15000;w=86400, 15000;w=2592000"},
                 "remaining=12 of 15000", id="a-policy-of-another-length-is-not-used"),
    pytest.param({"x-ratelimit-limit": "1, 15000, 9", "x-ratelimit-remaining": "0, 12, 3"}, None,
                 id="three-values-without-policy"),
    # Malformed: never a guess.
    pytest.param({**_DOC, "x-ratelimit-remaining": "12"}, None, id="counts-disagree"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "x, 12"}, None, id="an-unreadable-part"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "x, 0, 12"}, None,
                 id="an-unreadable-part-never-shifts-the-rest"),
    pytest.param({**_DOC, "x-ratelimit-remaining": "0, -5"}, None, id="negative-remaining"),
    pytest.param({"x-ratelimit-limit": "1, inf", "x-ratelimit-remaining": "0, 12"}, None,
                 id="infinite-limit"),
])
async def test_the_monthly_quota_warning_needs_a_real_quota(monkeypatch, configured, caplog,
                                                           headers, warned):
    _install(monkeypatch, [(200, _body([]), headers)])
    with caplog.at_level("INFO", logger=bs.__name__):
        await bs.web_search(QUERY)
    warnings = [r.getMessage() for r in _quota_records(caplog) if r.levelname == "WARNING"]
    if warned is None:
        assert warnings == []
    else:
        assert len(warnings) == 1
        assert "monthly request quota nearly spent" in warnings[0] and warned in warnings[0]


@pytest.mark.asyncio
async def test_a_metered_plan_is_logged_once_as_unlimited(monkeypatch, configured, caplog):
    _install(monkeypatch, [(200, _body([]), {**_METERED, "x-ratelimit-remaining": "49, 0"})] * 2)
    with caplog.at_level("INFO", logger=bs.__name__):
        await bs.web_search(QUERY)
        await bs.web_search(QUERY)
    assert [(r.levelname, r.getMessage()) for r in _quota_records(caplog)] == [
        ("INFO", _UNLIMITED_LINE)]


@pytest.mark.asyncio
async def test_a_quota_plan_logs_its_quota_once(monkeypatch, configured, caplog):
    _install(monkeypatch, [(200, _body([]), {**_DOC, "x-ratelimit-remaining": "0, 14523"}),
                           (200, _body([]), {**_DOC, "x-ratelimit-remaining": "0, 14522"})])
    with caplog.at_level("INFO", logger=bs.__name__):
        await bs.web_search(QUERY)
        await bs.web_search(QUERY)
    assert [r.getMessage() for r in _quota_records(caplog)] == [
        "brave search: monthly request quota 15000, 14523 left"]


@pytest.mark.asyncio
async def test_an_answer_without_rate_limit_headers_leaves_the_plan_unlogged(monkeypatch, configured,
                                                                             caplog):
    _install(monkeypatch, [(200, _body([]), {"x-ratelimit-reset": "1, 2592000"}),
                           (200, _body([]), {**_METERED, "x-ratelimit-remaining": "49, 0"})])
    with caplog.at_level("INFO", logger=bs.__name__):
        await bs.web_search(QUERY)
        assert _quota_records(caplog) == []
        assert bs._quota_logged is False and bs._unreadable_logged is False
        await bs.web_search(QUERY)   # the first answer that carries them is the one logged
    assert [r.getMessage() for r in _quota_records(caplog)] == [_UNLIMITED_LINE]


_BURST_ONLY = {"x-ratelimit-limit": "50", "x-ratelimit-policy": "50;w=1", "x-ratelimit-remaining": "49"}
_NO_QUOTA_READ = ("brave search: no monthly request quota could be read from these rate-limit headers, "
                  "so answers in this shape get no quota warning (limit=%s policy=%s remaining=%s)")
_NO_WINDOW_LINE = _NO_QUOTA_READ % ("'50'", "'50;w=1'", "'49'")


@pytest.mark.asyncio
async def test_headers_with_no_monthly_window_are_logged_once_and_never_hide_the_plan(
        monkeypatch, configured, caplog):
    # A shape the reader cannot place turns the warning off for those answers: that must show in
    # the logs once, never silently and never on every answer, and must not hide the plan line.
    _install(monkeypatch, [(200, _body([]), _BURST_ONLY), (200, _body([]), _BURST_ONLY),
                           (200, _body([]), {**_DOC, "x-ratelimit-remaining": "0, 12"})])
    with caplog.at_level("INFO", logger=bs.__name__):
        for _ in range(3):
            await bs.web_search(QUERY)
    assert [(r.levelname, r.getMessage()) for r in _quota_records(caplog)] == [
        ("INFO", _NO_WINDOW_LINE),
        ("INFO", "brave search: monthly request quota 15000, 12 left"),
        ("WARNING", "brave search: monthly request quota nearly spent (remaining=12 of 15000)"),
    ]


@pytest.mark.asyncio
async def test_a_shape_with_no_monthly_window_after_the_plan_line_still_shows(monkeypatch,
                                                                              configured, caplog):
    _install(monkeypatch, [(200, _body([]), {**_DOC, "x-ratelimit-remaining": "0, 14523"}),
                           (200, _body([]), _BURST_ONLY), (200, _body([]), _BURST_ONLY)])
    with caplog.at_level("INFO", logger=bs.__name__):
        for _ in range(3):
            await bs.web_search(QUERY)
    assert [r.getMessage() for r in _quota_records(caplog)] == [
        "brave search: monthly request quota 15000, 14523 left", _NO_WINDOW_LINE]


@pytest.mark.asyncio
async def test_broken_counts_beside_a_monthly_window_are_logged_as_unread(monkeypatch, configured,
                                                                          caplog):
    # The policy names a 30-day window here and the counts are what cannot be read, so the line
    # must not claim the headers have no monthly window.
    _install(monkeypatch, [(200, _body([]), {**_DOC, "x-ratelimit-remaining": "12"})])
    with caplog.at_level("INFO", logger=bs.__name__):
        await bs.web_search(QUERY)
    assert [r.getMessage() for r in _quota_records(caplog)] == [
        _NO_QUOTA_READ % ("'1, 15000'", "'1;w=1, 15000;w=2592000'", "'12'")]


@pytest.mark.asyncio
async def test_a_logged_header_value_is_cut_to_80_characters(monkeypatch, configured, caplog):
    _install(monkeypatch, [(200, _body([]), {"x-ratelimit-limit": "x" * 300})])
    with caplog.at_level("INFO", logger=bs.__name__):
        await bs.web_search(QUERY)
    (line,) = [r.getMessage() for r in _quota_records(caplog)]
    assert f"limit='{'x' * 80}'" in line and "x" * 81 not in line


@pytest.mark.asyncio
async def test_a_spent_quota_is_warned_on_the_429_too(monkeypatch, configured, caplog):
    _install(monkeypatch, [(429, {"error": {"code": "RATE_LIMITED"}},
                            {**_DOC, "x-ratelimit-remaining": "1, 0"})])
    with caplog.at_level("WARNING", logger=bs.__name__):
        with pytest.raises(bs.BraveSearchRateLimitException):
            await bs.web_search(QUERY)
    assert ["remaining=0 of 15000" in r.getMessage() for r in _quota_records(caplog)] == [True]


@pytest.mark.asyncio
async def test_a_header_reader_bug_never_breaks_a_search(monkeypatch, configured, caplog):
    # The reader runs on every answer BEFORE `_raise_for`: a bug in it must never stand in for the
    # typed exception (the callers refund on its `not_run`) or lose a billed answer's results.
    def boom(_headers):
        raise RuntimeError("bug")

    monkeypatch.setattr(bs, "_monthly_quota", boom)
    _install(monkeypatch, [(200, _body([_ROW]), {**_DOC, "x-ratelimit-remaining": "0, 12"}),
                           (429, {"error": {"code": "RATE_LIMITED"}}, {})])
    with caplog.at_level("WARNING", logger=bs.__name__):
        out = await bs.web_search(QUERY)
        with pytest.raises(bs.BraveSearchRateLimitException) as ei:
            await bs.web_search(QUERY)
    assert len(out["results"]) == 1 and ei.value.not_run is True
    assert [r.getMessage() for r in _quota_records(caplog)] == [
        "brave search: rate-limit headers not read (RuntimeError)"] * 2


def test_the_quota_reader_never_raises_and_answers_counts_or_none():
    # Built per window from realistic values, with one field corrupted about a third of the time,
    # so most cases reach the policy parser and the window choice. (A random token soup returned
    # None every time and let a policy parser that raises on "w=x" through.)
    rng = random.Random(20261029)
    limits = ["0", "1", "50", "2000", "15000", "20000000"]
    lefts = ["0", "1", "12", "50", "51", "14523"]
    windows = ["1", "60", "86400", "2592000", "2678400", "31536000"]
    junk = ["", " ", "x", "-5", "-0", "0.5", "1e3", "1_000", "inf", "nan", "9" * 400, ";", "="]
    counts = parsed = 0
    for _ in range(3000):
        n = rng.randint(1, 3)
        cols = {"limit": [rng.choice(limits) for _ in range(n)],
                "remaining": [rng.choice(lefts) for _ in range(n)],
                "window": [rng.choice(windows) for _ in range(n)]}
        if rng.random() < 0.3:
            col = rng.choice(sorted(cols))
            cols[col][rng.randrange(n)] = rng.choice(junk)
        if rng.random() < 0.1:
            cols["remaining"] = cols["remaining"][:-1]   # the counts disagree
        raw = {"x-ratelimit-limit": ", ".join(cols["limit"]),
               "x-ratelimit-remaining": ", ".join(cols["remaining"])}
        if rng.random() < 0.85:
            raw["x-ratelimit-policy"] = ", ".join(
                f"{lim};w={w}" for lim, w in zip(cols["limit"], cols["window"]))
        headers = httpx.Headers(raw)
        parsed += bs._policy_windows(headers.get("x-ratelimit-policy")) is not None
        quota = bs._monthly_quota(headers)
        if quota is not None:
            counts += 1
            assert type(quota[0]) is int and type(quota[1]) is int and min(quota) >= 0, (raw, quota)
    # The cases really reach the paths they test (1,434 answers and 2,356 parsed policies today).
    assert counts > 700 and parsed > 1200, (counts, parsed)


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
