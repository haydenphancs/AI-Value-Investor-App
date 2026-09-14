"""`refresh_cache` must never replace a good cache with half a fetch.

`_fetch_filter` returned `{}` for a failed page 1, so `_cache = {**crypto, **stocks}` with a
failed stocks filter silently dropped every stock, stamped the cache fresh for 30 minutes,
and `is_cache_populated()` (then `bool(_cache)`) told the sentiment service the miss was a
real zero. The daily social snapshot then wrote crypto-only rows and marked the day done.
"""
import time

import pytest

from app.integrations import apewisdom as ape


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ape, "_cache", {})
    monkeypatch.setattr(ape, "_cache_ts", 0.0)
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": False, "all-crypto": False})
    monkeypatch.setattr(ape, "_fetch_lock", None)
    monkeypatch.setattr(ape, "_FILTER_DELAY", 0.0)


def _rows(filter_name, *tickers):
    return {t: {"mentions": 5, "mentions_24h_ago": 4, "upvotes": 1, "rank": 1,
                "_filter": filter_name} for t in tickers}


class _NoClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def _wire(monkeypatch, answers):
    """answers: dict filter_name → dict | None | Exception, consumed per call."""
    calls = []

    async def _fetch(client, filter_name):
        calls.append(filter_name)
        a = answers[filter_name].pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(ape, "_fetch_filter", _fetch)
    monkeypatch.setattr(ape.httpx, "AsyncClient", lambda **k: _NoClient())
    return calls


@pytest.mark.asyncio
async def test_a_failed_filter_keeps_its_previous_entries_and_shortens_freshness(monkeypatch):
    _wire(monkeypatch, {"all-stocks": [_rows("all-stocks", "AAPL", "TSLA"), None],
                        "all-crypto": [_rows("all-crypto", "BTC"), _rows("all-crypto", "BTC", "ETH")]})
    first = await ape.refresh_cache()
    assert set(first) == {"AAPL", "TSLA", "BTC"}
    assert ape.is_cache_populated("ZZZZ") is True
    full_ts = ape._cache_ts
    ape._cache_ts = 0.0                       # force the next refresh
    second = await ape.refresh_cache()        # stocks FAILED, crypto answered
    assert set(second) == {"AAPL", "TSLA", "BTC", "ETH"}, "the failed filter's entries were dropped"
    assert ape._loaded == {"all-stocks": True, "all-crypto": True}
    # Fresh for only the partial window, not the full TTL.
    age_allowance = ape._CACHE_TTL - (time.time() - ape._cache_ts)
    assert 0 < age_allowance <= ape._PARTIAL_RETRY_SECONDS + 1
    assert ape._is_cache_fresh()


@pytest.mark.asyncio
async def test_a_cold_boot_with_one_failed_filter_is_not_populated_for_a_miss(monkeypatch):
    _wire(monkeypatch, {"all-stocks": [None], "all-crypto": [_rows("all-crypto", "BTC")]})
    got = await ape.refresh_cache()
    assert set(got) == {"BTC"}
    assert ape.is_cache_populated("AAPL") is False      # stocks never landed → unknown
    assert ape.is_cache_populated("BTC") is True        # present → known
    assert ape._loaded == {"all-stocks": False, "all-crypto": True}


@pytest.mark.asyncio
async def test_an_empty_but_successful_filter_is_a_real_empty_answer(monkeypatch):
    """`{}` from a 200 with no results is an answer; only None is a failure."""
    _wire(monkeypatch, {"all-stocks": [_rows("all-stocks", "AAPL")], "all-crypto": [{}]})
    got = await ape.refresh_cache()
    assert set(got) == {"AAPL"}
    assert ape._loaded == {"all-stocks": True, "all-crypto": True}
    assert ape.is_cache_populated("DOGE") is True
    assert time.time() - ape._cache_ts < 5           # full freshness


@pytest.mark.asyncio
async def test_both_filters_failing_keeps_the_whole_previous_cache(monkeypatch):
    _wire(monkeypatch, {"all-stocks": [_rows("all-stocks", "AAPL"), None],
                        "all-crypto": [_rows("all-crypto", "BTC"), None]})
    await ape.refresh_cache()
    ape._cache_ts = 0.0
    got = await ape.refresh_cache()
    assert set(got) == {"AAPL", "BTC"}


@pytest.mark.asyncio
async def test_fetch_filter_distinguishes_a_failed_page_from_an_empty_one():
    class _Resp:
        def __init__(self, status, payload): self.status_code = status; self._p = payload
        def json(self): return self._p

    class _C:
        def __init__(self, resp): self.resp = resp
        async def get(self, *a, **k):
            if isinstance(self.resp, Exception):
                raise self.resp
            return self.resp
    assert await ape._fetch_filter(_C(_Resp(429, {})), "all-stocks") is None
    assert await ape._fetch_filter(_C(RuntimeError("timeout")), "all-stocks") is None
    assert await ape._fetch_filter(_C(_Resp(200, {"pages": 1, "results": []})), "all-stocks") == {}
    got = await ape._fetch_filter(
        _C(_Resp(200, {"pages": 1, "results": [{"ticker": "aapl", "mentions": "7"}]})), "all-stocks")
    assert got == {"AAPL": {"mentions": 7, "mentions_24h_ago": 0, "upvotes": 0, "rank": 0,
                            "_filter": "all-stocks"}}
