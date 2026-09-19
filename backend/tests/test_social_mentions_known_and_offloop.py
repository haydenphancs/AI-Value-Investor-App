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
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": False, "all-crypto": False})
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
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": True, "all-crypto": True})
    assert await _svc([[]]).get_mentions_24h("ZZZZ") == (0, 0, True)


@pytest.mark.asyncio
async def test_24h_warm_cache_miss_never_serves_a_stale_snapshot_as_today(monkeypatch):
    """F17-6. A ticker that trended three weeks ago (snapshot row mentions=340, ≤30-day
    retention) and has since dropped off ApeWisdom's list: both filters are loaded, the
    miss IS the answer, and the DB must not be consulted — it would serve 340 as the
    CURRENT count with a fabricated previous of 0 → "+100% today" in green, known."""
    monkeypatch.setattr(ape, "_cache", {"AAPL": {"mentions": 1, "mentions_24h_ago": 1}})
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": True, "all-crypto": True})
    svc = _svc([[{"mentions": 340, "snapshot_date": "2026-08-27"}]])
    assert await svc.get_mentions_24h("GME") == (0, 0, True)
    assert svc.supabase.threads == [], "the DB fallback ran on a warm-cache miss"


@pytest.mark.asyncio
async def test_24h_half_loaded_cache_makes_a_miss_unknown_not_zero(monkeypatch):
    """Boot where `all-stocks` 429'd and `all-crypto` landed: the cache is non-empty, so
    `bool(_cache)` read as "consulted" and every stock published "0 mentions · known"
    for 30 minutes. One cold filter makes an absent ticker UNKNOWN."""
    monkeypatch.setattr(ape, "_cache", {"BTC": {"mentions": 9, "mentions_24h_ago": 3,
                                                "_filter": "all-crypto"}})
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": False, "all-crypto": True})
    assert await _svc([[]]).get_mentions_24h("AAPL") == (0, 0, False)
    # …while a ticker that IS in the cache is known regardless.
    assert await _svc([[]]).get_mentions_24h("BTC") == (9, 3, True)


@pytest.mark.asyncio
async def test_24h_cold_cache_single_snapshot_row_has_an_unknown_change_and_is_off_loop():
    """One row has no previous window. This used to return (7, 0, True): a hard-coded 0
    that `_pct_change` turned into "+100% today", published as measured."""
    svc = _svc([[{"mentions": 7, "snapshot_date": "2026-09-16"}]])
    assert await svc.get_mentions_24h("NVDA") == (7, 0, False)
    assert svc.supabase.threads and svc.supabase.threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_24h_cold_cache_two_consecutive_snapshots_give_a_real_previous_window():
    svc = _svc([[{"mentions": 7, "snapshot_date": "2026-09-16"},
                 {"mentions": 5, "snapshot_date": "2026-09-15"}]])
    assert await svc.get_mentions_24h("NVDA") == (7, 5, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_date", ["2026-09-13", "2026-09-17", None, "", "garbage"])
async def test_24h_cold_cache_a_gap_or_bad_date_leaves_the_change_unknown(prior_date):
    """Two rows that are not consecutive days (a missed snapshot, a duplicate, a malformed
    date) do not describe the prior 24h — never fabricate the change from them."""
    svc = _svc([[{"mentions": 7, "snapshot_date": "2026-09-16"},
                 {"mentions": 5, "snapshot_date": prior_date}]])
    assert await svc.get_mentions_24h("NVDA") == (7, 0, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("cell, expect", [
    (None, 0), ("", 0), ("12", 12), (3.9, 3), (float("nan"), 0), (-4, 0), ("x", 0),
])
async def test_24h_cold_cache_a_malformed_mentions_cell_degrades_to_zero(cell, expect):
    svc = _svc([[{"mentions": cell, "snapshot_date": "2026-09-16"},
                 {"mentions": 5, "snapshot_date": "2026-09-15"}]])
    cur, prev, known = await svc.get_mentions_24h("NVDA")
    assert (cur, prev, known) == (expect, 5, True)


@pytest.mark.asyncio
async def test_24h_cold_cache_rows_with_no_dates_are_a_single_row_shape():
    svc = _svc([[{"mentions": 7}, {"mentions": 5}]])
    assert await svc.get_mentions_24h("NVDA") == (7, 0, False)


# ── the writer, and the loop that finally calls it ─────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_all_upserts_off_loop_and_counts(monkeypatch):
    async def _all():
        return {f"T{i}": {"mentions": i, "upvotes": 0, "rank": i} for i in range(1200)}

    monkeypatch.setattr(sms, "get_all_mentions", _all)
    svc = _svc([[], [], [], []])   # 3 chunks of 500 + the retention delete
    assert await svc.snapshot_all() == (1200, 1200)
    assert len(svc.supabase.threads) == 4
    assert all(t != threading.get_ident() for t in svc.supabase.threads)


@pytest.mark.asyncio
async def test_snapshot_all_with_a_cold_cache_stores_nothing(monkeypatch):
    async def _none():
        return {}

    monkeypatch.setattr(sms, "get_all_mentions", _none)
    assert await _svc([]).snapshot_all() == (0, 0)


class _Edge(Exception):
    """A Cloudflare 520 as postgrest surfaces it: an INT `.code` (the body was not JSON)."""
    code = 520


@pytest.mark.asyncio
async def test_snapshot_all_retries_a_transient_chunk_in_place(monkeypatch, caplog):
    """F24-5. Prod stored 974 in two chunks; a 520 on chunk 2 used to be logged and
    counted as 500/974 — and the day marked done. The upsert is idempotent on the unique
    key, so a transient is replayed and the pair reports a full write."""
    async def _all():
        return {f"T{i}": {"mentions": i, "upvotes": 0, "rank": i} for i in range(974)}

    monkeypatch.setattr(sms, "get_all_mentions", _all)
    svc = _svc([[], _Edge("520"), [], []])          # chunk 1 ok, chunk 2 blips once, delete
    with caplog.at_level(logging.WARNING):
        assert await svc.snapshot_all() == (974, 974)
    assert any("retrying" in r.getMessage() and "chunk 500" in r.getMessage()
               for r in caplog.records), "the transient was not retried in place"


@pytest.mark.asyncio
async def test_snapshot_all_reports_a_hole_left_by_a_non_transient_chunk(monkeypatch, caplog):
    async def _all():
        return {f"T{i}": {"mentions": i, "upvotes": 0, "rank": i} for i in range(974)}

    monkeypatch.setattr(sms, "get_all_mentions", _all)
    svc = _svc([[], Exception("42501 permission denied"), []])
    with caplog.at_level(logging.WARNING):
        assert await svc.snapshot_all() == (500, 974), "the hole was not reported"
    assert any("PARTIAL" in r.getMessage() and "500/974" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_snapshot_all_a_single_row_and_an_exact_chunk_boundary(monkeypatch):
    async def _one():
        return {"T0": {"mentions": 1}}

    monkeypatch.setattr(sms, "get_all_mentions", _one)
    assert await _svc([[], []]).snapshot_all() == (1, 1)

    async def _five_hundred():
        return {f"T{i}": {"mentions": i} for i in range(500)}

    monkeypatch.setattr(sms, "get_all_mentions", _five_hundred)
    svc = _svc([[], []])
    assert await svc.snapshot_all() == (500, 500)
    assert len(svc.supabase.threads) == 2            # exactly one chunk + the delete


def _once_with(monkeypatch, outcomes, *, populated=True):
    """Wire `_social_snapshot_once` to a writer answering `outcomes` in order, over an
    ApeWisdom cache whose completeness is `populated`."""
    calls = []

    class _Svc:
        async def snapshot_all(self):
            calls.append(1)
            return outcomes[min(len(calls), len(outcomes)) - 1]

    monkeypatch.setattr(
        "app.services.social_mentions_service.get_social_mentions_service", lambda: _Svc()
    )
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": populated, "all-crypto": True})
    return calls


@pytest.mark.asyncio
async def test_snapshot_once_runs_one_snapshot_per_day_and_retries_a_zero(monkeypatch):
    calls = _once_with(monkeypatch, [(0, 0), (900, 900)])
    today = date.today()
    assert await main_mod._social_snapshot_once(None) is None        # stored 0 → retry later
    assert await main_mod._social_snapshot_once(None) == today       # stored → done for today
    assert await main_mod._social_snapshot_once(today) == today      # already done → no call
    assert len(calls) == 2
    assert await main_mod._social_snapshot_once(today - timedelta(days=1)) == today  # a new day runs
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_snapshot_once_does_not_mark_a_partial_write_done(monkeypatch, caplog):
    """F24-5: 500/974 stored → the day stays open and the next tick re-upserts."""
    calls = _once_with(monkeypatch, [(500, 974), (974, 974)])
    today = date.today()
    with caplog.at_level(logging.WARNING):
        assert await main_mod._social_snapshot_once(None) is None
    assert any("PARTIAL" in r.getMessage() and "500/974" in r.getMessage()
               for r in caplog.records)
    assert await main_mod._social_snapshot_once(None) == today
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_snapshot_once_does_not_mark_a_half_loaded_cache_done(monkeypatch, caplog):
    """F18-2 / F22-5: boot where all-stocks 429'd and all-crypto landed. The crypto rows
    ARE written (the writer is called), but the day is left open until both filters have
    landed — otherwise every stock lost that day's row for a week of 7-day sums."""
    calls = _once_with(monkeypatch, [(100, 100)], populated=False)
    with caplog.at_level(logging.WARNING):
        assert await main_mod._social_snapshot_once(None) is None
    assert len(calls) == 1, "the loaded class's rows must still be written"
    assert any("PARTIAL ApeWisdom cache" in r.getMessage() for r in caplog.records)
    # …and once the stocks filter lands, the same day completes.
    ape._loaded["all-stocks"] = True
    assert await main_mod._social_snapshot_once(None) == date.today()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_snapshot_once_reads_completeness_before_the_write(monkeypatch):
    """A refresh landing DURING the write may only make us retry, never skip: the writer
    flipping the cache to complete mid-call must not stamp a crypto-only write as done."""
    class _Svc:
        async def snapshot_all(self):
            ape._loaded["all-stocks"] = True          # lands mid-write
            return (100, 100)

    monkeypatch.setattr(
        "app.services.social_mentions_service.get_social_mentions_service", lambda: _Svc()
    )
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": False, "all-crypto": True})
    assert await main_mod._social_snapshot_once(None) is None


@pytest.mark.asyncio
async def test_the_loop_refreshes_a_half_loaded_cache_before_snapshotting(monkeypatch):
    """Readers only KICK a background refresh and the partial stamp is fresh for 300 s, so
    with no traffic the failed filter waited for the next hourly tick to even start — and
    that tick snapshotted the stale cache first."""
    order = []

    async def _refresh():
        order.append("refresh")
        if order.count("refresh") == 2:               # the boot pre-warm fails, the tick lands
            ape._loaded["all-stocks"] = True
        return {}

    async def _once(last_done):
        order.append("snapshot")
        raise asyncio.CancelledError()               # end the loop after one tick

    monkeypatch.setattr(ape, "refresh_cache", _refresh)
    monkeypatch.setattr(main_mod, "_social_snapshot_once", _once)
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": False, "all-crypto": True})

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(main_mod.asyncio, "sleep", _no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await main_mod._run_social_snapshot_loop()
    assert order == ["refresh", "refresh", "snapshot"], order
    assert ape._loaded["all-stocks"] is True


@pytest.mark.asyncio
async def test_the_loop_does_not_refresh_a_complete_cache(monkeypatch):
    order = []

    async def _refresh():
        order.append("refresh")
        return {}

    async def _once(last_done):
        order.append("snapshot")
        raise asyncio.CancelledError()

    monkeypatch.setattr(ape, "refresh_cache", _refresh)
    monkeypatch.setattr(main_mod, "_social_snapshot_once", _once)
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": True, "all-crypto": True})

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(main_mod.asyncio, "sleep", _no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await main_mod._run_social_snapshot_loop()
    # The boot pre-warm calls refresh once regardless; the tick must not add a second.
    assert order == ["refresh", "snapshot"], order


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

    monkeypatch.setattr(svc, "_get_articles", _articles)
    monkeypatch.setattr(svc, "_fetch_price_data", _price)
    monkeypatch.setattr(svc, "_fetch_historical_prices", _hist)

    class _Social:
        async def get_mentions_24h(self, t):
            return (40, 30, True)

        async def get_mentions_7d(self, t):
            raise RuntimeError("permission denied for table social_mentions_history")

    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: _Social())
    resp = await svc.get_sentiment("NVDA")
    assert resp.social_mentions == 40.0 and resp.social_mentions_known is True
    assert resp.social_mentions_7d == 0.0 and resp.social_mentions_7d_known is False


@pytest.mark.asyncio
async def test_get_sentiment_never_publishes_a_fabricated_plus_100_as_known(monkeypatch):
    """F17-6 on the wire: the REAL `get_mentions_24h` over a cold cache with one snapshot
    row must not reach the response as `social_mentions_change == 100.0` with
    `social_mentions_known == True` — iOS renders that as "+100% today" in green."""
    ss._cache.clear()
    svc = ss.SentimentService.__new__(ss.SentimentService)

    async def _articles(*a, **k):
        return []

    async def _price(*a, **k):
        return {}

    async def _hist(*a, **k):
        return []

    monkeypatch.setattr(svc, "_get_articles", _articles)
    monkeypatch.setattr(svc, "_fetch_price_data", _price)
    monkeypatch.setattr(svc, "_fetch_historical_prices", _hist)

    social = _svc([[{"mentions": 340, "snapshot_date": "2026-09-16"}], [], []])
    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: social)
    resp = await svc.get_sentiment("GME")
    assert not (resp.social_mentions_change == 100.0 and resp.social_mentions_known), (
        f"a stale count with a fabricated previous of 0 reached the wire as a measured "
        f"+100%: change={resp.social_mentions_change} known={resp.social_mentions_known}"
    )
    assert resp.social_mentions_known is False
    ss._cache.clear()
