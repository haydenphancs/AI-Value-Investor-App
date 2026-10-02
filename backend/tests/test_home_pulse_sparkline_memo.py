"""The finished-session sparkline memo (`HomeDashboardService._spark_memo`).

WHY THIS EXISTS — round-3 review of the instant-first-Home-paint plan (2026-10-01).

The Home warmer rebuilds the Market Pulse every ~40-50 s around the clock, and each build
fetched five uncached 5-min intraday series (`fetch_chart_data(..., "1D")`: 3 days plus a
7-day indicator warm-up, ~7-8 trading days, ~80-95 KB each) to draw only the latest regular
session. Outside 09:30-16:00 those regular-hours bars are final, so ~20 GB a month of the
download was the same bytes again. A per-(symbol, extended_hours) memo now serves a
finished session's series until the next open; in the regular session, and for every 24/7
series, the fetch is exactly what it was.

Hermetic: `fetch_chart_data` is replaced by a counting fake and `hds.time` by a settable
clock; the memo dicts are fresh per test.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List
from zoneinfo import ZoneInfo

import pytest

import app.services.home_dashboard_service as hds
from app.services.home_dashboard_service import HomeDashboardService

_ET = ZoneInfo("America/New_York")
_LOGGER = "app.services.home_dashboard_service"
_EMPTY = ([], *hds.FULL_SPAN)


def _et(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> float:
    return datetime(y, m, d, hh, mm, ss, tzinfo=_ET).timestamp()


WED = date(2026, 9, 30)
THU = date(2026, 10, 1)
FRI = date(2026, 10, 2)
HALF_DAY = date(2026, 11, 27)        # the Friday after Thanksgiving: the bell is 13:00


def _bars(day: date, *, last: str = "15:55", base: float = 100.0) -> List[Dict]:
    """One regular session of 5-min bars, 09:30 through ``last``, after a warm-up day."""
    rows: List[Dict] = []
    warm = day - timedelta(days=1)
    rows.append({"date": f"{warm} 15:50:00", "close": base - 5})
    rows.append({"date": f"{warm} 15:55:00", "close": base - 4})
    moment = datetime(day.year, day.month, day.day, 9, 30)
    end = datetime.strptime(f"{day} {last}", "%Y-%m-%d %H:%M")
    i = 0
    while moment <= end:
        rows.append({"date": moment.strftime("%Y-%m-%d %H:%M:%S"), "close": base + i * 0.25})
        moment += timedelta(minutes=5)
        i += 1
    return rows


class _Clock:
    def __init__(self, now: float):
        self.now = float(now)

    def time(self) -> float:
        return self.now


class _Feed:
    """Counting stand-in for `fetch_chart_data`. ``bars[symbol]`` is a list, or an
    Exception instance to raise; ``yields[symbol]`` makes that fetch take its time."""

    def __init__(self):
        self.calls: List[tuple] = []
        self.bars: Dict[str, object] = {}
        self.yields: Dict[str, int] = {}

    async def __call__(self, fmp, symbol, range_code, interval=None, extended_hours=False):
        self.calls.append((symbol, range_code, extended_hours))
        for _ in range(self.yields.get(symbol, 0)):
            await asyncio.sleep(0)
        value = self.bars.get(symbol, [])
        if isinstance(value, BaseException):
            raise value
        return [dict(b) for b in value]   # a fresh copy, like a real response

    def count(self, symbol: str) -> int:
        return sum(1 for c in self.calls if c[0] == symbol)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in ("_spark_memo", "_spark_memo_noted", "_cache", "_inflight"):
        monkeypatch.setattr(HomeDashboardService, name, {})


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock(_et(2026, 9, 30, 17, 0))
    monkeypatch.setattr(hds, "time", SimpleNamespace(time=c.time, monotonic=time.monotonic))
    return c


@pytest.fixture
def feed(monkeypatch) -> _Feed:
    f = _Feed()
    monkeypatch.setattr(hds, "fetch_chart_data", f)
    return f


def _svc() -> HomeDashboardService:
    svc = HomeDashboardService.__new__(HomeDashboardService)
    svc.fmp = None
    return svc


def _memo_days() -> Dict[str, date]:
    return {key[0]: memo.session_day for key, memo in HomeDashboardService._spark_memo.items()}


# ── 1. the memo's life: after the close, through the night, until the open ──────────────


@pytest.mark.asyncio
async def test_a_finished_session_is_fetched_once_and_reused_until_the_next_open(clock, feed, caplog):
    svc = _svc()
    feed.bars["SPY"] = _bars(WED)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        first = await svc._fetch_sparkline("SPY")
    assert first[0] and feed.count("SPY") == 1
    assert _memo_days() == {"SPY": WED}
    assert any("memoized for the 2026-09-30 session" in r.getMessage()
               and r.levelno == logging.INFO for r in caplog.records)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        for moment in (
            _et(2026, 9, 30, 21, 0),            # the same evening
            _et(2026, 10, 1, 2, 0),             # overnight
            _et(2026, 10, 1, 8, 0),             # Thursday pre-market: still Wednesday's bars
            _et(2026, 10, 1, 9, 29, 59),
        ):
            clock.now = moment
            assert await svc._fetch_sparkline("SPY") == first
    assert feed.count("SPY") == 1, "a finished session's bars were downloaded again"
    assert any("served from the memo" in r.getMessage() and r.levelno == logging.DEBUG
               for r in caplog.records)

    # 09:30: a new regular session has started — fetch, every build, as before.
    feed.bars["SPY"] = _bars(THU, last="09:30")
    clock.now = _et(2026, 10, 1, 9, 30)
    await svc._fetch_sparkline("SPY")
    clock.now = _et(2026, 10, 1, 9, 31)
    await svc._fetch_sparkline("SPY")
    assert feed.count("SPY") == 3


@pytest.mark.asyncio
async def test_the_regular_session_always_fetches(clock, feed):
    svc = _svc()
    clock.now = _et(2026, 9, 30, 11, 0)
    feed.bars["SPY"] = _bars(WED, last="10:55")
    for _ in range(3):
        assert (await svc._fetch_sparkline("SPY"))[0]
    assert feed.count("SPY") == 3
    assert HomeDashboardService._spark_memo == {}


@pytest.mark.asyncio
async def test_a_regular_session_fetch_is_never_stored_even_when_it_shows_the_last_session(
    clock, feed
):
    """At 09:30:05 the feed may not have today's first bar yet, so the series is still the
    finished previous session — which passes every other rule. It is not stored: nothing
    fetched in the regular session is."""
    svc = _svc()
    clock.now = _et(2026, 10, 1, 9, 30, 5)
    feed.bars["SPY"] = _bars(WED)
    assert (await svc._fetch_sparkline("SPY"))[0]
    assert HomeDashboardService._spark_memo == {}


@pytest.mark.asyncio
async def test_a_fetch_inside_the_settle_delay_is_never_memoized(clock, feed):
    """A closing bar can land late; until 10 min after the bell nothing is frozen."""
    svc = _svc()
    feed.bars["SPY"] = _bars(WED)
    for moment in (_et(2026, 9, 30, 16, 0, 30), _et(2026, 9, 30, 16, 9, 59)):
        clock.now = moment
        await svc._fetch_sparkline("SPY")
        assert HomeDashboardService._spark_memo == {}
    clock.now = _et(2026, 9, 30, 16, 10)
    await svc._fetch_sparkline("SPY")
    assert _memo_days() == {"SPY": WED}
    clock.now = _et(2026, 9, 30, 16, 30)
    await svc._fetch_sparkline("SPY")
    assert feed.count("SPY") == 3


def test_the_read_side_refuses_a_memo_fetched_inside_the_settle_delay():
    """The store refuses it already; the read checks again rather than trust the memo."""
    memo = hds._SparkMemo(
        session_day=WED, fetched_at=_et(2026, 9, 30, 16, 5), series=(1.0, 2.0),
        span_from=0.0, span_to=1.0,
    )
    assert "settle delay" in hds._spark_memo_problem(memo, _et(2026, 9, 30, 18, 0))
    ok = hds._SparkMemo(
        session_day=WED, fetched_at=_et(2026, 9, 30, 16, 10), series=(1.0, 2.0),
        span_from=0.0, span_to=1.0,
    )
    assert hds._spark_memo_problem(ok, _et(2026, 9, 30, 18, 0)) is None


@pytest.mark.asyncio
async def test_the_next_completed_session_replaces_the_memo(clock, feed):
    svc = _svc()
    feed.bars["SPY"] = _bars(WED, base=100.0)
    feed.bars["DIA"] = _bars(WED, base=400.0)
    wed_spy = await svc._fetch_sparkline("SPY")
    await svc._fetch_sparkline("DIA")
    assert _memo_days() == {"SPY": WED, "DIA": WED}

    # Thursday after its close: Wednesday's memo no longer describes the latest session.
    feed.bars["SPY"] = _bars(THU, base=200.0)
    clock.now = _et(2026, 10, 1, 16, 30)
    thu_spy = await svc._fetch_sparkline("SPY")
    assert feed.count("SPY") == 2 and thu_spy != wed_spy
    assert thu_spy[0][0] == 200.0
    # The store dropped every older-session entry (DIA's Wednesday memo included).
    assert _memo_days() == {"SPY": THU}
    clock.now = _et(2026, 10, 1, 20, 0)
    assert await svc._fetch_sparkline("SPY") == thu_spy
    assert feed.count("SPY") == 2


@pytest.mark.asyncio
async def test_a_half_day_memo_starts_ten_minutes_after_its_13_00_close(clock, feed):
    svc = _svc()
    feed.bars["SPY"] = _bars(HALF_DAY, last="12:55")
    clock.now = _et(2026, 11, 27, 13, 5)
    await svc._fetch_sparkline("SPY")
    assert HomeDashboardService._spark_memo == {}
    clock.now = _et(2026, 11, 27, 13, 10)
    served = await svc._fetch_sparkline("SPY")
    assert _memo_days() == {"SPY": HALF_DAY}
    for moment in (
        _et(2026, 11, 27, 15, 0),    # what would be the regular session on a full day
        _et(2026, 11, 28, 12, 0),    # Saturday
        _et(2026, 11, 30, 8, 0),     # Monday pre-market
    ):
        clock.now = moment
        assert await svc._fetch_sparkline("SPY") == served
    assert feed.count("SPY") == 2
    clock.now = _et(2026, 11, 30, 9, 30)
    await svc._fetch_sparkline("SPY")
    assert feed.count("SPY") == 3


@pytest.mark.asyncio
async def test_a_holiday_keeps_the_previous_session(clock, feed):
    """Labor Day (Mon 2026-09-07) has no session: Friday's bars stay the latest."""
    svc = _svc()
    feed.bars["SPY"] = _bars(date(2026, 9, 4))
    clock.now = _et(2026, 9, 5, 12, 0)
    served = await svc._fetch_sparkline("SPY")
    clock.now = _et(2026, 9, 7, 11, 0)
    assert await svc._fetch_sparkline("SPY") == served
    clock.now = _et(2026, 9, 8, 9, 30)
    await svc._fetch_sparkline("SPY")
    assert feed.count("SPY") == 2


# ── 2. what is never memoized ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("now, bars, why", [
    (_et(2026, 9, 30, 17, 0), _bars(WED, last="15:30"), "short of the 16:00 close"),
    (_et(2026, 11, 27, 13, 30), _bars(HALF_DAY, last="12:30"), "short of the 13:00 close"),
    # One bar short: bars are start-stamped, so the session's final bar is 15:55 (12:55 on a
    # half-day). A feed still missing it must be refetched, not frozen until the next open.
    (_et(2026, 9, 30, 17, 0), _bars(WED, last="15:50"), "short of the 16:00 close"),
    (_et(2026, 11, 27, 13, 30), _bars(HALF_DAY, last="12:50"), "short of the 13:00 close"),
    # A lagging feed: at Wednesday 17:00 it still ends on Tuesday.
    (_et(2026, 9, 30, 17, 0), _bars(date(2026, 9, 29)), "latest completed session is 2026-09-30"),
])
async def test_a_series_that_is_not_the_finished_session_is_never_frozen(
    clock, feed, caplog, now, bars, why
):
    svc = _svc()
    clock.now = now
    feed.bars["SPY"] = bars
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for _ in range(3):
            assert (await svc._fetch_sparkline("SPY"))[0]
    assert feed.count("SPY") == 3
    assert HomeDashboardService._spark_memo == {}
    notes = [r for r in caplog.records if "not memoized" in r.getMessage()]
    assert len(notes) == 1 and why in notes[0].getMessage(), "log once per reason, not per build"


@pytest.mark.asyncio
async def test_a_24_7_series_is_never_memoized(clock, feed):
    svc = _svc()
    clock.now = _et(2026, 10, 3, 12, 0)   # Saturday: nothing regular-hours is moving
    feed.bars["BTCUSD"] = _bars(FRI)
    for _ in range(3):
        await svc._fetch_sparkline("BTCUSD", extended_hours=True)
    assert feed.count("BTCUSD") == 3
    assert HomeDashboardService._spark_memo == {}
    # A regular-hours memo of the same symbol is a different key: the 24/7 path ignores it.
    feed.bars["SPY"] = _bars(FRI)
    await svc._fetch_sparkline("SPY")
    await svc._fetch_sparkline("SPY", extended_hours=True)
    assert feed.calls[-1] == ("SPY", "1D", True)
    assert HomeDashboardService._spark_memo_get("SPY", True, clock.now) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    [],
    [{"date": f"{FRI} 15:55:00", "close": 100.0}],          # one bar: no line
    [{"date": f"{FRI} 15:50:00", "close": None}, {"date": f"{FRI} 15:55:00", "close": 0.0}],
    RuntimeError("FMP 503"),
])
async def test_an_empty_or_failed_fetch_is_never_memoized(clock, feed, bad):
    svc = _svc()
    clock.now = _et(2026, 10, 3, 12, 0)
    feed.bars["SPY"] = bad
    assert await svc._fetch_sparkline("SPY") == _EMPTY
    assert await svc._fetch_sparkline("SPY") == _EMPTY
    assert feed.count("SPY") == 2
    assert HomeDashboardService._spark_memo == {}


@pytest.mark.asyncio
async def test_a_failed_fetch_never_serves_or_replaces_a_stale_memo(clock, feed):
    svc = _svc()
    feed.bars["SPY"] = _bars(WED)
    await svc._fetch_sparkline("SPY")
    clock.now = _et(2026, 10, 1, 17, 0)        # Thursday's session is now the latest
    feed.bars["SPY"] = RuntimeError("timeout")
    assert await svc._fetch_sparkline("SPY") == _EMPTY
    assert _memo_days() == {"SPY": WED}       # untouched, and never served
    assert await svc._fetch_sparkline("SPY") == _EMPTY
    assert feed.count("SPY") == 3


@pytest.mark.asyncio
async def test_a_broken_memo_entry_costs_one_fetch_never_the_tile(clock, feed, caplog):
    svc = _svc()
    HomeDashboardService._spark_memo[("SPY", False)] = ("not", "a", "memo")
    feed.bars["SPY"] = _bars(WED)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        served = await svc._fetch_sparkline("SPY")
    assert served[0] and feed.count("SPY") == 1
    assert any("memo read for SPY failed" in r.getMessage() for r in caplog.records)


def test_the_read_side_refuses_a_memo_of_another_session():
    """Not reachable through the store (it refuses bars of an older session), so planted:
    a memo whose session is not the latest completed one is never served, whatever else
    it claims."""
    memo = hds._SparkMemo(
        session_day=date(2026, 9, 29), fetched_at=_et(2026, 9, 30, 17, 0), series=(1.0, 2.0),
        span_from=0.0, span_to=1.0,
    )
    problem = hds._spark_memo_problem(memo, _et(2026, 9, 30, 18, 0))
    assert problem is not None and "latest completed session is 2026-09-30" in problem


def test_a_memo_from_the_future_is_refused():
    memo = hds._SparkMemo(
        session_day=WED, fetched_at=_et(2026, 9, 30, 20, 0), series=(1.0, 2.0),
        span_from=0.0, span_to=1.0,
    )
    assert "future" in hds._spark_memo_problem(memo, _et(2026, 9, 30, 17, 0))


# ── 3. concurrency, ordering, copies and the bound ──────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_builds_keep_every_symbol_its_own_series(clock, feed):
    svc = _svc()
    clock.now = _et(2026, 10, 3, 12, 0)
    symbols = [c["symbol"] for c in hds._PULSE_SYMBOLS]
    for n, sym in enumerate(symbols):
        feed.bars[sym] = _bars(FRI, base=100.0 * (n + 1))
        feed.yields[sym] = 5 - n            # they land in the reverse order
    feed.bars["IWM"] = RuntimeError("IWM upstream down")

    calls = [svc._fetch_sparkline(sym) for _ in range(4) for sym in symbols]
    results = await asyncio.gather(*calls)
    for (sym, res) in zip([s for _ in range(4) for s in symbols], results):
        if sym == "IWM":
            assert res == _EMPTY
        else:
            assert res[0][0] == 100.0 * (symbols.index(sym) + 1), sym
    memo = HomeDashboardService._spark_memo
    assert set(k[0] for k in memo) == set(symbols) - {"IWM"}
    for key, entry in memo.items():
        assert entry.series[0] == 100.0 * (symbols.index(key[0]) + 1)

    before = len(feed.calls)
    again = await asyncio.gather(*[svc._fetch_sparkline(sym) for sym in symbols])
    assert len(feed.calls) - before == 1     # only the failed IWM is fetched again
    assert again[0] == results[0]


def test_an_older_fetch_landing_late_never_replaces_a_newer_memo():
    bars = _bars(FRI)
    newer = ([3.0, 4.0], 0.0, 1.0)
    older = ([1.0, 2.0], 0.0, 1.0)
    HomeDashboardService._spark_memo_put("SPY", False, bars, newer, _et(2026, 10, 3, 12, 0))
    HomeDashboardService._spark_memo_put("SPY", False, bars, older, _et(2026, 10, 3, 11, 0))
    assert HomeDashboardService._spark_memo[("SPY", False)].series == (3.0, 4.0)


@pytest.mark.asyncio
async def test_a_served_series_is_a_copy(clock, feed):
    svc = _svc()
    clock.now = _et(2026, 10, 3, 12, 0)
    feed.bars["SPY"] = _bars(FRI)
    first = await svc._fetch_sparkline("SPY")
    expected = list(first[0])
    first[0].clear()
    hit = await svc._fetch_sparkline("SPY")
    hit[0].append(-1.0)
    assert (await svc._fetch_sparkline("SPY"))[0] == expected
    assert feed.count("SPY") == 1


def test_the_memo_is_bounded():
    bars = _bars(FRI)
    base = _et(2026, 10, 3, 12, 0)
    for n in range(hds._SPARK_MEMO_MAX_ENTRIES + 10):
        HomeDashboardService._spark_memo_put(f"S{n}", False, bars, ([1.0, 2.0], 0.0, 1.0), base + n)
    memo = HomeDashboardService._spark_memo
    assert len(memo) == hds._SPARK_MEMO_MAX_ENTRIES
    assert ("S0", False) not in memo and (f"S{hds._SPARK_MEMO_MAX_ENTRIES + 9}", False) in memo


# ── 4. the pulse rebuild itself ─────────────────────────────────────────────────────────


def _pulse_svc() -> HomeDashboardService:
    svc = _svc()

    async def quotes():
        return {
            c["symbol"]: {"symbol": c["symbol"], "price": 100.0, "changesPercentage": 0.5,
                          "previousClose": 99.5}
            for c in hds._PULSE_SYMBOLS
        }

    async def no_crypto():
        return None

    svc._pulse_quote_map = quotes
    svc._get_crypto_pulse_tile = no_crypto
    return svc


@pytest.mark.asyncio
async def test_a_pulse_rebuild_outside_the_session_makes_no_chart_call(clock, feed):
    svc = _pulse_svc()
    clock.now = _et(2026, 10, 3, 12, 0)
    for c in hds._PULSE_SYMBOLS:
        feed.bars[c["symbol"]] = _bars(FRI)
    first = await svc._build_pulse()
    assert len(first) == len(hds._PULSE_SYMBOLS) and len(feed.calls) == len(hds._PULSE_SYMBOLS)
    assert {c[1:] for c in feed.calls} == {("1D", False)}    # the call shape is unchanged
    clock.now += 45
    second = await svc._build_pulse()
    assert len(feed.calls) == len(hds._PULSE_SYMBOLS), "a closed-window rebuild re-downloaded"
    assert [t.spark for t in second] == [t.spark for t in first]
    assert [(t.spark_from, t.spark_to) for t in second] == [(t.spark_from, t.spark_to) for t in first]


@pytest.mark.asyncio
async def test_a_pulse_rebuild_in_the_session_fetches_every_series(clock, feed):
    svc = _pulse_svc()
    clock.now = _et(2026, 9, 30, 11, 0)
    for c in hds._PULSE_SYMBOLS:
        feed.bars[c["symbol"]] = _bars(WED, last="10:55")
    await svc._build_pulse()
    clock.now += 45
    await svc._build_pulse()
    assert len(feed.calls) == 2 * len(hds._PULSE_SYMBOLS)
