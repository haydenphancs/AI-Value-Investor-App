"""
FMP transient-failure retry + degrade behavior.

FMP's gateway intermittently returns 5xx (e.g. the 502 that paged on the
`profile` endpoint for CSWI) or drops the connection. A single blip must NOT
fail the request or page on-call: _make_request retries with backoff, then
degrades to a typed FMPUnavailableException logged at WARNING. Auth (401) and
quota (429) are NOT retried.

Pure logic — the httpx client is faked; no network. asyncio.sleep is stubbed so
the backoff adds no wall-clock. Run via `python -m pytest` from backend/.
"""

import asyncio

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.integrations.fmp import (
    FMPClient,
    FMPRateLimitException,
    FMPUnavailableException,
)


class _FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else []
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://financialmodelingprep.com/stable/x")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=req, response=self
            )


class _FakeClient:
    """Returns/raises a scripted item per .get() call (last item repeats)."""

    def __init__(self, script):
        self._script = script
        self.calls = 0

    async def get(self, url, params=None):
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast_sleep(_seconds):
        return None
    monkeypatch.setattr("app.integrations.fmp.asyncio.sleep", _fast_sleep)


def _client_with(fake):
    c = FMPClient()

    async def _get_client():
        return fake

    c._get_client = _get_client
    return c


def test_persistent_502_retries_then_degrades():
    fake = _FakeClient([_FakeResponse(502)])
    c = _client_with(fake)
    with pytest.raises(FMPUnavailableException):
        asyncio.run(c._make_request("profile", {"symbol": "CSWI"}))
    assert fake.calls == 3  # 3 attempts total (1 + 2 retries)


def test_502_then_200_recovers():
    fake = _FakeClient(
        [_FakeResponse(502), _FakeResponse(200, [{"symbol": "CSWI"}])]
    )
    c = _client_with(fake)
    result = asyncio.run(c._make_request("profile", {"symbol": "CSWI"}))
    assert result == [{"symbol": "CSWI"}]
    assert fake.calls == 2  # recovered on the retry


def test_network_error_retries_then_degrades():
    fake = _FakeClient([httpx.ConnectError("connection reset")])
    c = _client_with(fake)
    with pytest.raises(FMPUnavailableException):
        asyncio.run(c._make_request("profile", {"symbol": "CSWI"}))
    assert fake.calls == 3


def test_429_is_not_retried():
    fake = _FakeClient([_FakeResponse(429, headers={"Retry-After": "5"})])
    c = _client_with(fake)
    with pytest.raises(FMPRateLimitException):
        asyncio.run(c._make_request("profile", {"symbol": "CSWI"}))
    assert fake.calls == 1  # quota errors don't get retried


def test_non_retryable_4xx_is_not_retried():
    fake = _FakeClient([_FakeResponse(400)])
    c = _client_with(fake)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c._make_request("profile", {"symbol": "X"}))
    assert fake.calls == 1


def test_classifier_maps_fmp_unavailable():
    code, _status = classify_exception(FMPUnavailableException("boom"))
    assert code == ErrorCode.FMP_UNAVAILABLE
