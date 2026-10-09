"""The Tracking feed's per-ticker fan-out is BOUNDED and DEDUPED (F15-3).

`GET /tracking/assets` issues one FMP call per watchlist ticker for the sparkline and one
for the insider alert, on every cache miss, for every row of the watchlist. Nothing bounded
the watchlist, nothing bounded the fan-out, the per-user cache was written only at the END
of the build, and there was no `_inflight` dedup — so one free account with 2,000 rows and
two clients polling every 31 s cost ~4,000-6,000 FMP requests/min and rate-limited every
other user's screens.

Four bounds, each pinned here with the degraded path it must NOT take:
  * `_feed_inflight` — concurrent builds for one user collapse to ONE; a cancelled leader
    hands over instead of hanging (CancelledError is a BaseException) or failing joiners;
  * `TRACKING_FEED_MAX_TICKERS` — the per-request fan-out is capped, but EVERY row is still
    in the feed (the iOS Assets tab purges portfolio tickers missing from it);
  * the insider pass has a per-ticker tier-1 cache (a cached None is the saving) and skips
    non-equity classes BEFORE the call (`insider-trading/search` is not symbol-gated, so a
    coin row used to make a real HTTP round trip);
  * both per-ticker gathers run behind a semaphore.

No network / Supabase / FMP: every upstream is a stub, and the module caches are cleared.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import pytest

import app.services.tracking_service as ts
from app.services.tracking_service import TrackingService, TrackingFeedResponse, WatchlistUnavailableError
from app.config import settings


@pytest.fixture(autouse=True)
def _clean_caches():
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    ts._sparkline_cache.clear()
    ts._insider_cache.clear()
    ts.reset_earnings_calendar_cache()
    yield
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    ts._sparkline_cache.clear()
    ts._insider_cache.clear()
    ts.reset_earnings_calendar_cache()


# ── 1. in-flight dedup on the feed build ─────────────────────────────────────

class _SlowBuild:
    """Stands in for `_build_tracking_feed`: counts entries, holds until released."""

    def __init__(self, result=None, exc=None):
        self.entries = 0
        self.release = asyncio.Event()
        self.result = result or TrackingFeedResponse()
        self.exc = exc

    # A callable INSTANCE on the class is not a descriptor, so no `self` is bound:
    # the service calls it as `_build_tracking_feed(user_id, generation=...)`.
    async def __call__(self, user_id, **kwargs):
        self.entries += 1
        self.generations = getattr(self, "generations", []) + [kwargs.get("generation")]
        await self.release.wait()
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_user_build_once(monkeypatch):
    build = _SlowBuild()
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", build)
    svc = TrackingService()

    t1 = asyncio.create_task(svc.get_tracking_feed("u"))
    t2 = asyncio.create_task(svc.get_tracking_feed("u"))
    t3 = asyncio.create_task(svc.get_tracking_feed("u"))
    await asyncio.sleep(0)          # let all three reach the join
    assert build.entries == 1, "the second request must join the first, not start its own fan-out"
    build.release.set()
    r1, r2, r3 = await asyncio.gather(t1, t2, t3)
    assert r1 is build.result and r2 is build.result and r3 is build.result
    assert "u" not in ts._feed_inflight, "the in-flight entry must be released after the build"


@pytest.mark.asyncio
async def test_different_users_do_not_share_a_build(monkeypatch):
    build = _SlowBuild()
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", build)
    svc = TrackingService()
    ta = asyncio.create_task(svc.get_tracking_feed("a"))
    tb = asyncio.create_task(svc.get_tracking_feed("b"))
    await asyncio.sleep(0)
    assert build.entries == 2
    build.release.set()
    await asyncio.gather(ta, tb)


@pytest.mark.asyncio
async def test_a_leader_failure_reaches_every_joiner_and_is_not_cached(monkeypatch):
    """A `WatchlistUnavailableError` must surface to each caller as 503 — never as an
    empty feed (the client purges on that) — and must not pin anything."""
    build = _SlowBuild(exc=WatchlistUnavailableError("read failed"))
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", build)
    svc = TrackingService()
    t1 = asyncio.create_task(svc.get_tracking_feed("u"))
    t2 = asyncio.create_task(svc.get_tracking_feed("u"))
    await asyncio.sleep(0)
    build.release.set()
    r1, r2 = await asyncio.gather(t1, t2, return_exceptions=True)
    assert isinstance(r1, WatchlistUnavailableError) and isinstance(r2, WatchlistUnavailableError)
    assert build.entries == 1
    assert ts._feed_cache_get("u") is None and "u" not in ts._feed_inflight


@pytest.mark.asyncio
async def test_a_cancelled_leader_hands_over_instead_of_hanging_or_failing_joiners(monkeypatch):
    """CancelledError is a BaseException: an `except Exception` would leave the future
    unresolved and every joiner hung for the life of the process. The joiner must instead
    become the next leader and complete on its own."""
    build = _SlowBuild()
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", build)
    svc = TrackingService()
    leader = asyncio.create_task(svc.get_tracking_feed("u"))
    joiner = asyncio.create_task(svc.get_tracking_feed("u"))
    await asyncio.sleep(0)
    assert build.entries == 1
    leader.cancel()
    # cancel → leader's except arm → future resolved → joiner wakes → re-loops → new
    # build: several loop turns, so wait on the observable rather than counting ticks.
    for _ in range(50):
        if build.entries == 2:
            break
        await asyncio.sleep(0.001)
    assert build.entries == 2, "the joiner did not take over after the leader was cancelled"
    build.release.set()
    out = await asyncio.wait_for(joiner, timeout=2)
    assert out is build.result
    assert leader.cancelled()


@pytest.mark.asyncio
async def test_a_cancelled_joiner_does_not_cancel_the_shared_build(monkeypatch):
    build = _SlowBuild()
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", build)
    svc = TrackingService()
    leader = asyncio.create_task(svc.get_tracking_feed("u"))
    joiner = asyncio.create_task(svc.get_tracking_feed("u"))
    await asyncio.sleep(0)
    joiner.cancel()
    await asyncio.sleep(0)
    build.release.set()
    assert await asyncio.wait_for(leader, timeout=2) is build.result
    assert joiner.cancelled()


@pytest.mark.asyncio
async def test_a_cache_hit_never_touches_the_build(monkeypatch):
    feed = TrackingFeedResponse()
    ts._feed_cache_set("u", feed)
    build = _SlowBuild()
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", build)
    assert await TrackingService().get_tracking_feed("u") is feed
    assert build.entries == 0


# ── 2. the per-request fan-out cap ───────────────────────────────────────────

def test_fanout_cap_slices_newest_first_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(settings, "TRACKING_FEED_MAX_TICKERS", 3)
    with caplog.at_level(logging.WARNING):
        out = ts._fanout_tickers(["A", "B", "C", "D", "E"], "u")
    assert out == ["A", "B", "C"]
    assert any("TRACKING_FEED_MAX_TICKERS" in r.getMessage() and "u" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("cap", [0, -1, None])
def test_fanout_cap_disabled_passes_everything(monkeypatch, cap):
    monkeypatch.setattr(settings, "TRACKING_FEED_MAX_TICKERS", cap)
    tickers = [f"T{i}" for i in range(50)]
    assert ts._fanout_tickers(tickers, "u") == tickers


def test_fanout_cap_boundaries(monkeypatch, caplog):
    monkeypatch.setattr(settings, "TRACKING_FEED_MAX_TICKERS", 3)
    with caplog.at_level(logging.WARNING):
        assert ts._fanout_tickers([], "u") == []
        assert ts._fanout_tickers(["A"], "u") == ["A"]
        assert ts._fanout_tickers(["A", "B", "C"], "u") == ["A", "B", "C"]   # exactly at cap
    assert not caplog.records, "no warning below or at the cap"


class _FakeTable:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeSupabase:
    def __init__(self, watchlist): self._w = watchlist
    def table(self, name):
        return _FakeTable(self._w if name == "watchlist_items" else [])


@pytest.mark.asyncio
async def test_the_build_caps_the_per_ticker_passes_but_keeps_every_row(monkeypatch):
    """The load-bearing half: rows past the cap are STILL in the feed. The iOS Assets tab
    purges any portfolio ticker missing from this response, so dropping them would delete
    the user's own holdings."""
    monkeypatch.setattr(settings, "TRACKING_FEED_MAX_TICKERS", 2)
    watchlist = [{"id": i, "ticker": t, "company_name": t, "asset_type": "stock",
                  "sector": "Tech"} for i, t in enumerate(["AAPL", "MSFT", "NVDA", "AMZN"])]
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(watchlist))
    seen: Dict[str, List[str]] = {}

    async def _quotes(self, tickers): return {}
    async def _spark(self, tickers, asset_types=None):
        seen["spark"] = list(tickers); return {}
    async def _earn(self, tickers): return []
    async def _whale(self, tickers): return []
    async def _analyst(self, tickers): return []
    async def _insider(self, tickers, asset_types=None):
        seen["insider"] = list(tickers); return []
    async def _backfill(self, user_id, watchlist): return None
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _spark),
                     ("_get_earnings_alerts", _earn), ("_get_whale_trade_alerts", _whale),
                     ("_get_analyst_rating_alerts", _analyst),
                     ("_get_insider_transaction_alerts", _insider),
                     ("_backfill_classification", _backfill)]:
        monkeypatch.setattr(TrackingService, name, fn)

    feed = await TrackingService().get_tracking_feed("u")

    assert seen["spark"] == ["AAPL", "MSFT"]
    assert seen["insider"] == ["AAPL", "MSFT"]
    assert [a.ticker for a in feed.assets] == ["AAPL", "MSFT", "NVDA", "AMZN"], (
        "every watchlist row must stay in the feed — the client purges what is missing"
    )
    assert feed.assets[3].sparkline_data == []


# ── 3. the insider pass: cache, class skip, gate ─────────────────────────────

class _InsiderFMP:
    def __init__(self, rows_by_ticker=None, delay=0.0):
        self.calls: List[str] = []
        self.rows = rows_by_ticker or {}
        self.delay = delay
        self.live = 0
        self.max_live = 0

    async def get_insider_trading(self, ticker, limit=30):
        self.calls.append(ticker)
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self.rows.get(ticker, [])
        finally:
            self.live -= 1


def _svc(fmp):
    svc = TrackingService()
    svc.fmp = fmp
    return svc


@pytest.mark.asyncio
async def test_insider_pass_skips_coins_indices_and_commodities_before_the_call():
    fmp = _InsiderFMP()
    out = await _svc(fmp)._get_insider_transaction_alerts(
        ["AAPL", "BTCUSD", "^GSPC", "GCUSD", "SPY"],
        {"AAPL": "stock", "BTCUSD": "crypto", "^GSPC": "index", "GCUSD": "commodity", "SPY": "etf"},
    )
    assert out == []
    assert sorted(fmp.calls) == ["AAPL", "SPY"], (
        "a coin/index/commodity row must not cost an insider call — the endpoint is not "
        "symbol-gated, so this was a real HTTP round trip to be told nothing"
    )


@pytest.mark.asyncio
async def test_insider_pass_skips_a_coin_even_when_the_stored_type_is_the_default():
    """The column defaults to 'Stock' for rows written before it was persisted; the
    symbol's shape must still classify BTCUSD as crypto."""
    fmp = _InsiderFMP()
    await _svc(fmp)._get_insider_transaction_alerts(["BTCUSD", "ETHUSD"], {"BTCUSD": "Stock"})
    assert fmp.calls == []


@pytest.mark.asyncio
async def test_insider_pass_caches_the_empty_answer_per_ticker():
    """The saving IS the None: most tickers have no notable Form 4 in 14 days, and every
    feed build re-asked for each of them 2×/min per client."""
    fmp = _InsiderFMP()
    svc = _svc(fmp)
    await svc._get_insider_transaction_alerts(["AAPL", "MSFT"])
    await svc._get_insider_transaction_alerts(["AAPL", "MSFT"])
    await svc._get_insider_transaction_alerts(["aapl"])          # case-insensitive key
    assert sorted(fmp.calls) == ["AAPL", "MSFT"], fmp.calls


@pytest.mark.asyncio
async def test_insider_cache_expires():
    fmp = _InsiderFMP()
    svc = _svc(fmp)
    await svc._get_insider_transaction_alerts(["AAPL"])
    ts._insider_cache["AAPL"] = (ts._time.monotonic() - ts.INSIDER_CACHE_TTL - 1, None)
    await svc._get_insider_transaction_alerts(["AAPL"])
    assert fmp.calls == ["AAPL", "AAPL"]


@pytest.mark.asyncio
async def test_insider_cache_serves_a_real_roll_up_too():
    """A cached HIT (not just None) round-trips intact into the alert."""
    from datetime import datetime, timedelta
    day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    row = {"transactionDate": day, "transactionType": "P-Purchase", "securitiesTransacted": 10_000,
           "price": 50.0, "reportingName": "Jane Doe", "typeOfOwner": "CEO"}
    fmp = _InsiderFMP({"AAPL": [row]})
    svc = _svc(fmp)
    first = await svc._get_insider_transaction_alerts(["AAPL"])
    second = await svc._get_insider_transaction_alerts(["AAPL"])
    assert fmp.calls == ["AAPL"]
    assert [a.title for a in first] == ["Insider Bought"]
    assert [a.title for a in second] == ["Insider Bought"]
    assert second[0].insider_transaction_items[0].raw_amount == 500_000.0


@pytest.mark.asyncio
async def test_insider_fan_out_is_gated():
    fmp = _InsiderFMP(delay=0.005)
    await _svc(fmp)._get_insider_transaction_alerts([f"T{i}" for i in range(60)])
    assert len(fmp.calls) == 60
    assert 0 < fmp.max_live <= ts._PER_TICKER_FANOUT_CONCURRENCY, fmp.max_live


@pytest.mark.asyncio
async def test_insider_pass_tolerates_blank_and_none_tickers():
    fmp = _InsiderFMP()
    out = await _svc(fmp)._get_insider_transaction_alerts(["", None, "AAPL"])  # type: ignore[list-item]
    assert out == [] and fmp.calls == ["AAPL"]


def test_insider_cache_sweeps_at_the_threshold(monkeypatch):
    monkeypatch.setattr(ts, "_INSIDER_CACHE_SWEEP_AT", 5)
    for i in range(5):
        ts._insider_cache_set(f"T{i}", None)
    assert len(ts._insider_cache) == 5
    ts._insider_cache_set("T5", None)                # over the threshold → evicts oldest
    assert len(ts._insider_cache) <= 5
    assert "T5" in ts._insider_cache and "T0" not in ts._insider_cache


# ── 4. the sparkline fan-out is gated ────────────────────────────────────────

@pytest.mark.asyncio
async def test_sparkline_fan_out_is_gated(monkeypatch):
    live = {"n": 0, "max": 0}

    async def fake_fetch(fmp, ticker, extended_hours=False):
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])
        try:
            await asyncio.sleep(0.005)
            return []
        finally:
            live["n"] -= 1

    monkeypatch.setattr(ts, "fetch_sparkline_bars", fake_fetch)
    out = await TrackingService()._get_all_sparklines([f"T{i}" for i in range(60)])
    assert len(out) == 60
    assert 0 < live["max"] <= ts._PER_TICKER_FANOUT_CONCURRENCY, live["max"]


@pytest.mark.asyncio
async def test_sparkline_cache_hits_do_not_take_a_gate_slot(monkeypatch):
    """A cached ticker must answer without touching the upstream at all."""
    called = []

    async def fake_fetch(fmp, ticker, extended_hours=False):
        called.append(ticker); return []

    monkeypatch.setattr(ts, "fetch_sparkline_bars", fake_fetch)
    ts._sparkline_cache_set("AAPL", [1.0, 2.0], False)
    out = await TrackingService()._get_all_sparklines(["AAPL", "MSFT"])
    assert called == ["MSFT"]
    assert out["AAPL"][0] == [1.0, 2.0]


# ── W2 regress-C-3: a FAILED insider fetch is not "no insider activity" for 10 minutes ──


class _FailingInsiderFMP(_InsiderFMP):
    """First call fails (raise, or FMP's own []-on-429 degradation as EmptyAfterFailure);
    later calls answer a real roll-up."""
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def get_insider_trading(self, ticker, limit=30):
        self.calls.append(ticker)
        if len(self.calls) == 1:
            if self.mode == "raise":
                raise RuntimeError("503 from FMP")
            from app.integrations.fmp import EmptyAfterFailure
            return EmptyAfterFailure("insider-trading/search 429")
        from datetime import datetime, timedelta
        day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        return [{"transactionDate": day, "transactionType": "P-Purchase", "securitiesTransacted": 10_000,
                 "price": 50.0, "reportingName": "Jane Doe", "typeOfOwner": "CEO"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "empty_after_failure"])
async def test_a_failed_insider_fetch_is_not_cached_as_no_activity(mode):
    """The per-ticker cache stored a failed fetch's `None` for the full TTL, process-wide:
    one over-budget cold fan-out made every user's feed read "no insider activity" for
    ~200 tickers for 10 minutes."""
    fmp = _FailingInsiderFMP(mode)
    svc = _svc(fmp)
    first = await svc._get_insider_transaction_alerts(["AAPL"])
    assert first == [] and fmp.calls == ["AAPL"]
    assert "AAPL" not in ts._insider_cache, "a failure must not occupy the cache"
    second = await svc._get_insider_transaction_alerts(["AAPL"])
    assert fmp.calls == ["AAPL", "AAPL"], "the next pass re-asks instead of replaying the failure"
    assert [a.title for a in second] == ["Insider Bought"]


@pytest.mark.asyncio
async def test_a_rate_limited_insider_fetch_through_the_REAL_client_is_not_cached(monkeypatch):
    """The test above fakes `get_insider_trading` returning `EmptyAfterFailure` — a value the
    real client never produced: it swallowed a 429 into a bare `[]`, which this pass then
    cached for 10 minutes as "no insider activity" (audit 2026-10-03). Drive the REAL
    `FMPClient.get_insider_trading` with only the HTTP layer patched."""
    from datetime import datetime, timedelta
    from app.integrations.fmp import FMPClient, FMPRateLimitException

    calls = []

    async def make_request(self, endpoint, params=None, **kw):
        calls.append(endpoint)
        if len(calls) == 1:
            raise FMPRateLimitException("429 Too Many Requests")
        day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        return [{"transactionDate": day, "transactionType": "P-Purchase",
                 "securitiesTransacted": 10_000, "price": 50.0, "reportingName": "Jane Doe",
                 "typeOfOwner": "CEO", "securityName": "Common Stock"}]

    monkeypatch.setattr(FMPClient, "_make_request", make_request)
    svc = _svc(object.__new__(FMPClient))
    assert await svc._get_insider_transaction_alerts(["AAPL"]) == []
    assert "AAPL" not in ts._insider_cache, "a 429 must not be cached as no activity"
    second = await svc._get_insider_transaction_alerts(["AAPL"])
    assert len(calls) == 2 and [a.title for a in second] == ["Insider Bought"]


@pytest.mark.asyncio
async def test_a_measured_empty_insider_answer_is_still_cached():
    """Control: the genuine 'no notable Form 4 in the window' stays the cached common case."""
    fmp = _InsiderFMP({"AAPL": []})
    svc = _svc(fmp)
    await svc._get_insider_transaction_alerts(["AAPL"])
    await svc._get_insider_transaction_alerts(["AAPL"])
    assert fmp.calls == ["AAPL"]


# ── W2 regress-C-2: a joiner-less leader failure is not a Sentry event ────────

import gc


def _capture_loop_errors(loop):
    seen: list = []
    loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
    return seen


def _never_retrieved(seen) -> list:
    return [c for c in seen if "never retrieved" in str(c.get("message", ""))]


@pytest.mark.asyncio
async def test_harness_detects_an_unretrieved_future():
    loop = asyncio.get_running_loop()
    seen = _capture_loop_errors(loop)
    try:
        fut = loop.create_future()
        fut.set_exception(RuntimeError("bare"))
        del fut
        gc.collect()
        assert _never_retrieved(seen)
    finally:
        loop.set_exception_handler(None)


@pytest.mark.asyncio
async def test_a_joinerless_feed_failure_is_not_reported_as_never_retrieved(monkeypatch):
    """The feed future stores the leader's exception for joiners; with none (one client, no
    concurrent request in the build window — the common case) it was garbage-collected
    unread and asyncio logged the traceback at ERROR, a second Sentry event per failed build."""
    svc = TrackingService()

    async def _boom(self, user_id, **kwargs):
        raise WatchlistUnavailableError("520 from the edge")
    monkeypatch.setattr(TrackingService, "_build_tracking_feed", _boom)
    loop = asyncio.get_running_loop()
    seen = _capture_loop_errors(loop)
    try:
        try:
            await svc.get_tracking_feed("u-1")
        except WatchlistUnavailableError:
            pass
        gc.collect()
        assert not _never_retrieved(seen), seen
        assert "u-1" not in ts._feed_inflight
    finally:
        loop.set_exception_handler(None)


# ── 5. the write generation: a build that predates a watchlist write never pins ──
#
# The iOS 30 s price timer keeps a feed build running while a detail screen is pushed
# over the Tracking tab, so a star tap's POST/DELETE routinely lands MID-build. That
# build read the pre-write watchlist; `invalidate_feed_cache` popped an entry that did
# not exist yet, and the build then cached the stale list for another 30 s — the
# client's post-confirm reconcile read it, and the row the user had just removed came
# back (tracking_watchlist_portfolio E1).


@pytest.fixture(autouse=True)
def _clean_generations():
    ts._feed_generation.clear()
    ts._feed_inflight_generation.clear()
    yield
    ts._feed_generation.clear()
    ts._feed_inflight_generation.clear()


@pytest.mark.asyncio
async def test_a_build_that_started_before_an_invalidation_does_not_re_pin_the_feed(monkeypatch):
    result = TrackingFeedResponse()
    release = asyncio.Event()
    entries = []

    async def _build(self, user_id, *, generation=None):
        entries.append(generation)
        await release.wait()
        ts._feed_cache_set(user_id, result, generation=generation)   # what the real build does
        return result

    monkeypatch.setattr(TrackingService, "_build_tracking_feed", _build)
    leader = asyncio.create_task(TrackingService().get_tracking_feed("u-gen"))
    await asyncio.sleep(0)
    assert entries == [0]
    ts.invalidate_feed_cache("u-gen")          # the star's DELETE lands mid-build
    release.set()
    assert await leader is result, "the stale build is still SERVED to its caller"
    assert ts._feed_cache_get("u-gen") is None, "…but never pinned for the next 30 s"


@pytest.mark.asyncio
async def test_a_build_that_saw_no_write_is_cached_as_before(monkeypatch):
    result = TrackingFeedResponse()
    release = asyncio.Event()

    async def _build(self, user_id, *, generation=None):
        await release.wait()
        ts._feed_cache_set(user_id, result, generation=generation)
        return result

    monkeypatch.setattr(TrackingService, "_build_tracking_feed", _build)
    leader = asyncio.create_task(TrackingService().get_tracking_feed("u-gen"))
    await asyncio.sleep(0)
    release.set()
    await leader
    assert ts._feed_cache_get("u-gen") is result, "control: the generation check must not block a clean build"


@pytest.mark.asyncio
async def test_a_joiner_does_not_adopt_a_leader_that_predates_the_write(monkeypatch):
    """The joiner awaited a build the write invalidated; it must rebuild, not return it."""
    stale, fresh = TrackingFeedResponse(), TrackingFeedResponse()
    release = asyncio.Event()
    results = [stale, fresh]
    entries = []

    async def _build(self, user_id, *, generation=None):
        entries.append(generation)
        if len(entries) == 1:
            await release.wait()
        out = results[len(entries) - 1]
        ts._feed_cache_set(user_id, out, generation=generation)
        return out

    monkeypatch.setattr(TrackingService, "_build_tracking_feed", _build)
    leader = asyncio.create_task(TrackingService().get_tracking_feed("u-gen"))
    await asyncio.sleep(0)
    joiner = asyncio.create_task(TrackingService().get_tracking_feed("u-gen"))
    await asyncio.sleep(0)
    ts.invalidate_feed_cache("u-gen")
    release.set()
    got_leader, got_joiner = await asyncio.gather(leader, joiner)

    assert got_leader is stale, "the leader serves what it built"
    assert got_joiner is fresh, "the joiner rebuilt under the new generation instead of adopting the stale result"
    assert entries == [0, 1], entries
    assert ts._feed_cache_get("u-gen") is fresh


def test_invalidate_bumps_only_that_users_generation():
    ts.invalidate_feed_cache("u-a")
    ts.invalidate_feed_cache("u-a")
    assert ts._feed_generation == {"u-a": 2}
    assert ts._feed_generation.get("u-b", 0) == 0


@pytest.mark.asyncio
async def test_the_leaders_late_finally_never_pops_a_successors_entry(monkeypatch):
    """A leader whose client disconnected is superseded by a joiner that installs its
    own future; the old leader's `finally` must leave that entry alone (identity check).
    Driven through the real `get_tracking_feed`: cancel the leader while a joiner waits."""
    release = asyncio.Event()
    entries = []
    result = TrackingFeedResponse()

    async def _build(self, user_id, *, generation=None):
        entries.append(generation)
        await release.wait()
        ts._feed_cache_set(user_id, result, generation=generation)
        return result

    monkeypatch.setattr(TrackingService, "_build_tracking_feed", _build)
    leader = asyncio.create_task(TrackingService().get_tracking_feed("u-late"))
    await asyncio.sleep(0)
    joiner = asyncio.create_task(TrackingService().get_tracking_feed("u-late"))
    await asyncio.sleep(0)
    leader.cancel()
    for _ in range(6):              # cancel → leader's finally → joiner wakes → takes over
        await asyncio.sleep(0)
    successor = ts._feed_inflight.get("u-late")
    assert successor is not None, "the joiner must have become the leader"
    release.set()
    assert await joiner is result
    assert "u-late" not in ts._feed_inflight and "u-late" not in ts._feed_inflight_generation
    assert entries == [0, 0]


@pytest.mark.asyncio
async def test_release_only_pops_its_own_future():
    """The identity check itself: a late release from a superseded leader is a no-op."""
    loop = asyncio.get_running_loop()
    mine, successor = loop.create_future(), loop.create_future()
    ts._feed_inflight["u-rel"] = successor
    ts._feed_inflight_generation["u-rel"] = 7
    ts._release_feed_inflight("u-rel", mine)
    assert ts._feed_inflight["u-rel"] is successor, "a superseded leader must not pop its successor"
    assert ts._feed_inflight_generation["u-rel"] == 7
    ts._release_feed_inflight("u-rel", successor)
    assert "u-rel" not in ts._feed_inflight and "u-rel" not in ts._feed_inflight_generation


def test_the_generation_table_is_bounded_and_spares_in_flight_users(monkeypatch):
    monkeypatch.setattr(ts, "_FEED_GENERATION_MAX_ENTRIES", 3)
    for u in ("a", "b", "c"):
        ts.invalidate_feed_cache(u)
    ts._feed_inflight["a"] = object()          # a build is running for "a"
    ts.invalidate_feed_cache("d")              # 4th entry → evict the oldest that is not in flight
    assert set(ts._feed_generation) == {"a", "c", "d"}, ts._feed_generation
    assert ts._feed_generation["a"] == 1, "an in-flight user keeps its generation"
    ts.invalidate_feed_cache("a")              # move-to-end + bump, no eviction needed
    assert ts._feed_generation["a"] == 2
    ts._feed_inflight.clear()



# ── 6. the sector backfill runs INSIDE the feed gather (2026-10-08) ──────────
#
# It used to be awaited BEFORE the gather: one serial ~100 ms `company_profile_cache`
# read in front of every build while any equity/ETF row lacked a sector. It is now the
# gather's 7th, LAST member. These pin the three ways that move can go wrong: it still
# runs serially, it is detached (so the merge reads the unhealed row), or a raise in it
# takes the feed down (`section_names` too short → IndexError in the logging loop).


_HEAL_WATCHLIST = [
    {"id": 1, "ticker": "AAPL", "company_name": "Apple", "asset_type": "stock", "sector": None},
    {"id": 2, "ticker": "SPY", "company_name": "SPDR S&P 500", "asset_type": "etf", "sector": None},
]


def _stub_sections(monkeypatch, *, backfill, quotes=None):
    """Stub the six data sections and the backfill; the watchlist read stays REAL
    (against `_FakeSupabase`), so the order watchlist → gather is exercised."""
    async def _quotes(self, tickers):
        if quotes is not None:
            return await quotes(tickers)
        return {t: {"symbol": t, "price": 10.0, "changePercentage": 1.0} for t in tickers}
    async def _spark(self, tickers, asset_types=None): return {}
    async def _earn(self, tickers): return []
    async def _whale(self, tickers): return []
    async def _analyst(self, tickers): return []
    async def _insider(self, tickers, asset_types=None): return []
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _spark),
                     ("_get_earnings_alerts", _earn), ("_get_whale_trade_alerts", _whale),
                     ("_get_analyst_rating_alerts", _analyst),
                     ("_get_insider_transaction_alerts", _insider),
                     ("_backfill_classification", backfill)]:
        monkeypatch.setattr(TrackingService, name, fn)


def _watchlist_copy():
    return [dict(row) for row in _HEAL_WATCHLIST]


@pytest.mark.asyncio
async def test_the_sector_backfill_runs_inside_the_gather(monkeypatch):
    """The backfill can only finish once the quotes section has STARTED. Awaited
    before the gather (the old shape), it would wait for an event nothing sets yet,
    time out, and the build would raise."""
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(_watchlist_copy()))
    quotes_started = asyncio.Event()
    backfill_calls = []

    async def _quotes(tickers):
        quotes_started.set()
        return {t: {"symbol": t, "price": 10.0, "changePercentage": 1.0} for t in tickers}

    async def _backfill(self, user_id, watchlist):
        backfill_calls.append(user_id)
        await asyncio.wait_for(quotes_started.wait(), 1.0)
        for item in watchlist:
            item["sector"] = "Technology"

    _stub_sections(monkeypatch, backfill=_backfill, quotes=_quotes)
    feed = await TrackingService().get_tracking_feed("u-heal")

    assert backfill_calls == ["u-heal"]
    assert [a.sector for a in feed.assets] == ["Technology", "Technology"]


@pytest.mark.asyncio
async def test_the_merge_sees_a_late_heal(monkeypatch):
    """The other sections answer at once; the heal lands later. The merge runs after
    the WHOLE gather, so it still serves the healed value (a detached backfill would
    not)."""
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(_watchlist_copy()))

    async def _backfill(self, user_id, watchlist):
        await asyncio.sleep(0.02)
        watchlist[0]["sector"] = "Technology"
        watchlist[0]["country"] = "US"

    _stub_sections(monkeypatch, backfill=_backfill)
    feed = await TrackingService().get_tracking_feed("u-late-heal")

    by_ticker = {a.ticker: a for a in feed.assets}
    assert by_ticker["AAPL"].sector == "Technology"
    assert by_ticker["AAPL"].country == "US"
    assert by_ticker["SPY"].sector is None, "an unhealed row stays honestly null"


@pytest.mark.asyncio
async def test_a_raising_backfill_does_not_cost_the_feed(monkeypatch, caplog):
    """A raise lands in results[6]. Every row is still served (unclassified), the feed
    is still cached, and the ERROR names the section and the user — without
    `sector_backfill` in `section_names` the logging loop raised IndexError instead."""
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(_watchlist_copy()))

    async def _backfill(self, user_id, watchlist):
        raise RuntimeError("profile cache exploded")

    _stub_sections(monkeypatch, backfill=_backfill)
    with caplog.at_level(logging.ERROR, logger=ts.logger.name):
        feed = await TrackingService().get_tracking_feed("u-backfill-boom")

    assert [a.ticker for a in feed.assets] == ["AAPL", "SPY"]
    assert all(a.sector is None for a in feed.assets)
    assert all(a.price_known for a in feed.assets), "the other sections are untouched"
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("sector_backfill" in m and "u-backfill-boom" in m and "RuntimeError" in m
               for m in errors), errors
    # The ERROR carries the section's own stack (the gather result keeps __traceback__),
    # so a failure is diagnosable from the log alone.
    section_errs = [r for r in caplog.records
                    if r.levelno == logging.ERROR and "sector_backfill" in r.getMessage()]
    assert len(section_errs) == 1
    exc_info = section_errs[0].exc_info
    assert exc_info and isinstance(exc_info[1], RuntimeError), exc_info
    assert exc_info[2] is not None, "the traceback must reach the log record"
    assert ts._feed_cache_get("u-backfill-boom") is feed, "a cosmetic failure must not stop caching"


@pytest.mark.asyncio
async def test_an_unreadable_watchlist_still_raises_before_any_backfill(monkeypatch):
    """The 503 path is unchanged: the read fails, nothing in the gather starts."""
    class _Boom(_FakeTable):
        def execute(self):
            raise ValueError("column watchlist_items.bogus does not exist")

    class _BoomSupabase:
        def table(self, name):
            return _Boom([])

    monkeypatch.setattr(ts, "get_supabase", lambda: _BoomSupabase())
    backfill_calls = []

    async def _backfill(self, user_id, watchlist):
        backfill_calls.append(user_id)

    _stub_sections(monkeypatch, backfill=_backfill)
    with pytest.raises(WatchlistUnavailableError):
        await TrackingService().get_tracking_feed("u-unreadable")
    assert backfill_calls == []
    assert ts._feed_cache_get("u-unreadable") is None


@pytest.mark.asyncio
async def test_an_empty_watchlist_never_starts_the_backfill(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase([]))
    backfill_calls = []

    async def _backfill(self, user_id, watchlist):
        backfill_calls.append(user_id)

    _stub_sections(monkeypatch, backfill=_backfill)
    feed = await TrackingService().get_tracking_feed("u-empty")
    assert feed.assets == [] and backfill_calls == []


class _HealTable:
    """watchlist read + `company_profile_cache` read + the write-back UPDATE."""

    def __init__(self, sb, name):
        self.sb, self.name = sb, name
        self._patch = None
        self._eqs: List[tuple] = []

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def update(self, patch):
        self._patch = dict(patch)
        return self

    def execute(self):
        if self._patch is not None:
            self.sb.updates.append((self.name, self._patch, list(self._eqs)))
            return type("R", (), {"data": []})()
        if self.name == "watchlist_items":
            return type("R", (), {"data": [dict(r) for r in self.sb.watchlist]})()
        if self.name == "company_profile_cache":
            self.sb.profile_reads += 1
            return type("R", (), {"data": self.sb.profiles})()
        return type("R", (), {"data": []})()


class _HealSupabase:
    def __init__(self, watchlist, profiles):
        self.watchlist, self.profiles = watchlist, profiles
        self.updates: List[tuple] = []
        self.profile_reads = 0

    def table(self, name):
        return _HealTable(self, name)


@pytest.mark.asyncio
async def test_the_real_backfill_heals_and_writes_back_inside_the_gather(monkeypatch):
    """Not stubbed: the real `_backfill_classification` reads the shared profile cache,
    heals the row this request serves, and its write-back has LANDED by the time the
    feed returns (it is awaited inside the gather, never detached)."""
    sb = _HealSupabase(
        _watchlist_copy(),
        [{"ticker": "AAPL", "profile_json": {"sector": "Technology", "country": "US"}},
         {"ticker": "SPY", "profile_json": {"sector": "N/A"}}],     # placeholder: not healed
    )
    monkeypatch.setattr(ts, "get_supabase", lambda: sb)
    _stub_sections(monkeypatch, backfill=TrackingService._backfill_classification)

    feed = await TrackingService().get_tracking_feed("u-real-heal")

    by_ticker = {a.ticker: a for a in feed.assets}
    assert by_ticker["AAPL"].sector == "Technology"
    assert by_ticker["SPY"].sector is None
    assert sb.profile_reads == 1
    assert sb.updates == [
        ("watchlist_items", {"sector": "Technology", "country": "US"},
         [("user_id", "u-real-heal"), ("ticker", "AAPL")]),
    ], sb.updates


@pytest.mark.asyncio
async def test_a_failed_profile_read_serves_rows_unclassified(monkeypatch, caplog):
    """The backfill's own degrade path, now inside the gather: WARNING, rows served
    null, no write-back, the feed intact."""
    class _NoProfiles(_HealSupabase):
        def table(self, name):
            if name == "company_profile_cache":
                raise ConnectionError("profile cache unreachable")
            return super().table(name)

    sb = _NoProfiles(_watchlist_copy(), [])
    monkeypatch.setattr(ts, "get_supabase", lambda: sb)
    _stub_sections(monkeypatch, backfill=TrackingService._backfill_classification)
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        feed = await TrackingService().get_tracking_feed("u-no-profiles")

    assert [a.sector for a in feed.assets] == [None, None]
    assert sb.updates == []
    assert any("sector backfill" in r.getMessage() and "ConnectionError" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
