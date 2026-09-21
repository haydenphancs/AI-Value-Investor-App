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
    yield
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    ts._sparkline_cache.clear()
    ts._insider_cache.clear()


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

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])
        try:
            await asyncio.sleep(0.005)
            return []
        finally:
            live["n"] -= 1

    monkeypatch.setattr(ts, "fetch_chart_data", fake_fetch)
    out = await TrackingService()._get_all_sparklines([f"T{i}" for i in range(60)])
    assert len(out) == 60
    assert 0 < live["max"] <= ts._PER_TICKER_FANOUT_CONCURRENCY, live["max"]


@pytest.mark.asyncio
async def test_sparkline_cache_hits_do_not_take_a_gate_slot(monkeypatch):
    """A cached ticker must answer without touching the upstream at all."""
    called = []

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        called.append(ticker); return []

    monkeypatch.setattr(ts, "fetch_chart_data", fake_fetch)
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

