"""`get_senate_disclosure` / `get_house_disclosure` fail CLOSED like their `*_latest` siblings.

Both caught EVERY exception from the one-call `senate-trades` / `house-trades` path —
including a rate limit, a bad key and a licence refusal — and fell back to four parallel
pages of `*-latest`, whose own `except Exception` swallowed a partial sweep into `[]`. Under
a 429 one Holders build turned into 18 congressional requests, and the caller's
`critical=True` guard (which assumes these raise) was dead.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from app.integrations.fmp import (
    FMPAuthException,
    FMPClient,
    FMPNotEntitledException,
    FMPPartialPageException,
    FMPRateLimitException,
)


def _client(behavior):
    """`behavior(endpoint, params) -> rows | Exception`; counts calls per endpoint."""
    c = FMPClient()
    calls = {}

    async def _impl(endpoint, params=None):
        calls[endpoint] = calls.get(endpoint, 0) + 1
        out = behavior(endpoint, params or {})
        if isinstance(out, BaseException):
            raise out
        return out
    c._make_request_impl = _impl  # type: ignore[method-assign]
    return c, calls


@pytest.mark.parametrize("exc", [
    FMPRateLimitException("429"), FMPAuthException("401"), FMPNotEntitledException("no package"),
])
@pytest.mark.parametrize("chamber", ["senate", "house"])
def test_a_typed_refusal_on_the_one_call_path_re_raises_without_a_sweep(exc, chamber):
    c, calls = _client(lambda ep, p: exc if ep == f"{chamber}-trades" else [])
    getter = getattr(c, f"get_{chamber}_disclosure")
    with pytest.raises(type(exc)):
        asyncio.run(getter("AAPL"))
    assert calls.get(f"{chamber}-latest", 0) == 0, "the 4-page sweep ran anyway"


@pytest.mark.parametrize("chamber", ["senate", "house"])
def test_a_generic_failure_falls_back_and_a_lost_page_propagates(chamber):
    def _behave(ep, p):
        if ep == f"{chamber}-trades":
            return RuntimeError("connection reset")
        if p.get("page") == 2:
            return asyncio.TimeoutError()
        return [{"symbol": "AAPL", "page": p.get("page")}]
    c, calls = _client(_behave)
    getter = getattr(c, f"get_{chamber}_disclosure")
    with pytest.raises(FMPPartialPageException):
        asyncio.run(getter("AAPL"))
    assert calls[f"{chamber}-latest"] == 4


@pytest.mark.parametrize("chamber", ["senate", "house"])
def test_a_healthy_fallback_filters_to_the_symbol(chamber):
    def _behave(ep, p):
        if ep == f"{chamber}-trades":
            return RuntimeError("404 retired")
        return [{"symbol": "AAPL"}, {"symbol": "msft"}, {"symbol": "aapl"}]
    c, _ = _client(_behave)
    rows = asyncio.run(getattr(c, f"get_{chamber}_disclosure")("aapl"))
    assert [r["symbol"] for r in rows] == ["AAPL", "aapl"] * 4


@pytest.mark.parametrize("chamber", ["senate", "house"])
def test_the_one_call_path_answers_without_a_sweep(chamber):
    c, calls = _client(lambda ep, p: [{"symbol": "AAPL"}] if ep == f"{chamber}-trades" else [])
    rows = asyncio.run(getattr(c, f"get_{chamber}_disclosure")("AAPL"))
    assert rows == [{"symbol": "AAPL"}] and calls.get(f"{chamber}-latest", 0) == 0
