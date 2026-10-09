"""The two thin FMP methods A6 added: `get_key_executives` and `get_press_releases`.

Both are on the signed Order Form (Company Information / Market News) and both RAISE a typed
`FMPException` on every failure — their callers keep caches, and a swallowed failure would be
cached as "no executives" / "no press releases". Driven through a fake httpx transport (no
network); `asyncio.sleep` is stubbed so the retry backoff adds no wall clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from app.integrations.fmp import (
    FMPClient,
    FMPException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.integrations.fmp_entitlements import is_entitled

_SENTINEL_KEY = "SENTINELKEY0123456789"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast(_s):
        return None
    monkeypatch.setattr("app.integrations.fmp.asyncio.sleep", _fast)


def _client(handler) -> tuple:
    calls = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    c = FMPClient()
    c.api_key = _SENTINEL_KEY
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(_wrapped))
    return c, calls


def _json(status: int, body) -> callable:
    return lambda request: httpx.Response(status, json=body)


# ── licence ────────────────────────────────────────────────────────────────────

def test_both_paths_are_on_the_order_form():
    assert is_entitled("key-executives")
    assert is_entitled("news/press-releases")


# ── key executives ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_key_executives_calls_the_stable_path_with_the_symbol():
    rows = [{"name": "Tim Cook", "title": "Chief Executive Officer", "yearBorn": 1960},
            "not-a-row", None]
    c, calls = _client(_json(200, rows))
    out = await c.get_key_executives(" aapl ")
    assert out == [rows[0]], "non-dict rows are dropped, dict rows pass through untouched"
    assert len(calls) == 1
    req = calls[0]
    assert req.url.path.endswith("/stable/key-executives")
    assert "/api/v3" not in str(req.url)
    assert req.url.params["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_key_executives_empty_and_404_are_a_measured_empty_answer():
    c, _ = _client(_json(200, []))
    assert await c.get_key_executives("AAPL") == []
    c, _ = _client(_json(404, {"message": "not found"}))
    assert await c.get_key_executives("AAPL") == []
    c, _ = _client(lambda r: httpx.Response(200, content=b"null",
                                            headers={"content-type": "application/json"}))
    assert await c.get_key_executives("AAPL") == []


@pytest.mark.asyncio
async def test_key_executives_blank_ticker_makes_no_call():
    c, calls = _client(_json(200, [{"name": "x"}]))
    assert await c.get_key_executives("") == []
    assert await c.get_key_executives("   ") == []
    assert await c.get_key_executives(None) == []  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.asyncio
async def test_key_executives_rate_limit_raises_typed():
    c, _ = _client(lambda r: httpx.Response(429, json={}, headers={"Retry-After": "7"}))
    with pytest.raises(FMPRateLimitException) as exc:
        await c.get_key_executives("AAPL")
    assert exc.value.retry_after == "7"


@pytest.mark.asyncio
async def test_key_executives_persistent_5xx_raises_unavailable_after_retries():
    c, calls = _client(_json(503, {}))
    with pytest.raises(FMPUnavailableException):
        await c.get_key_executives("AAPL")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_key_executives_other_status_is_a_typed_error_without_the_key():
    c, _ = _client(_json(400, {"Error Message": "bad"}))
    with pytest.raises(FMPException) as exc:
        await c.get_key_executives("AAPL")
    assert not isinstance(exc.value, httpx.HTTPError)
    assert _SENTINEL_KEY not in str(exc.value)
    assert "apikey" not in str(exc.value).lower()
    assert "400" in str(exc.value)


@pytest.mark.asyncio
async def test_key_executives_wrong_shape_raises():
    c, _ = _client(_json(200, {"Error Message": "Limit Reach"}))
    with pytest.raises(FMPException):
        await c.get_key_executives("AAPL")


@pytest.mark.asyncio
async def test_key_executives_network_failure_is_unavailable_without_the_key():
    def _boom(request):
        raise httpx.ConnectError(f"connect failed for {request.url}")

    c, _ = _client(_boom)
    with pytest.raises(FMPUnavailableException) as exc:
        await c.get_key_executives("AAPL")
    assert _SENTINEL_KEY not in str(exc.value)


@pytest.mark.asyncio
async def test_a_typed_http_status_message_never_carries_the_url():
    """httpx's own message for a non-2xx is the request URL, `apikey=` included."""
    def _teapot(request):
        return httpx.Response(418, json={})

    c, _ = _client(_teapot)
    with pytest.raises(FMPException) as exc:
        await c.get_key_executives("AAPL")
    assert _SENTINEL_KEY not in str(exc.value)
    assert _SENTINEL_KEY not in repr(exc.value)


# ── press releases ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_press_releases_always_send_the_symbols_filter():
    c, calls = _client(_json(200, [{"symbol": "MSFT", "title": "t", "publishedDate": "2026-10-01"}]))
    out = await c.get_press_releases("msft", limit=5)
    assert len(out) == 1
    req = calls[0]
    assert req.url.path.endswith("/stable/news/press-releases")
    assert req.url.params["symbols"] == "MSFT"
    assert req.url.params["limit"] == "5"
    assert req.url.params["page"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,expected", [
    (0, "1"), (-5, "1"), (999, "50"), (True, "10"), ("x", "10"), (None, "10"), (7.9, "7"),
])
async def test_press_release_limit_is_clamped(limit, expected):
    c, calls = _client(_json(200, []))
    await c.get_press_releases("AAPL", limit=limit)  # type: ignore[arg-type]
    assert calls[0].url.params["limit"] == expected


@pytest.mark.asyncio
async def test_press_releases_blank_ticker_makes_no_call():
    """Without `symbols` FMP's news endpoints serve a default symbol's feed — so no call."""
    c, calls = _client(_json(200, [{"symbol": "AAPL"}]))
    assert await c.get_press_releases("") == []
    assert calls == []


@pytest.mark.asyncio
async def test_press_releases_failures_raise_typed():
    c, _ = _client(lambda r: httpx.Response(429, json={}))
    with pytest.raises(FMPRateLimitException):
        await c.get_press_releases("AAPL")
    c, _ = _client(_json(502, {}))
    with pytest.raises(FMPUnavailableException):
        await c.get_press_releases("AAPL")
    c, _ = _client(_json(400, {}))
    with pytest.raises(FMPException) as exc:
        await c.get_press_releases("AAPL")
    assert _SENTINEL_KEY not in str(exc.value)
    c, _ = _client(_json(200, "a string body"))
    with pytest.raises(FMPException):
        await c.get_press_releases("AAPL")
    c, _ = _client(_json(404, {}))
    assert await c.get_press_releases("AAPL") == []


@pytest.mark.asyncio
async def test_a_failure_moves_the_request_failure_counter():
    c, _ = _client(_json(503, {}))
    before = c.request_failures
    with pytest.raises(FMPUnavailableException):
        await c.get_press_releases("AAPL")
    assert c.request_failures == before + 1


# ── source guard (AST: comments and docstrings cannot satisfy it) ──────────────

def _method(name: str) -> ast.AsyncFunctionDef:
    src = (Path(__file__).resolve().parents[1] / "app" / "integrations" / "fmp.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in fmp.py")


def _request_calls(fn: ast.AsyncFunctionDef):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "_make_request"]


@pytest.mark.parametrize("name,path,param", [
    ("get_key_executives", "key-executives", "symbol"),
    ("get_press_releases", "news/press-releases", "symbols"),
])
def test_each_method_requests_its_entitled_path_with_its_filter(name, path, param):
    calls = _request_calls(_method(name))
    assert len(calls) == 1, f"{name} must make exactly one request"
    call = calls[0]
    assert isinstance(call.args[0], ast.Constant) and call.args[0].value == path
    params = next(k.value for k in call.keywords if k.arg == "params")
    assert isinstance(params, ast.Dict)
    keys = {k.value for k in params.keys if isinstance(k, ast.Constant)}
    assert param in keys


@pytest.mark.parametrize("name", ["get_key_executives", "get_press_releases"])
def test_neither_method_swallows_a_failure_into_an_empty_list(name):
    """A bare `except Exception: return []` is the outage-as-empty-answer bug."""
    fn = _method(name)
    for handler in (n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)):
        caught = ast.unparse(handler.type) if handler.type is not None else "BaseException"
        if caught in ("Exception", "BaseException"):
            raise AssertionError(f"{name} catches {caught}")
