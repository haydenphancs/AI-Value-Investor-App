"""WIRING of the Market relevance rule — every place the Market feed is written or read.

`test_market_news_relevance.py` proves the rule; this proves each consumer applies it and
that a verdict is fixed before a row can be read:

  1. ingest   — `_fetch_market_raw` drops an exchange-qualified single-company citation;
                macro cashtags / acronyms survive (review 2026-10-06, finding 2);
  2. writer   — the cold path and the refresh JUDGE new rows before writing them (one
                summary call per ≤25 rows, ≤2 per write, under a time budget), and write
                the scope stamp in the same write that makes the row readable;
  3. later    — a later summary (the client's scroll window, the sweeper, the pre-warmer)
                never adds or changes a stamp, so a served row can never be hidden;
  4. paging   — walked exactly as iOS walks it (offset = rows received) WHILE the client's
                own summaries run between pages and new rows land on top: no market story
                is skipped, nothing loops, and repeats are only the ones new rows cause
                (review 2026-10-06, finding 1);
  5. corpus   — `get_cached_bulk` filters the MARKET bucket before its cap;
  6. endpoint — the reported screen (timeline + "Latest Market headlines") and `has_more`;
  7. summary  — the Market-only scope question and schema on the existing call.

Hermetic: an in-memory `ticker_news_cache`, a fake model client, no network.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.api.v1.endpoints.updates as updates_endpoint
import app.services.news_cache_service as ncs
import app.services.news_llm as news_llm
from app.config import settings
from app.services.market_news_relevance import (
    MODEL_SCOPES,
    classify_market_story,
    stamp_model_scope,
)
from app.services.news_cache_service import (
    MARKET_SCOPE,
    MARKET_SCOPE_FIELD,
    MARKET_SCOPE_RULE,
    NewsCacheService,
    build_enrichment_prompt,
)
from app.services.news_insight_service import NewsInsightService
from _price_fakes import PriceFromFMPFake

NOW = datetime.now(timezone.utc)
MODEL = "gemini-2.5-flash-lite"
FAR = (NOW + timedelta(days=2)).isoformat()


# ── An in-memory `ticker_news_cache` (reads, projections, updates, upserts) ──

class _Result:
    def __init__(self, data):
        self.data = data


class _DB:
    def __init__(self, rows=(), *, fail_on_call=None, fail_lookup=False):
        self.rows = [dict(r) for r in rows]
        self.calls = []
        self.fail_on_call = fail_on_call
        self.fail_lookup = fail_lookup
        #: The next N page reads (`select("*")`) raise, like a transient PostgREST error.
        self.fail_reads = 0
        #: Every upsert raises (a write outage).
        self.fail_upserts = False
        #: The next N create-only pre-passes (`ignore_duplicates=True`) raise; the merge
        #: upsert after them still succeeds (review round 5, R5-1).
        self.fail_prepass = 0
        self.prepass_failures = 0
        #: 1-based ordinals of page reads (`select("*")`) that raise.
        self.fail_read_calls = set()
        self.read_calls = 0
        self.upserts = []
        self._n = 0

    def table(self, name):
        assert name == "ticker_news_cache"
        return _Query(self)

    def rpc(self, name, params):
        assert name == "get_top_watchlist_tickers"

        class _Rpc:
            def execute(self):
                return _Result([])

        return _Rpc()

    def scope_rows(self, scope):
        return [r for r in self.rows if r.get("ticker") == scope]

    def upsert(self, payload, kw):
        self.upserts.append(([dict(p) for p in payload], dict(kw)))
        out = []
        for p in payload:
            hit = next((r for r in self.rows if r["ticker"] == p["ticker"]
                        and r["external_id"] == p["external_id"]), None)
            if hit is not None:
                if kw.get("ignore_duplicates"):
                    continue
                hit.update(p)
                out.append(dict(hit))
            else:
                self._n += 1
                row = {"id": f"db-{self._n:04d}", **p}
                self.rows.append(row)
                out.append(dict(row))
        return [] if kw.get("returning") == "minimal" else out


class _Query:
    def __init__(self, db):
        self._db = db
        self._mode = "select"
        self._cols = None
        self._filters = []
        self._lo, self._hi = 0, None
        self._ordered = False
        self._payload = None
        self._kw = {}

    def select(self, cols):
        # Honoured, like PostgREST: an unprojected column is not returned, so a
        # projection that drops `ai_model` really does blind the reader.
        self._cols = None if cols.strip() == "*" else [c.strip() for c in cols.split(",")]
        return self

    def eq(self, col, value):
        self._filters.append((col, "eq", value))
        return self

    def in_(self, col, values):
        self._filters.append((col, "in", list(values)))
        return self

    def gte(self, col, value):
        self._filters.append((col, "gte", value))
        return self

    def order(self, col, **kwargs):
        self._ordered = True
        return self

    def range(self, lo, hi):
        self._lo, self._hi = lo, hi
        return self

    def update(self, data):
        self._mode, self._payload = "update", data
        return self

    def upsert(self, rows, **kwargs):
        self._mode, self._payload, self._kw = "upsert", rows, kwargs
        return self

    def execute(self):
        db = self._db
        db.calls.append((self._mode, tuple((c, o) for c, o, _ in self._filters)))
        if db.fail_on_call is not None and len(db.calls) == db.fail_on_call:
            raise RuntimeError("supabase 520")
        if db.fail_lookup and any(c == "external_id" for c, _, _ in self._filters):
            raise RuntimeError("supabase 520 on lookup")
        if self._mode == "select" and self._cols is None:
            db.read_calls += 1
            if db.read_calls in db.fail_read_calls:
                raise RuntimeError(f"supabase 520 on read #{db.read_calls}")
        if self._mode == "select" and self._cols is None and db.fail_reads > 0:
            db.fail_reads -= 1
            raise RuntimeError("supabase 520 on read")
        if self._mode == "upsert":
            if db.fail_upserts:
                raise RuntimeError("supabase 520 on write")
            if self._kw.get("ignore_duplicates") and db.fail_prepass > 0:
                db.fail_prepass -= 1
                db.prepass_failures += 1
                raise RuntimeError("supabase 520 on the create-only pre-pass")
            return _Result(db.upsert(self._payload, self._kw))
        matched = [r for r in db.rows if all(_match(r, f) for f in self._filters)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return _Result([])
        if self._ordered:
            matched.sort(key=lambda r: (r.get("published_at") or "", r.get("id") or ""),
                         reverse=True)
        if self._hi is not None:
            matched = matched[self._lo: self._hi + 1]
        if self._cols is not None:
            return _Result([{c: r.get(c) for c in self._cols} for r in matched])
        return _Result([dict(r) for r in matched])


def _match(row, flt):
    col, op, value = flt
    if op == "eq":
        return row.get(col) == value
    if op == "in":
        return row.get(col) in value
    return (row.get(col) or "") >= value


def _cache_row(i, headline, *, scope=MARKET_SCOPE, verdict=None, tags=None,
               hours_ago=None, enriched=None):
    """A row as the cache holds it. `verdict` = the scope written at insert (None:
    written without one); `enriched` defaults to "judged at insert"."""
    when = NOW - timedelta(minutes=i if hours_ago is None else hours_ago * 60)
    judged = verdict is not None
    return {
        "id": f"{scope}-{i:04d}", "ticker": scope, "external_id": f"https://n/{scope}/{i}",
        "headline": headline, "summary": "body",
        "summary_bullets": json.dumps(["A.", "B."] if judged else []),
        "sentiment": "bearish" if judged else None, "sentiment_confidence": 0,
        "ai_processed": judged if enriched is None else enriched,
        "ai_model": stamp_model_scope(MODEL, verdict) if judged else None,
        "published_at": when.isoformat(), "article_url": f"https://n/{scope}/{i}",
        "source_name": "Reuters", "source_logo_url": None, "thumbnail_url": None,
        "related_tickers": list(tags or []), "cached_at": NOW.isoformat(),
        "expires_at": FAR,
    }


def _market(i, **kw):
    return _cache_row(i, f"Fed watch {i}: Treasury yields edge higher", verdict="market", **kw)


def _company(i, **kw):
    return _cache_row(i, f"Acme Robotics {i} recalls its delivery robots",
                      verdict="company", **kw)


def _unjudged(i, **kw):
    return _cache_row(i, f"Acme Robotics {i} names a new finance chief", **kw)


def _service(rows=(), **db_kwargs):
    svc = object.__new__(NewsCacheService)
    svc.supabase = _DB(rows, **db_kwargs)
    svc._inflight = {}
    svc._enrich_inflight = {}
    return svc


# ── A fake model: answers per title, counts calls ────────────────────────────

_TITLE = re.compile(r"^Title: (.*)$", re.MULTILINE)


class _Model:
    def __init__(self, scope_for, *, tags_for=None, delay=0.0, fail=False, on_call=None,
                 delay_for=None):
        self.scope_for = scope_for
        self.tags_for = tags_for or (lambda title: [])
        self.delay = delay
        self.fail = fail
        #: Runs when a call starts — e.g. a refresh landing while the model works.
        self.on_call = on_call
        #: Per-batch delay from its titles (a slow batch beside a fast one).
        self.delay_for = delay_for
        self.calls = []

    async def generate_json(self, **kwargs):
        titles = _TITLE.findall(kwargs["prompt"])
        self.calls.append(titles)
        if self.on_call is not None:
            self.on_call(titles)
        delay = self.delay_for(titles) if self.delay_for is not None else self.delay
        if delay:
            await asyncio.sleep(delay)
        if self.fail:
            raise RuntimeError("model 500")
        items = []
        for i, title in enumerate(titles):
            item = {"index": i, "bullets": ["A point.", "The conclusion."],
                    "sentiment": "bearish", "confidence": 70,
                    "related_tickers": self.tags_for(title)}
            scope = self.scope_for(title)
            if scope is not None:
                item["scope"] = scope
            items.append(item)
        return {"text": json.dumps(items), "model": "fake", "tokens_used": 0,
                "finish_reason": "STOP"}


def _by_keyword(title):
    return "company" if "Acme" in title or "Aramco" in title else "market"


@pytest.fixture
def labels(monkeypatch):
    """Gemini provider settings + a recording stand-in for the news-tone label log."""
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", MODEL)
    seen = []

    async def _record(supabase, scope, rows, **kwargs):
        seen.append((scope, [dict(r) for r in rows], kwargs.get("model")))
        return len(seen[-1][1])

    monkeypatch.setattr("app.services.news_sentiment_trend_service.record_labels", _record)
    return seen


def _raw(i, title, symbol=None):
    # `publishedDate` carries its offset (review round 7). The service reads a NAIVE
    # stamp as New York time (`to_utc_instant`, FMP's real shape), so the UTC clock
    # written naive was a different instant — and on the US spring-forward night the
    # 02:xx ET hour does not exist, which swapped the order of rows either side of it.
    return {"url": f"https://g/{i}", "title": title, "symbol": symbol,
            "publishedDate": (NOW - timedelta(minutes=i)).isoformat(),
            "publisher": "Reuters", "site": "reuters.com", "text": "body", "image": None}


def _stub_raw(svc, rows):
    async def _fetch(limit, from_date=None):
        return list(rows)[:limit]

    svc._fetch_market_raw = _fetch


# ── 1. Ingest filter ──────────────────────────────────────────────────────────

class _IngestService(NewsCacheService):
    def __init__(self, general, index=()):
        self.supabase = None
        self._inflight = {}

        class _FMP:
            async def get_general_news(self, limit=50, page=0):
                return list(general)

            async def get_stock_news(self, ticker=None, limit=10, from_date=None,
                                     to_date=None, page=0):
                return list(index)

        self.fmp = _FMP()
        self.price = PriceFromFMPFake(self.fmp)

    async def _market_trending_tickers(self):
        return frozenset()


def test_ingest_drops_an_exchange_qualified_single_company_citation():
    svc = _IngestService(
        general=[_raw(0, "Tesla (NASDAQ:TSLA) stock sinks as market gains"),
                 _raw(1, "Fed holds rates steady"),
                 _raw(2, "Treasury yields touch generational highs")],
        index=[_raw(3, "S&P 500 hits resistance", symbol="SPY")],
    )
    titles = [r["title"] for r in asyncio.run(svc._fetch_market_raw(25))]
    assert titles == ["Fed holds rates steady", "Treasury yields touch generational highs",
                      "S&P 500 hits resistance"]


def test_ingest_keeps_macro_cashtags_and_institution_acronyms():
    """Finding 2, end to end through the real ingest step: every one of these was dropped
    before it could ever be judged. The exchange-qualified twin still goes."""
    macro = ["Bank of Korea (BOK) holds benchmark rate steady",
             "Reserve Bank of New Zealand (RBNZ) cuts official cash rate",
             "Dollar index ($DXY) hits two-year high", "World Bank (WB) cuts growth forecast",
             "Mortgage applications fall, Mortgage Bankers Association (MBA) says",
             "European Central Bank (ECB) holds rates", "10-year yield ($TNX) tops 5%",
             "OPEC+ (OPEC) extends cuts"]
    twin = "Shares of Boeing Co (NYSE: BA) slide on delivery delay"
    svc = _IngestService(general=[_raw(i, t) for i, t in enumerate(macro + [twin])])
    titles = [r["title"] for r in asyncio.run(svc._fetch_market_raw(50))]
    assert titles == macro


def test_ingest_fills_the_limit_with_market_stories_not_limit_minus_hidden():
    general = [_raw(i, f"Acme {i} (NYSE: AC{i}) jumps") for i in range(3)]
    general += [_raw(10 + i, f"Fed decision take {i}") for i in range(12)]
    out = asyncio.run(_IngestService(general)._fetch_market_raw(10))
    assert len(out) == 10 and all("Fed decision" in r["title"] for r in out)


def test_ingest_falls_back_to_the_quality_filtered_corpus_if_the_rule_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("bug in the relevance rule")

    monkeypatch.setattr(ncs, "select_market_stories", _boom)
    svc = _IngestService(general=[_raw(0, "Tesla (NASDAQ:TSLA) sinks"), _raw(1, "Fed holds")])
    assert len(asyncio.run(svc._fetch_market_raw(25))) == 2, "a bug must not empty the feed"


def test_ingest_never_empties_a_feed_of_only_single_company_citations():
    general = [_raw(i, f"Acme {i} (NYSE: AC{i}) jumps") for i in range(6)]
    out = asyncio.run(_IngestService(general)._fetch_market_raw(25))
    assert [r["url"] for r in out] == ["https://g/0", "https://g/1", "https://g/2"]


# ── 2. The writer judges new rows before they are readable ───────────────────

def _cold(svc, raws, model, limit=50):
    _stub_raw(svc, raws)
    svc.gemini = model
    return asyncio.run(svc._fetch_market_news(limit))


def test_the_cold_path_judges_every_row_before_writing_it(labels):
    raws = [_raw(i, ("Fire, smoke seen near Aramco facility in Riyadh" if i % 4 == 0
                     else f"Oil jumps after attack on Saudi facilities, take {i}"))
            for i in range(50)]
    svc = _service()
    model = _Model(_by_keyword)
    env = _cold(svc, raws, model)

    size = NewsCacheService._MARKET_INGEST_BATCH_COLD
    assert sorted(len(c) for c in model.calls) == sorted(
        min(size, 50 - s) for s in range(0, 50, size)
    ), "one page of 50, in the cold path's smaller batches"
    assert len(model.calls) <= 4
    rows = svc.supabase.scope_rows(MARKET_SCOPE)
    assert len(rows) == 50
    assert all(r["ai_processed"] for r in rows)
    stamps = {r["external_id"]: r["ai_model"] for r in rows}
    assert stamps["https://g/0"] == f"{MODEL}|scope=company"
    assert stamps["https://g/1"] == f"{MODEL}|scope=market"
    # The response is the verdict the cache now holds — and carries no stamp.
    assert not any("Aramco" in a["headline"] for a in env["articles"])
    assert len(env["articles"]) == 37
    assert all("ai_model" not in a for a in env["articles"])
    assert all(a["summary_bullets"] and a["ai_processed"] for a in env["articles"])
    # The news-tone log still hears every label, once, before the write.
    [(scope, logged, model_name)] = labels
    assert scope == MARKET_SCOPE and len(logged) == 50 and model_name == MODEL


def test_the_cold_response_matches_the_next_cached_read(labels):
    raws = [_raw(i, "Aramco plant fire" if i % 3 == 0 else f"Stocks take {i}")
            for i in range(30)]
    svc = _service()
    cold = _cold(svc, raws, _Model(_by_keyword))
    hit = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert [a["headline"] for a in cold["articles"]] == [a["headline"] for a in hit["articles"]]


def test_a_slow_model_cannot_hold_the_cold_path(monkeypatch, labels):
    """Past the budget the rows are written WITHOUT a verdict — kept for their life,
    never judged later — rather than keeping a person on a spinner."""
    monkeypatch.setattr(NewsCacheService, "_MARKET_CLASSIFY_BUDGET_COLD_SECONDS", 0.05)
    svc = _service()
    started = time.monotonic()
    env = _cold(svc, [_raw(i, "Aramco plant fire") for i in range(5)], _Model(_by_keyword,
                                                                              delay=2.0))
    assert time.monotonic() - started < 1.5
    rows = svc.supabase.scope_rows(MARKET_SCOPE)
    assert all(r["ai_model"] is None and not r["ai_processed"] for r in rows)
    assert len(env["articles"]) == 5, "unjudged rows are kept"
    assert labels == []


def test_a_failing_model_writes_rows_without_a_verdict(labels):
    svc = _service()
    env = _cold(svc, [_raw(i, "Aramco plant fire") for i in range(4)], _Model(_by_keyword,
                                                                              fail=True))
    assert all(r["ai_model"] is None for r in svc.supabase.scope_rows(MARKET_SCOPE))
    assert len(env["articles"]) == 4


def test_a_missing_scope_writes_the_summary_without_a_stamp(labels):
    svc = _service()
    _cold(svc, [_raw(i, f"Story {i}") for i in range(3)], _Model(lambda t: None))
    rows = svc.supabase.scope_rows(MARKET_SCOPE)
    assert all(r["ai_processed"] and r["ai_model"] == MODEL for r in rows)


def test_the_refresh_judges_only_rows_it_creates(labels):
    """An existing row keeps the verdict it was first served with: it is neither sent to
    the model again nor overwritten by the create-only pre-pass."""
    existing = [_cache_row(i, f"Old story {i}", hours_ago=10) for i in range(3)]
    for r, i in zip(existing, range(3)):
        r["external_id"] = f"https://g/{i}"
    svc = _service(existing)
    raws = [_raw(i, "Aramco plant fire" if i == 4 else f"New story {i}") for i in range(6)]
    _stub_raw(svc, raws)
    model = _Model(_by_keyword)
    svc.gemini = model
    asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))

    assert model.calls == [["New story 3", "Aramco plant fire", "New story 5"]]
    rows = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    for i in range(3):
        assert rows[f"https://g/{i}"]["ai_model"] is None, "an existing row is never judged"
    assert rows["https://g/4"]["ai_model"] == f"{MODEL}|scope=company"
    assert rows["https://g/3"]["ai_model"] == f"{MODEL}|scope=market"
    # The merge upsert never carries the AI columns.
    merge = [p for p, kw in svc.supabase.upserts if not kw.get("ignore_duplicates")]
    assert merge and all("ai_model" not in row for row in merge[-1])


def test_a_failed_lookup_judges_everything_but_still_overwrites_nothing(labels):
    existing = [_cache_row(0, "Old story 0", hours_ago=10)]
    existing[0]["external_id"] = "https://g/0"
    svc = _service(existing, fail_lookup=True)
    _stub_raw(svc, [_raw(0, "Old story 0"), _raw(1, "Aramco plant fire")])
    model = _Model(_by_keyword)
    svc.gemini = model
    asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))
    assert model.calls == [["Old story 0", "Aramco plant fire"]]
    rows = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    assert rows["https://g/0"]["ai_model"] is None
    assert rows["https://g/1"]["ai_model"] == f"{MODEL}|scope=company"


def test_a_ticker_refresh_never_calls_the_model(labels):
    svc = _service()

    class _FMP:
        async def get_stock_news(self, ticker=None, limit=10, from_date=None, to_date=None,
                                 page=0):
            return [_raw(0, "Oracle beats on cloud", symbol="ORCL")]

    svc.fmp = _FMP()
    model = _Model(_by_keyword)
    svc.gemini = model
    asyncio.run(svc.refresh_scope_news("ORCL"))
    assert model.calls == []


# ── 3. A later summary never adds or changes a stamp ─────────────────────────

def test_the_clients_summary_of_an_unjudged_row_writes_no_stamp(labels):
    row = _unjudged(0)
    svc = _service([row])
    svc.gemini = _Model(lambda t: "company", tags_for=lambda t: ["ACME"])
    out = asyncio.run(svc.enrich_articles(MARKET_SCOPE, [row["id"]]))
    [stored] = svc.supabase.scope_rows(MARKET_SCOPE)
    assert stored["ai_processed"] and stored["ai_model"] == MODEL
    assert stored["related_tickers"] == ["ACME"]
    assert classify_market_story(stored) == "not_judged"     # unchanged, still served
    assert out and all("ai_model" not in a for a in out)


def test_a_judged_row_keeps_its_stamp_whatever_a_later_answer_says(labels):
    """Defensive: a judged row is `ai_processed`, so nothing re-summarises it — but if
    anything ever did, the first verdict must survive."""
    row = _market(0)
    row["ai_processed"] = False
    svc = _service([row])
    svc.gemini = _Model(lambda t: "company")
    asyncio.run(svc.enrich_articles(MARKET_SCOPE, [row["id"]]))
    [stored] = svc.supabase.scope_rows(MARKET_SCOPE)
    assert stored["ai_model"] == f"{MODEL}|scope=market"


def test_a_ticker_rows_provenance_is_never_stamped(labels):
    row = _cache_row(0, "Oracle beats on cloud", scope="ORCL")
    svc = _service([row])
    svc.gemini = _Model(lambda t: "company")
    asyncio.run(svc.enrich_articles("ORCL", [row["id"]]))
    [stored] = svc.supabase.scope_rows("ORCL")
    assert stored["ai_model"] == MODEL


# ── 4. Paging, walked the way iOS walks it ───────────────────────────────────

def _ios_walk(svc, *, limit, between_pages=None, max_pages=60):
    """build 10 / 1.1: `loadedOffset += dtos.count`, keep `hasMore`, drop repeated ids
    (`dedupedByApiID`). Returns (unique ids in order, total rows received, pages)."""
    offset, received, unique, seen, pages = 0, 0, [], set(), 0
    while True:
        env = asyncio.run(svc.get_market_news(limit=limit, offset=offset))
        page = env["articles"]
        pages += 1
        assert pages <= max_pages, "the client is stuck re-requesting the same page"
        received += len(page)
        for a in page:
            if a["id"] not in seen:
                seen.add(a["id"])
                unique.append(a["id"])
        offset += len(page)
        if not env.get("has_more"):
            return unique, received, pages
        if between_pages is not None:
            between_pages(svc, page, pages)


def _client_scroll_summary(answer_scope, tags):
    """What the app does before every load-more: POST /updates/news/enrich for the
    unsummarised rows it is showing, and wait for it — the reviewers' trigger."""
    def _run(svc, page, _n):
        ids = [a["id"] for a in page if not a["ai_processed"]]
        if ids:
            svc.gemini = _Model(lambda t: answer_scope, tags_for=lambda t: list(tags))
            asyncio.run(svc.enrich_articles(MARKET_SCOPE, ids))
    return _run


@pytest.mark.parametrize("answer_scope,tags", [
    ("company", ["ACME"]),      # the model would hide every one of them
    ("unclear", ["ACME"]),      # the second trigger the reviewers found
    (None, ["ACME"]),           # a provider that never answers scope
])
def test_summaries_between_pages_cannot_make_the_client_skip_a_story(labels, answer_scope,
                                                                     tags):
    rows = []
    for i in range(25):     # the top, judged at insert: every 5th is one company's story
        rows.append(_company(i) if i % 5 == 0 else _market(i))
    rows += [_unjudged(i) for i in range(25, 160)]   # written without a verdict
    svc = _service(rows)
    visible = [r["id"] for r in sorted(rows, key=lambda r: r["published_at"], reverse=True)
               if classify_market_story(r) != "model_company"]

    unique, received, pages = _ios_walk(
        svc, limit=50, between_pages=_client_scroll_summary(answer_scope, tags),
    )

    assert unique == visible, "a market story was skipped (or a hidden one served)"
    assert received == len(visible), "no repeats: nothing moved"
    assert len(visible) == 155 and pages == 4          # 50 + 50 + 50 + 5
    # The client's summaries did land — bullets and tickers — just never a verdict.
    summarised = [r for r in svc.supabase.scope_rows(MARKET_SCOPE) if r["id"] in unique[:100]
                  and r["id"].endswith(tuple(f"{i:04d}" for i in range(25, 160)))]
    assert summarised and all(r["ai_processed"] and r["ai_model"] == MODEL
                              for r in summarised)


def test_new_rows_on_top_between_pages_only_repeat_and_the_client_drops_the_repeats(labels):
    """The one shift paging still meets: news arriving above page 0 pushes the list
    RIGHT, so the next page starts with rows the client already has — absorbed by id."""
    rows = [_company(i) if i % 7 == 0 else _market(i) for i in range(10, 130)]
    svc = _service(rows)
    visible = [r["id"] for r in sorted(rows, key=lambda r: r["published_at"], reverse=True)
               if classify_market_story(r) != "model_company"]
    inserted = []

    def _arrive(svc, page, n):
        for k in range(2):     # two fresh market stories, newer than anything served
            new = _market(0)
            new["id"] = new["external_id"] = f"new-{n}-{k}"
            new["published_at"] = (NOW + timedelta(minutes=n * 10 + k)).isoformat()
            svc.supabase.rows.append(new)
            inserted.append(new["id"])

    unique, received, pages = _ios_walk(svc, limit=30, between_pages=_arrive)
    assert [i for i in unique if not i.startswith("new-")] == visible, "nothing skipped"
    assert received - len(unique) == 2 * (pages - 1), "exactly the inserted rows repeat"


def test_page_zero_hides_judged_company_rows_and_says_more_exist():
    rows = [(_company(i) if i % 3 == 1 else _market(i)) for i in range(60)]
    svc = _service(rows)
    env = asyncio.run(svc.get_market_news(limit=10, offset=0))
    assert len(env["articles"]) == 10
    assert not any("Acme" in a["headline"] for a in env["articles"])
    assert env["has_more"] is True and env["cached"] is True
    assert len([c for c in svc.supabase.calls if c[0] == "select"]) == 1


def test_a_chunk_of_only_hidden_rows_does_not_stall_the_client():
    rows = [_market(i) for i in range(10)]
    rows += [_company(i) for i in range(10, 170)]
    rows += [_market(i) for i in range(170, 185)]
    unique, received, pages = _ios_walk(_service(rows), limit=10)
    assert unique == [r["id"] for r in rows if "Fed watch" in r["headline"]]
    assert received == len(unique) and pages == 3


def test_the_last_page_says_no_more():
    rows = [_market(i) for i in range(12)] + [_company(i) for i in range(12, 20)]
    env = asyncio.run(_service(rows).get_market_news(limit=10, offset=10))
    assert [a["id"] for a in env["articles"]] == [rows[10]["id"], rows[11]["id"]]
    assert env["has_more"] is False


def test_exactly_a_full_last_page_says_no_more():
    rows = [_market(i) for i in range(10)] + [_company(i) for i in range(10, 30)]
    env = asyncio.run(_service(rows).get_market_news(limit=10, offset=0))
    assert len(env["articles"]) == 10 and env["has_more"] is False


def test_a_cache_of_only_single_company_rows_serves_the_floor_not_nothing():
    rows = [_company(i) for i in range(5)]
    svc = _service(rows)

    async def _no_fetch(limit):
        raise AssertionError("a populated cache must not be treated as a miss")

    svc._fetch_market_news = _no_fetch
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert [a["id"] for a in env["articles"]] == [r["id"] for r in rows[:3]]
    assert env["has_more"] is False


def test_an_empty_cache_is_still_a_miss_that_fetches():
    svc = _service()
    fetched = []

    async def _fetch(limit):
        fetched.append(limit)
        return {"articles": [], "ticker": MARKET_SCOPE, "cached": False,
                "cache_age_seconds": 0}

    svc._fetch_market_news = _fetch
    asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert fetched == [50]


def test_a_failed_cache_read_never_reaches_the_cold_fetch():
    """Inverted 2026-10-07 (F1): this test used to pin the fall-through — a failed read
    WAS the cold fetch, and the cold fetch re-wrote rows clients were holding."""
    svc = _service([_market(i) for i in range(5)])
    svc.supabase.fail_reads = 10 ** 6

    async def _cold(limit):
        raise AssertionError("a failed read took the cold write path")

    svc._fetch_market_news = _cold
    _stub_raw(svc, [_raw(0, "Fed holds rates")])
    env = asyncio.run(svc.get_market_news(limit=10, offset=0))
    assert [a["headline"] for a in env["articles"]] == ["Fed holds rates"]


def test_a_read_failure_mid_scan_is_retried_whole():
    """The page is read again from the top — never served half-read (it used to serve the
    floor's three hidden stories when the second chunk failed)."""
    rows = [_company(i) for i in range(100)] + [_market(i) for i in range(100, 130)]
    svc = _service(rows, fail_on_call=2)
    env = asyncio.run(svc.get_market_news(limit=10, offset=0))
    assert [a["id"] for a in env["articles"]] == [r["id"] for r in rows[100:110]]
    assert env["has_more"] is True and svc.supabase.upserts == []


def test_a_deep_page_settles_in_one_round_trip():
    svc = _service([_market(i) for i in range(200)])
    env = asyncio.run(svc.get_market_news(limit=50, offset=100))
    assert [a["id"] for a in env["articles"]] == [_market(i)["id"] for i in range(100, 150)]
    assert env["has_more"] is True
    assert len([c for c in svc.supabase.calls if c[0] == "select"]) == 1


def test_the_scan_guard_stops_a_runaway(monkeypatch, caplog):
    svc = _service([_company(i) for i in range(400)])
    monkeypatch.setattr(NewsCacheService, "_MARKET_MAX_SCAN_ROWS", 200)
    page, has_more = svc._get_cached_market_page(10, 0)
    assert len([c for c in svc.supabase.calls if c[0] == "select"]) == 4
    assert "scan guard" in caplog.text
    assert len(page) == 3 and has_more is False


# ── 5. The bulk corpus read ───────────────────────────────────────────────────

def test_the_market_bucket_is_filtered_before_its_cap():
    market = [(_company(i) if i % 2 else _market(i)) for i in range(80)]
    ticker = [_cache_row(i, f"Acme Robotics {i} recalls robots", scope="ACME", tags=["ACME"])
              for i in range(5)]
    grouped = _service(market + ticker).get_cached_bulk([MARKET_SCOPE, "ACME"], 25)
    assert [r["id"] for r in grouped[MARKET_SCOPE]] == \
        [r["id"] for r in market if "Fed watch" in r["headline"]][:25]
    assert len(grouped["ACME"]) == 5, "a ticker scope is never filtered"


def test_the_card_corpus_reads_the_insert_time_verdict():
    """`get_cached_bulk` projects `ai_model`, or the card would cite a hidden story."""
    rows = [_company(0)] + [_market(i) for i in range(1, 6)]
    grouped = _service(rows).get_cached_bulk([MARKET_SCOPE], 25)
    assert [r["id"] for r in grouped[MARKET_SCOPE]] == [r["id"] for r in rows[1:]]


def test_the_market_bucket_keeps_its_floor():
    grouped = _service([_company(i) for i in range(6)]).get_cached_bulk([MARKET_SCOPE], 25)
    assert [r["id"] for r in grouped[MARKET_SCOPE]] == [_company(i)["id"] for i in range(3)]


# ── 6. The endpoint: timeline + "Latest Market headlines" card ───────────────

class _NoCardInsights:
    """No stored AI card (the reported weekend screen) → the real fallback builder."""

    async def get_cards(self, scopes):
        return {}

    def build_fallback_card(self, scope, corpus):
        real = object.__new__(NewsInsightService)
        return NewsInsightService.build_fallback_card(real, scope, corpus, market_active=False)


def test_the_reported_screen_has_no_single_company_story(monkeypatch):
    rows = [
        _cache_row(0, "Fire, smoke seen near Aramco facility in Riyadh, witness says",
                   verdict="company", hours_ago=1),
        _cache_row(1, "Tesla recalls more than 300,000 vehicles", verdict="company",
                   tags=["TSLA"], hours_ago=2),
        _cache_row(2, "One Trade, Two Markets, One Hidden Correction", verdict="market",
                   tags=["SPY"], hours_ago=3),
        _cache_row(3, "As Treasury yields touch generational highs, investors brace for "
                      "the market fallout", verdict="market", hours_ago=4),
        _cache_row(4, "Oil jumps after attack on Saudi facilities", verdict="market",
                   hours_ago=5),
        _cache_row(5, "Stocks fall as Nvidia slides on export curbs", tags=["NVDA"],
                   hours_ago=6),   # never judged → kept
    ]
    svc = _service(rows)
    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", lambda: svc)
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", _NoCardInsights)

    resp = asyncio.run(updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=50, offset=0))

    assert [a.headline for a in resp.articles] == [rows[i]["headline"] for i in (2, 3, 4, 5)]
    assert resp.insight is not None and resp.insight.headline == "Latest Market headlines"
    card_text = " ".join(resp.insight.bullets + [s.title for s in resp.insight.sources or []])
    assert "Aramco" not in card_text and "Tesla" not in card_text
    assert resp.has_more is False
    assert all("ai_model" not in a.model_dump() for a in resp.articles)


def test_the_endpoint_follows_the_services_has_more(monkeypatch):
    class _News:
        def __init__(self, has_more, n):
            self._env = {"articles": [_market(i) for i in range(n)], "cached": True,
                         "has_more": has_more}

        async def get_market_news(self, **kwargs):
            return self._env

    class _Quiet(_NoCardInsights):
        def build_fallback_card(self, scope, corpus):
            return None

    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", _Quiet)
    for has_more, n, expected in ((False, 10, False), (True, 7, True)):
        monkeypatch.setattr(updates_endpoint, "get_news_cache_service",
                            lambda h=has_more, k=n: _News(h, k))
        resp = asyncio.run(updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=10,
                                                             offset=0))
        assert resp.has_more is expected
    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", lambda: _News(True, 10))
    resp = asyncio.run(updates_endpoint.get_updates_feed(scope=MARKET_SCOPE, limit=10,
                                                         offset=495))
    assert resp.has_more is False, "the offset ceiling still binds"


def test_a_feed_without_the_key_keeps_the_page_length_rule(monkeypatch):
    class _News:
        async def get_ticker_news(self, scope, **kwargs):
            return {"articles": [_market(i) for i in range(10)], "cached": True}

    class _Quiet(_NoCardInsights):
        def build_fallback_card(self, scope, corpus):
            return None

    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", _News)
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", _Quiet)
    resp = asyncio.run(updates_endpoint.get_updates_feed(scope="AAPL", limit=10, offset=0))
    assert resp.has_more is True


# ── 7. The summary step: one call, a Market-only scope field ─────────────────

_ARTICLES = [
    {"title": "Fire, smoke seen near Aramco facility in Riyadh, witness says",
     "text": "Smoke rose near the site on Friday."},
    {"title": "Oil jumps after attack on Saudi facilities", "text": "Brent rose 4%."},
]


def test_only_the_market_prompt_asks_what_an_article_is_about():
    market = build_enrichment_prompt(_ARTICLES, MARKET_SCOPE)
    assert MARKET_SCOPE_RULE in market and MARKET_SCOPE_FIELD in market
    for ticker in ("ORCL", ""):
        other = build_enrichment_prompt(_ARTICLES, ticker)
        assert '"scope"' not in other and "5. Scope" not in other


def test_the_market_prompt_no_longer_tells_the_model_the_answer():
    assert "not about one company" not in build_enrichment_prompt(_ARTICLES, MARKET_SCOPE)


def test_the_market_prompt_names_no_model_vendor_and_keeps_the_fence():
    market = build_enrichment_prompt(_ARTICLES, MARKET_SCOPE)
    for word in ("gemini", "google", "openai", "language model"):
        assert word not in market.lower(), word
    assert "<<<ARTICLE 0>>>" in market and "<<<END_ARTICLE 1>>>" in market
    assert "UNTRUSTED THIRD-PARTY TEXT" in market


def test_the_market_schema_adds_a_required_scope_and_leaves_the_shared_one_alone():
    base = NewsCacheService._ENRICHMENT_SCHEMA
    market = NewsCacheService._MARKET_ENRICHMENT_SCHEMA
    assert "scope" not in base["items"]["properties"]
    assert "scope" not in base["items"]["required"]
    assert market["items"]["properties"]["scope"] == {"type": "STRING", "enum": list(MODEL_SCOPES)}
    assert "scope" in market["items"]["required"]
    for key, spec in base["items"]["properties"].items():
        assert market["items"]["properties"][key] == spec
    assert market["items"]["properties"]["bullets"] is not base["items"]["properties"]["bullets"]


def test_the_openai_compatible_translation_carries_the_scope_enum():
    schema = news_llm.gemini_schema_to_json_schema(NewsCacheService._MARKET_ENRICHMENT_SCHEMA)
    assert schema["items"]["properties"]["scope"] == {"type": "string",
                                                      "enum": list(MODEL_SCOPES)}
    assert schema["items"]["additionalProperties"] is False


class _CapModel:
    def __init__(self, items):
        self.calls = []
        self._items = items

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": json.dumps(self._items), "model": "fake", "tokens_used": 0,
                "finish_reason": "STOP"}


def _item(scope="__absent__"):
    item = {"index": 0, "bullets": ["A point.", "The conclusion."], "sentiment": "bearish",
            "confidence": 70, "related_tickers": []}
    if scope != "__absent__":
        item["scope"] = scope
    return item


def test_one_call_carries_the_market_schema_and_the_scope_comes_back(labels):
    cap = _CapModel([_item("company"), _item(" Market ")])
    svc = object.__new__(NewsCacheService)
    svc.gemini = cap
    out = asyncio.run(svc._batch_enrich_articles(_ARTICLES, ticker=MARKET_SCOPE))
    assert len(cap.calls) == 1, "the scope rides on the existing call"
    assert cap.calls[0]["response_schema"] is NewsCacheService._MARKET_ENRICHMENT_SCHEMA
    assert [out[0]["scope"], out[1]["scope"]] == ["company", "market"]


def test_a_ticker_call_keeps_the_shared_schema(labels):
    cap = _CapModel([_item("company"), _item("market")])
    svc = object.__new__(NewsCacheService)
    svc.gemini = cap
    asyncio.run(svc._batch_enrich_articles(_ARTICLES, ticker="ORCL"))
    assert cap.calls[0]["response_schema"] is NewsCacheService._ENRICHMENT_SCHEMA


@pytest.mark.parametrize("raw,expected", [
    ("company", "company"), ("SECTOR", "sector"), (" unclear ", "unclear"),
    ("market", "market"), ("Company story", None), ("", None), (None, None), (3, None),
    (["company"], None), ("__absent__", None),
])
def test_the_mapper_keeps_only_a_listed_scope(raw, expected):
    mapped = NewsCacheService._map_enrichments([_item(raw)], 1)
    assert mapped[0]["scope"] == expected
    assert mapped[0]["bullets"] == ["A point.", "The conclusion."], "the rest still maps"


# ── 8. Final review 2026-10-07 — F1: the cold path never re-writes an existing row ──
#
# F1: a transient read error looked like an empty cache, so page 0 fell into the cold
# fetch, which judged every fetched row again and MERGE-upserted the answer (or, past its
# budget, empty AI columns) over rows clients were already holding: verdicts flipped under
# their offsets, hidden stories came back. A refresh landing during a cold fetch did the
# same. F2: one slow batch threw away the batches that had already answered.

def _raw_for(row):
    """The FMP shape of an article the cache already holds — what a cold fetch gets back
    for a row that is still there. `publishedDate` is the stored instant WITH its offset,
    so the merge that renews the row writes back exactly the instant it held (review
    round 7: a naive UTC clock is read as New York time — see `_raw`)."""
    return {"url": row["external_id"], "title": row["headline"], "symbol": None,
            "publishedDate": row["published_at"],
            "publisher": "Reuters", "site": "reuters.com", "text": "body", "image": None}


def _stamps(svc):
    return {r["external_id"]: (r.get("ai_model"), r.get("ai_processed"),
                               r.get("summary_bullets"))
            for r in svc.supabase.scope_rows(MARKET_SCOPE)}


def test_a_failed_read_never_rewrites_a_served_rows_verdict(labels):
    """The reviewers' reproduction: client A holds page 0; a read blip on someone else's
    request must not re-judge A's rows, and A's next page must skip nothing."""
    rows = [_market(i) for i in range(80)]
    svc = _service(rows)
    _stub_raw(svc, [_raw_for(r) for r in rows[:50]])
    before = _stamps(svc)

    page0 = asyncio.run(svc.get_market_news(limit=50, offset=0))          # client A
    flip = {r["headline"] for r in rows[:3]}
    svc.gemini = _Model(lambda t: "company" if t in flip else "market")    # disagrees
    svc.supabase.fail_reads = 1                                           # one blip
    asyncio.run(svc.get_market_news(limit=50, offset=0))                  # someone else
    page1 = asyncio.run(svc.get_market_news(limit=50, offset=len(page0["articles"])))

    assert _stamps(svc) == before, "an existing row's verdict or summary was rewritten"
    served = [a["id"] for a in page0["articles"] + page1["articles"]]
    assert served == [r["id"] for r in rows], "client A skipped a story"


def test_one_failed_read_is_retried_not_treated_as_an_empty_cache(labels):
    rows = [_market(i) for i in range(30)]
    svc = _service(rows)
    svc.supabase.fail_reads = 1

    async def _no_fetch(limit, from_date=None):
        raise AssertionError("a read blip is not an empty cache")

    svc._fetch_market_raw = _no_fetch
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert [a["id"] for a in env["articles"]] == [r["id"] for r in rows]
    assert env["cached"] is True and svc.supabase.upserts == []


def test_a_cache_that_cannot_be_read_is_never_written(labels):
    """Reads down: serve a read-only page straight from FMP — nothing written, nothing
    judged, `has_more` false so no client pages into a cache it cannot read."""
    rows = [_company(0)] + [_market(i) for i in range(1, 30)]
    svc = _service(rows)
    before = _stamps(svc)
    svc.supabase.fail_reads = 10 ** 6
    _stub_raw(svc, [_raw_for(r) for r in rows] + [_raw(99, "Tesla (NASDAQ: TSLA) sinks")])
    model = _Model(lambda t: "company")
    svc.gemini = model

    env = asyncio.run(svc.get_market_news(limit=50, offset=0))

    assert svc.supabase.upserts == [] and model.calls == []
    assert _stamps(svc) == before
    assert env["has_more"] is False and env["cached"] is False
    assert len(env["articles"]) == 30, "kept: unjudged rows (the headline rule still applies)"
    assert not any("TSLA" in a["headline"] for a in env["articles"])
    assert all(a["id"].startswith("raw_") for a in env["articles"]), "never enrichable"


def test_a_cold_write_never_rejudges_rows_a_refresh_wrote_meanwhile(labels):
    """The race: the cache really was empty, but a refresh inserted (and judged) the same
    articles while the cold fetch's model was working."""
    landed = [_market(i) for i in range(20)]
    svc = _service()
    _stub_raw(svc, [_raw_for(r) for r in landed])

    def _refresh_lands(_titles):
        if not svc.supabase.rows:
            svc.supabase.rows.extend(dict(r) for r in landed)

    svc.gemini = _Model(lambda t: "company", on_call=_refresh_lands)   # disagrees with all
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))

    assert _stamps(svc) == {r["external_id"]: (r["ai_model"], r["ai_processed"],
                                               r["summary_bullets"]) for r in landed}
    assert [a["id"] for a in env["articles"]] == [r["id"] for r in landed], \
        "page 0 must come from the cache's own verdicts"


def test_a_timed_out_cold_write_never_wipes_rows_a_refresh_wrote_meanwhile(monkeypatch,
                                                                          labels):
    monkeypatch.setattr(NewsCacheService, "_MARKET_CLASSIFY_BUDGET_COLD_SECONDS", 0.05)
    landed = [_company(i) if i % 4 == 0 else _market(i) for i in range(20)]
    svc = _service()
    _stub_raw(svc, [_raw_for(r) for r in landed])

    def _refresh_lands(_titles):
        if not svc.supabase.rows:
            svc.supabase.rows.extend(dict(r) for r in landed)

    svc.gemini = _Model(lambda t: "market", delay=1.0, on_call=_refresh_lands)
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))

    assert _stamps(svc) == {r["external_id"]: (r["ai_model"], r["ai_processed"],
                                               r["summary_bullets"]) for r in landed}
    assert not any("Acme" in a["headline"] for a in env["articles"]), \
        "a hidden story came back"


def test_a_cold_fetch_over_existing_rows_only_renews_them(labels):
    """Every fetched row already cached (a race, or rows past their TTL): no model call,
    no verdict or summary touched — only `expires_at` and the FMP columns renewed."""
    stored = [_company(0), _market(1), _unjudged(2)]
    for r in stored:
        r["expires_at"] = (NOW - timedelta(hours=1)).isoformat()      # expired: unreadable
    svc = _service(stored)
    _stub_raw(svc, [_raw_for(r) for r in stored])
    model = _Model(lambda t: "market")
    svc.gemini = model
    before = _stamps(svc)

    env = asyncio.run(svc.get_market_news(limit=50, offset=0))

    assert model.calls == [] and _stamps(svc) == before
    assert all(r["expires_at"] > NOW.isoformat() for r in svc.supabase.scope_rows(MARKET_SCOPE))
    assert [a["headline"] for a in env["articles"]] == [stored[1]["headline"],
                                                        stored[2]["headline"]] or \
        len(env["articles"]) == 3, "page 0 honours the stored verdicts (floor aside)"


def test_a_failed_page_read_past_page_zero_ends_paging_without_looping(labels):
    """A deep page that cannot be read ends the paging — it must NEVER fall into the
    read-only FMP page, whose `raw_` ids would be appended below the reader as copies of
    the newest stories (review round 5, R5-3). The stub RECORDS and returns real rows:
    a raising stub would be swallowed by the read-only page's own `except`."""
    svc = _service([_market(i) for i in range(80)])
    svc.supabase.fail_reads = 2                     # the read and its retry
    fetched = []

    async def _record(limit, from_date=None):
        fetched.append(limit)
        return [_raw(0, "Fed holds rates steady"), _raw(1, "Treasury yields climb")]

    svc._fetch_market_raw = _record
    env = asyncio.run(svc.get_market_news(limit=50, offset=50))
    assert fetched == [], "a deep page fetched from FMP"
    assert env["articles"] == [] and env["has_more"] is False
    assert svc.supabase.upserts == []


# ── 9. Final review 2026-10-07 — F2: a slow batch never costs a finished one ──

def _cold_batch_size():
    return getattr(NewsCacheService, "_MARKET_INGEST_BATCH_COLD",
                   getattr(NewsCacheService, "_MARKET_INGEST_BATCH", 25))


def test_a_slow_batch_does_not_cost_the_finished_batches_their_verdicts(monkeypatch, labels):
    monkeypatch.setattr(NewsCacheService, "_MARKET_CLASSIFY_BUDGET_COLD_SECONDS", 0.3)
    raws = [_raw(i, ("Aramco plant fire" if i % 3 == 0 else "Stocks take") + f" {i}"
                 + (" SLOW" if i >= 26 else "")) for i in range(30)]
    svc = _service()
    _stub_raw(svc, raws)
    svc.gemini = _Model(_by_keyword,
                        delay_for=lambda titles: 2.0 if any("SLOW" in t for t in titles) else 0)
    started = time.monotonic()
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert time.monotonic() - started < 1.5

    size = _cold_batch_size()
    batches = [list(range(s, min(s + size, 30))) for s in range(0, 30, size)]
    answered = {i for b in batches if not any(i >= 26 for i in b) for i in b}
    assert answered, "the test needs at least one batch that finishes"
    rows = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    for i in range(30):
        stamp = rows[f"https://g/{i}"]["ai_model"]
        if i in answered:
            assert stamp in (f"{MODEL}|scope=company", f"{MODEL}|scope=market"), i
        else:
            assert stamp is None, i
    index_of = {raw["title"]: i for i, raw in enumerate(raws)}
    shown = [index_of[a["headline"]] for a in env["articles"]]
    assert not any(i % 3 == 0 and i in answered for i in shown), \
        "a judged single-company story was served"
    assert any(i % 3 == 0 and i not in answered for i in shown), \
        "an unjudged row is kept (fail-open), Aramco or not"


def test_the_refresh_keeps_every_batch_that_answered_too(monkeypatch, labels):
    monkeypatch.setattr(NewsCacheService, "_MARKET_CLASSIFY_BUDGET_REFRESH_SECONDS", 0.3)
    raws = [_raw(i, f"Story {i}" + (" SLOW" if i >= 40 else "")) for i in range(50)]
    svc = _service()
    _stub_raw(svc, raws)
    svc.gemini = _Model(lambda t: "market",
                        delay_for=lambda titles: 2.0 if any("SLOW" in t for t in titles) else 0)
    asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))
    stamped = [r for r in svc.supabase.scope_rows(MARKET_SCOPE)
               if r["ai_model"] == f"{MODEL}|scope=market"]
    assert len(stamped) == 25, "the first batch of 25 answered and must be kept"


def test_each_batch_logs_its_elapsed_time(monkeypatch, labels, caplog):
    import logging

    monkeypatch.setattr(NewsCacheService, "_MARKET_CLASSIFY_BUDGET_COLD_SECONDS", 0.3)
    raws = [_raw(i, f"Story {i}" + (" SLOW" if i >= 26 else "")) for i in range(30)]
    svc = _service()
    _stub_raw(svc, raws)
    svc.gemini = _Model(lambda t: "market",
                        delay_for=lambda titles: 2.0 if any("SLOW" in t for t in titles) else 0)
    with caplog.at_level(logging.INFO, logger=ncs.__name__):
        asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert "answered in" in caplog.text and "did not answer within" in caplog.text


# ── 10. Hardening: every way the Market write path can degrade ───────────────

def _quota():
    from app.integrations.fmp import FMPRateLimitException

    return FMPRateLimitException("429")


@pytest.mark.parametrize("reads_down", [False, True])
def test_an_fmp_quota_error_stays_a_structured_error_and_writes_nothing(labels, reads_down):
    """Cold path (cache readable and empty) and read-only page (cache unreadable) alike."""
    from app.integrations.fmp import FMPRateLimitException

    svc = _service([_market(0)] if reads_down else [])
    svc.supabase.fail_reads = 10 ** 6 if reads_down else 0

    async def _raise(limit, from_date=None):
        raise _quota()

    svc._fetch_market_raw = _raise
    with pytest.raises(FMPRateLimitException):
        asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert svc.supabase.upserts == []


@pytest.mark.parametrize("reads_down", [False, True])
def test_an_empty_fmp_fetch_writes_nothing_and_judges_nothing(labels, reads_down):
    svc = _service([_market(0)] if reads_down else [])
    svc.supabase.fail_reads = 10 ** 6 if reads_down else 0
    _stub_raw(svc, [])
    model = _Model(lambda t: "market")
    svc.gemini = model
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert env["articles"] == [] and model.calls == [] and svc.supabase.upserts == []


def test_a_failed_write_still_serves_what_was_judged_and_is_never_enrichable(labels):
    svc = _service()
    svc.supabase.fail_upserts = True
    _stub_raw(svc, [_raw(i, "Aramco plant fire" if i == 0 else f"Stocks take {i}")
                    for i in range(5)])
    svc.gemini = _Model(_by_keyword)
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert [a["headline"] for a in env["articles"]] == [f"Stocks take {i}" for i in range(1, 5)]
    assert all(a["id"].startswith("temp_") for a in env["articles"])
    assert env["has_more"] is False


def test_a_read_back_that_fails_twice_serves_every_row_of_the_write(labels):
    """Inverted in review round 5 (R5-2): the fallback served only rows this write
    CREATED, so when every fetched row already existed (expired, or a racing refresh) the
    Market tab came back EMPTY while the cache held them all. Every row of the write is
    served, fail-open like the read-only page, with `has_more` false — nothing to page."""
    old = _company(90, hours_ago=1)
    old["expires_at"] = (NOW - timedelta(minutes=1)).isoformat()     # expired → cold miss
    svc = _service([old])
    raws = [_raw_for(old)] + [_raw(i, f"Stocks take {i}") for i in range(1, 4)]
    _stub_raw(svc, raws)

    def _reads_break(_titles):
        svc.supabase.fail_reads = 2                  # the read-back AND its retry

    svc.gemini = _Model(lambda t: "market", on_call=_reads_break)
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert [a["headline"] for a in env["articles"]] == \
        [old["headline"]] + [f"Stocks take {i}" for i in range(1, 4)]
    assert env["has_more"] is False
    [stored] = [r for r in svc.supabase.scope_rows(MARKET_SCOPE)
                if r["external_id"] == old["external_id"]]
    assert stored["ai_model"] == old["ai_model"], "the existing row was not re-judged"


def test_a_read_back_that_fails_once_is_retried(labels):
    """Every fetched row already exists (all expired → page 0 read empty → cold path);
    one read error on the read-back used to serve an empty tab (R5-2). The retry serves
    the CACHE's verdicts: the five stored `company` rows stay hidden — the fail-open
    fallback, which knows no stored verdict, would show them."""
    rows = [_company(i) if i % 10 == 0 else _market(i) for i in range(50)]
    for r in rows:
        r["expires_at"] = (NOW - timedelta(minutes=1)).isoformat()
    svc = _service(rows)
    _stub_raw(svc, [_raw_for(r) for r in rows])
    svc.supabase.fail_read_calls = {2}       # read #1 = page 0 (empty), #2 = the read-back
    model = _Model(lambda t: "market")
    svc.gemini = model
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert model.calls == [], "every row already existed: nothing to judge"
    assert [a["id"] for a in env["articles"]] == [r["id"] for i, r in enumerate(rows) if i % 10]
    assert env["has_more"] is False


# ── 11. Review round 5 — R5-1: a verdict is never lost to a failed pre-pass ──

def test_a_failed_market_prepass_is_retried_once(labels):
    raws = [_raw(i, "Aramco plant fire" if i % 4 == 0 else f"Stocks take {i}")
            for i in range(20)]
    svc = _service()
    _stub_raw(svc, raws)
    svc.gemini = _Model(_by_keyword)
    svc.supabase.fail_prepass = 1
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    rows = svc.supabase.scope_rows(MARKET_SCOPE)
    assert svc.supabase.prepass_failures == 1
    assert len(rows) == 20
    assert all(r.get("ai_processed") and "|scope=" in (r.get("ai_model") or "") for r in rows), \
        "a row was created without the verdict judged for it"
    assert not any("Aramco" in a["headline"] for a in env["articles"])


def test_a_healthy_market_write_sends_the_prepass_once(labels):
    """Must-keep twin of the retry: it runs only after a failure."""
    svc = _service()
    _stub_raw(svc, [_raw(i, f"Stocks take {i}") for i in range(5)])
    svc.gemini = _Model(lambda t: "market")
    asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))
    assert [bool(kw.get("ignore_duplicates")) for _, kw in svc.supabase.upserts] == [True, False]
    assert len(svc.supabase.scope_rows(MARKET_SCOPE)) == 5


def test_a_prepass_that_succeeds_on_its_retry_is_a_success(labels, caplog):
    """Review round 6 (R6-1): a retry that succeeds clears the failure. Kept marked as
    failed, the write would log an ERROR claiming judged verdicts were lost and leave
    the new rows out of the merge — though the retry had already created them."""
    import logging

    svc = _service()
    _stub_raw(svc, [_raw(i, f"Stocks take {i}") for i in range(5)])
    svc.gemini = _Model(lambda t: "market")
    svc.supabase.fail_prepass = 1
    with caplog.at_level(logging.ERROR, logger=ncs.__name__):
        asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))
    assert svc.supabase.prepass_failures == 1, "the first attempt did not fail"
    assert "left out" not in caplog.text and "merge skipped" not in caplog.text
    assert [bool(kw.get("ignore_duplicates")) for _, kw in svc.supabase.upserts] == [True, False]
    merge = [p for p, kw in svc.supabase.upserts if not kw.get("ignore_duplicates")][0]
    assert sorted(r["external_id"] for r in merge) == sorted(f"https://g/{i}" for i in range(5)), \
        "the merge did not carry every row"


def test_a_prepass_that_keeps_failing_never_creates_a_row_without_its_verdict(labels, caplog):
    """The merge upsert carries no AI columns: had it created the new rows, they would be
    readable with no verdict for life. They are left for the next write to create and
    judge; rows that already exist are still renewed; the loss is logged at ERROR."""
    import logging

    old = [_market(90 + i, hours_ago=2) for i in range(3)]
    for i, r in enumerate(old):
        r["external_id"] = f"https://g/{i}"                  # FMP returns them again
        r["expires_at"] = (NOW + timedelta(minutes=1)).isoformat()
    svc = _service(old)
    raws = [_raw(i, "Aramco plant fire" if i == 4 else f"Story {i}") for i in range(6)]
    _stub_raw(svc, raws)
    svc.gemini = _Model(_by_keyword)
    svc.supabase.fail_prepass = 10 ** 6
    with caplog.at_level(logging.ERROR, logger=ncs.__name__):
        asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))

    rows = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    assert set(rows) == {f"https://g/{i}" for i in range(3)}, \
        "a new row was created without its verdict"
    renewed = (NOW + timedelta(hours=1)).isoformat()
    assert all(rows[f"https://g/{i}"]["expires_at"] > renewed for i in range(3)), \
        "rows that already exist are still renewed"
    assert "3 new row(s) left out" in caplog.text
    assert "3 judged verdict(s) lost" in caplog.text

    svc.supabase.fail_prepass = 0                            # the next write, healthy
    asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))
    rows = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    assert rows["https://g/4"]["ai_model"] == f"{MODEL}|scope=company"
    assert rows["https://g/3"]["ai_model"] == f"{MODEL}|scope=market"


def test_a_cold_fetch_whose_prepass_keeps_failing_still_serves_page_zero(labels):
    """R5-1 meets R5-2 on the request path: on a truly empty cache nothing may be created
    without its verdict, so nothing is written — yet page 0 is served (from memory, with
    the verdicts just judged: the Aramco story stays hidden), never as an empty tab. The
    next healthy request creates and judges the rows."""
    raws = [_raw(i, "Aramco plant fire" if i == 2 else f"Stocks take {i}") for i in range(6)]
    svc = _service()
    _stub_raw(svc, raws)
    svc.gemini = _Model(_by_keyword)
    svc.supabase.fail_prepass = 10 ** 6
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    assert svc.supabase.scope_rows(MARKET_SCOPE) == []
    assert [a["headline"] for a in env["articles"]] == \
        [f"Stocks take {i}" for i in (0, 1, 3, 4, 5)]
    assert env["has_more"] is False
    assert not any("ai_model" in a for a in env["articles"])

    svc.supabase.fail_prepass = 0
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))
    stamps = {r["external_id"]: r["ai_model"] for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    assert stamps["https://g/2"] == f"{MODEL}|scope=company"
    assert len(stamps) == 6 and all("|scope=" in s for s in stamps.values())
    assert [a["headline"] for a in env["articles"]] == \
        [f"Stocks take {i}" for i in (0, 1, 3, 4, 5)]


def test_a_cold_fetch_over_expired_rows_whose_prepass_keeps_failing_renews_them(labels):
    """Review round 6 (R6-1): the cold path hands the writer the rows it found cached.
    Over EXPIRED rows, a pre-pass that keeps failing still renews every one of them, and
    page 0 is read back from the cache — real ids, the stored `company` rows hidden.
    Without `existing` the merge is skipped: nothing renewed, and page 0 served from
    memory with temp_ ids and the stored single-company stories shown, the cold path
    taken again on every later request."""
    rows = [_company(i) if i % 5 == 0 else _market(i) for i in range(20)]
    for r in rows:
        r["expires_at"] = (NOW - timedelta(minutes=1)).isoformat()
    svc = _service(rows)
    _stub_raw(svc, [_raw_for(r) for r in rows]
              + [_raw(30 + j, f"Stocks take {j}") for j in range(5)])
    svc.gemini = _Model(lambda t: "market")
    svc.supabase.fail_prepass = 10 ** 6
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))

    stored = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    assert set(stored) == {r["external_id"] for r in rows}, \
        "a new row was created without its verdict"
    renewed = (NOW + timedelta(hours=1)).isoformat()
    assert all(stored[r["external_id"]]["expires_at"] > renewed for r in rows), \
        "a row already in the cache was not renewed"
    assert [a["id"] for a in env["articles"]] == \
        [r["id"] for i, r in enumerate(rows) if i % 5], \
        "page 0 was not read back from the cache (real ids, stored company rows hidden)"


def test_a_ticker_scope_prepass_failure_still_writes_every_row_once(labels):
    """Must-keep twin of R5-1: the retry and the left-out rule are MARKET-only. A ticker
    row's AI columns are filled later by enrichment (it lands `ai_processed` false), so a
    ticker write still renews AND creates every row when its pre-pass fails — after one
    attempt, as before. Applied to a ticker scope (whose writers pass no `existing`), the
    rule would skip the merge and a News tab would age out on a pre-pass blip."""
    stored = _cache_row(0, "Apple story 0", scope="AAPL")
    stored["external_id"] = "https://g/0"
    stored["expires_at"] = (NOW + timedelta(minutes=1)).isoformat()
    svc = _service([stored])
    svc.supabase.fail_prepass = 10 ** 6
    written = svc._build_and_cache_rows(
        "AAPL", [_raw(i, f"Apple story {i}", "AAPL") for i in range(4)], 10, "AAPL",
        "AAPL (refresh)", True,
    )
    assert svc.supabase.prepass_failures == 1
    rows = {r["external_id"]: r for r in svc.supabase.scope_rows("AAPL")}
    assert set(rows) == {f"https://g/{i}" for i in range(4)}
    assert rows["https://g/0"]["expires_at"] > (NOW + timedelta(hours=1)).isoformat()
    assert not any(a["id"].startswith("temp_") for a in written)


def test_with_existence_unknown_a_failing_prepass_skips_the_merge(labels, caplog):
    import logging

    old = _market(90, hours_ago=2)
    old["external_id"] = "https://g/0"
    svc = _service([old], fail_lookup=True)
    _stub_raw(svc, [_raw(0, "Story 0"), _raw(1, "Story 1")])
    svc.gemini = _Model(lambda t: "market")
    svc.supabase.fail_prepass = 10 ** 6
    with caplog.at_level(logging.ERROR, logger=ncs.__name__):
        asyncio.run(svc.refresh_scope_news(MARKET_SCOPE))
    assert [r["external_id"] for r in svc.supabase.scope_rows(MARKET_SCOPE)] == ["https://g/0"]
    assert [kw for _, kw in svc.supabase.upserts if not kw.get("ignore_duplicates")] == []
    assert "merge skipped, 2 row(s) not written (2 judged verdict(s) lost" in caplog.text


# ── 12. Review round 5 — R5-4: the structural guard has a test of its own ────

def test_a_market_write_asked_for_as_a_full_write_is_forced_create_only(labels, caplog):
    """A future Market writer that forgets `ingest_only` (how F1 happened) must still
    leave an existing row's verdict, summary and tickers alone."""
    import logging

    stored = _company(0)
    svc = _service([stored])
    disagreeing = {stored["external_id"]: {
        "bullets": ["Rewritten."], "sentiment": "bullish", "confidence": 99,
        "related_tickers": ["ZZZ"], "scope": "market",
    }}
    with caplog.at_level(logging.ERROR, logger=ncs.__name__):
        svc._build_and_cache_rows(MARKET_SCOPE, [_raw_for(stored)], 10, None,
                                  "a future writer", enrichments=disagreeing)

    [row] = svc.supabase.scope_rows(MARKET_SCOPE)
    for col in ("ai_model", "ai_processed", "summary_bullets", "sentiment",
                "related_tickers", "cached_at"):
        assert row[col] == stored[col], col
    merges = [p for p, kw in svc.supabase.upserts if not kw.get("ignore_duplicates")]
    assert merges and not set(merges[-1][0]) & {
        "ai_model", "ai_processed", "summary_bullets", "sentiment", "sentiment_confidence",
        "related_tickers", "cached_at",
    }, "the merge carried AI columns"
    assert any(kw.get("ignore_duplicates") for _, kw in svc.supabase.upserts)
    assert "forced create-only" in caplog.text


def test_cancelling_a_cold_fetch_cancels_its_model_calls(labels):
    svc = _service()
    _stub_raw(svc, [_raw(i, f"Story {i}") for i in range(30)])
    cancelled = []

    class _Hanging(_Model):
        async def generate_json(self, **kwargs):
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.append(True)

    svc.gemini = _Hanging(lambda t: "market")

    async def _run():
        task = asyncio.create_task(svc.get_market_news(limit=50, offset=0))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for _ in range(3):
            await asyncio.sleep(0)
        # Snapshot INSIDE the loop: `asyncio.run` cancels leftovers on teardown, which
        # would make a leak look like a clean cancel.
        return task, len(cancelled)

    task, cancelled_in_loop = asyncio.run(_run())
    assert task.cancelled()
    assert cancelled_in_loop == -(-30 // NewsCacheService._MARKET_INGEST_BATCH_COLD), \
        "a paid call was left running unowned"
    assert svc.supabase.upserts == []


def test_the_pre_warmer_ingests_through_the_refresh_not_the_cold_path(labels):
    svc = _service()
    _stub_raw(svc, [_raw(i, "Aramco plant fire" if i % 5 == 0 else f"Stocks take {i}")
                    for i in range(30)])
    model = _Model(_by_keyword)
    svc.gemini = model

    async def _cold(limit):
        raise AssertionError("the pre-warmer led a cold miss under the user budget")

    svc._fetch_market_news = _cold
    asyncio.run(svc.pre_warm_popular_tickers(top_n=5))

    size = NewsCacheService._MARKET_INGEST_BATCH_REFRESH
    assert sorted(len(c) for c in model.calls) == sorted(
        min(size, 30 - s) for s in range(0, 30, size)
    ), "refresh-sized batches"
    rows = svc.supabase.scope_rows(MARKET_SCOPE)
    assert len(rows) == 30 and all(r["ai_model"] and "|scope=" in r["ai_model"] for r in rows)


# ── 13. Review round 7 — the fixtures' timestamps round-trip exactly ─────────

def test_the_cold_read_back_keeps_its_order_on_the_us_spring_forward_night(labels,
                                                                           monkeypatch):
    """Both FMP fixtures used to write a NAIVE UTC clock, which the service reads as New
    York time (`to_utc_instant`). On the spring-forward night the 02:xx ET hour does not
    exist: a naive 02:59 became 07:59Z while 03:00 became 07:00Z, so the merge that renews
    a row moved it across its neighbours and page 0 came back out of order — a red suite
    on correct code (collected 2026-03-08 from 03:01Z). With the module clock frozen
    there, every stamp must survive the write and the read-back exactly: the cached rows
    renewed through `_raw_for`, and the new ones created through `_raw`."""
    import sys

    monkeypatch.setattr(sys.modules[__name__], "NOW",
                        datetime(2026, 3, 8, 3, 5, tzinfo=timezone.utc))
    stored = [_market(i) for i in range(0, 40, 2)]                 # even minutes, cached
    for r in stored:
        r["expires_at"] = (NOW - timedelta(minutes=1)).isoformat()    # expired → cold
    svc = _service(stored)
    _stub_raw(svc, [_raw_for(r) for r in stored]
              + [_raw(i, f"Stocks take {i}") for i in range(1, 40, 2)])   # odd: new
    svc.gemini = _Model(lambda t: "market")
    env = asyncio.run(svc.get_market_news(limit=50, offset=0))

    rows = {r["external_id"]: r for r in svc.supabase.scope_rows(MARKET_SCOPE)}
    assert len(rows) == 40
    for r in stored:
        assert rows[r["external_id"]]["published_at"] == r["published_at"], r["headline"]
    for i in range(1, 40, 2):
        assert rows[f"https://g/{i}"]["published_at"] == \
            (NOW - timedelta(minutes=i)).isoformat(), i
    assert [a["headline"] for a in env["articles"]] == [
        _market(i)["headline"] if i % 2 == 0 else f"Stocks take {i}" for i in range(40)
    ], "page 0 is out of order"
