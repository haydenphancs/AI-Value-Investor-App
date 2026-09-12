"""D2 (2026-09-11, Railway): the social-mentions arm must be (a) written, (b) off the event
loop, and (c) honest about "unknown" versus "zero".

What shipped: `SocialMentionsService.snapshot_all` — the ONLY writer of
`social_mentions_history` — had no caller, so `get_mentions_7d` answered (0, 0) for every
ticker forever; its two sync PostgREST round-trips ran INSIDE the request coroutine; and a
failed lookup (the 42501 migration 169 fixes, an ApeWisdom timeout) came back as the same
(0, 0) a genuinely un-mentioned ticker gets, which the response then published as a
measured "0 mentions / +0% this week".
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.integrations.apewisdom as ape
import app.main as main_mod
import app.services.sentiment_service as ss
import app.services.social_mentions_service as sms
from app.schemas.sentiment import SentimentAnalysisResponse


# ── fakes ───────────────────────────────────────────────────────────────────────────


class _Query:
    """Records the thread each execute() ran on; raises or returns per `plan`."""

    def __init__(self, plan, log):
        self.plan, self.log = plan, log

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        self.log.append(threading.get_ident())
        nxt = self.plan.pop(0) if self.plan else []
        if isinstance(nxt, Exception):
            raise nxt
        return SimpleNamespace(data=nxt)


class _Supabase:
    def __init__(self, plan):
        self.plan, self.threads = list(plan), []

    def table(self, _name):
        return _Query(self.plan, self.threads)


def _svc(plan):
    svc = sms.SocialMentionsService.__new__(sms.SocialMentionsService)
    svc.supabase = _Supabase(plan)
    return svc


@pytest.fixture(autouse=True)
def _cold_apewisdom(monkeypatch):
    monkeypatch.setattr(ape, "_cache", {})
    monkeypatch.setattr(ape, "_kick_background_refresh", lambda: None)


# ── 7d: known vs unknown, and off-loop ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_7d_query_failure_is_unknown_not_zero(caplog):
    svc = _svc([Exception("permission denied for table social_mentions_history")])
    with caplog.at_level(logging.WARNING, logger="app.services.social_mentions_service"):
        assert await svc.get_mentions_7d("nvda") == (0, 0, False)
    assert any("7d mentions query failed for NVDA" in r.getMessage() and "Exception" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_7d_sums_both_windows_and_is_known_even_when_empty():
    rows_cur = [{"mentions": 10}, {"mentions": 15}]
    rows_prev = [{"mentions": 5}]
    assert await _svc([rows_cur, rows_prev]).get_mentions_7d("NVDA") == (25, 5, True)
    # A successful EMPTY answer (warm-up week, an un-mentioned ticker) is a real zero.
    assert await _svc([[], []]).get_mentions_7d("ZZZZ") == (0, 0, True)


@pytest.mark.asyncio
async def test_7d_db_calls_run_off_the_event_loop():
    svc = _svc([[{"mentions": 1}], []])
    await svc.get_mentions_7d("NVDA")
    assert svc.supabase.threads and all(t != threading.get_ident() for t in svc.supabase.threads), \
        "PostgREST round-trips must not run on the event-loop thread"


# ── 24h: cache hit / cold cache / not tracked ──────────────────────────────────────


@pytest.mark.asyncio
async def test_24h_cache_hit_is_known(monkeypatch):
    monkeypatch.setattr(ape, "_cache", {"NVDA": {"mentions": 40, "mentions_24h_ago": 30}})
    assert await _svc([]).get_mentions_24h("nvda") == (40, 30, True)


@pytest.mark.asyncio
async def test_24h_cold_cache_and_failed_db_is_unknown():
    assert await _svc([Exception("42501")]).get_mentions_24h("NVDA") == (0, 0, False)


@pytest.mark.asyncio
async def test_24h_cold_cache_and_empty_db_is_unknown_not_untracked():
    """The cache never populated AND the DB has nothing: nobody looked, so nobody may say
    'Reddit is not talking about it'."""
    assert await _svc([[]]).get_mentions_24h("NVDA") == (0, 0, False)


@pytest.mark.asyncio
async def test_24h_populated_cache_without_the_ticker_is_a_real_zero(monkeypatch):
    monkeypatch.setattr(ape, "_cache", {"AAPL": {"mentions": 1, "mentions_24h_ago": 1}})
    assert await _svc([[]]).get_mentions_24h("ZZZZ") == (0, 0, True)


@pytest.mark.asyncio
async def test_24h_db_fallback_row_is_known_and_off_loop():
    svc = _svc([[{"mentions": 7}]])
    assert await svc.get_mentions_24h("NVDA") == (7, 0, True)
    assert svc.supabase.threads and svc.supabase.threads[0] != threading.get_ident()


# ── the writer, and the loop that finally calls it ─────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_all_upserts_off_loop_and_counts(monkeypatch):
    async def _all():
        return {f"T{i}": {"mentions": i, "upvotes": 0, "rank": i} for i in range(1200)}

    monkeypatch.setattr(sms, "get_all_mentions", _all)
    svc = _svc([[], [], [], []])   # 3 chunks of 500 + the retention delete
    assert await svc.snapshot_all() == 1200
    assert len(svc.supabase.threads) == 4
    assert all(t != threading.get_ident() for t in svc.supabase.threads)


@pytest.mark.asyncio
async def test_snapshot_all_with_a_cold_cache_stores_nothing(monkeypatch):
    async def _none():
        return {}

    monkeypatch.setattr(sms, "get_all_mentions", _none)
    assert await _svc([]).snapshot_all() == 0


@pytest.mark.asyncio
async def test_snapshot_once_runs_one_snapshot_per_day_and_retries_a_zero(monkeypatch):
    calls = []

    class _Svc:
        async def snapshot_all(self):
            calls.append(1)
            return 0 if len(calls) == 1 else 900

    monkeypatch.setattr(
        "app.services.social_mentions_service.get_social_mentions_service", lambda: _Svc()
    )
    today = date.today()
    assert await main_mod._social_snapshot_once(None) is None        # stored 0 → retry later
    assert await main_mod._social_snapshot_once(None) == today       # stored → done for today
    assert await main_mod._social_snapshot_once(today) == today      # already done → no call
    assert len(calls) == 2
    assert await main_mod._social_snapshot_once(today - timedelta(days=1)) == today  # a new day runs
    assert len(calls) == 3


def test_the_lifespan_spawns_the_snapshot_loop_not_the_old_one_shot():
    src = open(main_mod.__file__, encoding="utf-8").read()
    assert '_spawn(_run_social_snapshot_loop(), "run_social_snapshot_loop")' in src
    assert "_warm_social_cache" not in src, "the caller-less one-shot warm must not come back"


# ── the wire ────────────────────────────────────────────────────────────────────────


def test_known_flags_default_true_and_the_builder_sets_both_explicitly():
    fields = SentimentAnalysisResponse.model_fields
    assert fields["social_mentions_known"].default is True
    assert fields["social_mentions_7d_known"].default is True
    # `social_mentions*` stay non-Optional floats: old builds decode a plain Double.
    assert fields["social_mentions"].annotation is float and fields["social_mentions_7d"].annotation is float
    import ast
    tree = ast.parse(open(ss.__file__, encoding="utf-8").read())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "SentimentAnalysisResponse"]
    assert calls, "the builder call was not found"
    kw = {k.arg for k in calls[0].keywords}
    assert {"social_mentions_known", "social_mentions_7d_known"} <= kw, kw


@pytest.mark.asyncio
async def test_get_sentiment_publishes_unknown_for_a_failed_7d_arm(monkeypatch):
    ss._cache.clear()
    svc = ss.SentimentService.__new__(ss.SentimentService)

    async def _articles(*a, **k):
        return []

    async def _price(*a, **k):
        return {}

    async def _hist(*a, **k):
        return []

    monkeypatch.setattr(svc, "_get_articles", _articles, raising=False)
    monkeypatch.setattr(svc, "_fetch_price_data", _price, raising=False)
    monkeypatch.setattr(svc, "_fetch_historical_prices", _hist, raising=False)

    class _Social:
        async def get_mentions_24h(self, t):
            return (40, 30, True)

        async def get_mentions_7d(self, t):
            raise RuntimeError("permission denied for table social_mentions_history")

    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: _Social())
    resp = await svc.get_sentiment("NVDA")
    assert resp.social_mentions == 40.0 and resp.social_mentions_known is True
    assert resp.social_mentions_7d == 0.0 and resp.social_mentions_7d_known is False
