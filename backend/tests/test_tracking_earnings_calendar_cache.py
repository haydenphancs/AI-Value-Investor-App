"""The Tracking feed's market-wide earnings calendar: per ET day, memoised at two speeds,
deduped (2026-10-08; per-day since 2026-10-22).

`_get_earnings_alerts` used to download FMP's WHOLE 15-day `earnings-calendar` in ONE
request. FMP cuts that answer at 4,000 rows and keeps the NEWEST dates, so in season it
held only the last three days of the window and every watchlist alert for the next twelve
days — today's first — was missing (Sentry PYTHON-FASTAPI-4S; live 2026-10-22: the
10-23..11-06 answer held only Nov 4–6). `_cached_earnings_calendar` now:

  * asks ONE ET day per call (`fetch_calendar_days`: all-or-nothing, rows kept only under
    their own date, a single day at the cap reported at ERROR);
  * memoises each day: today and tomorrow for `EARNINGS_CALENDAR_TTL` (30 min), later days
    for `EARNINGS_CALENDAR_FAR_TTL` (6 h) — owner decision 2026-10-22, ~150 calls a day;
  * stores an empty DAY (weekends) but never an empty WINDOW, a failure or a non-list
    answer (a cached empty reads exactly like "nobody on this watchlist reports this
    fortnight");
  * after a failed or empty round serves the days it already holds and waits before the
    next round, so an outage costs one round per wait, not one per feed build;
  * collapses concurrent misses to one round; a joiner whose leader was CANCELLED (a
    client disconnect) TAKES OVER — it never serves its own user a feed with no earnings
    alerts (that feed is then cached for 30 s and saved by the iOS snapshot);
  * is keyed on the client object, so one fake's rows never reach another.

Hermetic: every client is an inline fake; the module state is reset around each test and
the ET day is pinned (a test that crossed ET midnight would otherwise move the window).
"""
from __future__ import annotations

import asyncio
import gc
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

import app.services.tracking_service as ts
from app.integrations.fmp import EmptyAfterFailure, FMPUnavailableException
from app.services.tracking_service import TrackingService

_REAL_ET_TODAY = ts._et_today
# A Thursday in earnings season; the window is 10-22..11-05.
_DAY = date(2026, 10, 22)
_WINDOW = [_DAY + timedelta(days=i) for i in range(15)]


def _reset_state() -> None:
    ts.reset_earnings_calendar_cache()
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    ts._feed_generation.clear()
    ts._feed_inflight_generation.clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(ts, "_et_today", lambda: _DAY)
    _reset_state()
    yield
    _reset_state()


def _iso(days_ahead: int = 0) -> str:
    return (ts._et_today() + timedelta(days=days_ahead)).isoformat()


def _row(symbol: str, days_ahead: int = 3) -> dict:
    return {
        "symbol": symbol, "date": _iso(days_ahead), "time": "amc",
        "companyName": f"{symbol} Inc", "epsEstimated": 1.25, "revenueEstimated": 2.5e9,
    }


class _CalendarFMP:
    """Answers `get_earnings_calendar` like FMP: the rows dated inside ``from..to``.

    ``day_answers``: ISO day -> a queue of values (or exceptions to raise) served for a
    single-day call on that day before the default. ``lenient``: every call returns EVERY
    row, whatever its date (as a sloppy upstream might). ``cap``: FMP's silent row cap —
    a larger answer keeps only its NEWEST dates. ``release``: an Event every call waits
    on (so a test can hold a round open), else ``delay`` seconds.
    """

    def __init__(self, rows=None, *, day_answers: Optional[Dict[str, List[Any]]] = None,
                 delay: float = 0.0, gated: bool = False, lenient: bool = False,
                 cap: Optional[int] = None):
        self.rows = rows if rows is not None else [_row("AAPL"), _row("MSFT")]
        self.day_answers = {k: list(v) for k, v in (day_answers or {}).items()}
        self.delay = delay
        self.lenient = lenient
        self.cap = cap
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
        queue = self.day_answers.get(from_date) if from_date == to_date else None
        if queue:
            answer = queue.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        if self.lenient:
            out = [dict(r) if isinstance(r, dict) else r for r in self.rows]
        else:
            out = [
                dict(r) for r in self.rows
                if isinstance(r, dict)
                and from_date <= str(r.get("date") or "")[:10] <= to_date
            ]
        if self.cap is not None and len(out) > self.cap:
            out = sorted(out, key=lambda r: str(r.get("date")))[-self.cap:]
        return out


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


def _rounds(fmp, today: Optional[date] = None) -> int:
    """Rounds started: every round asks for its window's first day, and asks first."""
    first = (today or ts._et_today()).isoformat()
    return sum(1 for f, _t in fmp.calls if f == first)


def _age_memo(seconds: float) -> None:
    """Make every held day ``seconds`` older."""
    client, held = ts._earnings_calendar_memo
    ts._earnings_calendar_memo = (
        client, {d: (at - seconds, rows) for d, (at, rows) in held.items()}
    )


def _end_the_wait() -> None:
    retry = ts._earnings_calendar_retry_after
    assert retry is not None, "a failed or empty round must set a wait"
    ts._earnings_calendar_retry_after = (retry[0], 0.0)


# ── the per-day fetch ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_call_per_et_day_today_through_two_weeks_ahead():
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert sorted(fmp.calls) == [(d.isoformat(), d.isoformat()) for d in _WINDOW]


@pytest.mark.asyncio
async def test_today_and_tomorrow_survive_a_season_the_single_request_would_cut(caplog):
    """THE regression (Sentry PYTHON-FASTAPI-4S). 400 reports a day overflow FMP's 4,000
    row cap over 15 days, and the cap keeps the NEWEST dates, so the one 15-day request
    lost today's and tomorrow's reports. One call per day loses nothing."""
    filler = [_row(f"F{d}X{i}", days_ahead=d) for d in range(15) for i in range(400)]
    rows = filler + [_row("AAPL", 0), _row("MSFT", 1), _row("NVDA", 14)]
    fmp = _CalendarFMP(rows=rows, cap=4000)

    # Non-vacuous: the fake cuts a whole-window request the way FMP did.
    whole = await fmp.get_earnings_calendar(from_date=_iso(0), to_date=_iso(14))
    assert len(whole) == 4000
    assert {"AAPL", "MSFT"}.isdisjoint(r["symbol"] for r in whole)
    fmp.calls.clear()

    with caplog.at_level(logging.WARNING):
        alerts = await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT", "NVDA"])
    assert _tickers(alerts) == ["AAPL", "MSFT", "NVDA"], "oldest day first, none lost"
    assert len(fmp.calls) == 15
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
        "a window over 4,000 rows is normal now; only a single day at the cap is an error"


@pytest.mark.asyncio
async def test_a_single_day_at_the_cap_is_reported_and_still_builds(caplog):
    rows = [_row(f"S{i}", days_ahead=3) for i in range(3999)] + [_row("AAPL")]
    fmp = _CalendarFMP(rows=rows)
    with caplog.at_level(logging.WARNING):
        alerts = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert _tickers(alerts) == ["AAPL"]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "silent cap" in errors[0] and _iso(3) in errors[0], errors


def test_the_window_starts_on_the_et_date_not_the_utc_date(monkeypatch):
    """From 20:00 ET the UTC date is already tomorrow; the old `datetime.now()` window
    (UTC on the server) dropped the evening's after-close reports."""
    monkeypatch.setattr(ts, "_et_today", _REAL_ET_TODAY)
    instant = {"t": datetime(2026, 10, 23, 0, 30, tzinfo=timezone.utc)}   # 20:30 EDT

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            t = instant["t"]
            return t.astimezone(tz) if tz is not None else t.replace(tzinfo=None)

    monkeypatch.setattr(ts, "datetime", _Clock)
    assert ts._et_today() == date(2026, 10, 22)
    instant["t"] = datetime(2026, 10, 23, 4, 30, tzinfo=timezone.utc)      # 00:30 EDT
    assert ts._et_today() == date(2026, 10, 23)


@pytest.mark.asyncio
async def test_rows_are_kept_once_under_their_own_date_only():
    """A sloppy upstream that answers every day with every row must not repeat an alert
    15 times, and a row with no readable date cannot be placed on a day, so it is left
    out rather than guessed."""
    rows = [_row("AAPL", 2), {"symbol": "NODT", "companyName": "No Date"},
            {"symbol": "BADT", "date": "next week"}]
    fmp = _CalendarFMP(rows=rows, lenient=True)
    alerts = await _svc(fmp)._get_earnings_alerts(["AAPL", "NODT", "BADT"])
    assert _tickers(alerts) == ["AAPL"]


@pytest.mark.asyncio
async def test_a_timestamp_date_still_gets_its_day_and_month():
    """FMP sometimes sends "YYYY-MM-DD HH:MM:SS". The per-day fetch places the row on its
    day, so the alert must read the same 10 characters, not fall back to "undated"."""
    row = {**_row("AAPL", 4), "date": f"{_iso(4)} 16:00:00"}
    fmp = _CalendarFMP(rows=[row])
    (alert,) = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    expected = _DAY + timedelta(days=4)
    assert alert.day == expected.day and alert.month == expected.strftime("%b").upper()


# ── the two-speed memo ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_builds_share_one_round_across_services_on_one_client():
    fmp = _CalendarFMP()
    a1 = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    a2 = await _svc(fmp)._get_earnings_alerts(["aapl", "TSLA"])
    a3 = await _svc(fmp)._get_earnings_alerts(["MSFT"])
    assert len(fmp.calls) == 15 and _rounds(fmp) == 1, fmp.calls
    assert _tickers(a1) == ["AAPL"] and _tickers(a2) == ["AAPL"] and _tickers(a3) == ["MSFT"]


@pytest.mark.asyncio
async def test_near_days_refetch_after_30_minutes_far_days_after_6_hours():
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 15

    _age_memo(ts.EARNINGS_CALENDAR_TTL - 5)
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 15, "nothing is due before 30 minutes"

    _age_memo(10)
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert sorted(fmp.calls[15:]) == [(_iso(0), _iso(0)), (_iso(1), _iso(1))], \
        "after 30 minutes only today and tomorrow are asked again"

    _age_memo(ts.EARNINGS_CALENDAR_FAR_TTL)
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 17 + 15, "after 6 hours every day is due"


@pytest.mark.asyncio
async def test_the_day_roll_asks_for_the_new_near_days_and_the_new_last_day(monkeypatch):
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    _age_memo(3600)                       # an hour later the ET day rolls
    nxt = _DAY + timedelta(days=1)
    monkeypatch.setattr(ts, "_et_today", lambda: nxt)
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    expected = [nxt, nxt + timedelta(days=1), nxt + timedelta(days=14)]
    assert sorted(fmp.calls[15:]) == [(d.isoformat(), d.isoformat()) for d in expected], \
        "the old day 2 is tomorrow now (near TTL); days 3..14 are still fresh"
    held = ts._earnings_calendar_memo[1]
    assert len(held) == 15 and _DAY not in held, "the memo holds only the window"


@pytest.mark.asyncio
async def test_a_zero_ttl_never_serves_from_memory(monkeypatch):
    monkeypatch.setattr(ts, "EARNINGS_CALENDAR_TTL", 0)
    monkeypatch.setattr(ts, "EARNINGS_CALENDAR_FAR_TTL", 0)
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 30


@pytest.mark.asyncio
async def test_an_empty_day_is_stored_and_not_asked_again():
    """Weekends and quiet days are empty. Refusing to store them would re-ask those days
    on every feed build."""
    fmp = _CalendarFMP(rows=[_row("AAPL", 3)])
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    held = ts._earnings_calendar_memo[1]
    assert len(held) == 15 and held[_DAY][1] == []
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 15


@pytest.mark.asyncio
async def test_an_empty_window_is_served_but_not_memoised_and_waits(caplog):
    fmp = _CalendarFMP(rows=[])
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_memo is None
    assert any("NOT memoised" in r.getMessage() for r in caplog.records)

    assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert len(fmp.calls) == 15, "inside the wait no new round starts"

    _end_the_wait()
    fmp.rows = [_row("AAPL")]
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 30
    assert ts._earnings_calendar_retry_after is None, "a good round clears the wait"


@pytest.mark.asyncio
async def test_an_empty_round_over_held_days_serves_them_like_the_wait_does(caplog):
    """Today's and tomorrow's rows were held; the round refetching them comes back empty
    (and so does every other day). That build gets the held rows — the same answer every
    build inside the wait gets — never an empty feed followed by the old rows again."""
    fmp = _CalendarFMP(rows=[_row("AAPL", 0), _row("MSFT", 1)])
    await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    _age_memo(ts.EARNINGS_CALENDAR_TTL + 1)
    fmp.rows = []
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        first = await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    assert _tickers(first) == ["AAPL", "MSFT"]
    assert any("NOT memoised" in r.getMessage() for r in caplog.records)
    held = ts._earnings_calendar_memo[1]
    assert [r["symbol"] for r in held[_DAY][1]] == ["AAPL"], "the empty round stored nothing"
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])) == ["AAPL", "MSFT"]
    assert len(fmp.calls) == 17


@pytest.mark.asyncio
async def test_the_memo_is_keyed_on_the_client():
    """One fake's rows never reach another — the strict-xfail reschedule test builds its
    own client and would otherwise be served a neighbour's calendar — not even as the
    held days served after a failure."""
    a = _CalendarFMP(rows=[_row("AAPL")])
    b = _CalendarFMP(rows=[_row("NVDA")])
    assert _tickers(await _svc(a)._get_earnings_alerts(["AAPL", "NVDA"])) == ["AAPL"]
    assert _tickers(await _svc(b)._get_earnings_alerts(["AAPL", "NVDA"])) == ["NVDA"]
    assert len(a.calls) == 15 and len(b.calls) == 15

    c = _CalendarFMP(day_answers={_iso(0): [FMPUnavailableException("503")]})
    assert await _svc(c)._get_earnings_alerts(["AAPL", "NVDA"]) == []


@pytest.mark.asyncio
async def test_the_shared_rows_are_never_mutated_by_the_alert_builder():
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    stored = ts._earnings_calendar_memo[1][_DAY + timedelta(days=3)][1]
    snapshot = [dict(r) for r in stored]
    await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    assert stored == snapshot


@pytest.mark.asyncio
async def test_a_malformed_row_in_the_memoised_answer_does_not_cost_every_alert():
    """The answer lives for 30 min to 6 h, so one bad row must not blank every build's
    earnings alerts for that long (a non-dict row used to raise AttributeError → the
    whole section degraded to no alerts)."""
    day = _iso(3)
    raw = [None, "garbage", 7, {"symbol": 12345, "date": day},
           {"symbol": None, "date": day}, _row("AAPL")]
    fmp = _CalendarFMP(rows=raw, lenient=True)
    first = await _svc(fmp)._get_earnings_alerts(["AAPL", "12345"])
    second = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert _tickers(first) == ["12345", "AAPL"]
    assert _tickers(second) == ["AAPL"]
    assert len(fmp.calls) == 15, "the (non-empty) answer is memoised as served"


@pytest.mark.asyncio
async def test_a_malformed_dict_row_does_not_cost_every_alert(caplog):
    """A non-str `companyName` (AlertResponse ValidationError) or a field whose processing
    raises (`time: 5` → AttributeError) used to return [] for the whole section — and the
    memo kept that. Each bad row now degrades on its own; the good row LAST still gets its
    alert. A row whose `date` is not a readable day is left out by the per-day fetch."""
    day = _iso(3)
    raw = [
        # unreadable date → no day to place it on, so no alert (never a guessed date)
        {"symbol": "DINT", "date": 20261010, "time": "bmo", "companyName": "Dint Co"},
        {"symbol": "DLST", "date": [day], "time": "amc", "companyName": "Dlst Co"},
        # non-str companyName → falls back to the symbol, never a coerced/garbage name
        {"symbol": "NINT", "date": day, "time": "amc", "companyName": 12345},
        {"symbol": "NDCT", "date": day, "time": "amc", "companyName": {"en": "Ndct"}},
        # a field whose processing raises → only THIS row is skipped, and it is logged
        {"symbol": "TINT", "date": day, "time": 5, "companyName": "Tint Co"},
        _row("AAPL"),
    ]
    fmp = _CalendarFMP(rows=raw, lenient=True)
    watch = ["DINT", "DLST", "NINT", "NDCT", "TINT", "AAPL"]
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        first = await _svc(fmp)._get_earnings_alerts(watch)
    by = {a.ticker: a for a in first}

    assert _tickers(first) == ["NINT", "NDCT", "AAPL"]
    good = by["AAPL"]
    assert good.company_name == "AAPL Inc"
    assert good.day is not None and good.month is not None
    assert good.report_time == "after_close"
    assert good.eps_estimate == 1.25 and good.revenue_estimate == 2.5e9
    assert by["NINT"].company_name == "NINT"
    assert by["NDCT"].company_name == "NDCT"

    skips = [r.getMessage() for r in caplog.records
             if r.levelno == logging.WARNING and "earnings row skipped" in r.getMessage()]
    assert len(skips) == 1 and "TINT" in skips[0] and "AttributeError" in skips[0], skips
    assert not [r for r in caplog.records if "Earnings alerts failed" in r.getMessage()]

    second = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert _tickers(second) == ["AAPL"], "the memoised rows still serve the good alert"
    assert len(fmp.calls) == 15


@pytest.mark.asyncio
async def test_the_fetch_logs_its_days_rows_and_elapsed_time(caplog):
    fmp = _CalendarFMP()
    with caplog.at_level(logging.INFO, logger=ts.logger.name):
        await _svc(fmp)._get_earnings_alerts(["AAPL"])
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("fetched 15 of 15 days" in m and "2 rows in the window" in m and " ms" in m
               for m in infos), infos


# ── failures: nothing memoised, the held days served, one round per wait ──────


@pytest.mark.asyncio
async def test_a_failed_day_fails_the_round_and_nothing_is_memoised():
    fmp = _CalendarFMP(day_answers={_iso(5): [FMPUnavailableException("503 from FMP")]})
    assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_memo is None and ts._earnings_calendar_inflight == {}

    assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert len(fmp.calls) == 15, "inside the wait no new round starts (no 15-call storm)"

    _end_the_wait()
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 30
    assert ts._earnings_calendar_retry_after is None


@pytest.mark.asyncio
async def test_an_empty_after_failure_day_fails_the_round(caplog):
    """`EmptyAfterFailure` is an outage, not a day nobody reports: storing it would hide
    that day's alerts for 30 min to 6 h."""
    fmp = _CalendarFMP(day_answers={_iso(0): [EmptyAfterFailure("earnings-calendar 429")]})
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_memo is None
    assert any("empty after a failed fetch" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_non_list_day_is_refused_and_not_memoised():
    fmp = _CalendarFMP(day_answers={_iso(0): [{"symbol": "AAPL", "date": _iso(0)}]})
    with pytest.raises(TypeError):
        await ts._cached_earnings_calendar(fmp, _DAY)
    assert ts._earnings_calendar_memo is None and ts._earnings_calendar_inflight == {}
    # Through the alert builder the refusal degrades to no alerts, never a crash.
    _end_the_wait()
    fmp.day_answers = {_iso(0): [None]}
    assert await _svc(fmp)._get_earnings_alerts(["AAPL"]) == []
    assert ts._earnings_calendar_memo is None


@pytest.mark.asyncio
async def test_a_failed_round_serves_the_held_days_and_waits(caplog):
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    _age_memo(ts.EARNINGS_CALENDAR_TTL + 1)                     # today, tomorrow due
    fmp.day_answers = {_iso(0): [FMPUnavailableException("503")]}

    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        alerts = await _svc(fmp)._get_earnings_alerts(["AAPL", "MSFT"])
    assert _tickers(alerts) == ["AAPL", "MSFT"], "a failed round keeps the held alerts"
    assert any("serving the held days" in r.getMessage() for r in caplog.records)
    assert len(fmp.calls) == 17

    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert len(fmp.calls) == 17, "inside the wait the held days serve without a call"

    _end_the_wait()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert len(fmp.calls) == 19, "after the wait only the due days are asked again"


@pytest.mark.asyncio
async def test_a_failure_with_part_of_the_window_held_serves_that_part(monkeypatch, caplog):
    fmp = _CalendarFMP()
    await _svc(fmp)._get_earnings_alerts(["AAPL"])
    _age_memo(3600)
    nxt = _DAY + timedelta(days=1)
    monkeypatch.setattr(ts, "_et_today", lambda: nxt)
    new_last = (nxt + timedelta(days=14)).isoformat()
    fmp.day_answers = {new_last: [FMPUnavailableException("503")]}
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        alerts = await _svc(fmp)._get_earnings_alerts(["AAPL"])
    assert _tickers(alerts) == ["AAPL"]
    msgs = [r.getMessage() for r in caplog.records if "serving the held days" in r.getMessage()]
    assert len(msgs) == 1 and "14 of 15 days" in msgs[0] and new_last in msgs[0], msgs


@pytest.mark.asyncio
async def test_a_round_past_its_deadline_fails_for_leader_and_joiner(monkeypatch):
    monkeypatch.setattr(ts, "_EARNINGS_CALENDAR_DEADLINE_SECONDS", 0.05)
    fmp = _CalendarFMP(gated=True)                               # never answers
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    joiner = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    r1, r2 = await asyncio.wait_for(asyncio.gather(leader, joiner), 2.0)
    assert r1 == [] and r2 == []
    assert _rounds(fmp) == 1
    assert ts._earnings_calendar_inflight == {} and ts._earnings_calendar_memo is None
    assert ts._earnings_calendar_retry_after is not None
    fmp.release.set()


# ── the in-flight dedup ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_misses_collapse_to_one_round():
    fmp = _CalendarFMP(delay=0.02)
    results = await asyncio.gather(
        *[_svc(fmp)._get_earnings_alerts(["AAPL"]) for _ in range(5)]
    )
    assert len(fmp.calls) == 15 and _rounds(fmp) == 1, fmp.calls
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
    assert _rounds(fmp) == 1, "the joiner must join, not start its own round"

    leader.cancel()
    await _wait_for(lambda: _rounds(fmp) == 2, "the joiner taking over the round")
    fmp.release.set()
    alerts = await asyncio.wait_for(joiner, 2.0)

    assert _tickers(alerts) == ["AAPL"]
    assert leader.cancelled()
    assert ts._earnings_calendar_inflight == {}
    assert ts._earnings_calendar_memo is not None, "the takeover's answer is memoised"
    assert ts._earnings_calendar_retry_after is None, "a cancellation is not a failure"


@pytest.mark.asyncio
async def test_two_parked_joiners_and_a_cancelled_leader_both_get_the_alert():
    """One joiner takes over; the other joins the takeover (still ONE more round)."""
    fmp = _CalendarFMP(gated=True)
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    j1 = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    j2 = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["MSFT"]))
    await asyncio.sleep(0.01)

    leader.cancel()
    await _wait_for(lambda: _rounds(fmp) == 2, "a joiner taking over")
    await asyncio.sleep(0.01)
    assert _rounds(fmp) == 2, "the second joiner joins the takeover, it does not lead too"
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
    assert ts._earnings_calendar_inflight == {} and ts._earnings_calendar_memo is None
    assert ts._earnings_calendar_retry_after is None
    fmp.release.set()
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert _rounds(fmp) == 2


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
    assert len(fmp.calls) == 15 and _rounds(fmp) == 1


@pytest.mark.asyncio
async def test_a_leader_failure_reaches_parked_joiners_and_is_not_memoised():
    """A failure is shared (that is the point of the dedup) but never memoised: both
    degrade to no alerts without hanging, and the round after the wait asks again."""
    fmp = _CalendarFMP(gated=True, day_answers={_iso(0): [FMPUnavailableException("503")]})
    leader = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.wait_for(fmp.started.wait(), 2.0)
    joiner = asyncio.create_task(_svc(fmp)._get_earnings_alerts(["AAPL"]))
    await asyncio.sleep(0.01)
    fmp.release.set()
    r1, r2 = await asyncio.wait_for(asyncio.gather(leader, joiner), 2.0)
    assert r1 == [] and r2 == [] and _rounds(fmp) == 1
    assert ts._earnings_calendar_memo is None and ts._earnings_calendar_inflight == {}
    _end_the_wait()
    assert _tickers(await _svc(fmp)._get_earnings_alerts(["AAPL"])) == ["AAPL"]
    assert _rounds(fmp) == 2


@pytest.mark.asyncio
async def test_a_joinerless_failure_is_not_reported_as_never_retrieved():
    loop = asyncio.get_running_loop()
    seen: list = []
    loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
    try:
        fmp = _CalendarFMP(day_answers={_iso(0): [FMPUnavailableException("503")]})
        with pytest.raises(FMPUnavailableException):
            await ts._cached_earnings_calendar(fmp, _DAY)
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
    """User A's build leads the calendar round; user B's build joins it. A's client goes
    away. B's feed must still carry the AAPL earnings alert — and that is the feed cached
    for B's next 30 s."""
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
    assert _rounds(fmp) == 1, "B must join A's round"

    build_a.cancel()
    await _wait_for(lambda: _rounds(fmp) == 2, "B taking over the round")
    fmp.release.set()
    feed_b = await asyncio.wait_for(build_b, 2.0)

    assert [(a.type, a.ticker) for a in feed_b.alerts] == [("earnings", "AAPL")]
    cached = ts._feed_cache_get("u-b")
    assert cached is feed_b and [a.ticker for a in cached.alerts] == ["AAPL"]
    assert build_a.cancelled()


def test_reset_clears_every_piece_of_state(monkeypatch):
    monkeypatch.setattr(ts, "_earnings_calendar_memo",
                        (object(), {_DAY: (0.0, [{"symbol": "X"}])}))
    ts._earnings_calendar_inflight[(1, _DAY)] = object()          # type: ignore[assignment]
    monkeypatch.setattr(ts, "_earnings_calendar_retry_after", (object(), 1e12))
    ts.reset_earnings_calendar_cache()
    assert ts._earnings_calendar_memo is None
    assert ts._earnings_calendar_inflight == {}
    assert ts._earnings_calendar_retry_after is None
