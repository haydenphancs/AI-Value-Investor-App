"""The Updates news-sentiment timeline (migration 180): label log, daily series, endpoint.

Hermetic: every Supabase client here is an in-memory fake; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import app.api.v1.endpoints.updates as updates_ep
import app.services.news_sentiment_trend_service as trend
from app.services.news_cache_service import MARKET_SCOPE, NewsCacheService
from app.services.news_sentiment_trend_service import (
    MAX_LABEL_AGE_HOURS,
    RETENTION_DAYS,
    NewsSentimentTrendService,
    SentimentTrendUnavailable,
    article_key,
    build_log_rows,
    et_day,
    net_score,
    normalize_sentiment,
    open_since,
    record_labels,
    shape_series,
    summarize_trend,
)

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)  # Sun Sep 27, 11:00 ET


def _row(ext="https://x/1", sentiment="bullish", published="2026-09-27T13:00:00+00:00", conf=80):
    return {"external_id": ext, "sentiment": sentiment, "published_at": published,
            "sentiment_confidence": conf}


# ── article_key: must equal Postgres md5(external_id)::uuid ───────────────────────


def test_article_key_matches_the_postgres_md5_uuid_cast():
    # md5('abc') = 900150983cd24fb0d6963f7d28e17f72; `::uuid` hyphenates it 8-4-4-4-12.
    assert article_key("abc") == "90015098-3cd2-4fb0-d696-3f7d28e17f72"
    ext = "https://www.reuters.com/markets/ümlaut"
    assert article_key(ext) == str(uuid.UUID(hashlib.md5(ext.encode("utf-8")).hexdigest()))


@pytest.mark.parametrize("value", [None, "", 5, ["a"], "unknown_0", "unknown_17"])
def test_article_key_refuses_non_identities(value):
    assert article_key(value) is None


def test_article_key_hashes_the_stored_value_verbatim():
    # The seed hashes the column as-is; stripping here would split one article in two.
    assert article_key(" a") != article_key("a")


# ── labels ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("bullish", "bullish"), ("Bearish", "bearish"), (" neutral ", "neutral"),
    ("Positive", "bullish"), ("negative", "bearish"),
    ("mixed", None), ("", None), (None, None), (1, None),
])
def test_normalize_sentiment_never_defaults(raw, expected):
    assert normalize_sentiment(raw) == expected


# ── build_log_rows ─────────────────────────────────────────────────────────────


def test_a_row_becomes_one_log_row():
    [out] = build_log_rows("ORCL", [_row()], now=NOW)
    assert out == {
        "scope": "ORCL", "article_key": article_key("https://x/1"), "et_day": "2026-09-27",
        "sentiment": "bullish", "confidence": 80, "source": "live",
        "labelled_at": NOW.isoformat(),
    }


@pytest.mark.parametrize("published,expected_day", [
    # UTC after midnight is still the previous ET evening.
    ("2026-09-11T02:30:00+00:00", "2026-09-10"),
    # FMP's naive wall clock is New York time.
    ("2026-09-10 21:30:00", "2026-09-10"),
    # Spring forward (2026-03-08 02:00 EST → 03:00 EDT).
    ("2026-03-08T04:30:00+00:00", "2026-03-07"),
    ("2026-03-08T07:30:00+00:00", "2026-03-08"),
    # Fall back (2026-11-01 02:00 EDT → 01:00 EST).
    ("2026-11-01T03:59:00+00:00", "2026-10-31"),
    ("2026-11-01T04:30:00+00:00", "2026-11-01"),
])
def test_rows_are_bucketed_by_the_et_day(published, expected_day):
    now = datetime.fromisoformat("2026-11-02T00:00:00+00:00") if published.startswith("2026-11") \
        else datetime.fromisoformat("2026-03-09T00:00:00+00:00") if published.startswith("2026-03") \
        else datetime.fromisoformat("2026-09-11T12:00:00+00:00")
    [out] = build_log_rows("ORCL", [_row(published=published)], now=now)
    assert out["et_day"] == expected_day


def test_a_missing_or_future_timestamp_charts_on_the_labelling_day():
    rows = [
        _row(ext="a", published=None),
        _row(ext="b", published="not a date"),
        _row(ext="c", published="2026-09-30T00:00:00+00:00"),  # days in the future
    ]
    assert {r["et_day"] for r in build_log_rows("ORCL", rows, now=NOW)} == {"2026-09-27"}


def test_a_label_made_long_after_publication_is_not_logged():
    edge = (NOW - timedelta(hours=MAX_LABEL_AGE_HOURS)).isoformat()
    late = (NOW - timedelta(hours=MAX_LABEL_AGE_HOURS, seconds=1)).isoformat()
    out = build_log_rows("ORCL", [_row(ext="edge", published=edge), _row(ext="late", published=late)], now=NOW)
    assert [r["article_key"] for r in out] == [article_key("edge")]


def test_rows_without_identity_or_label_are_skipped_and_duplicates_collapse():
    rows = [
        _row(ext="unknown_0"), _row(ext=""), _row(ext=None),
        _row(ext="k", sentiment="mixed"), _row(ext="k2", sentiment=None),
        "garbage", None,
        _row(ext="dup", sentiment="bearish"), _row(ext="dup", sentiment="bullish"),
    ]
    out = build_log_rows("ORCL", rows, now=NOW)
    assert [(r["article_key"], r["sentiment"]) for r in out] == [(article_key("dup"), "bearish")]


@pytest.mark.parametrize("conf,expected", [(150, 100), (-5, 0), ("x", None), (True, None), (None, None), ("42", 42)])
def test_confidence_is_clamped_or_dropped(conf, expected):
    [out] = build_log_rows("ORCL", [_row(conf=conf)], now=NOW)
    assert out["confidence"] == expected


@pytest.mark.parametrize("scope", ["", "   ", "X" * 33])
def test_an_impossible_scope_writes_nothing(scope):
    assert build_log_rows(scope, [_row()], now=NOW) == []


def test_a_naive_now_is_read_as_utc():
    [out] = build_log_rows("ORCL", [_row()], now=NOW.replace(tzinfo=None))
    assert out["et_day"] == "2026-09-27"


# ── record_labels ──────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def upsert(self, payload, **kwargs):
        self.client.upserts.append((self.name, payload, kwargs))
        self._payload = payload
        return self

    def execute(self):
        if self.client.raise_on_execute:
            raise RuntimeError('relation "news_sentiment_log" does not exist')
        # ignore_duplicates: only the rows that were new come back.
        return _Result(self._payload[: self.client.inserted])


class _WriteClient:
    def __init__(self, *, raise_on_execute=False, inserted=99):
        self.upserts = []
        self.raise_on_execute = raise_on_execute
        self.inserted = inserted

    def table(self, name):
        return _Table(self, name)


@pytest.mark.asyncio
async def test_record_labels_writes_one_batch_first_label_wins():
    client = _WriteClient(inserted=1)
    n = await record_labels(client, "ORCL", [_row(ext="a"), _row(ext="b")], now=NOW)
    assert n == 1, "counts only the rows that were NEW"
    [(name, payload, kwargs)] = client.upserts
    assert name == "news_sentiment_log"
    assert kwargs == {"on_conflict": "scope,article_key", "ignore_duplicates": True}
    assert len(payload) == 2


@pytest.mark.asyncio
async def test_record_labels_never_raises_and_says_why(caplog):
    client = _WriteClient(raise_on_execute=True)
    with caplog.at_level(logging.WARNING):
        assert await record_labels(client, "ORCL", [_row()], now=NOW) == 0
    assert "dropped 1 label(s) for ORCL" in caplog.text
    assert "does not exist" in caplog.text


@pytest.mark.asyncio
async def test_record_labels_without_a_client_or_rows_is_a_no_op():
    assert await record_labels(None, "ORCL", [_row()], now=NOW) == 0
    client = _WriteClient()
    assert await record_labels(client, "ORCL", [_row(ext="unknown_1")], now=NOW) == 0
    assert client.upserts == [], "an all-skipped batch must not issue a write"


# ── the enrichment hook: every label written also lands in the log ─────────────


class _EnrichSelect:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "ticker_news_cache"
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def in_(self, *_a):
        return self

    def execute(self):
        return _Result([dict(r) for r in self.rows])


@pytest.mark.asyncio
async def test_the_enrich_path_logs_labels_before_the_cache_marks_rows_processed(monkeypatch):
    """The cache updates commit in worker threads even when the task is cancelled (the
    sweeper is, on every deploy), and an ai_processed row is never enriched again — so a
    label logged AFTER them was lost for good by a cancel in between. Logged first; a row
    whose update then fails is re-enriched later and first-label-wins ignores the repeat.
    A sentiment the model did not give (missing / off-list) never reaches the log."""
    rows = [
        {"id": f"id{i}", "external_id": f"https://x/{i}", "headline": f"H{i}", "summary": "s",
         "published_at": "2026-09-27T13:00:00+00:00", "ai_processed": False, "ticker": "ORCL"}
        for i in range(4)
    ]
    svc = object.__new__(NewsCacheService)
    svc.supabase = _EnrichSelect(rows)
    order = []

    async def _enrich(articles, ticker=""):
        return NewsCacheService._map_enrichments([
            {"bullets": ["a", "b"], "sentiment": s, "confidence": 70, "related_tickers": []}
            for s in ["bullish", "bearish", "neutral", "mixed"]
        ], 4)

    async def _update(row_id, data):
        order.append(("update", row_id))
        if row_id == "id1":
            raise RuntimeError("update failed")

    seen = {}

    async def _record(client, scope, labelled, **kw):
        order.append(("log", None))
        seen["client"], seen["scope"] = client, scope
        seen["rows"] = [(r["id"], r["sentiment"]) for r in labelled]
        return len(labelled)

    svc._batch_enrich_articles = _enrich
    svc._update_enrichment_row = _update
    monkeypatch.setattr(trend, "record_labels", _record)

    out = await svc._enrich_articles_uncached("orcl", ["id0", "id1", "id2", "id3"])
    assert seen["scope"] == "ORCL"
    assert seen["client"] is svc.supabase
    assert order[0] == ("log", None), "the log is written before any cache update"
    assert seen["rows"] == [("id0", "bullish"), ("id1", "bearish"), ("id2", "neutral")], \
        "id3's 'mixed' is not a label the model gave"
    by_id = {r["id"]: r for r in out}
    assert by_id["id3"]["sentiment"] == "neutral", "the badge keeps its old display default"


@pytest.mark.asyncio
async def test_a_cancel_during_the_cache_updates_keeps_the_labels(monkeypatch):
    rows = [{"id": "id0", "external_id": "https://x/0", "headline": "H", "summary": "s",
             "published_at": "2026-09-27T13:00:00+00:00", "ai_processed": False, "ticker": "ORCL"}]
    svc = object.__new__(NewsCacheService)
    svc.supabase = _EnrichSelect(rows)
    logged = []
    started = asyncio.Event()

    async def _enrich(articles, ticker=""):
        return NewsCacheService._map_enrichments(
            [{"bullets": ["a", "b"], "sentiment": "bearish", "confidence": 70}], 1)

    async def _update(row_id, data):
        started.set()
        await asyncio.Event().wait()          # the deploy cancels us here

    async def _record(client, scope, labelled, **kw):
        logged.extend(r["id"] for r in labelled)
        return len(labelled)

    svc._batch_enrich_articles = _enrich
    svc._update_enrichment_row = _update
    monkeypatch.setattr(trend, "record_labels", _record)
    task = asyncio.create_task(svc._enrich_articles_uncached("orcl", ["id0"]))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert logged == ["id0"]


@pytest.mark.asyncio
async def test_the_enrich_path_survives_a_client_less_stub(monkeypatch):
    # Many hermetic stubs bypass __init__; the hook must not assume a client exists.
    svc = object.__new__(NewsCacheService)
    svc.supabase = _EnrichSelect([
        {"id": "id0", "external_id": "https://x/0", "headline": "H", "summary": "s",
         "published_at": "2026-09-27T13:00:00+00:00", "ai_processed": False},
    ])

    async def _enrich(articles, ticker=""):
        return {0: {"bullets": ["a", "b"], "sentiment": "bullish", "confidence": 70, "related_tickers": []}}

    async def _update(row_id, data):
        return None

    svc._batch_enrich_articles = _enrich
    svc._update_enrichment_row = _update
    # The select fake has no .upsert, so the real record_labels hits an AttributeError
    # inside its guard — it must log and carry on, not break enrichment.
    out = await svc._enrich_articles_uncached("ORCL", ["id0"])
    assert out and out[0]["sentiment"] in ("bullish", "Bullish", "positive", "Positive")


# ── the Market prompt no longer calls the market a ticker ──────────────────────


class _CapturingGemini:
    def __init__(self):
        self.prompts = []

    async def generate_json(self, **kwargs):
        self.prompts.append(kwargs.get("prompt") or "")
        return {"text": json.dumps([
            {"index": 0, "bullets": ["a", "b"], "sentiment": "neutral", "confidence": 50,
             "related_tickers": ["__MARKET__", "SPY", "_X"]},
        ])}


@pytest.mark.asyncio
async def test_market_articles_are_judged_for_the_market_not_a_stock():
    gem = _CapturingGemini()
    svc = object.__new__(NewsCacheService)
    svc.gemini = gem
    out = await svc._batch_enrich_articles([{"title": "Fed holds", "text": "x"}], ticker=MARKET_SCOPE)
    prompt = gem.prompts[0]
    assert "fetched for ticker __MARKET__" not in prompt
    assert "__MARKET__" not in prompt
    assert "NET directional lean for the overall US stock market" in prompt
    assert out[0]["related_tickers"] == ["SPY"], "a reserved key is never a ticker chip"


@pytest.mark.asyncio
async def test_a_ticker_prompt_is_unchanged():
    gem = _CapturingGemini()
    svc = object.__new__(NewsCacheService)
    svc.gemini = gem
    await svc._batch_enrich_articles([{"title": "Oracle beats", "text": "x"}], ticker="ORCL")
    prompt = gem.prompts[0]
    assert "These articles were fetched for ticker ORCL. Always include ORCL" in prompt
    assert "NET directional lean for the stock," in prompt


# ── shape_series / net_score / summarize_trend ────────────────────────────────


TODAY = date(2026, 9, 27)


def _raw(day, bull, bear, neut):
    return {"day": day, "bullish": bull, "bearish": bear, "neutral": neut}


def test_shape_series_orders_drops_and_marks_today():
    raw = [
        _raw("2026-09-27", 1, 0, 0),
        _raw("2026-09-25", 2, 3, 1),
        _raw("2026-09-26", 0, 0, 0),        # all-zero: absent, not a flat zero
        _raw("2026-08-01", 5, 0, 0),        # before the window
        _raw("2026-09-28", 5, 0, 0),        # after today
        _raw("garbage", 1, 1, 1), "junk", None,
        {"day": "2026-09-24", "bullish": "2", "bearish": None, "neutral": True},
    ]
    out = shape_series(raw, today=TODAY, since=TODAY - timedelta(days=6))
    assert [d["date"] for d in out] == ["2026-09-24", "2026-09-25", "2026-09-27"]
    assert out[0] == {"date": "2026-09-24", "bullish": 2, "bearish": 0, "neutral": 0,
                      "total": 2, "net_score": 100, "is_partial": False}
    assert out[1]["net_score"] == -17  # (2-3)/6
    assert out[-1]["is_partial"] is True
    assert not any(d["is_partial"] for d in out[:-1])


def test_shape_series_accepts_date_objects_and_negative_counts_are_zero():
    out = shape_series([{"day": date(2026, 9, 26), "bullish": -3, "bearish": 2, "neutral": 0}],
                       today=TODAY, since=TODAY - timedelta(days=6))
    assert out[0]["bullish"] == 0 and out[0]["net_score"] == -100


@pytest.mark.parametrize("b,r,t,expected", [
    (0, 0, 0, 0), (1, 0, 1, 100), (0, 1, 1, -100), (1, 1, 3, 0), (2, 1, 3, 33),
    # Halves round AWAY from zero, like Swift's `.rounded()` on the chart — Python's
    # `round` would say 12 / -12 / 62 / 2 and the chat would contradict the chart by one.
    (21, 16, 40, 13), (16, 21, 40, -13), (13, 3, 16, 63), (1, 0, 8, 13), (5, 4, 40, 3), (4, 5, 40, -3),
    (2, 1, 6, 17), (1, 2, 6, -17),
])
def test_net_score(b, r, t, expected):
    assert net_score(b, r, t) == expected


def test_net_score_matches_the_swift_rounding_rule_everywhere():
    import math

    for total in range(1, 60):
        for bull in range(total + 1):
            for bear in range(total + 1 - bull):
                raw = (bull - bear) * 100 / total
                swift = math.copysign(math.floor(abs(raw) + 0.5), raw)
                assert net_score(bull, bear, total) == int(swift), (bull, bear, total)


def test_summarize_trend_is_none_when_empty():
    assert summarize_trend([], days=30, today=TODAY) is None


def test_summarize_trend_states_the_charted_numbers():
    series = shape_series([
        _raw("2026-09-14", 4, 1, 1),   # prior week
        _raw("2026-09-22", 2, 9, 1),   # most bearish
        _raw("2026-09-25", 6, 1, 0),   # most bullish
        _raw("2026-09-27", 1, 0, 0),   # today, partial
    ], today=TODAY, since=TODAY - timedelta(days=29))
    text = summarize_trend(series, days=30, today=TODAY, tracking_since=date(2026, 9, 1))
    assert "26 scored — 13 bullish, 11 bearish, 2 neutral" in text
    assert "Last 7 days net -5 vs the 7 days before +50." in text
    assert "tracked since Tue Sep 1" in text
    assert "Most bearish day: Tue Sep 22 (9 bearish vs 2 bullish)." in text
    assert "Most bullish day: Fri Sep 25 (6 bullish vs 1 bearish)." in text
    assert "Last 7 days net" in text and "the 7 days before +50" in text
    assert "Counts from Sun Sep 27 on are still filling in" in text


def test_summarize_trend_single_balanced_day_names_no_extreme():
    series = shape_series([_raw("2026-09-25", 1, 1, 0)], today=TODAY, since=TODAY - timedelta(days=6))
    text = summarize_trend(series, days=7, today=TODAY)
    assert "Most bearish" not in text and "Most bullish" not in text
    assert "Last 7 days net" not in text, "a 7-day window has no prior week to compare"


def test_et_day_helper():
    assert et_day("2026-09-11T02:30:00+00:00") == date(2026, 9, 10)
    assert et_day(datetime(2026, 9, 11, 2, 30)) == date(2026, 9, 10)
    assert et_day("nope") is None and et_day(None) is None


# ── the service: RPC read, cache, dedup, failure ───────────────────────────────


class _ReadClient:
    def __init__(self, daily, first=None, *, fail=False):
        self.daily, self.first, self.fail = daily, first, fail
        self.rpc_calls = []
        self.table_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return self

    def table(self, name):
        self.table_calls.append(name)
        self._mode = "first"
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("PGRST202 function news_sentiment_daily not found")
        if getattr(self, "_mode", None) == "first":
            self._mode = None
            return _Result([{"et_day": self.first}] if self.first else [])
        return _Result(self.daily)


@pytest.mark.asyncio
async def test_get_trend_reads_the_window_and_tracking_start():
    client = _ReadClient([_raw("2026-09-25", 2, 1, 0)], first="2026-09-20")
    svc = NewsSentimentTrendService(client)
    out = await svc.get_trend("ORCL", 7, now=NOW)
    assert client.rpc_calls == [("news_sentiment_daily", {"p_scope": "ORCL", "p_since": "2026-09-21"})]
    assert out["scope"] == "ORCL" and out["days"] == 7
    assert out["tracking_since"] == "2026-09-20"
    assert [d["date"] for d in out["series"]] == ["2026-09-25"]

    await svc.get_trend("ORCL", 7, now=NOW)
    assert len(client.rpc_calls) == 1, "the second read is served from memory"
    await svc.get_trend("ORCL", 30, now=NOW)
    assert len(client.rpc_calls) == 2, "each window is its own entry"


@pytest.mark.asyncio
async def test_get_trend_failure_is_typed_and_never_cached():
    client = _ReadClient([], fail=True)
    svc = NewsSentimentTrendService(client)
    with pytest.raises(SentimentTrendUnavailable, match="PGRST202"):
        await svc.get_trend("ORCL", 30, now=NOW)
    client.fail = False
    out = await svc.get_trend("ORCL", 30, now=NOW)
    assert out["series"] == [] and out["tracking_since"] is None


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_fetch(monkeypatch):
    svc = NewsSentimentTrendService(object())
    gate = asyncio.Event()
    calls = []

    async def _fetch(scope, days, today, now=None):
        calls.append(scope)
        await gate.wait()
        return {"scope": scope, "days": days, "series": [], "tracking_since": None}

    monkeypatch.setattr(svc, "_fetch", _fetch)
    tasks = [asyncio.create_task(svc.get_trend("ORCL", 30, now=NOW)) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert calls == ["ORCL"]
    assert all(r == results[0] for r in results)
    assert svc._inflight == {}


@pytest.mark.asyncio
async def test_a_bad_window_is_refused():
    with pytest.raises(ValueError):
        await NewsSentimentTrendService(object()).get_trend("ORCL", 14, now=NOW)


class _DeleteClient:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def delete(self):
        self.calls.append(("delete",))
        return self

    def lt(self, col, value):
        self.calls.append(("lt", col, value))
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("boom")
        return _Result([{}, {}])


def test_sweep_deletes_before_the_retention_cutoff():
    client = _DeleteClient()
    assert NewsSentimentTrendService(client).sweep_expired(today=TODAY) == 2
    cutoff = (TODAY - timedelta(days=RETENTION_DAYS)).isoformat()
    assert client.calls == [("table", "news_sentiment_log"), ("delete",), ("lt", "et_day", cutoff)]


def test_sweep_failure_is_logged_not_raised(caplog):
    with caplog.at_level(logging.WARNING):
        assert NewsSentimentTrendService(_DeleteClient(fail=True)).sweep_expired(today=TODAY) == 0
    assert "news_sentiment_log sweep failed" in caplog.text


def test_the_windows_cover_retention():
    assert max(trend.TREND_DAYS) < RETENTION_DAYS


# ── the endpoint ────────────────────────────────────────────────────────────────


class _StubTrendService:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    async def get_trend(self, scope, days):
        self.calls.append((scope, days))
        if self.exc:
            raise self.exc
        return self.result


def _body(resp):
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_endpoint_serves_the_series(monkeypatch):
    stub = _StubTrendService({"scope": "ORCL", "days": 7, "tracking_since": "2026-09-20", "series": [
        {"date": "2026-09-27", "bullish": 1, "bearish": 0, "neutral": 0, "total": 1,
         "net_score": 100, "is_partial": True},
    ]})
    monkeypatch.setattr(updates_ep, "get_news_sentiment_trend_service", lambda: stub)
    out = await updates_ep.get_updates_sentiment_trend(scope=" orcl ", days=7, _rate_limit=None)
    assert stub.calls == [("ORCL", 7)]
    assert out.series[0].is_partial is True and out.tracking_since == "2026-09-20"


@pytest.mark.asyncio
async def test_endpoint_market_scope_is_not_uppercased_away(monkeypatch):
    stub = _StubTrendService({"scope": MARKET_SCOPE, "days": 30, "series": [], "tracking_since": None})
    monkeypatch.setattr(updates_ep, "get_news_sentiment_trend_service", lambda: stub)
    out = await updates_ep.get_updates_sentiment_trend(scope=MARKET_SCOPE, days=30, _rate_limit=None)
    assert stub.calls == [(MARKET_SCOPE, 30)] and out.series == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,days,field", [
    ("OR CL", 7, "scope"), ("X" * 40, 7, "scope"), ("", 7, "scope"),
    ("ORCL", 14, "days"), ("ORCL", 0, "days"), ("ORCL", -7, "days"),
])
async def test_endpoint_rejects_bad_input(monkeypatch, scope, days, field):
    stub = _StubTrendService()
    monkeypatch.setattr(updates_ep, "get_news_sentiment_trend_service", lambda: stub)
    resp = await updates_ep.get_updates_sentiment_trend(scope=scope, days=days, _rate_limit=None)
    body = _body(resp)
    assert resp.status_code == 400 and body["error_code"] == "INVALID_INPUT"
    assert field in body["details"]
    assert stub.calls == []


@pytest.mark.asyncio
async def test_endpoint_unavailable_is_a_typed_503(monkeypatch, caplog):
    stub = _StubTrendService(exc=SentimentTrendUnavailable("APIError: relation does not exist"))
    monkeypatch.setattr(updates_ep, "get_news_sentiment_trend_service", lambda: stub)
    with caplog.at_level(logging.WARNING):
        resp = await updates_ep.get_updates_sentiment_trend(scope="ORCL", days=30, _rate_limit=None)
    body = _body(resp)
    assert resp.status_code == 503
    assert body["error_code"] == "SENTIMENT_TREND_UNAVAILABLE"
    assert body["action"] == "retry_later"
    assert "sentiment trend unavailable for scope=ORCL" in caplog.text


def test_the_route_is_on_the_account_only_router():
    paths = {r.path for r in updates_ep.router.routes}
    assert "/sentiment-trend" in paths
    deps = [d.dependency.__name__ for d in updates_ep.router.dependencies]
    assert "get_current_user_id" in deps


# ── review 2026-09-27: late labels, and what a joiner receives ─────────────────


def test_open_since_is_the_et_day_of_the_late_label_window():
    # Sun Sep 27 15:00Z − 96 h = Wed Sep 23 15:00Z = Wed 11:00 ET.
    assert open_since(NOW) == date(2026, 9, 23)
    # 02:30Z is still the previous ET evening.
    assert open_since(datetime(2026, 9, 28, 2, 30, tzinfo=timezone.utc)) == date(2026, 9, 23)


def test_every_day_a_late_label_can_still_reach_is_partial():
    raw = [_raw(d, 1, 0, 0) for d in ("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-26", "2026-09-27")]
    out = shape_series(raw, today=TODAY, since=TODAY - timedelta(days=29), partial_from=date(2026, 9, 23))
    assert {d["date"]: d["is_partial"] for d in out} == {
        "2026-09-21": False, "2026-09-22": False,
        "2026-09-23": True, "2026-09-26": True, "2026-09-27": True,
    }


@pytest.mark.asyncio
async def test_get_trend_marks_the_open_window_partial():
    client = _ReadClient([_raw("2026-09-22", 2, 1, 0), _raw("2026-09-24", 1, 0, 0)], first="2026-09-20")
    out = await NewsSentimentTrendService(client).get_trend("ORCL", 30, now=NOW)
    assert [(d["date"], d["is_partial"]) for d in out["series"]] == [
        ("2026-09-22", False), ("2026-09-24", True),
    ]


def test_the_summary_says_which_days_are_still_filling_in():
    series = shape_series([_raw("2026-09-20", 1, 0, 0), _raw("2026-09-25", 0, 1, 0)],
                          today=TODAY, since=TODAY - timedelta(days=29), partial_from=date(2026, 9, 23))
    text = summarize_trend(series, days=30, today=TODAY)
    assert "Counts from Fri Sep 25 on are still filling in" in text
    assert "earlier days are final" in text


@pytest.mark.asyncio
async def test_a_joiner_gets_the_typed_failure_not_a_bare_runtime_error(monkeypatch):
    svc = NewsSentimentTrendService(object())
    gate = asyncio.Event()

    async def _fetch(scope, days, today, now=None):
        await gate.wait()
        raise asyncio.CancelledError()

    monkeypatch.setattr(svc, "_fetch", _fetch)
    leader = asyncio.create_task(svc.get_trend("ORCL", 30, now=NOW))
    await asyncio.sleep(0)
    joiner = asyncio.create_task(svc.get_trend("ORCL", 30, now=NOW))
    await asyncio.sleep(0)
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(SentimentTrendUnavailable, match="CancelledError"):
        await joiner
    assert svc._inflight == {}


@pytest.mark.asyncio
async def test_the_history_status_is_read_before_the_series(monkeypatch):
    """Read after the series, a run finishing between the two reads was cached (5 min on each
    side) as 'ready' with its newest labels missing. Read first, the race errs to 'building'."""
    order = []
    client = _ReadClient([_raw("2026-09-25", 2, 1, 0)], first="2026-09-20")
    real_rpc = client.rpc

    def _rpc(name, params):
        order.append("series")
        return real_rpc(name, params)

    monkeypatch.setattr(client, "rpc", _rpc)
    svc = NewsSentimentTrendService(client)

    def _status(scope, today):
        order.append("status")
        return "building"

    monkeypatch.setattr(svc, "_history_status", _status)
    out = await svc.get_trend("ORCL", 30, now=NOW)
    assert order == ["status", "series"]
    assert out["history_status"] == "building"


@pytest.mark.asyncio
async def test_a_failed_status_read_is_short_lived_and_keeps_building(monkeypatch, caplog):
    """A blip on the status read used to answer "no backfill" for 5 minutes (logged at
    DEBUG), which also stopped the app's "Building…" re-checks."""
    client = _ReadClient([_raw("2026-09-25", 2, 1, 0)], first="2026-09-20")
    svc = NewsSentimentTrendService(client)
    state = {"fail": False}

    def _status(scope, today):
        if state["fail"]:
            raise RuntimeError("stale connection")
        return "building"

    monkeypatch.setattr(svc, "_history_status", _status)
    clock = {"t": 1000.0}
    monkeypatch.setattr(trend.time, "monotonic", lambda: clock["t"])

    first = await svc.get_trend("ORCL", 30, now=NOW)
    assert first["history_status"] == "building"
    clock["t"] += 31                              # past the building TTL
    state["fail"] = True
    with caplog.at_level("WARNING"):
        blip = await svc.get_trend("ORCL", 30, now=NOW)
    assert blip["history_status"] == "building", "the last known building survives a blip"
    assert "status read failed for ORCL" in caplog.text
    assert ("ORCL", 30, first_today()) in svc._status_unknown
    clock["t"] += 31                              # short TTL, not five minutes
    state["fail"] = False
    calls = len(client.rpc_calls)
    await svc.get_trend("ORCL", 30, now=NOW)
    assert len(client.rpc_calls) == calls + 1, "re-read after 30 s, not 5 min"
    assert svc._status_unknown == set()


def first_today():
    from app.services.news_sentiment_trend_service import _today_et

    return _today_et(NOW)


@pytest.mark.asyncio
async def test_a_failed_status_read_without_history_is_none_not_building(monkeypatch):
    svc = NewsSentimentTrendService(_ReadClient([], first=None))

    def _status(scope, today):
        raise RuntimeError("blip")

    monkeypatch.setattr(svc, "_history_status", _status)
    out = await svc.get_trend("ORCL", 7, now=NOW)
    assert out["history_status"] is None


@pytest.mark.asyncio
async def test_a_blip_after_ready_is_re_read_in_30_seconds(monkeypatch):
    client = _ReadClient([_raw("2026-09-25", 2, 1, 0)], first="2026-09-20")
    svc = NewsSentimentTrendService(client)
    state = {"fail": False}

    def _status(scope, today):
        if state["fail"]:
            raise RuntimeError("blip")
        return "ready"

    monkeypatch.setattr(svc, "_history_status", _status)
    clock = {"t": 5000.0}
    monkeypatch.setattr(trend.time, "monotonic", lambda: clock["t"])
    await svc.get_trend("ORCL", 30, now=NOW)
    clock["t"] += 301                             # the ready answer expires
    state["fail"] = True
    assert (await svc.get_trend("ORCL", 30, now=NOW))["history_status"] is None
    state["fail"] = False
    clock["t"] += 31
    calls = len(client.rpc_calls)
    assert (await svc.get_trend("ORCL", 30, now=NOW))["history_status"] == "ready"
    assert len(client.rpc_calls) == calls + 1, "the failed answer lived 30 s, not 5 min"


class _StatusAndReadClient(_ReadClient):
    """Routes the backfill-status read and the log reads apart, so the REAL
    `_history_status` → `read_history_status` path runs (no monkeypatch of the method)."""

    def __init__(self, *a, status_row=None, **k):
        super().__init__(*a, **k)
        self.status_row, self.status_fails = status_row, False
        self._status_mode = False

    def table(self, name):
        if name == trend.BACKFILL_TABLE:
            self._status_mode = True
            return self
        self._status_mode = False
        return super().table(name)

    def execute(self):
        if self._status_mode:
            self._status_mode = False
            if self.status_fails:
                raise RuntimeError("stale connection on the status read")
            return _Result([self.status_row] if self.status_row else [])
        return super().execute()


@pytest.mark.asyncio
async def test_the_real_status_path_raises_into_the_short_ttl(monkeypatch):
    """Pins the contract itself: `_history_status` must RAISE on a failed read. Reverted to
    the swallowing `history_status_for`, a blip reads as "no backfill" for 5 minutes."""
    from app.config import settings

    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", True)
    client = _StatusAndReadClient([_raw("2026-09-25", 2, 1, 0)], first="2026-09-20",
                                  status_row={"status": "running", "covered_from": None, "covered_to": None})
    svc = NewsSentimentTrendService(client)
    clock = {"t": 9000.0}
    monkeypatch.setattr(trend.time, "monotonic", lambda: clock["t"])
    assert (await svc.get_trend("ORCL", 30, now=NOW))["history_status"] == "building"
    clock["t"] += 31
    client.status_fails = True
    with pytest.raises(RuntimeError):
        trend.read_history_status(client, "ORCL", first_today())
    out = await svc.get_trend("ORCL", 30, now=NOW)
    assert out["history_status"] == "building"
    assert ("ORCL", 30, first_today()) in svc._status_unknown
