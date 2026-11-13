"""The news enrichment write-back survives a dropped Supabase connection.

Sentry PYTHON-FASTAPI-59 (2026-11-12): "Failed to update enrichment for article 1: Server
disconnected without sending a response." The gateway closed a pooled keep-alive connection
as `_update_enrichment_row` reused it; the UPDATE had no retry and the failure was logged
at ERROR with only a batch index. Now the UPDATE is retried (idempotent: by id, fixed
values), and what still fails is logged by kind — transient → WARNING, a real bug → ERROR —
with the ticker and the row id.
"""

import logging
import threading

import httpx
import pytest
from postgrest.exceptions import APIError

import app.utils.supabase_errors as supabase_errors
from app.services import news_sentiment_trend_service as trend
from app.services.news_cache_service import MARKET_SCOPE, NewsCacheService


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(supabase_errors.time, "sleep", lambda *_a: None)


class _Builder:
    def __init__(self, client):
        self.client, self.mode, self.data, self.eq_ = client, None, None, None

    def select(self, *_a, **_k):
        self.mode = "select"
        return self

    def update(self, data):
        self.mode, self.data = "update", data
        return self

    def eq(self, col, val):
        self.eq_ = (col, val)
        return self

    def in_(self, *_a):
        return self

    def execute(self):
        return self.client.execute(self)


class _FlakyUpdates:
    """A fake client: SELECT returns `rows`; an UPDATE fails per `fail(row_id)` or, when
    that is None, consumes the next of `outcomes`. One builder per call, so the
    concurrent update threads share no builder state."""

    def __init__(self, rows=(), outcomes=(), fail=None):
        self.rows = list(rows)
        self.outcomes = list(outcomes)
        self.fail = fail
        self.updates = []
        self.attempts = []
        self._lock = threading.Lock()

    def table(self, name):
        assert name == "ticker_news_cache"
        return _Builder(self)

    def execute(self, b):
        if b.mode == "select":
            return type("R", (), {"data": [dict(r) for r in self.rows]})()
        with self._lock:
            self.attempts.append(b.eq_)
            if self.fail is not None:
                outcome = self.fail(b.eq_[1])
            else:
                outcome = self.outcomes.pop(0) if self.outcomes else None
            if isinstance(outcome, BaseException):
                raise outcome
            self.updates.append((b.eq_, b.data))
        return type("R", (), {"data": []})()


def _disconnect():
    return httpx.RemoteProtocolError("Server disconnected without sending a response.")


def _svc(client):
    svc = object.__new__(NewsCacheService)
    svc.supabase = client
    return svc


@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_and_the_update_lands(caplog):
    client = _FlakyUpdates(outcomes=[_disconnect(), None])
    with caplog.at_level(logging.WARNING):
        await _svc(client)._update_enrichment_row("row-1", {"ai_processed": True})
    assert client.updates == [(("id", "row-1"), {"ai_processed": True})]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_the_market_path_is_retried_too(monkeypatch):
    client = _FlakyUpdates(outcomes=[_disconnect(), None])
    svc = _svc(client)
    invalidated = []

    async def _off_loop(reason, fn, *a, **k):
        invalidated.append(reason)
        return fn(*a, **k)

    svc._market_write_off_loop = _off_loop
    await svc._update_enrichment_row("row-m", {"ai_processed": True}, scope=MARKET_SCOPE)
    assert len(client.updates) == 1
    assert invalidated == ["Market enrichment update"]


@pytest.mark.asyncio
async def test_a_schema_error_is_not_retried():
    err = APIError({"message": "no column", "code": "PGRST204", "hint": None, "details": None})
    client = _FlakyUpdates(outcomes=[err, None])
    with pytest.raises(APIError):
        await _svc(client)._update_enrichment_row("row-1", {"x": 1})
    assert client.updates == [] and client.outcomes == [None], "no second attempt"


_ROWS = [
    {"id": f"id{i}", "external_id": f"https://x/{i}", "headline": f"H{i}", "summary": "s",
     "published_at": "2026-11-12T13:00:00+00:00", "ai_processed": False, "ticker": "ORCL"}
    for i in range(2)
]


@pytest.mark.asyncio
async def test_a_persistent_disconnect_is_a_warning_naming_ticker_and_row(monkeypatch, caplog):
    # id0 lands; id1 fails on all three attempts. Updates run concurrently in threads, so
    # route the failures by row id rather than by call order.
    client = _FlakyUpdates(rows=_ROWS, fail=lambda rid: _disconnect() if rid == "id1" else None)
    svc = _svc(client)

    async def _enrich(articles, ticker=""):
        return NewsCacheService._map_enrichments([
            {"bullets": ["a"], "sentiment": "bullish", "confidence": 70, "related_tickers": []}
            for _ in range(2)
        ], 2)

    async def _record(*_a, **_k):
        return 0

    svc._batch_enrich_articles = _enrich
    monkeypatch.setattr(trend, "record_labels", _record)
    with caplog.at_level(logging.WARNING):
        out = await svc._enrich_articles_uncached("orcl", ["id0", "id1"])

    assert len(out) == 2, "the caller still gets both enriched rows"
    assert [u[0] for u in client.updates] == [("id", "id0")]
    assert client.attempts.count(("id", "id1")) == 3, "retried up to the default 3 attempts"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    final = [r.getMessage() for r in caplog.records if "failed after retries" in r.getMessage()]
    assert len(final) == 1 and "ORCL" in final[0] and "id1" in final[0]


@pytest.mark.asyncio
async def test_a_non_transient_failure_stays_an_error_with_the_stack(monkeypatch, caplog):
    err = APIError({"message": "no column", "code": "PGRST204", "hint": None, "details": None})
    client = _FlakyUpdates(rows=_ROWS, fail=lambda rid: err if rid == "id1" else None)
    svc = _svc(client)

    async def _enrich(articles, ticker=""):
        return NewsCacheService._map_enrichments([
            {"bullets": ["a"], "sentiment": "bullish", "confidence": 70, "related_tickers": []}
            for _ in range(2)
        ], 2)

    async def _record(*_a, **_k):
        return 0

    svc._batch_enrich_articles = _enrich
    monkeypatch.setattr(trend, "record_labels", _record)
    with caplog.at_level(logging.WARNING):
        await svc._enrich_articles_uncached("orcl", ["id0", "id1"])

    assert client.attempts.count(("id", "id1")) == 1, "a schema error is never retried"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "id1" in errors[0].getMessage() and "ORCL" in errors[0].getMessage()
    assert errors[0].exc_info is not None
