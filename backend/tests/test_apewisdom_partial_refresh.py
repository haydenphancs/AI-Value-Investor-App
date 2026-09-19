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


# ── F18-1: a lost page 2..N is a FAILED filter, not a short answer ─────────────────────
#
# `_fetch_filter` only distinguished a failed PAGE 1. Every later 429 / timeout was
# `continue`d and the truncated dict returned as a complete answer, so `refresh_cache`
# replaced 874 stock entries with the ~100 that arrived, stamped the cache fresh for the
# full TTL, and `is_cache_populated()` answered "real zero" for the ~774 dropped tickers.


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._p = status, payload or {}

    def json(self):
        return self._p


class _PagedClient:
    """`pages` maps page number → _Resp | Exception; records the pages requested."""

    def __init__(self, pages):
        self.pages, self.requested = pages, []

    async def get(self, url, **_k):
        page = int(url.rsplit("/page/", 1)[1])
        self.requested.append(page)
        ans = self.pages[page]
        if isinstance(ans, Exception):
            raise ans
        return ans

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _page(total, *tickers):
    return _Resp(200, {"pages": total, "results": [{"ticker": t, "mentions": 3} for t in tickers]})


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [_Resp(429), RuntimeError("timeout"), _Resp(503)])
async def test_a_lost_later_page_fails_the_whole_filter(monkeypatch, lost):
    monkeypatch.setattr(ape, "_PAGE_DELAY", 0.0)
    client = _PagedClient({1: _page(3, "A1", "A2"), 2: lost, 3: _page(3, "C1")})
    assert await ape._fetch_filter(client, "all-stocks") is None, (
        "a truncated list came back as a complete answer"
    )
    assert client.requested == [1, 2, 3], "every page is still attempted before deciding"


@pytest.mark.asyncio
async def test_all_pages_landing_is_the_merged_complete_answer(monkeypatch):
    monkeypatch.setattr(ape, "_PAGE_DELAY", 0.0)
    client = _PagedClient({1: _page(3, "A1"), 2: _page(3, "B1"), 3: _page(3, "C1")})
    got = await ape._fetch_filter(client, "all-stocks")
    assert set(got) == {"A1", "B1", "C1"}


@pytest.mark.asyncio
async def test_a_single_page_filter_is_unaffected(monkeypatch):
    """Boundary: `pages: 1` never enters the later-page loop; `pages: 0` / missing too."""
    monkeypatch.setattr(ape, "_PAGE_DELAY", 0.0)
    assert set(await ape._fetch_filter(_PagedClient({1: _page(1, "A1")}), "all-stocks")) == {"A1"}
    assert set(await ape._fetch_filter(_PagedClient({1: _page(0, "A1")}), "all-stocks")) == {"A1"}
    assert set(await ape._fetch_filter(
        _PagedClient({1: _Resp(200, {"results": [{"ticker": "A1"}]})}), "all-stocks")) == {"A1"}


@pytest.mark.asyncio
async def test_a_truncated_refresh_keeps_the_previous_entries_and_the_short_stamp(monkeypatch):
    """End to end through the REAL `_fetch_filter`: a warm 874-stock cache, then a refresh
    whose all-stocks page 1 lands and pages 2-9 answer 429."""
    monkeypatch.setattr(ape, "_PAGE_DELAY", 0.0)
    previous = _rows("all-stocks", *[f"S{i}" for i in range(874)])
    previous.update(_rows("all-crypto", "BTC", "ETH"))
    monkeypatch.setattr(ape, "_cache", previous)
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": True, "all-crypto": True})
    monkeypatch.setattr(ape, "_cache_ts", 0.0)      # stale → refresh proceeds

    pages = {1: _page(9, *[f"N{i}" for i in range(100)])}
    pages.update({p: _Resp(429) for p in range(2, 10)})
    clients = {"all-stocks": _PagedClient(pages), "all-crypto": _PagedClient({1: _page(1, "BTC")})}
    order = iter(["all-stocks", "all-crypto"])

    class _Router:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            name = "all-stocks" if "/all-stocks/" in url else "all-crypto"
            return await clients[name].get(url, **k)

    monkeypatch.setattr(ape.httpx, "AsyncClient", lambda **k: _Router())
    got = await ape.refresh_cache()

    stocks = {t for t, v in got.items() if v["_filter"] == "all-stocks"}
    assert len(stocks) == 874 and "N0" not in stocks, (
        f"the good cache was replaced by the truncated fetch ({len(stocks)} stocks)"
    )
    assert set(t for t, v in got.items() if v["_filter"] == "all-crypto") == {"BTC"}
    assert ape._loaded == {"all-stocks": True, "all-crypto": True}   # unchanged
    age_allowance = ape._CACHE_TTL - (time.time() - ape._cache_ts)
    assert 0 < age_allowance <= ape._PARTIAL_RETRY_SECONDS + 1, "stamped FULL fresh"
    assert ape.is_cache_populated("S500") is True                   # kept, so still known


@pytest.mark.asyncio
async def test_a_truncated_cold_boot_leaves_the_class_cold_not_half_known(monkeypatch):
    monkeypatch.setattr(ape, "_PAGE_DELAY", 0.0)
    pages = {1: _page(2, "N0"), 2: _Resp(429)}
    clients = {"all-stocks": _PagedClient(pages), "all-crypto": _PagedClient({1: _page(1, "BTC")})}

    class _Router:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            name = "all-stocks" if "/all-stocks/" in url else "all-crypto"
            return await clients[name].get(url, **k)

    monkeypatch.setattr(ape.httpx, "AsyncClient", lambda **k: _Router())
    got = await ape.refresh_cache()
    assert set(got) == {"BTC"}
    assert ape._loaded == {"all-stocks": False, "all-crypto": True}
    assert ape.is_cache_populated("N0") is False, "a dropped ticker read as a real zero"
    assert ape.is_cache_populated("AAPL") is False
