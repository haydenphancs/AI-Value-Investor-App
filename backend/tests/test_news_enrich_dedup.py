"""
Concurrent-batch dedup for the PAID news-enrichment path.

`enrich_articles` skips rows already marked `ai_processed`, but that check races:
two users opening the same un-enriched ticker both read `ai_processed=false`
before either writes, so both fire the (expensive) `gemini-2.5-flash` batch for
the SAME article ids. The `_enrich_inflight` dedup collapses concurrent identical
batches onto ONE Gemini call.

These tests exercise the dedup wrapper directly by counting invocations of
`_enrich_articles_uncached` (the method that would call Gemini) — no network, no
Supabase.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.news_cache_service import NewsCacheService


class _CountingService(NewsCacheService):
    """NewsCacheService with the real dedup wrapper but a counted, no-network
    inner method. Bypasses __init__ so no clients are constructed."""

    def __init__(self, *, fail_times: int = 0, delay: float = 0.02):
        self._enrich_inflight = {}
        self.calls = 0
        self._fail_times = fail_times
        self._delay = delay

    async def _enrich_articles_uncached(self, ticker, article_ids):
        self.calls += 1
        my_call = self.calls
        # Hold the batch open long enough for a concurrent caller to join.
        await asyncio.sleep(self._delay)
        if my_call <= self._fail_times:
            raise RuntimeError(f"boom #{my_call}")
        return [{"id": i, "ticker": ticker, "call": my_call} for i in article_ids]


@pytest.mark.asyncio
async def test_concurrent_identical_batches_call_gemini_once():
    svc = _CountingService()
    ids = ["c", "a", "b"]
    r1, r2, r3 = await asyncio.gather(
        svc.enrich_articles("AAPL", ids),
        svc.enrich_articles("AAPL", ids),
        svc.enrich_articles("AAPL", ids),
    )
    # One paid batch for three concurrent identical requests.
    assert svc.calls == 1
    # Every joiner gets the leader's result.
    assert r1 == r2 == r3
    assert r1[0]["call"] == 1


@pytest.mark.asyncio
async def test_id_order_does_not_matter_for_dedup():
    svc = _CountingService()
    # Same set, different order + a duplicate id → same batch → one call.
    r1, r2 = await asyncio.gather(
        svc.enrich_articles("AAPL", ["a", "b", "c"]),
        svc.enrich_articles("AAPL", ["c", "b", "a", "a"]),
    )
    assert svc.calls == 1
    assert r1 == r2


@pytest.mark.asyncio
async def test_different_batches_are_not_deduped():
    svc = _CountingService()
    await asyncio.gather(
        svc.enrich_articles("AAPL", ["a", "b"]),
        svc.enrich_articles("AAPL", ["c", "d"]),   # different ids
        svc.enrich_articles("MSFT", ["a", "b"]),   # different ticker
    )
    assert svc.calls == 3


@pytest.mark.asyncio
async def test_inflight_key_is_released_so_later_calls_rerun():
    svc = _CountingService()
    await svc.enrich_articles("AAPL", ["a", "b"])
    assert svc._enrich_inflight == {}   # cleaned up
    await svc.enrich_articles("AAPL", ["a", "b"])
    # A fresh call after completion must re-run (the batch may have new articles
    # next time); the future must not be cached forever.
    assert svc.calls == 2


@pytest.mark.asyncio
async def test_a_joiner_retries_when_the_leader_fails():
    # Leader raises; the joiner must NOT inherit the leader's exception — it
    # falls through and tries once itself.
    svc = _CountingService(fail_times=1)
    results = await asyncio.gather(
        svc.enrich_articles("AAPL", ["a", "b"]),
        svc.enrich_articles("AAPL", ["a", "b"]),
        return_exceptions=True,
    )
    # First (leader) attempt fails, joiner falls through → a second attempt.
    assert svc.calls == 2
    # At least one caller ends up with a real result rather than the error.
    successes = [r for r in results if not isinstance(r, Exception)]
    assert successes, results
    assert svc._enrich_inflight == {}


@pytest.mark.asyncio
async def test_empty_ids_short_circuit_without_touching_inflight():
    svc = _CountingService()
    assert await svc.enrich_articles("AAPL", []) == []
    assert svc.calls == 0
    assert svc._enrich_inflight == {}
