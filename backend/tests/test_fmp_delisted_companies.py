"""`FMPClient.get_delisted_companies` — the thin client behind the "Delisted" label.

It must stay THIN (paging, parsing and every rule live in `listing_status_service`), send
FMP's page cap, refuse a bad page BEFORE any call, and never let a failure read as an empty
page: an empty answer means "not delisted", which is the one thing a failure must not say.

Hermetic: `_make_request_impl` (the HTTP leg) is replaced on the instance, so the licence
guard in `_make_request` still runs for real.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from app.integrations.fmp import FMPClient, FMPUnavailableException
from app.integrations.fmp_entitlements import ENTITLED_PATHS, normalize_path


@pytest.fixture
def client(monkeypatch):
    c = FMPClient()
    calls: List[Tuple[str, Dict[str, Any]]] = []
    answer: Dict[str, Any] = {"body": [{"symbol": "ROKU", "exchange": "NASDAQ",
                                        "delistedDate": "2026-11-09"}]}

    async def _impl(endpoint, params=None):
        calls.append((endpoint, dict(params or {})))
        body = answer["body"]
        if isinstance(body, BaseException):
            raise body
        return body

    monkeypatch.setattr(c, "_make_request_impl", _impl)
    return c, calls, answer


@pytest.mark.asyncio
async def test_one_page_with_the_literal_path_and_page_params(client):
    c, calls, _ = client
    rows = await c.get_delisted_companies(3)
    assert rows[0]["symbol"] == "ROKU"
    assert calls == [("delisted-companies", {"page": 3, "limit": 100})]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, sent", [
    (100, 100), (250, 100), (1_000_000, 100),   # FMP's page cap
    (50, 50), (1, 1),
    (0, 1), (-7, 1),                            # never a zero or negative page size
    ("abc", 100), (None, 100),                  # unreadable → the cap
])
async def test_the_limit_is_clamped_to_one_through_a_hundred(client, limit, sent):
    c, calls, _ = client
    await c.get_delisted_companies(0, limit=limit)
    assert calls[-1][1]["limit"] == sent


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [-1, -100, 1.5, "0", None, True])
async def test_a_bad_page_raises_before_any_call(client, page):
    c, calls, _ = client
    with pytest.raises(ValueError):
        await c.get_delisted_companies(page)
    assert calls == [], "a refused page must cost nothing"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"Error Message": "Limit Reach"}, "oops", None, 42])
async def test_a_non_list_body_is_unavailable_never_an_empty_page(client, body):
    c, _, answer = client
    answer["body"] = body
    with pytest.raises(FMPUnavailableException):
        await c.get_delisted_companies(0)


@pytest.mark.asyncio
async def test_an_empty_list_is_returned_as_is(client):
    """The END of the list is a real answer; the service (not the client) decides that an
    empty PAGE 0 is a failure."""
    c, _, answer = client
    answer["body"] = []
    assert await c.get_delisted_companies(30) == []


@pytest.mark.asyncio
async def test_a_transport_failure_propagates_typed(client):
    c, _, answer = client
    answer["body"] = FMPUnavailableException("503 after retries")
    with pytest.raises(FMPUnavailableException):
        await c.get_delisted_companies(0)


def test_the_path_is_licensed():
    """Raises before any I/O if it were not (`_raise_if_not_entitled`)."""
    assert normalize_path("delisted-companies") in ENTITLED_PATHS
    FMPClient._raise_if_not_entitled("delisted-companies", {"page": 0, "limit": 100})
