"""The Tracking feed's market-wide earnings calendar is memoised and deduped (2026-10-08).

`_get_earnings_alerts` used to download FMP's WHOLE 15-day `earnings-calendar` (~430 KB,
thousands of rows) on every feed build — every cold open and every 30 s poll of every
user — to keep the handful of rows on one watchlist. `_cached_earnings_calendar` now keeps
one answer for `EARNINGS_CALENDAR_TTL` (30 min), collapses concurrent misses to one
download, and must keep every degraded path honest:

  * `[]`, a non-list answer and a failure are NEVER memoised (a cached empty reads exactly
    like "nobody on this watchlist reports this fortnight");
  * a joiner whose leader was CANCELLED (a client disconnect) TAKES OVER the download — it
    never serves its own user a feed with no earnings alerts (that feed is then cached for
    30 s and saved by the iOS snapshot as a healthy answer);
  * the memo is keyed on the client object, so one fake's rows never reach another;
  * a calendar at FMP's silent 4,000-row cap is reported at ERROR once per ET day.

Hermetic: every client is an inline fake; the module state is reset around each test.
"""
from __future__ import annotations

import asyncio
import gc
import logging
from datetime import date, datetime, timedelta
from typing import Any, List

import pytest

import app.services.tracking_service as ts
from app.integrations.fmp import EmptyAfterFailure, FMPUnavailableException
from app.services.tracking_service import TrackingService


@pytest.fixture(autouse=True)
def _clean():
    ts.reset_earnings_calendar_cache()
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    ts._feed_generation.clear()
    ts._feed_inflight_generation.clear()
    yield
    ts.reset_earnings_calendar_cache()
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    ts._feed_generation.clear()
    ts._feed_inflight_generation.clear()


def _row(symbol: str, days_ahead: int = 3) -> dict:
    day = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    return {
        "symbol": symbol, "date": day, "time": "amc", "companyName": f"{symbol} Inc",
        "epsEstimated": 1.25, "revenueEstimated": 2.5e9,
    }


class _CalendarFMP:
    """Records each `get_earnings_calendar` call.

    ``answers``: a queue of values (or exceptions to raise), one per call; when it runs
    dry the default rows are served. ``release``: an Event every call waits on (so a test
    can hold a download open), else ``delay`` seconds.
    """

    def __init__(self, rows=None, *, answers=None, delay: float = 0.0, gated: bool = False):
        self.rows = rows if rows is not None else [_row("AAPL"), _row("MSFT")]
        self.answers: List[Any] = list(answers or [])
        self.delay = delay
        self.release = asyncio.Event() if gated else None
        self.calls: List[tuple] = []
        self.started = asyncio.Event()

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls.append((from_date, to_date))
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        elif self.delay:
            await asyncio.sleep(self.delay)
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return [dict(r) for r in self.rows]


def _svc(fmp) -> TrackingService:
    svc = TrackingService.__new__(TrackingService)
    svc.fmp = fmp
    return svc


async def _wait_for(predicate, what: str, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for: {what}")
        await asyncio.sleep(0.001)


def _tickers(alerts) -> List[str]:
    return [a.ticker for a in alerts]


# ── the memo ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_builds_share_one_download_across_services_on_one_client():
    fmp = _CalendarFMP()
    a1 = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    a2 = await _svc(fmp)._get_earnings_alerts(["aapl", "TSLA"])
    a3 = await _svc(fmp)._get_earnings_alerts(["MSFT"])
    assert len(fmp.calls) == 1, fmp.calls
    assert _tickers(a1) == ["AAPL"] and _tickers(a2) == ["AAPL"] and _tickers(a3) == ["MSFT"]


@pytest.mark.asyncio
async def test_the_window_asked_for_is_unchanged():
    """Same `datetime.now()` strings as before the memo: today → today + 14 days."""
    fmp = _CalendarFMP()
    before = datetime.now()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    after = datetime.now()
    (from_date, to_date), = fmp.calls
    assert from_date in {before.strftime("%Y-%m-%d"), after.strftime("%Y-%m-%d")}
    assert to_date in {(before + timedelta(days=14)).strftime("%Y-%m-%d"),
                       (after + timedelta(days=14)).strftime("%Y-%m-%d")}


@pytest.mark.asyncio
async def test_an_empty_answer_is_served_but_not_memoised(caplog):
    fmp = _CalendarFMP(answers=[[]])
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        first = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert first == []
    assert ts._earnings_calendar_slot is None
    assert any("NOT memoised" in r.getMessage() for r in caplog.records)
    second = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 2, "the next build must ask again, not replay the empty answer"
    assert _tickers(second) == ["AAPL"]


@pytest.mark.asyncio
async def test_an_empty_after_failure_answer_is_not_memoised_and_says_so(caplog):
    fmp = _CalendarFMP(answers=[EmptyAfterFailure("earnings-calendar 429")])
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_slot is None
    assert any("failed fetch" in r.getMessage() for r in caplog.records)
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 2


@pytest.mark.asyncio
async def test_a_failure_is_not_memoised():
    fmp = _CalendarFMP(answers=[FMPUnavailableException("503 from FMP")])
    assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_slot is None and ts._earnings_calendar_inflight == {}
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 2


@pytest.mark.asyncio
async def test_a_non_list_answer_is_refused_and_not_memoised():
    fmp = _CalendarFMP(answers=[{"symbol": "AAPL", "date": "2026-10-10"}])
    with pytest.raises(TypeError):
        await ts._cached_earnings_calendar(fmp, "2026-10-08", "2026-10-22")
    assert ts._earnings_calendar_slot is None and ts._earnings_calendar_inflight == {}
    # Through the alert builder the refusal degrades to no alerts, never a crash.
    fmp.answers = [None]
    assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_slot is None
    assert len(fmp.calls) == 2


@pytest.mark.asyncio
async def test_the_memo_is_keyed_on_the_client():
    """One fake's rows never reach another — the strict-xfail reschedule test builds its
    own client and would otherwise be served a neighbour's calendar."""
    a = _CalendarFMP(rows=[_row("AAPL")])
    b = _CalendarFMP(rows=[_row("NVDA")])
    assert _tickers(await _svc(a)._get_earnings_alerts(["AAPL", "NVDA"])) == ["AAPL"]
    assert _tickers(await _svc(b)._get_earnings_alerts(["AAPL", "NVDA"])) == ["NVDA"]
    assert len(a.calls) == 1 and len(b.calls) == 1


@pytest.mark.asyncio
async def test_the_memo_expires(monkeypatch):
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    slot = ts._earnings_calendar_slot
    monkeypatch.setattr(ts, "_earnings_calendar_slot",
                        (slot[0], slot[1], slot[2], slot[3] - ts.EARNINGS_CALENDAR_TTL - 1,
                         slot[4]))
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 2, "an expired memo must refetch"


@pytest.mark.asyncio
async def test_a_zero_ttl_never_serves_from_memory(monkeypatch):
    monkeypatch.setattr(ts, "EARNINGS_CALENDAR_TTL", 0)
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 2


@pytest.mark.asyncio
async def test_the_window_is_part_of_the_key_and_the_memo_is_one_slot():
    fmp = _CalendarFMP()
    await ts._cached_earnings_calendar(fmp, "2026-10-08", "2026-10-22")
    await ts._cached_earnings_calendar(fmp, "2026-10-08", "2026-10-22")
    assert len(fmp.calls) == 1
    await ts._cached_earnings_calendar(fmp, "2026-10-09", "2026-10-23")   # the next day
    assert len(fmp.calls) == 2, "a new window is a new download"
    await ts._cached_earnings_calendar(fmp, "2026-10-08", "2026-10-22")
    assert len(fmp.calls) == 3, "ONE slot: the old window was replaced, memory stays bounded"


@pytest.mark.asyncio
async def test_the_shared_rows_are_never_mutated_by_the_alert_builder():
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    stored = ts._earnings_calendar_slot[4]
    snapshot = [dict(r) for r in stored]
    await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    assert stored == snapshot


@pytest.mark.asyncio
async def test_a_malformed_row_in_the_memoised_answer_does_not_cost_every_alert():
    """The answer now lives for 30 min, so one bad row must not blank every build's
    earnings alerts for that long (a non-dict row used to raise AttributeError → the
    whole section degraded to no alerts)."""
    raw = [None, "garbage", 7, {"symbol": 12345}, {"symbol": None}, _row("AAPL")]
    fmp = _CalendarFMP(answers=[raw])
    first = await _svc(fmp)._get_earnings_alerts(["AAPL", "12345"])
    second = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert _tickers(first) == ["12345", "AAPL"]
    assert _tickers(second) == ["AAPL"]
    assert len(fmp.calls) == 1, "the (non-empty) answer is memoised as served"


@pytest.mark.asyncio
async def test_a_malformed_dict_row_does_not_cost_every_alert(caplog):
    """The non-dict guard alone left malformed DICT rows on the old all-or-nothing path:
    a non-str `date` (strptime TypeError), a non-str `companyName` (AlertResponse
    ValidationError) or a field whose processing raises (`time: 5` → AttributeError)
    returned [] for the whole section — and the memo kept that for 30 min. Each bad row
    now degrades on its own; the good row LAST (after every bad one) still gets its alert."""
    day = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    raw = [
        # non-str date → the alert survives, honestly undated
        {"symbol": "DINT", "date": 20261010, "time": "bmo", "companyName": "Dint Co"},
        {"symbol": "DLST", "date": [day], "time": "amc", "companyName": "Dlst Co"},
        # non-str companyName → falls back to the symbol, never a coerced/garbage name
        {"symbol": "NINT", "date": day, "time": "amc", "companyName": 12345},
        {"symbol": "NDCT", "date": day, "time": "amc", "companyName": {"en": "Ndct"}},
        # a field whose processing raises → only THIS row is skipped, and it is logged
        {"symbol": "TINT", "date": day, "time": 5, "companyName": "Tint Co"},
        _row("AAPL"),
    ]
    fmp = _CalendarFMP(answers=[raw])
    watch = ["DINT", "DLST", "NINT", "NDCT", "TINT", "AAPL"]
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        first = await _svc(fmp)._get_earnings_alerts(watch)
    by = {a.ticker: a for a in first}

    assert _tickers(first) == ["DINT", "DLST", "NINT", "NDCT", "AAPL"]
    good = by["AAPL"]
    assert good.company_name == "AAPL Inc"
    assert good.day is not None and good.month is not None
    assert good.report_time == "after_close"
    assert good.eps_estimate == 1.25 and good.revenue_estimate == 2.5e9

    for sym in ("DINT", "DLST"):
        a = by[sym]
        assert a.day is None and a.month is None, "an unreadable date is absent, not guessed"
        assert a.description.startswith(f"{sym} reports earnings")
        assert " on " not in a.description
    assert by["DINT"].report_time == "before_open"
    assert by["NINT"].company_name == "NINT"
    assert by["NDCT"].company_name == "NDCT"

    skips = [r.getMessage() for r in caplog.records
             if r.levelno == logging.WARNING and "earnings row skipped" in r.getMessage()]
    assert len(skips) == 1 and "TINT" in skips[0] and "AttributeError" in skips[0], skips
    assert not [r for r in caplog.records if "Earnings alerts failed" in r.getMessage()]

    second = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert _tickers(second) == ["AAPL"], "the memoised rows still serve the good alert"
    assert len(fmp.calls) == 1


@pytest.mark.asyncio
async def test_the_fetch_logs_its_row_count_and_elapsed_time(caplog):
    fmp = _CalendarFMP()
    with caplog.at_level(logging.INFO, logger=ts.logger.name):
        await _svc(fmp)._get_earnings_alerts(["AAPL"])
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("earnings calendar" in m and "2 rows" in m and " ms" in m for m in infos), infos


# ── the in-flight dedup ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_misses_collapse_to_one_download():
    fmp = _CalendarFMP(delay=0.02)
    results = await asyncio.gather(
        *[_svc(fmp)._get_earnings_alerts(["AAPL"]) for _ in range(5)]
    )
    assert len(fmp.calls) == 1, fmp.calls
    assert all(_tickers(r) == ["AAPL"] for r in results)
    assert ts._earnings_calendar_inflight == {}


@pytest.mark.asyncio
async def test_a_cancelled_leader_hands_the_download_to_a_joiner():
    """The joiner must TAKE OVER, not return []: an alert-less feed is cached for 30 s
    and the iOS snapshot would persist it as a healthy answer."""
    fmp = _CalendarFMP(gated=True)
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    joiner = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.sleep(0.01)                       # park the joiner on the shared future
    assert len(fmp.calls) == 1, "the joiner must join, not start its own download"

    leader.cancel()
    await _wait_for(lambda: len(fmp.calls) == 2, "the joiner taking over the download")
    fmp.release.set()
    alerts = await asyncio.wait_for(joiner, 2.0)

    assert _tickers(alerts) == ["AAPL"]
    assert leader.cancelled()
    assert ts._earnings_calendar_inflight == {}
    assert ts._earnings_calendar_slot is not None, "the takeover's answer is memoised"


@pytest.mark.asyncio
async def test_two_parked_joiners_and_a_cancelled_leader_both_get_the_alert():
    """One joiner takes over; the other joins the takeover (still ONE more download)."""
    fmp = _CalendarFMP(gated=True)
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    j1 = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    j2 = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["MSFT"]))
    await asyncio.sleep(0.01)

    leader.cancel()
    await _wait_for(lambda: len(fmp.calls) == 2, "a joiner taking over")
    await asyncio.sleep(0.01)
    assert len(fmp.calls) == 2, "the second joiner joins the takeover, it does not lead too"
    fmp.release.set()
    r1, r2 = await asyncio.wait_for(asyncio.gather(j1, j2), 2.0)
    assert _tickers(r1) == ["AAPL"] and _tickers(r2) == ["MSFT"]
    assert ts._earnings_calendar_inflight == {}


@pytest.mark.asyncio
async def test_a_cancelled_leader_with_no_joiner_leaves_nothing_behind():
    fmp = _CalendarFMP(gated=True)
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert ts._earnings_calendar_inflight == {} and ts._earnings_calendar_slot is None
    fmp.release.set()
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 2


@pytest.mark.asyncio
async def test_a_cancelled_joiner_does_not_cancel_the_shared_download():
    fmp = _CalendarFMP(gated=True)
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    j1 = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    j2 = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["MSFT"]))
    await asyncio.sleep(0.01)
    j1.cancel()
    await asyncio.sleep(0.01)
    fmp.release.set()
    lead, other = await asyncio.wait_for(asyncio.gather(leader, j2), 2.0)
    assert _tickers(lead) == ["AAPL"] and _tickers(other) == ["MSFT"]
    assert j1.cancelled()
    assert len(fmp.calls) == 1


@pytest.mark.asyncio
async def test_a_leader_failure_reaches_parked_joiners_and_is_not_memoised():
    """A failure is shared (that is the point of the dedup) but never memoised: both
    degrade to no alerts without hanging, and the next build asks again."""
    fmp = _CalendarFMP(gated=True, answers=[FMPUnavailableException("503")])
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    joiner = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.sleep(0.01)
    fmp.release.set()
    r1, r2 = await asyncio.wait_for(asyncio.gather(leader, joiner), 2.0)
    assert r1 == [] and r2 == [] and len(fmp.calls) == 1
    assert ts._earnings_calendar_slot is None and ts._earnings_calendar_inflight == {}
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 2


@pytest.mark.asyncio
async def test_a_joinerless_failure_is_not_reported_as_never_retrieved():
    loop = asyncio.get_running_loop()
    seen: list = []
    loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
    try:
        fmp = _CalendarFMP(answers=[FMPUnavailableException("503")])
        with pytest.raises(FMPUnavailableException):
            await ts._cached_earnings_calendar(fmp, "2026-10-08", "2026-10-22")
        gc.collect()
        await asyncio.sleep(0)
        assert not [c for c in seen if "never retrieved" in str(c.get("message", ""))], seen
    finally:
        loop.set_exception_handler(None)


# ── through the feed: one user's disconnect never strips another user's alert ──


class _Table:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": [dict(r) for r in self._rows]})()


class _Supabase:
    def __init__(self, watchlist): self._w = watchlist
    def table(self, name):
        return _Table(self._w if name == "watchlist_items" else [])


@pytest.mark.asyncio
async def test_a_cancelled_feed_build_never_strips_another_users_earnings_alert(monkeypatch):
    """User A's build leads the calendar download; user B's build joins it. A's client
    goes away. B's feed must still carry the AAPL earnings alert — and that is the feed
    cached for B's next 30 s."""
    watchlist = [{"id": 1, "ticker": "AAPL", "company_name": "Apple",
                  "asset_type": "stock", "sector": "Technology"}]
    monkeypatch.setattr(ts, "get_supabase", lambda: _Supabase(watchlist))
    whale_calls: List[int] = []

    async def _quotes(self, tickers):
        return {t: {"symbol": t, "price": 10.0, "changePercentage": 1.0} for t in tickers}
    async def _spark(self, tickers, asset_types=None): return {}
    async def _whale(self, tickers):
        whale_calls.append(1)      # runs right after the earnings member parked
        return []
    async def _analyst(self, tickers): return []
    async def _insider(self, tickers, asset_types=None): return []
    async def _backfill(self, user_id, wl): return None
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _spark),
                     ("_get_whale_trade_alerts", _whale),
                     ("_get_analyst_rating_alerts", _analyst),
                     ("_get_insider_transaction_alerts", _insider),
                     ("_backfill_classification", _backfill)]:
        monkeypatch.setattr(TrackingService, name, fn)

    fmp = _CalendarFMP(rows=[_row("AAPL")], gated=True)
    build_a = asyncio.create_task(_svc(fmp).get_tracking_feed("u-a"))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    build_b = asyncio.create_task(_svc(fmp).get_tracking_feed("u-b"))
    await _wait_for(lambda: len(whale_calls) == 2, "user B's gather reaching the calendar join")
    await asyncio.sleep(0)
    assert len(fmp.calls) == 1, "B must join A's download"

    build_a.cancel()
    await _wait_for(lambda: len(fmp.calls) == 2, "B taking over the download")
    fmp.release.set()
    feed_b = await asyncio.wait_for(build_b, 2.0)

    assert [(a.type, a.ticker) for a in feed_b.alerts] == [("earnings", "AAPL")]
    cached = ts._feed_cache_get("u-b")
    assert cached is feed_b and [a.ticker for a in cached.alerts] == ["AAPL"]
    assert build_a.cancelled()


# ── truncation: ERROR once per ET day ─────────────────────────────────────────


def _full_calendar(n: int) -> List[dict]:
    rows = [_row(f"S{i}", days_ahead=10) for i in range(n - 1)]
    rows.append(_row("AAPL"))
    return rows


@pytest.mark.asyncio
async def test_a_truncated_calendar_is_an_error_once_per_et_day(monkeypatch, caplog):
    monkeypatch.setattr(ts, "EARNINGS_CALENDAR_TTL", 0)       # every build refetches
    day = {"d": date(2026, 10, 15)}
    monkeypatch.setattr(ts, "_et_today", lambda: day["d"])
    fmp = _CalendarFMP(rows=_full_calendar(ts._EARNINGS_TRUNCATION_ROWS))

    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        first = await _svc(fmp)._get_earnings_alerts(["AAPL"])
        second = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 2
    assert _tickers(first) == ["AAPL"] and _tickers(second) == ["AAPL"], \
        "a truncated calendar still builds the alerts it has"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "silent cap" in r.getMessage()]
    assert len(errors) == 1 and "TRUNCATED" in errors[0].getMessage(), errors
    assert len(warnings) == 1, "the repeat is still visible, at WARNING"

    caplog.clear()
    day["d"] = date(2026, 10, 16)                              # the next ET day
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


@pytest.mark.asyncio
async def test_a_calendar_below_the_cap_is_not_reported(caplog):
    fmp = _CalendarFMP(rows=_full_calendar(ts._EARNINGS_TRUNCATION_ROWS - 1))
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert not [r for r in caplog.records if "silent cap" in r.getMessage()]


def test_the_truncation_cap_matches_the_other_calendar_readers():
    from app.services import earnings_window_service as ews
    assert ts._EARNINGS_TRUNCATION_ROWS == ews._TRUNCATION_ROWS


def test_reset_clears_every_piece_of_state(monkeypatch):
    monkeypatch.setattr(ts, "_earnings_calendar_slot",
                        (object(), "a", "b", 0.0, [{"symbol": "X"}]))
    ts._earnings_calendar_inflight[(1, "a", "b")] = object()      # type: ignore[assignment]
    monkeypatch.setattr(ts, "_earnings_truncation_error_day", date(2026, 1, 1))
    ts.reset_earnings_calendar_cache()
    assert ts._earnings_calendar_slot is None
    assert ts._earnings_calendar_inflight == {}
    assert ts._earnings_truncation_error_day is None
