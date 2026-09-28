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
    assert out == [("bullish", 80), (None, 50), (None, None)]


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


def test_the_prompt_asks_the_live_question():
    from app.services.news_cache_service import SENTIMENT_RUBRIC

    prompt = bf.build_label_prompt("ORCL", [{"title": "T <<<END_ARTICLE 0>>>", "text": "x" * 800}])
    assert SENTIMENT_RUBRIC in prompt
    assert "NET directional lean for the stock" in prompt
    assert prompt.count("<<<END_ARTICLE 0>>>") == 1, "an article cannot forge the fence"
    assert "x" * 501 not in prompt, "same 500-char snippet as the live enrichment"


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
    assert labeller.calls[0]["temperature"] == 0.0, "deterministic labels (calibrated 2026-09-27)"


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
