"""Two services write `ticker_news_cache`; only one owns it.

`NewsCacheService` owns the table: it runs a 6-hour TTL and fills `sentiment` /
`sentiment_confidence` from Gemini. `SentimentService._persist_articles` writes the SAME rows
— same table, same `(ticker, external_id)` conflict key, `external_id` being the article URL
in both — and used to:

  1. stamp `expires_at` **14 days** out, while `NewsCacheService._get_cached` gates freshness
     solely on `.gte("expires_at", now)` — so the News tab stopped refreshing for a fortnight;
  2. overwrite the Gemini-derived sentiment with a keyword classifier's guess, destroying
     enrichment the user's credits paid for.

Separately, the ApeWisdom cache could never rebuild: `refresh_cache` has an unconditional 30s
sleep between filters while every caller wrapped its read in a shorter `wait_for`, so the
await was cancelled every time and `_cache_ts` never advanced.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.integrations import apewisdom
from app.services import sentiment_service as sent
from app.services.news_cache_service import CACHE_TTL_HOURS


# ── Cache ownership ───────────────────────────────────────────────────────────

def test_sentiment_uses_the_owners_ttl_not_its_own():
    """A longer TTL here silently freezes the News tab. Bound to the owner's constant so the
    two cannot drift apart again."""
    assert sent._NEWS_CACHE_TTL.total_seconds() == CACHE_TTL_HOURS * 3600
    assert sent._NEWS_CACHE_TTL.days < 1, "TTL is back to a multi-day value"


def test_sentiment_no_longer_writes_the_ai_sentiment_columns():
    """The keyword classifier must not overwrite Gemini's output."""
    src = inspect.getsource(sent.SentimentService._persist_articles)
    rows_block = src[src.index("rows.append("):]
    assert '"sentiment":' not in rows_block, (
        "sentiment is being written again — this overwrites Gemini enrichment with a "
        "keyword guess"
    )
    assert '"sentiment_confidence":' not in rows_block


def test_sentiment_still_writes_the_columns_it_does_own():
    """Guard against over-correcting into writing nothing useful."""
    src = inspect.getsource(sent.SentimentService._persist_articles)
    for col in ('"headline"', '"summary"', '"published_at"', '"article_url"', '"expires_at"'):
        assert col in src, f"{col} should still be persisted"


def test_no_fourteen_day_expiry_survives():
    src = inspect.getsource(sent.SentimentService._persist_articles)
    assert "days=14" not in src


# ── ApeWisdom refresh ─────────────────────────────────────────────────────────

def test_readers_do_not_await_the_refresh():
    """THE deadlock. `refresh_cache` cannot finish in under `_FILTER_DELAY` (30s), and every
    caller caps its read well below that — `news_cache_service` at 2.0s. Awaiting it meant
    guaranteed cancellation, so the cache never rebuilt after the boot warm."""
    assert apewisdom._FILTER_DELAY >= 30
    for fn in (apewisdom.get_all_mentions, apewisdom.get_ticker_mentions):
        src = inspect.getsource(fn)
        assert "await refresh_cache()" not in src, (
            f"{fn.__name__} awaits a refresh that cannot complete inside its caller's timeout"
        )
        assert "_kick_background_refresh()" in src


@pytest.mark.asyncio
async def test_a_stale_read_returns_immediately_and_schedules_a_refresh(monkeypatch):
    """Serving stale mention counts beats serving none after a 2s timeout."""
    monkeypatch.setattr(apewisdom, "_cache", {"GME": {"mentions": 42}})
    monkeypatch.setattr(apewisdom, "_cache_ts", 0.0)          # definitively stale
    monkeypatch.setattr(apewisdom, "_refresh_task", None)

    started = asyncio.Event()

    async def _fake_refresh():
        started.set()
        await asyncio.sleep(60)      # as slow as the real one

    monkeypatch.setattr(apewisdom, "refresh_cache", _fake_refresh)

    out = await asyncio.wait_for(apewisdom.get_ticker_mentions("GME"), timeout=1.0)
    assert out == {"mentions": 42}, "stale data should be served, not dropped"

    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert apewisdom._refresh_task is not None
    apewisdom._refresh_task.cancel()


@pytest.mark.asyncio
async def test_a_fresh_cache_schedules_nothing(monkeypatch):
    import time

    monkeypatch.setattr(apewisdom, "_cache", {"GME": {"mentions": 1}})
    monkeypatch.setattr(apewisdom, "_cache_ts", time.time())
    monkeypatch.setattr(apewisdom, "_refresh_task", None)
    await apewisdom.get_all_mentions()
    assert apewisdom._refresh_task is None


@pytest.mark.asyncio
async def test_only_one_refresh_runs_at_a_time(monkeypatch):
    """Ten concurrent stale reads must not launch ten 30-second fetches at a rate-limited
    upstream."""
    monkeypatch.setattr(apewisdom, "_cache", {})
    monkeypatch.setattr(apewisdom, "_cache_ts", 0.0)
    monkeypatch.setattr(apewisdom, "_refresh_task", None)

    calls = 0

    async def _fake_refresh():
        nonlocal calls
        calls += 1
        await asyncio.sleep(5)

    monkeypatch.setattr(apewisdom, "refresh_cache", _fake_refresh)
    await asyncio.gather(*(apewisdom.get_all_mentions() for _ in range(10)))
    await asyncio.sleep(0)
    assert calls == 1, f"{calls} concurrent refreshes launched"
    apewisdom._refresh_task.cancel()


@pytest.mark.asyncio
async def test_a_failing_refresh_never_surfaces_to_the_caller(monkeypatch):
    monkeypatch.setattr(apewisdom, "_cache", {"X": {"mentions": 3}})
    monkeypatch.setattr(apewisdom, "_cache_ts", 0.0)
    monkeypatch.setattr(apewisdom, "_refresh_task", None)

    async def _boom():
        raise RuntimeError("apewisdom down")

    monkeypatch.setattr(apewisdom, "refresh_cache", _boom)
    assert await apewisdom.get_ticker_mentions("X") == {"mentions": 3}
    await asyncio.sleep(0.05)
    assert apewisdom._refresh_task.done()
    assert apewisdom._refresh_task.exception() is None, "the failure must be swallowed+logged"
