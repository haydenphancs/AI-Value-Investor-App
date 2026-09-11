"""Two budget guards on the CoinGecko path (100,000 calls/month is the binding limit).

1. A 429 `Retry-After` is honoured but BOUNDED. CoinGecko sends 429 for a burst AND for
   monthly-quota exhaustion, and the header was slept on unbounded inside `_make_request`
   while the in-flight leader (and every joiner shielded on its future) waited — hours,
   in the quota case — after every HTTP handler had already timed out.

2. An EMPTY `market_chart` series is memoised (briefly). It is "looked, found nothing" —
   a failure raises and never reaches the cache — but `_cg_history` refused to cache it,
   so a coin with no chart data was re-fetched on every 30-second chart poll.
"""
from __future__ import annotations

import asyncio

import pytest

from app.integrations import coingecko as cg
from app.services import crypto_service as cs


# ── 1. Retry-After is capped ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_huge_retry_after_sleeps_at_most_the_cap(monkeypatch):
    client = cg.CoinGeckoClient()
    calls = {"n": 0}

    async def _once(endpoint, params):
        calls["n"] += 1
        raise cg.CoinGeckoRateLimitException("429", retry_after=86_400)

    slept = []

    async def _sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(client, "_request_once", _once)
    monkeypatch.setattr(cg.asyncio, "sleep", _sleep)
    with pytest.raises(cg.CoinGeckoRateLimitException):
        await client._make_request("coins/bitcoin", {})
    assert slept, "no retry sleep happened"
    assert max(slept) <= client._MAX_RETRY_AFTER_SECONDS, slept
    assert calls["n"] == client._MAX_RETRIES


@pytest.mark.asyncio
async def test_a_small_retry_after_is_still_honoured(monkeypatch):
    client = cg.CoinGeckoClient()

    async def _once(endpoint, params):
        raise cg.CoinGeckoRateLimitException("429", retry_after=7)

    slept = []

    async def _sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(client, "_request_once", _once)
    monkeypatch.setattr(cg.asyncio, "sleep", _sleep)
    with pytest.raises(cg.CoinGeckoRateLimitException):
        await client._make_request("coins/bitcoin", {})
    assert slept and slept[0] >= 7


# ── 2. an empty series is memoised ───────────────────────────────────────────

class _CG:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    async def get_market_chart(self, symbol, days, interval=None):
        self.calls += 1
        return self.payload


@pytest.fixture(autouse=True)
def _clear():
    cs._cache.clear()
    yield
    cs._cache.clear()


@pytest.mark.asyncio
async def test_an_empty_series_is_not_refetched_on_every_poll(monkeypatch):
    svc = object.__new__(cs.CryptoService)
    fake = _CG({"prices": [], "total_volumes": []})
    svc.coingecko = fake
    for _ in range(5):
        assert await svc._cg_history("NEWCOIN", 1, intraday=True) == []
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_the_empty_memo_expires_sooner_than_a_real_series(monkeypatch):
    import time as _t
    svc = object.__new__(cs.CryptoService)
    fake = _CG({"prices": [], "total_volumes": []})
    svc.coingecko = fake
    await svc._cg_history("NEWCOIN", 90, intraday=False)
    t0 = _t.time()
    monkeypatch.setattr(cs.time, "time", lambda: t0 + cs._CG_EMPTY_TTL + 1)
    await svc._cg_history("NEWCOIN", 90, intraday=False)
    assert fake.calls == 2, "an empty series must be re-asked after its short TTL"


@pytest.mark.asyncio
async def test_a_failure_is_still_never_cached(monkeypatch):
    svc = object.__new__(cs.CryptoService)

    class _Boom:
        def __init__(self):
            self.calls = 0

        async def get_market_chart(self, *a, **k):
            self.calls += 1
            raise cg.CoinGeckoUnavailableException("down")

    boom = _Boom()
    svc.coingecko = boom
    for _ in range(2):
        with pytest.raises(cg.CoinGeckoUnavailableException):
            await svc._cg_history("BTC", 1, intraday=True)
    assert boom.calls == 2


@pytest.mark.asyncio
async def test_an_unresolved_coin_id_is_served_empty_but_never_memoised(monkeypatch, caplog):
    """`get_market_chart` answers None — not raise — when `resolve_coin_id` returned
    None, which it does on a transient `/search` 429 for any symbol outside the
    hardcoded map and the id cache. That is a FAILED lookup: memoising it under
    `:empty` served a long-tail coin an empty chart for `_CG_EMPTY_TTL` while the
    header's own `resolve_coin_id` call recovered. Two polls must ask twice."""
    import logging
    svc = object.__new__(cs.CryptoService)
    fake = _CG(None)
    svc.coingecko = fake
    with caplog.at_level(logging.WARNING, logger=cs.logger.name):
        for _ in range(2):
            assert await svc._cg_history("PEPE", 1, intraday=True) == []
    assert fake.calls == 2, "a None payload (unresolved coin id) was memoised as 'no data'"
    assert any("coin id unresolved" in r.getMessage() and "PEPE" in r.getMessage() for r in caplog.records)
    assert cs._cache_get("cg:hist:PEPE:1:i:empty", cs._CG_EMPTY_TTL) is None
