"""`GET /updates/feed` page 0 reads the Insights card BESIDE the timeline (2026-10-08).

The card read needs only the scope, and a card that misses its 5-minute memory tier is a
Supabase round trip that used to run in series after the news read. Both now run in one
`asyncio.gather`:

  * page 0 overlaps the two reads (the news stub refuses to finish until the card read has
    STARTED, so a serial endpoint times out into an error body);
  * page > 0 never reads the card (a page-2 fallback card would summarise yesterday);
  * a failed feed still answers the typed APIErrorResponse body, whatever the card did;
  * a failed card read never costs the timeline, and is never silent;
  * a request that goes away cancels both reads — nothing outlives it.

Hermetic: the endpoint is called directly with stubbed services, as
`test_updates_insight_subject_wiring.py` does.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import app.api.v1.endpoints.updates as updates_endpoint
from app.api.error_response import ErrorCode
from app.integrations.fmp import FMPRateLimitException
from app.schemas.updates import UpdatesFeedResponse
from app.services.news_cache_service import MARKET_SCOPE


def _row(i, *, hours_ago=1.0):
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago, minutes=i)
    return {
        "id": f"db-{i}", "headline": f"Treasury yields edge higher, take {i}",
        "summary": "body", "summary_bullets": ["A.", "B."], "sentiment": "neutral",
        "sentiment_confidence": 50, "source_name": "Reuters", "source_logo_url": None,
        "published_at": when.isoformat(), "thumbnail_url": None,
        "article_url": f"https://n/{i}", "related_tickers": [], "ai_processed": True,
    }


def _card(scope):
    return {
        "scope": scope, "headline": "Yields climb", "bullets": ["a", "b"],
        "sentiment": "Neutral", "article_count": 4,
        "generated_at": datetime.now(timezone.utc).isoformat(), "is_stale": False,
        "refreshing": False, "ai_generated": True, "trigger_reason": None, "sources": [],
    }


class _News:
    """`gate`: the news read waits for it (the card read sets it). `exc`: raised instead."""

    def __init__(self, rows, *, gate=None, exc=None, block=None):
        self.rows = rows
        self.gate = gate
        self.exc = exc
        self.block = block
        self.cancelled = False

    async def _answer(self):
        try:
            if self.block is not None:
                await self.block.wait()
            if self.gate is not None:
                await asyncio.wait_for(self.gate.wait(), timeout=1.0)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.exc is not None:
            raise self.exc
        return {"articles": list(self.rows), "cached": True, "cache_age_seconds": 5}

    async def get_market_news(self, **kwargs):
        return await self._answer()

    async def get_ticker_news(self, scope, **kwargs):
        return await self._answer()


class _Insights:
    def __init__(self, *, started=None, exc=None, block=None):
        self.calls = []
        self.started = started
        self.exc = exc
        self.block = block
        self.cancelled = False

    async def get_cards(self, scopes):
        self.calls.append(list(scopes))
        if self.started is not None:
            self.started.set()
        try:
            if self.block is not None:
                await self.block.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.exc is not None:
            raise self.exc
        return {s: _card(s) for s in scopes}

    def build_fallback_card(self, scope, corpus):
        return None


def _wire(monkeypatch, news, insights):
    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", lambda: news)
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", lambda: insights)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [MARKET_SCOPE, "AAPL"])
async def test_the_card_read_overlaps_the_news_read(monkeypatch, scope):
    """The news read cannot finish until the card read has started. Serial reads would
    time the news read out (→ an error body, not a feed)."""
    started = asyncio.Event()
    insights = _Insights(started=started)
    _wire(monkeypatch, _News([_row(i) for i in range(5)], gate=started), insights)

    resp = await updates_endpoint.get_updates_feed(scope=scope, limit=50, offset=0)

    assert isinstance(resp, UpdatesFeedResponse), f"the reads ran in series: {resp!r}"
    assert insights.calls == [[scope]], "page 0 reads the card exactly once"
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert len(resp.articles) == 5


@pytest.mark.asyncio
async def test_page_two_never_reads_the_card(monkeypatch):
    insights = _Insights()
    _wire(monkeypatch, _News([_row(i) for i in range(5)]), insights)

    resp = await updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=50, offset=50)

    assert isinstance(resp, UpdatesFeedResponse)
    assert insights.calls == [], "a page past 0 started a card read"
    assert resp.insight is None and len(resp.articles) == 5


@pytest.mark.asyncio
async def test_a_failed_feed_still_answers_the_typed_error(monkeypatch, caplog):
    """Moving the read into a gather must not lose the APIErrorResponse mapping: a quota
    error is FMP_RATE_LIMITED, never a bare 500, and the card result is ignored."""
    insights = _Insights()
    _wire(monkeypatch, _News([], exc=FMPRateLimitException("quota", retry_after="30")),
          insights)

    with caplog.at_level("ERROR", logger=updates_endpoint.logger.name):
        resp = await updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=50,
                                                       offset=0)

    assert not isinstance(resp, UpdatesFeedResponse)
    body = json.loads(resp.body)
    assert resp.status_code >= 400
    assert body["error_code"] == ErrorCode.FMP_RATE_LIMITED.value
    assert {"error_code", "message", "user_message"} <= set(body)
    assert body["details"]["step"] == "updates_feed"
    assert "Updates feed failed for scope=__MARKET__" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_feed_with_a_failed_card_read_logs_both(monkeypatch, caplog):
    _wire(monkeypatch, _News([], exc=RuntimeError("supabase 520")),
          _Insights(exc=RuntimeError("cards table down")))

    with caplog.at_level("WARNING", logger=updates_endpoint.logger.name):
        resp = await updates_endpoint.get_updates_feed(scope="AAPL", limit=50, offset=0)

    assert not isinstance(resp, UpdatesFeedResponse)
    assert json.loads(resp.body)["error_code"]
    assert "Updates insight read failed for scope=AAPL" in caplog.text
    assert "Updates feed failed for scope=AAPL" in caplog.text


@pytest.mark.asyncio
async def test_a_raising_card_read_keeps_the_timeline(monkeypatch, caplog):
    _wire(monkeypatch, _News([_row(i) for i in range(3)]),
          _Insights(exc=RuntimeError("cards table down")))

    with caplog.at_level("WARNING", logger=updates_endpoint.logger.name):
        resp = await updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=50,
                                                       offset=0)

    assert isinstance(resp, UpdatesFeedResponse)
    assert len(resp.articles) == 3 and resp.insight is None
    assert "Updates insight read failed for scope=__MARKET__" in caplog.text
    assert "cards table down" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_card_read_is_logged_even_with_no_recent_news(monkeypatch, caplog):
    """No recent news means no card either way — the failure is still never silent."""
    _wire(monkeypatch, _News([_row(0, hours_ago=200)]),
          _Insights(exc=RuntimeError("cards table down")))

    with caplog.at_level("WARNING", logger=updates_endpoint.logger.name):
        resp = await updates_endpoint.get_updates_feed(scope="AAPL", limit=50, offset=0)

    assert isinstance(resp, UpdatesFeedResponse) and resp.insight is None
    assert "Updates insight read failed for scope=AAPL" in caplog.text


@pytest.mark.asyncio
async def test_a_request_that_goes_away_cancels_both_reads(monkeypatch):
    """A gather, not a detached task: nothing outlives the request."""
    block = asyncio.Event()                      # never set
    news = _News([_row(0)], block=block)
    started = asyncio.Event()
    insights = _Insights(started=started, block=block)
    _wire(monkeypatch, news, insights)

    task = asyncio.create_task(
        updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=50, offset=0)
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert news.cancelled and insights.cancelled, "a read outlived its request"


@pytest.mark.asyncio
async def test_an_empty_feed_shows_no_card_even_when_one_is_stored(monkeypatch):
    """The card is read unconditionally on page 0 now; the show/hide gate is still the
    feed's own recent window."""
    insights = _Insights()
    _wire(monkeypatch, _News([]), insights)

    resp = await updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=50, offset=0)

    assert isinstance(resp, UpdatesFeedResponse)
    assert insights.calls == [[MARKET_SCOPE]]
    assert resp.insight is None and resp.articles == []
