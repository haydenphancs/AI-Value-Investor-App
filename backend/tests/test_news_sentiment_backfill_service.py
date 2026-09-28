"""The 90-day news-sentiment backfill (app/services/news_sentiment_backfill_service.py).

Hermetic: Supabase, FMP, the daily budget and the model are all in-memory fakes. Each test
pins one of the rails in the module docstring — most of them a failure that would otherwise
leave a permanent hole, double-count an article, or bill a model call twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

import pytest

import app.services.news_sentiment_backfill_service as bf
import app.services.news_sentiment_trend_service as trend
from app.config import settings
from app.integrations.fmp import EmptyAfterFailure, FMPRateLimitException
from app.services.chat_budget_service import ChatBudgetUnavailable
from app.services.news_cache_service import article_external_id
from app.services.news_sentiment_backfill_service import (
    Claim,
    NewsSentimentBackfillService,
    map_fmp_rows,
    merge_coverage,
    next_nightly_run,
    parse_labels,
    plan_windows,
    route_for,
)
from app.services.news_sentiment_trend_service import article_key, history_status_for

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)   # Sun 11:00 ET
# The prompt's own instructions show the fence as "<<<END_ARTICLE i>>>" (a letter); only a
# numbered one is an article.
_ARTICLE_END = re.compile(r"<<<END_ARTICLE \d+>>>")


def _n_articles(prompt: str) -> int:
    return len(_ARTICLE_END.findall(prompt))
TODAY = date(2026, 9, 27)
HORIZON = TODAY - timedelta(days=89)                      # 2026-06-30


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(bf, "FMP_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_DAYS", 90)
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_DAILY_CALLS", 1500)
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_FLEX", True)
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "gemini-2.5-flash-lite")


# ── fakes ───────────────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table):
        self.sb, self.table, self.filters, self.op, self.payload = sb, table, {}, "select", None

    def select(self, *_a):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[col] = list(vals)
        return self

    def upsert(self, payload, **kwargs):
        self.op, self.payload, self.kwargs = "upsert", payload, kwargs
        return self

    def execute(self):
        if self.op == "upsert":
            self.sb.upserts.append((self.payload, self.kwargs))
            new = [r for r in self.payload if r["article_key"] not in self.sb.logged]
            self.sb.logged.update(r["article_key"] for r in self.payload)
            return _Res(new)
        wanted = self.filters.get("article_key", [])
        self.sb.dedupe_queries += 1
        return _Res([{"article_key": k} for k in wanted if k in self.sb.logged])


class _RpcCall:
    def __init__(self, sb, name, params):
        self.sb, self.name, self.params = sb, name, params

    def execute(self):
        self.sb.rpcs.append((self.name, self.params))
        if self.name in self.sb.rpc_errors:
            raise self.sb.rpc_errors[self.name]
        handler = self.sb.rpc_results.get(self.name)
        return _Res(handler(self.params) if callable(handler) else handler)


class _FakeSupabase:
    def __init__(self, logged=()):
        self.logged = set(logged)
        self.upserts = []
        self.rpcs = []
        self.dedupe_queries = 0
        self.rpc_errors = {}
        self.rpc_results = {bf.RENEW_RPC: True, bf.FINISH_RPC: True, bf.DISCOVER_RPC: 0}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        return _RpcCall(self, name, params)

    def calls(self, name):
        return [p for n, p in self.rpcs if n == name]


def _article(day: date, n: int, *, hour=10, symbol="ORCL"):
    return {
        "title": f"{symbol} story {day} #{n}",
        "text": "body",
        "url": f"https://x/{symbol}/{day}/{n}",
        "publishedDate": f"{day.isoformat()} {hour:02d}:00:00",   # FMP: naive New York time
        "symbol": symbol,
    }


class _FakeFMP:
    """Serves articles by ET day; honours from/to and 250-row pages like FMP."""

    def __init__(self, per_day=None, *, fail_on=None, rate_limit_on=None, page_size=250):
        self.per_day = per_day or {}
        self.fail_on, self.rate_limit_on = fail_on, rate_limit_on
        self.calls = []
        self.page_size = page_size

    async def _serve(self, feed, ticker, limit, page, from_date, to_date):
        self.calls.append((feed, ticker, from_date, to_date, page))
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if self.rate_limit_on and start <= self.rate_limit_on <= end:
            raise FMPRateLimitException("429")
        if self.fail_on and start <= self.fail_on <= end:
            return EmptyAfterFailure(reason="HTTP 500")
        rows = []
        d = end
        while d >= start:
            rows.extend(self.per_day.get(d, []))
            d -= timedelta(days=1)
        return rows[page * self.page_size:(page + 1) * self.page_size]

    async def get_stock_news(self, ticker=None, limit=10, from_date=None, to_date=None, page=0):
        return await self._serve("stock", ticker, limit, page, from_date, to_date)

    async def get_crypto_news(self, ticker=None, limit=10, page=0, from_date=None, to_date=None):
        return await self._serve("crypto", ticker, limit, page, from_date, to_date)


class _FakeBudget:
    def __init__(self, result=1, exc=None):
        self.result, self.exc = result, exc
        self.claims, self.refunds = 0, 0

    def try_claim_turn(self, bucket, limit):
        assert bucket == bf.BUDGET_BUCKET
        self.claims += 1
        if self.exc:
            raise self.exc
        return self.result

    def refund_turn(self, bucket):
        self.refunds += 1


class _Labeller:
    """Labels every article bullish unless told otherwise; records each call."""

    def __init__(self, *, answers=None, exc_first=None, label="bullish"):
        self.calls = []
        self.answers = list(answers or [])
        self.exc_first = exc_first
        self.label = label

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc_first is not None:
            exc, self.exc_first = self.exc_first, None
            raise exc
        n = _n_articles(kwargs["prompt"])
        if self.answers:
            answer = self.answers.pop(0)
            return {"text": answer(n) if callable(answer) else answer}
        return {"text": json.dumps([{"index": i, "sentiment": self.label, "confidence": 70} for i in range(n)])}


def _svc(fmp, *, sb=None, budget=None, labeller=None):
    return NewsSentimentBackfillService(
        supabase=sb or _FakeSupabase(), fmp=fmp, budget=budget or _FakeBudget(),
        labeller=labeller or _Labeller(),
    )


def _claim(scope="ORCL", cf=None, ct=None, attempts=1):
    return Claim(scope=scope, token="tok-1", covered_from=cf, covered_to=ct, attempts=attempts)


# ── planning ────────────────────────────────────────────────────────────────────


def test_a_fresh_scope_plans_the_whole_horizon_newest_first():
    windows = plan_windows(TODAY, None, None, HORIZON)
    assert windows[0] == (TODAY - timedelta(days=6), TODAY)
    assert windows[-1][0] == HORIZON
    days = sum((we - ws).days + 1 for ws, we in windows)
    assert days == 90
    for (a_start, _), (_, b_end) in zip(windows, windows[1:]):
        assert b_end == a_start - timedelta(days=1), "windows are contiguous"


def test_a_covered_scope_rescans_recent_days_then_fills_the_old_end():
    windows = plan_windows(TODAY, date(2026, 8, 1), date(2026, 9, 26), HORIZON)
    assert windows[0] == (date(2026, 9, 24), TODAY), "re-scan the last 3 covered days through today"
    assert windows[1][1] == date(2026, 7, 31) and windows[-1][0] == HORIZON


def test_a_fully_covered_scope_is_only_the_recent_rescan():
    assert plan_windows(TODAY, HORIZON, TODAY, HORIZON) == [(date(2026, 9, 25), TODAY)]


def test_nonsense_coverage_replans_everything():
    assert plan_windows(TODAY, date(2026, 9, 20), date(2026, 9, 1), HORIZON)[0] == (date(2026, 9, 21), TODAY)
    assert plan_windows(TODAY, date(2026, 1, 1), date(2026, 2, 1), HORIZON)[-1][0] == HORIZON


def test_merge_coverage_only_records_contiguous_processed_ranges():
    d = date.fromisoformat
    # A fresh run: the first window becomes the coverage, the next extends it.
    cov, pend = merge_coverage((None, None), (d("2026-09-21"), d("2026-09-27")), None)
    assert cov == (d("2026-09-21"), d("2026-09-27")) and pend is None
    cov, pend = merge_coverage(cov, (d("2026-09-14"), d("2026-09-20")), pend)
    assert cov == (d("2026-09-14"), d("2026-09-27"))
    # A run over a dormant scope: the new block is kept aside until it touches the old one.
    cov, pend = merge_coverage((d("2026-08-01"), d("2026-08-31")), (d("2026-09-21"), d("2026-09-27")), None)
    assert cov == (d("2026-08-01"), d("2026-08-31")) and pend == (d("2026-09-21"), d("2026-09-27"))
    cov, pend = merge_coverage(cov, (d("2026-09-01"), d("2026-09-20")), pend)
    assert cov == (d("2026-08-01"), d("2026-09-27")) and pend is None


# ── mapping, labels, routing ────────────────────────────────────────────────────


def test_rows_are_kept_by_their_own_et_day_with_the_live_identity():
    rows = [
        _article(date(2026, 9, 20), 1, hour=23),          # 23:00 ET Sep 20 → Sep 20
        {**_article(date(2026, 9, 21), 2), "publishedDate": "2026-09-22T03:30:00Z"},  # 23:30 ET Sep 21
        _article(date(2026, 9, 19), 3),                    # outside the window
        {"title": "undated", "url": "u", "publishedDate": None},
        "junk",
    ]
    out = map_fmp_rows(rows, window=(date(2026, 9, 20), date(2026, 9, 21)))
    assert [r["title"] for r in out] == ["ORCL story 2026-09-20 #1", "ORCL story 2026-09-21 #2"]
    assert out[0]["external_id"] == article_external_id(rows[0], 0)


def test_parse_labels_refuses_bad_answers_and_never_defaults():
    assert parse_labels("not json", 2) is None
    assert parse_labels(json.dumps([{"sentiment": "bullish"}]), 2) is None
    assert parse_labels(json.dumps({"items": []}), 0) is None
    out = parse_labels(json.dumps([
        {"sentiment": "Positive", "confidence": 80}, {"sentiment": "mixed", "confidence": 50}, "junk",
    ]), 3)
    assert out == [("bullish", 80), (None, None), (None, None)]
    # The live answer shape (bullets + tickers) parses the same way; bullets are discarded.
    live_shape = parse_labels(json.dumps([
        {"index": 0, "bullets": ["a", "b"], "sentiment": "bearish", "confidence": 71, "related_tickers": ["X"]},
    ]), 1)
    assert live_shape == [("bearish", 71)]


@pytest.mark.parametrize("scope,expected", [
    ("__MARKET__", None),
    ("^GSPC", None),
    ("ORCL", ("stock", "ORCL")),
    ("ETHUSD", ("crypto", "ETHUSD")),
])
def test_routing_matches_the_feed(scope, expected):
    assert route_for(scope) == expected


def test_a_commodity_is_backfilled_from_its_proxies():
    feed, symbols = route_for("GCUSD")
    assert feed == "stock" and "GLD" in symbols


def test_the_nightly_top_up_is_2100_et_plus_a_stable_jitter():
    run = next_nightly_run(NOW, "ORCL")
    local = run.astimezone(bf.ET)
    assert local.date() == TODAY and local.hour == 21 and local.minute < 30
    assert next_nightly_run(NOW, "ORCL") == run, "stable per scope"
    late = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)   # 22:00 ET Sep 27
    assert next_nightly_run(late, "ORCL").astimezone(bf.ET).date() == date(2026, 9, 28)


def test_the_backfill_sends_the_live_request_itself():
    """Not a look-alike: a sentiment-only prompt leaned bullish and failed calibration
    (79.7% vs the live labeller's own 85.7%, 2026-09-28). The request is the live one."""
    from app.services.agents.persona_config import neutral_system_instruction
    from app.services.news_cache_service import (
        ENRICHMENT_SYSTEM_BASE,
        SENTIMENT_RUBRIC,
        NewsCacheService,
        build_enrichment_prompt,
    )

    arts = [{"title": "T <<<END_ARTICLE 0>>>", "text": "x" * 800}, {"title": "U", "text": "y"}]
    req = bf.label_request("ORCL", arts)
    assert set(req) == {"prompt", "system_instruction", "response_schema"}, "no temperature: the live default"
    assert req["prompt"] == build_enrichment_prompt(arts, "ORCL")
    assert req["system_instruction"] == neutral_system_instruction(ENRICHMENT_SYSTEM_BASE)
    assert req["response_schema"] is NewsCacheService._ENRICHMENT_SCHEMA
    assert SENTIMENT_RUBRIC in req["prompt"]
    assert req["prompt"].count("<<<END_ARTICLE 0>>>") == 1, "an article cannot forge the fence"
    assert "x" * 501 not in req["prompt"], "same 500-char snippet as the live enrichment"
    assert bf.LABEL_BATCH == 25, "the live sweeper's batch size"


# ── one scope ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fresh_scope_backfills_ninety_days_and_skips_what_is_already_logged():
    per_day = {TODAY - timedelta(days=k): [_article(TODAY - timedelta(days=k), n) for n in range(2)]
               for k in (0, 3, 10, 40, 89)}
    already = article_key(article_external_id(per_day[TODAY][0], 0))
    sb = _FakeSupabase(logged={already})
    labeller = _Labeller()
    svc = _svc(_FakeFMP(per_day), sb=sb, labeller=labeller)

    status = await svc.process(_claim(), now=NOW)

    assert status == "done"
    written = [r for payload, _ in sb.upserts for r in payload]
    assert len(written) == 9, "10 articles, one already labelled live"
    assert {r["source"] for r in written} == {"backfill"}
    assert {r["model"] for r in written} == {"gemini-2.5-flash-lite"}
    assert already not in {r["article_key"] for r in written}
    assert min(r["et_day"] for r in written) == HORIZON.isoformat()
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_status"] == "done"
    assert (finish["p_covered_from"], finish["p_covered_to"]) == (HORIZON.isoformat(), TODAY.isoformat())
    assert finish["p_labels"] == 9
    assert len(sb.calls(bf.RENEW_RPC)) == 13, "a checkpoint after every window"
    # Flex first, and the shared response cache is bypassed.
    assert labeller.calls[0]["service_tier"] == "flex" and labeller.calls[0]["cache"] is False
    assert labeller.calls[0]["usage_tag"] == "sentiment_backfill"
    assert "temperature" not in labeller.calls[0], "the live labeller's own default temperature"


@pytest.mark.asyncio
async def test_a_failed_fetch_never_advances_coverage_past_it():
    per_day = {TODAY: [_article(TODAY, 0)]}
    fmp = _FakeFMP(per_day, fail_on=TODAY - timedelta(days=20))
    sb = _FakeSupabase()
    status = await _svc(fmp, sb=sb).process(_claim(attempts=2), now=NOW)
    assert status == "failed"
    [finish] = sb.calls(bf.FINISH_RPC)
    # Weeks [21..27] and [14..20] completed; the week containing day 20-back did not.
    assert finish["p_covered_to"] == TODAY.isoformat()
    assert finish["p_covered_from"] == (TODAY - timedelta(days=13)).isoformat()
    assert "fetch failed" in finish["p_error"]
    next_run = datetime.fromisoformat(finish["p_next_run_at"])
    assert next_run == NOW + timedelta(seconds=bf.FAILURE_BACKOFF_SECONDS * 2)


@pytest.mark.asyncio
async def test_a_rate_limit_defers_without_a_failed_attempt():
    sb = _FakeSupabase()
    status = await _svc(_FakeFMP({}, rate_limit_on=TODAY), sb=sb).process(_claim(), now=NOW)
    assert status == "queued"
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_status"] == "queued"
    assert datetime.fromisoformat(finish["p_next_run_at"]) == NOW + timedelta(seconds=bf.DEFER_RATE_LIMIT_SECONDS)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,expected_wait", [
    (_FakeBudget(result=-1), "midnight"),
    (_FakeBudget(exc=ChatBudgetUnavailable("rpc down")), "15min"),
])
async def test_the_daily_budget_fails_closed(budget, expected_wait):
    sb = _FakeSupabase()
    labeller = _Labeller()
    svc = _svc(_FakeFMP({TODAY: [_article(TODAY, 0)]}), sb=sb, budget=budget, labeller=labeller)
    assert await svc.process(_claim(), now=NOW) == "queued"
    assert labeller.calls == [], "no model call without a budget unit"
    [finish] = sb.calls(bf.FINISH_RPC)
    next_run = datetime.fromisoformat(finish["p_next_run_at"])
    if expected_wait == "midnight":
        assert next_run.astimezone(bf.ET).date() == TODAY + timedelta(days=1)
    else:
        assert next_run == NOW + timedelta(seconds=bf.DEFER_RATE_LIMIT_SECONDS)


@pytest.mark.asyncio
async def test_a_lost_lease_stops_without_finishing():
    sb = _FakeSupabase()
    sb.rpc_results[bf.RENEW_RPC] = False
    status = await _svc(_FakeFMP({TODAY: [_article(TODAY, 0)]}), sb=sb).process(_claim(), now=NOW)
    assert status == "lost"
    assert sb.calls(bf.FINISH_RPC) == [], "the new holder's progress is not overwritten"
    assert len(sb.calls(bf.RENEW_RPC)) == 1


@pytest.mark.asyncio
async def test_a_wrong_count_answer_is_split_once_then_left_unlabelled():
    arts = {TODAY: [_article(TODAY, n) for n in range(4)]}
    sb = _FakeSupabase()
    bad = lambda n: json.dumps([{"index": 0, "sentiment": "bullish", "confidence": 1}])  # always 1
    labeller = _Labeller(answers=[bad, bad, bad])
    await _svc(_FakeFMP(arts), sb=sb, labeller=labeller).process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert len(labeller.calls) == 3, "one call, then the two halves — no deeper recursion"
    assert sb.upserts == [], "nothing is ever defaulted to neutral"


@pytest.mark.asyncio
async def test_unknown_labels_are_dropped_not_neutral():
    arts = {TODAY: [_article(TODAY, n) for n in range(2)]}
    sb = _FakeSupabase()
    answer = lambda n: json.dumps([{"index": 0, "sentiment": "mixed", "confidence": 5},
                                   {"index": 1, "sentiment": "bearish", "confidence": 90}])
    await _svc(_FakeFMP(arts), sb=sb, labeller=_Labeller(answers=[answer])).process(
        _claim(ct=TODAY, cf=HORIZON), now=NOW)
    written = [r for payload, _ in sb.upserts for r in payload]
    assert [r["sentiment"] for r in written] == ["bearish"]


@pytest.mark.asyncio
async def test_a_busy_flex_tier_is_retried_once_on_standard():
    from app.integrations.gemini import GeminiQuotaError

    arts = {TODAY: [_article(TODAY, 0)]}
    labeller = _Labeller(exc_first=GeminiQuotaError("429 flex busy"))
    sb = _FakeSupabase()
    await _svc(_FakeFMP(arts), sb=sb, labeller=labeller).process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert [c["service_tier"] for c in labeller.calls] == ["flex", None]
    assert len([r for p, _ in sb.upserts for r in p]) == 1


@pytest.mark.asyncio
async def test_flex_is_never_used_on_another_provider(monkeypatch):
    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "deepseek-flash")
    labeller = _Labeller()
    sb = _FakeSupabase()
    await _svc(_FakeFMP({TODAY: [_article(TODAY, 0)]}), sb=sb, labeller=labeller).process(
        _claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert labeller.calls[0]["service_tier"] is None
    assert {r["model"] for p, _ in sb.upserts for r in p} == {"deepseek-flash"}


@pytest.mark.asyncio
async def test_a_model_failure_refunds_its_budget_unit_and_defers():
    from app.integrations.gemini import GeminiQuotaError

    class _AlwaysBusy(_Labeller):
        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            raise GeminiQuotaError("429")

    budget = _FakeBudget()
    sb = _FakeSupabase()
    status = await _svc(_FakeFMP({TODAY: [_article(TODAY, 0)]}), sb=sb, budget=budget,
                        labeller=_AlwaysBusy()).process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert status == "queued" and budget.refunds == 1


@pytest.mark.asyncio
async def test_a_week_over_the_page_cap_is_fetched_day_by_day(monkeypatch, caplog):
    monkeypatch.setattr(bf, "MAX_PAGES_PER_WINDOW", 1)
    monkeypatch.setattr(bf, "MAX_PAGES_PER_DAY", 1)
    monkeypatch.setattr(bf, "PAGE_SIZE", 3)
    fmp = _FakeFMP({TODAY: [_article(TODAY, n) for n in range(5)],
                    TODAY - timedelta(days=1): [_article(TODAY - timedelta(days=1), n) for n in range(2)]},
                   page_size=3)
    sb = _FakeSupabase()
    with caplog.at_level(logging.WARNING):
        await _svc(fmp, sb=sb).process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    per_day_calls = [c for c in fmp.calls if c[2] == c[3]]
    assert per_day_calls, "the capped week was re-fetched one day at a time"
    assert f"{TODAY} exceeded 1 pages" in caplog.text
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_status"] == "done"


@pytest.mark.asyncio
async def test_an_unsupported_scope_costs_nothing():
    fmp = _FakeFMP({})
    sb = _FakeSupabase()
    assert await _svc(fmp, sb=sb).process(_claim(scope="^GSPC"), now=NOW) == "unsupported"
    assert fmp.calls == []
    assert sb.calls(bf.FINISH_RPC)[0]["p_status"] == "unsupported"


@pytest.mark.asyncio
async def test_the_same_article_in_two_windows_is_labelled_once():
    art = _article(TODAY - timedelta(days=7), 0)       # sits in the ±1-day pad of two windows
    labeller = _Labeller()
    sb = _FakeSupabase()
    await _svc(_FakeFMP({TODAY - timedelta(days=7): [art]}), sb=sb, labeller=labeller).process(
        _claim(), now=NOW)
    assert sum(_n_articles(c["prompt"]) for c in labeller.calls) == 1


# ── the drain + the nudge ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tick_discovers_then_drains_until_nothing_is_due():
    sb = _FakeSupabase()
    queue = [{"scope": "ORCL", "covered_from": HORIZON.isoformat(), "covered_to": TODAY.isoformat(), "attempts": 1},
             {"scope": "CRM", "covered_from": HORIZON.isoformat(), "covered_to": TODAY.isoformat(), "attempts": 1}]
    sb.rpc_results[bf.CLAIM_RPC] = lambda params: [queue.pop(0)] if queue else []
    sb.rpc_results[bf.DISCOVER_RPC] = 2
    processed = await _svc(_FakeFMP({}), sb=sb).run_one_tick(now=NOW, workers=2)
    assert processed == 2
    assert sb.calls(bf.DISCOVER_RPC) == [{}]
    claims = sb.calls(bf.CLAIM_RPC)
    assert all(c["p_limit"] == 1 and c["p_lease_seconds"] == bf.LEASE_SECONDS for c in claims)
    assert len({c["p_token"] for c in claims}) == len(claims), "a fresh fencing token per claim"


@pytest.mark.asyncio
async def test_a_tick_without_migration_181_logs_and_idles(caplog):
    sb = _FakeSupabase()
    sb.rpc_errors[bf.DISCOVER_RPC] = RuntimeError("PGRST202 function not found")
    with caplog.at_level(logging.WARNING):
        assert await _svc(_FakeFMP({}), sb=sb).run_one_tick(now=NOW) == 0
    assert "discovery failed" in caplog.text
    assert sb.calls(bf.CLAIM_RPC) == []


@pytest.mark.asyncio
async def test_the_nudge_is_off_while_the_backfill_is_off(monkeypatch):
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", False)
    sb = _FakeSupabase()
    await bf.nudge_backfill(sb, ["orcl"])
    assert sb.rpcs == []


@pytest.mark.asyncio
async def test_the_nudge_queues_and_wakes(monkeypatch):
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", True)
    monkeypatch.setattr(bf, "_wake", None)
    sb = _FakeSupabase()
    sb.rpc_results[bf.ENQUEUE_RPC] = 1
    await bf.nudge_backfill(sb, [" orcl ", "ORCL", "", None, 5])
    assert sb.calls(bf.ENQUEUE_RPC) == [{"p_scopes": ["ORCL"]}], "never the string 'NONE'"
    assert bf._wake_event().is_set()


@pytest.mark.asyncio
async def test_the_nudge_never_fails_the_add(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", True)
    sb = _FakeSupabase()
    sb.rpc_errors[bf.ENQUEUE_RPC] = RuntimeError("no rpc in this fake")
    with caplog.at_level(logging.WARNING):
        await bf.nudge_backfill(sb, ["ORCL"])
    assert "enqueue failed for ORCL" in caplog.text


# ── history status for the app ─────────────────────────────────────────────────


class _StatusClient:
    def __init__(self, row=None, exc=None):
        self.row, self.exc = row, exc

    def table(self, name):
        assert name == "news_sentiment_backfill"
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.exc:
            raise self.exc
        return _Res([self.row] if self.row else [])


@pytest.mark.parametrize("row,expected", [
    (None, None),
    ({"status": "queued"}, "building"),
    ({"status": "running", "covered_from": "2026-09-20", "covered_to": "2026-09-27"}, "building"),
    ({"status": "running", "covered_from": HORIZON.isoformat(), "covered_to": "2026-09-25"}, "ready"),
    ({"status": "done"}, "ready"),
    ({"status": "failed"}, None),
    ({"status": "unsupported"}, None),
    ({"et_day": "2026-09-20"}, None),     # not a backfill row at all (a shared test fake)
])
def test_history_status(monkeypatch, row, expected):
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", True)
    assert history_status_for(_StatusClient(row), "ORCL", TODAY) == expected


def test_history_status_is_none_when_off_for_the_market_or_on_error(monkeypatch):
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", False)
    assert history_status_for(_StatusClient({"status": "queued"}), "ORCL", TODAY) is None
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", True)
    assert history_status_for(_StatusClient({"status": "queued"}), "__MARKET__", TODAY) is None
    assert history_status_for(_StatusClient(exc=RuntimeError("relation missing")), "ORCL", TODAY) is None


# ── the live writer survives a deploy ahead of migration 181 ────────────────────


class _NoModelColumn:
    def __init__(self):
        self.payloads = []

    def table(self, name):
        return self

    def upsert(self, payload, **kwargs):
        self._payload = payload
        return self

    def execute(self):
        self.payloads.append(self._payload)
        if any("model" in r for r in self._payload):
            raise RuntimeError("{'code': 'PGRST204', 'message': \"Could not find the 'model' column\"}")
        return _Res(self._payload)


def test_labels_are_written_without_model_until_181_exists(monkeypatch, caplog):
    monkeypatch.setattr(trend, "_model_column_missing", False)
    sb = _NoModelColumn()
    rows = trend.build_log_rows("ORCL", [{"external_id": "a", "sentiment": "bullish",
                                          "published_at": NOW.isoformat()}], now=NOW, model="m")
    with caplog.at_level(logging.WARNING):
        assert trend.upsert_log_rows(sb, rows) == 1
        assert trend.upsert_log_rows(sb, rows) == 1
    assert len(sb.payloads) == 3, "one failed try, one retry, then straight without the column"
    assert "migration 181 not applied" in caplog.text
    monkeypatch.setattr(trend, "_model_column_missing", False)


def test_the_row_builder_rejects_unknown_sources():
    with pytest.raises(ValueError):
        trend.build_log_rows("ORCL", [], now=NOW, source="guess")


# ── review fixes (2026-09-27 deep-check) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stale_coverage_is_replaced_not_kept_forever():
    """A scope whose coverage ends before the horizon (dormant 90+ days) used to re-fetch all
    90 days every night: the new newest-first block never touched the stale range, so it was
    dropped and the stale range written back."""
    sb = _FakeSupabase()
    fmp = _FakeFMP({TODAY: [_article(TODAY, 0)]})
    status = await _svc(fmp, sb=sb).process(
        _claim(cf=date(2026, 4, 20), ct=date(2026, 6, 19)), now=NOW)
    assert status == "done"
    [finish] = sb.calls(bf.FINISH_RPC)
    assert (finish["p_covered_from"], finish["p_covered_to"]) == (HORIZON.isoformat(), TODAY.isoformat())
    # The next night is then only the recent re-scan, not the whole horizon again.
    assert plan_windows(TODAY, HORIZON, TODAY, HORIZON) == [(date(2026, 9, 25), TODAY)]


@pytest.mark.asyncio
async def test_a_deferred_stale_run_leaves_the_stored_range_to_coalesce():
    sb = _FakeSupabase()
    fmp = _FakeFMP({}, rate_limit_on=TODAY)
    status = await _svc(fmp, sb=sb).process(_claim(cf=date(2026, 4, 20), ct=date(2026, 6, 19)), now=NOW)
    assert status == "queued"
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_covered_from"] is None and finish["p_covered_to"] is None


@pytest.mark.asyncio
async def test_the_day_split_keeps_the_one_day_pad(monkeypatch):
    monkeypatch.setattr(bf, "MAX_PAGES_PER_WINDOW", 1)
    monkeypatch.setattr(bf, "PAGE_SIZE", 3)
    edge = TODAY - timedelta(days=3)       # the day BEFORE the recent window (Sep 25..27)
    fmp = _FakeFMP({TODAY: [_article(TODAY, n) for n in range(5)]}, page_size=3)
    await _svc(fmp, sb=_FakeSupabase()).process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    day_calls = sorted({c[2] for c in fmp.calls if c[2] == c[3]})
    assert day_calls[0] == edge.isoformat(), "padded on the old side"
    assert day_calls[-1] == (TODAY + timedelta(days=1)).isoformat(), "padded on the new side"


@pytest.mark.asyncio
async def test_shutdown_hands_back_without_erasing_renewed_coverage_and_refunds():
    started = asyncio.Event()

    class _Hangs(_Labeller):
        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            started.set()
            await asyncio.Event().wait()

    sb = _FakeSupabase()
    queue = [{"scope": "ORCL", "covered_from": HORIZON.isoformat(), "covered_to": TODAY.isoformat(), "attempts": 1}]
    sb.rpc_results[bf.CLAIM_RPC] = lambda params: [queue.pop(0)] if queue else []
    budget = _FakeBudget()
    svc = _svc(_FakeFMP({TODAY: [_article(TODAY, 0)]}), sb=sb, budget=budget, labeller=_Hangs())
    task = asyncio.create_task(svc.run_one_tick(now=NOW, workers=1))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_status"] == "queued"
    assert finish["p_covered_from"] is None and finish["p_covered_to"] is None, \
        "NULLs: COALESCE keeps what renew already saved"
    assert budget.claims == 1 and budget.refunds == 1, "the unanswered call's unit is given back"


@pytest.mark.asyncio
async def test_one_scope_blowing_up_does_not_escape_the_tick(monkeypatch, caplog):
    real_route = bf.route_for

    def _route(scope):
        if scope == "BOOM":
            raise RuntimeError("unexpected")
        return real_route(scope)

    monkeypatch.setattr(bf, "route_for", _route)
    sb = _FakeSupabase()
    queue = [{"scope": "BOOM", "attempts": 1},
             {"scope": "ORCL", "covered_from": HORIZON.isoformat(), "covered_to": TODAY.isoformat(), "attempts": 1}]
    sb.rpc_results[bf.CLAIM_RPC] = lambda params: [queue.pop(0)] if queue else []
    with caplog.at_level(logging.ERROR):
        processed = await _svc(_FakeFMP({}), sb=sb).run_one_tick(now=NOW, workers=1)
    assert processed == 1, "the worker went on to the next scope"
    assert "worker error for BOOM" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_finish_is_logged_not_raised(caplog):
    sb = _FakeSupabase()
    sb.rpc_errors[bf.FINISH_RPC] = RuntimeError("connection reset")
    with caplog.at_level(logging.WARNING):
        status = await _svc(_FakeFMP({}), sb=sb).process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert status == "done"
    assert "finish(done) failed for ORCL" in caplog.text


def _tier_refusal():
    from google.genai import errors as genai_errors

    return genai_errors.ClientError(400, {"error": {
        "code": 400, "status": "INVALID_ARGUMENT",
        "message": "service_tier FLEX is not supported for this model"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("blip", [
    RuntimeError("Server disconnected"),
    ConnectionResetError("[Errno 104] Connection reset by peer"),
    ValueError("400 INVALID_ARGUMENT: service tier not supported"),   # not a genai ClientError
])
async def test_a_flex_blip_falls_back_once_but_keeps_flex(blip):
    arts = {TODAY: [_article(TODAY, n) for n in range(3)]}
    labeller = _Labeller(exc_first=blip)
    svc = _svc(_FakeFMP(arts), labeller=labeller)
    await svc.process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert [c["service_tier"] for c in labeller.calls] == ["flex", None]
    svc._supabase = _FakeSupabase()
    await svc.process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert labeller.calls[-1]["service_tier"] == "flex", "one blip must not double every later price"


@pytest.mark.asyncio
async def test_a_flex_refusal_that_is_not_busy_turns_flex_off_for_the_process():
    arts = {TODAY: [_article(TODAY, n) for n in range(3)]}
    labeller = _Labeller(exc_first=_tier_refusal())
    svc = _svc(_FakeFMP(arts), labeller=labeller)
    await svc.process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert [c["service_tier"] for c in labeller.calls] == ["flex", None]
    svc._supabase = _FakeSupabase()          # nothing logged yet: the next run labels again
    await svc.process(_claim(ct=TODAY, cf=HORIZON), now=NOW)
    assert len(labeller.calls) == 3
    assert labeller.calls[-1]["service_tier"] is None, "no second refusal per batch"


@pytest.mark.asyncio
async def test_a_flex_429_is_one_call_off_the_shared_breaker(monkeypatch, caplog):
    from app.integrations import gemini

    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0
    calls = []

    @gemini.async_retry(max_attempts=2, delay=0.0)
    async def _fake_generate(**kwargs):
        calls.append(kwargs.get("service_tier"))
        raise RuntimeError("429 RESOURCE_EXHAUSTED: flex capacity")

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        await _fake_generate(service_tier="flex")
    assert calls == ["flex"], "exactly one Flex attempt"
    assert gemini._quota_circuit._consecutive == 0, "the breaker chat and reports share is untouched"
    assert "giving up" not in caplog.text
    # The standard tier still gets the full ladder (and books its strikes).
    with pytest.raises(RuntimeError):
        await _fake_generate(service_tier=None)
    assert calls.count(None) > 1 and gemini._quota_circuit._consecutive > 0
    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0


async def _no_sleep(*_a, **_k):
    return None


def test_history_status_uses_the_workers_clamped_horizon(monkeypatch):
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_ENABLED", True)
    monkeypatch.setattr(settings, "SENTIMENT_BACKFILL_DAYS", 120)   # the worker clamps to 90
    row = {"status": "running", "covered_from": HORIZON.isoformat(), "covered_to": TODAY.isoformat()}
    assert history_status_for(_StatusClient(row), "ORCL", TODAY) == "ready"


def test_a_concurrent_batch_still_heals_after_another_set_the_flag(monkeypatch):
    """Batch B was built WITH `model` while the flag was False; batch A's error set the flag
    first. B's own error must still retry without the column, not drop its labels."""
    monkeypatch.setattr(trend, "_model_column_missing", False)

    class _RacingClient(_NoModelColumn):
        def execute(self):
            if any("model" in r for r in self._payload):
                trend._model_column_missing = True     # A got there first
            return super().execute()

    sb = _RacingClient()
    rows = trend.build_log_rows("ORCL", [{"external_id": "b", "sentiment": "bearish",
                                          "published_at": NOW.isoformat()}], now=NOW, model="m")
    assert trend.upsert_log_rows(sb, rows) == 1
    monkeypatch.setattr(trend, "_model_column_missing", False)


@pytest.mark.asyncio
async def test_a_window_the_model_could_not_label_is_not_recorded_as_covered():
    """An answer wrapped under the wrong key (or blocked, or cut off) left the window marked
    covered with no labels — a permanent hole under a 'done' status."""
    older = TODAY - timedelta(days=10)
    arts = {TODAY: [_article(TODAY, 0)], older: [_article(older, 0)]}
    bad = lambda n: json.dumps({"results": [{"index": 0, "sentiment": "bullish", "confidence": 9}]})
    good = lambda n: json.dumps([{"index": i, "sentiment": "bearish", "confidence": 70} for i in range(n)])
    # Newest window labels fine; the next one gets an unusable answer (and its split retry).
    labeller = _Labeller(answers=[good, bad, bad])
    sb = _FakeSupabase()
    status = await _svc(_FakeFMP(arts), sb=sb, labeller=labeller).process(_claim(attempts=1), now=NOW)
    assert status == "failed"
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_covered_to"] == TODAY.isoformat()
    assert finish["p_covered_from"] == (TODAY - timedelta(days=6)).isoformat(), \
        "coverage held at the last fully labelled window"
    assert "left unlabelled" in finish["p_error"]
    assert date.fromisoformat(finish["p_covered_from"]) > older

    # The retry re-labels only the gap's article (the rest are in the log already).
    sb2 = _FakeSupabase(logged=sb.logged)
    labeller2 = _Labeller()
    status = await _svc(_FakeFMP(arts), sb=sb2, labeller=labeller2).process(
        _claim(cf=date.fromisoformat(finish["p_covered_from"]), ct=TODAY, attempts=2), now=NOW)
    assert status == "done"
    assert sum(_n_articles(c["prompt"]) for c in labeller2.calls) == 1
    assert sb2.calls(bf.FINISH_RPC)[0]["p_covered_from"] == HORIZON.isoformat()


@pytest.mark.asyncio
async def test_at_the_attempt_cap_a_gap_is_accepted_not_retried_forever(caplog):
    arts = {TODAY: [_article(TODAY, 0)]}
    bad = lambda n: "not json"
    sb = _FakeSupabase()
    with caplog.at_level(logging.WARNING):
        status = await _svc(_FakeFMP(arts), sb=sb, labeller=_Labeller(answers=[bad])).process(
            _claim(attempts=bf.MAX_ATTEMPTS), now=NOW)
    assert status == "done"
    assert sb.calls(bf.FINISH_RPC)[0]["p_covered_from"] == HORIZON.isoformat()
    assert "accepting 1 unlabelled article(s)" in caplog.text


@pytest.mark.asyncio
async def test_a_moderation_refusal_is_a_gap_not_a_stalled_scope(monkeypatch):
    """One article a provider's moderation refuses used to _Fail the whole scope on the same
    batch every retry. Now it is an unusable answer: split, the rest labelled, gap rule."""
    from app.integrations.openai_compat import OpenAICompatContentRejected

    monkeypatch.setattr(settings, "NEWS_LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(settings, "NEWS_LLM_BASE_URL", "https://api.example.test")
    monkeypatch.setattr(settings, "NEWS_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "NEWS_LLM_MODEL", "qwen-flash")

    class _Moderates(_Labeller):
        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            if "FORBIDDEN" in kwargs["prompt"]:
                raise OpenAICompatContentRejected("400 data_inspection_failed")
            return await super().__call__(**{**kwargs})

    arts = {TODAY: [_article(TODAY, 0), {**_article(TODAY, 1), "title": "FORBIDDEN"}]}
    budget = _FakeBudget()
    sb = _FakeSupabase()
    labeller = _Moderates()
    labeller.calls = []
    status = await _svc(_FakeFMP(arts), sb=sb, budget=budget, labeller=labeller).process(
        _claim(attempts=bf.MAX_ATTEMPTS), now=NOW)
    assert status == "done", "at the cap the one refused article is an accepted gap"
    written = [r for p, _ in sb.upserts for r in p]
    assert len(written) == 1, "the other article of the batch was still labelled"
    assert budget.refunds == 2, "each refused call gives its unit back"


@pytest.mark.asyncio
async def test_at_the_cap_a_systematic_failure_is_never_accepted():
    """Every answer unusable (a provider wrapping under the wrong key): at the cap the run
    must still fail with nothing covered — never 'done' over 90 empty days."""
    arts = {TODAY - timedelta(days=k): [_article(TODAY - timedelta(days=k), n) for n in range(3)]
            for k in range(0, 40, 3)}
    bad = lambda n: json.dumps({"wrong": []})
    labeller = _Labeller(answers=[bad] * 200)
    sb = _FakeSupabase()
    status = await _svc(_FakeFMP(arts), sb=sb, labeller=labeller).process(
        _claim(attempts=bf.MAX_ATTEMPTS + 3), now=NOW)
    assert status == "failed"
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_covered_from"] is None and finish["p_covered_to"] is None
    assert "too many to accept at the cap" in finish["p_error"]
    assert all(p["p_covered_from"] is None for p in sb.calls(bf.RENEW_RPC)), \
        "no gap window was renewed as covered before the decision"


@pytest.mark.asyncio
async def test_a_rescan_miss_on_an_already_covered_day_does_not_fail_the_night():
    """An article accepted as a gap earlier sits on a covered day; the nightly re-scan of
    that day must not re-fail the scope (and run 4 failed retries) every night."""
    covered_day = TODAY - timedelta(days=2)
    arts = {covered_day: [{**_article(covered_day, 0), "title": "BLOCKED"}],
            TODAY: [_article(TODAY, 1)]}

    class _Blocks(_Labeller):
        async def __call__(self, **kwargs):
            self.calls.append(kwargs)
            if "BLOCKED" in kwargs["prompt"]:
                return {"text": ""}          # a blocked prompt: empty answer
            return await super().__call__(**kwargs)

    labeller = _Blocks()
    sb = _FakeSupabase()
    status = await _svc(_FakeFMP(arts), sb=sb, labeller=labeller).process(
        _claim(cf=HORIZON, ct=TODAY - timedelta(days=1), attempts=1), now=NOW)
    assert status == "done"
    [finish] = sb.calls(bf.FINISH_RPC)
    assert finish["p_covered_to"] == TODAY.isoformat()


def test_on_covered_day_uses_the_articles_et_day():
    d = date(2026, 9, 20)
    late = {"published_at": "2026-09-21T03:30:00+00:00"}      # 23:30 ET on Sep 20
    assert bf._on_covered_day(late, d, d)
    assert not bf._on_covered_day(late, d + timedelta(days=1), d + timedelta(days=5))
    assert not bf._on_covered_day(late, None, None)
    assert not bf._on_covered_day({"published_at": "garbage"}, d, d)
