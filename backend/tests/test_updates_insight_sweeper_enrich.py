"""
The sweeper's proactive per-article enrichment step (`_enrich_windows`).

The news pass enriches each scope's whole in-memory windowed corpus so the feed
shows bullets + sentiment on scroll, not just the top few. This is a paid Gemini
fan-out with NO per-call rate limiter of its own, so the admission logic is the
safety-critical part: it must (1) skip fully-enriched scopes (self-limiting, zero
calls in steady state), (2) bound the per-cycle scope count, (3) bound the ET-day
batch-call count, (4) bound live concurrency, (5) prioritise MARKET first, and
(6) never let one scope's failure break the batch.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.news_cache_service import NewsCacheService
from app.services.updates_insight_sweeper import (
    InsightSweeper,
    MARKET_SCOPE,
    _ENRICH_CONCURRENCY,
    _ENRICH_DAILY_CAP,
    _ENRICH_SCOPES_PER_CYCLE,
    _ENRICH_WINDOW_CAP,
)
from app.utils.market_hours import ET

NOW = datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)


class _FakeNews:
    """Real `_enrichable_ids` (so id-filtering is exercised for real) + a
    recording, concurrency-tracking `enrich_window` that mimics the production
    contract: it NEVER raises (returns 0 on 'failure', like the real catch)."""

    # Reuse the real pure filter — the sweeper calls self.news._enrichable_ids.
    _enrichable_ids = NewsCacheService._enrichable_ids

    def __init__(self, fail_scopes=None, delay=0.0):
        self.calls = []
        self.live = 0
        self.peak = 0
        self.fail_scopes = set(fail_scopes or [])
        self.delay = delay

    async def enrich_window(self, scope, articles, *, cap):
        self.calls.append(scope)
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            else:
                await asyncio.sleep(0)  # yield so tasks can interleave
            if scope in self.fail_scopes:
                return 0  # the real enrich_window swallows errors → 0, never raises
            return len(self._enrichable_ids(articles, cap))
        finally:
            self.live -= 1


class _StubSweeper(InsightSweeper):
    """InsightSweeper with no network clients (see .claude/rules/testing.md)."""

    def __init__(self, news):
        self.news = news
        self.supabase = None
        self.fmp = None
        self.insights = None
        self.vol = None
        self._enrich_day = None
        self._enrich_count = 0


def _fresh(scope_ids):
    """{scope: [un-enriched article dict per id]}."""
    return {s: [{"id": i, "ai_processed": False} for i in ids] for s, ids in scope_ids.items()}


@pytest.mark.asyncio
async def test_enrich_windows_selects_only_unenriched_ids():
    news = _FakeNews()
    sweeper = _StubSweeper(news)
    corpora = {
        "AAPL": [{"id": "a1", "ai_processed": False}, {"id": "a2", "ai_processed": True}],
        "MSFT": [{"id": "m1", "ai_processed": True}],   # fully enriched → NOT admitted
        "NVDA": [{"id": "n1", "ai_processed": False}],
    }
    rows, deferred = await sweeper._enrich_windows(corpora, ["AAPL", "MSFT", "NVDA"], NOW)
    assert set(news.calls) == {"AAPL", "NVDA"}   # MSFT never enriched (no fresh ids)
    assert deferred == 0
    assert rows == 2                              # a1 + n1


@pytest.mark.asyncio
async def test_enrich_windows_no_calls_when_everything_enriched():
    news = _FakeNews()
    sweeper = _StubSweeper(news)
    corpora = {"AAPL": [{"id": "a1", "ai_processed": True}], "MSFT": [{"id": "m1", "ai_processed": True}]}
    rows, deferred = await sweeper._enrich_windows(corpora, ["AAPL", "MSFT"], NOW)
    assert news.calls == []       # steady state: zero paid calls
    assert (rows, deferred) == (0, 0)


@pytest.mark.asyncio
async def test_enrich_windows_respects_scopes_per_cycle():
    n = _ENRICH_SCOPES_PER_CYCLE + 5
    corpora = _fresh({f"S{i}": [f"i{i}"] for i in range(n)})
    news = _FakeNews()
    sweeper = _StubSweeper(news)
    rows, deferred = await sweeper._enrich_windows(corpora, [f"S{i}" for i in range(n)], NOW)
    assert len(news.calls) == _ENRICH_SCOPES_PER_CYCLE
    assert deferred == 5          # the tail defers to the next cycle


@pytest.mark.asyncio
async def test_enrich_windows_prioritises_market_first():
    n = _ENRICH_SCOPES_PER_CYCLE + 5
    scopes = [MARKET_SCOPE] + [f"S{i}" for i in range(n)]
    corpora = _fresh({s: [f"id_{s}"] for s in scopes})
    news = _FakeNews()
    sweeper = _StubSweeper(news)
    await sweeper._enrich_windows(corpora, scopes, NOW)
    # Cap binds, but MARKET (first in _universe order) is always admitted.
    assert MARKET_SCOPE in news.calls
    assert len(news.calls) == _ENRICH_SCOPES_PER_CYCLE


@pytest.mark.asyncio
async def test_enrich_windows_bounds_live_concurrency():
    n = 12
    corpora = _fresh({f"S{i}": [f"i{i}"] for i in range(n)})
    news = _FakeNews(delay=0.02)   # hold each call so they pile up against the semaphore
    sweeper = _StubSweeper(news)
    await sweeper._enrich_windows(corpora, [f"S{i}" for i in range(n)], NOW)
    assert news.peak <= _ENRICH_CONCURRENCY   # the bound — the safety-critical property
    assert news.peak >= 2                      # ...and it genuinely ran concurrently


@pytest.mark.asyncio
async def test_enrich_daily_cap_stops_admission_and_resets_next_day():
    news = _FakeNews()
    sweeper = _StubSweeper(news)
    # Pre-spend today's batch-call budget.
    sweeper._enrich_day = NOW.astimezone(ET).date()
    sweeper._enrich_count = _ENRICH_DAILY_CAP
    corpora = _fresh({"AAPL": ["a1"]})
    rows, deferred = await sweeper._enrich_windows(corpora, ["AAPL"], NOW)
    assert news.calls == []       # cap hit → nothing admitted
    assert deferred == 1
    # A new ET trading day resets the counter.
    rows2, deferred2 = await sweeper._enrich_windows(corpora, ["AAPL"], NOW + timedelta(days=1))
    assert news.calls == ["AAPL"]
    assert deferred2 == 0


@pytest.mark.asyncio
async def test_enrich_windows_tolerates_a_failing_scope():
    news = _FakeNews(fail_scopes={"BAD"})
    sweeper = _StubSweeper(news)
    corpora = _fresh({"AAPL": ["a1"], "BAD": ["b1"], "NVDA": ["n1"]})
    rows, deferred = await sweeper._enrich_windows(corpora, ["AAPL", "BAD", "NVDA"], NOW)
    assert set(news.calls) == {"AAPL", "BAD", "NVDA"}   # all attempted
    assert rows == 2              # AAPL + NVDA succeeded; BAD contributed 0, didn't break the batch
    assert deferred == 0


def test_enrich_window_cap_matches_the_corpus_window():
    # The sweeper's per-scope cap must equal the news get_cached_bulk(scopes, 25)
    # window, or it would ask to enrich ids it never materialised.
    from app.services.news_cache_service import _ENRICH_WINDOW_CAP as SVC_CAP
    assert _ENRICH_WINDOW_CAP == SVC_CAP == 25
