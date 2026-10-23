"""The earnings push pass reads the calendar ONE ET DAY PER CALL (2026-10-22).

It used to ask FMP for yesterday..tomorrow in one request. FMP cuts an `earnings-calendar`
answer at 4,000 rows without saying so and keeps the NEWEST dates, and in season three
days pass that (Oct 28–30 2026 ≈ 4,528 rows, Nov 4–6 ≈ 4,471, measured on 2026-10-22), so
the cut fell on YESTERDAY — the late-arriving actuals the result pass exists for — and a
watched company's "beat/missed" push was silently never sent. The same cut emptied the
Tracking feed's earnings alerts (Sentry PYTHON-FASTAPI-4S).

Hermetic: the claim, FMP, the watched-ticker read and the dispatcher are all fakes.
"""
from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

import app.services.notification_senders.earnings_sender as es
from app.integrations.fmp import FMPUnavailableException

# 21:00 UTC on 2026-11-05 = 16:00 EST: the run's ET day is Nov 5.
_NOW = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)
_TODAY = date(2026, 11, 5)
_YESTERDAY = _TODAY - timedelta(days=1)
_TOMORROW = _TODAY + timedelta(days=1)


def _cal(symbol: str, when: date, est: Optional[float] = None,
         act: Optional[float] = None, time: str = "amc") -> dict:
    return {"symbol": symbol, "date": when.isoformat(), "time": time,
            "epsEstimated": est, "epsActual": act}


class _FMP:
    """FMP-like: the rows dated inside ``from..to``, cut to the NEWEST ``cap`` rows."""

    def __init__(self, rows: List[dict], *, cap: Optional[int] = None,
                 day_answers: Optional[Dict[str, Any]] = None):
        self.rows = rows
        self.cap = cap
        self.day_answers = dict(day_answers or {})
        self.calls: List[tuple] = []

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls.append((from_date, to_date))
        if from_date == to_date and from_date in self.day_answers:
            answer = self.day_answers[from_date]
            if isinstance(answer, BaseException):
                raise answer
            return answer
        out = [dict(r) for r in self.rows if from_date <= r["date"] <= to_date]
        if self.cap is not None and len(out) > self.cap:
            out = sorted(out, key=lambda r: r["date"])[-self.cap:]
        return out


class _Run:
    def __init__(self):
        self.success = False
        self.notified = 0
        self.cursor = None
        self.error = None


def _wire(monkeypatch, fmp: _FMP, watched: set) -> tuple:
    run = _Run()
    dispatched: List[dict] = []
    watched_reads: List[int] = []

    @contextlib.asynccontextmanager
    async def _claim(_job):
        yield run

    async def _dispatch(**kw):
        dispatched.append(kw)
        return 1

    def _watched():
        watched_reads.append(1)
        return set(watched)

    monkeypatch.setattr(es, "claimed_job", _claim)
    monkeypatch.setattr(es, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(es, "_dispatch", _dispatch)
    monkeypatch.setattr(es, "_watched_tickers", _watched)
    return run, dispatched, watched_reads


@pytest.mark.asyncio
async def test_yesterdays_result_survives_a_season_the_single_request_would_cut(monkeypatch):
    """THE regression. Yesterday 1,700 reports, today 1,500, tomorrow 1,300: one 3-day
    request is cut to the newest 4,000 rows and loses 501 of yesterday's — NVDA's beat
    among them. One call per day loses nothing."""
    rows = (
        [_cal(f"Y{i}", _YESTERDAY) for i in range(1700)]
        + [_cal(f"T{i}", _TODAY) for i in range(1500)]
        + [_cal(f"M{i}", _TOMORROW) for i in range(1300)]
    )
    # Placed FIRST in their day so a newest-kept cut drops them (the sort is stable).
    rows.insert(0, _cal("NVDA", _YESTERDAY, est=1.0, act=1.2))
    rows.insert(1701, _cal("AAPL", _TOMORROW))
    fmp = _FMP(rows, cap=4000)

    # Non-vacuous: the fake cuts the old single request the way FMP does.
    whole = await fmp.get_earnings_calendar(from_date=_YESTERDAY.isoformat(),
                                           to_date=_TOMORROW.isoformat())
    assert len(whole) == 4000 and "NVDA" not in {r["symbol"] for r in whole}
    fmp.calls.clear()

    run, dispatched, _ = _wire(monkeypatch, fmp, {"NVDA", "AAPL"})
    stats = await es.run_earnings_notifications(now=_NOW)

    sent = {(d["kind"], d["symbol"], d["when"]) for d in dispatched}
    assert (es.KIND_EARNINGS_RESULT, "NVDA", _YESTERDAY.isoformat()) in sent
    assert (es.KIND_EARNINGS_UPCOMING, "AAPL", _TOMORROW.isoformat()) in sent
    assert stats["results"] == 1 and stats["upcoming"] == 1
    assert run.success is True
    assert sorted(fmp.calls) == sorted(
        (d.isoformat(), d.isoformat()) for d in (_YESTERDAY, _TODAY, _TOMORROW)
    ), "one single-day call each for yesterday, today and tomorrow"


@pytest.mark.asyncio
async def test_todays_row_wins_over_yesterdays_for_the_same_symbol(monkeypatch):
    """`select_results` keeps the FIRST row per symbol. FMP's multi-day answer listed the
    newest day first, so today's row won; the per-day rows keep that order."""
    rows = [_cal("NVDA", _YESTERDAY, est=1.0, act=1.3), _cal("NVDA", _TODAY, est=1.0, act=1.1)]
    run, dispatched, _ = _wire(monkeypatch, _FMP(rows), {"NVDA"})
    await es.run_earnings_notifications(now=_NOW)
    (only,) = dispatched
    assert only["kind"] == es.KIND_EARNINGS_RESULT and only["when"] == _TODAY.isoformat()


@pytest.mark.asyncio
async def test_one_failed_day_fails_the_pass_so_the_day_is_retried(monkeypatch):
    """All-or-nothing: a missing day must become a retry, never a day stamped complete
    with that day's notifications never sent."""
    rows = [_cal("NVDA", _TODAY, est=1.0, act=1.5), _cal("AAPL", _TOMORROW)]
    fmp = _FMP(rows, day_answers={_YESTERDAY.isoformat(): FMPUnavailableException("503")})
    run, dispatched, _ = _wire(monkeypatch, fmp, {"NVDA", "AAPL"})
    with pytest.raises(FMPUnavailableException):
        await es.run_earnings_notifications(now=_NOW)
    assert run.success is False and dispatched == []


@pytest.mark.asyncio
async def test_a_non_list_day_fails_the_pass(monkeypatch):
    fmp = _FMP([_cal("AAPL", _TOMORROW)], day_answers={_TODAY.isoformat(): {"error": "x"}})
    run, dispatched, _ = _wire(monkeypatch, fmp, {"AAPL"})
    with pytest.raises(TypeError):
        await es.run_earnings_notifications(now=_NOW)
    assert run.success is False and dispatched == []


@pytest.mark.asyncio
async def test_three_empty_days_are_a_successful_run_of_zero(monkeypatch):
    run, dispatched, watched_reads = _wire(monkeypatch, _FMP([]), {"AAPL"})
    stats = await es.run_earnings_notifications(now=_NOW)
    assert run.success is True and dispatched == []
    assert stats == {"upcoming": 0, "results": 0, "symbols": 0}
    assert watched_reads == [], "a holiday needs no watched-ticker read"


@pytest.mark.asyncio
async def test_rows_under_another_date_or_none_are_dropped(monkeypatch):
    """A sloppy upstream answering every day with every row, or a row without a date,
    must not reach the selectors (a non-dict row used to crash the pass on `.get`)."""

    class _Lenient(_FMP):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            self.calls.append((from_date, to_date))
            return [_cal("AAPL", _TOMORROW), {"symbol": "NODT"}, "junk", None]

    run, dispatched, _ = _wire(monkeypatch, _Lenient([]), {"AAPL", "NODT"})
    await es.run_earnings_notifications(now=_NOW)
    assert [(d["kind"], d["symbol"]) for d in dispatched] == [
        (es.KIND_EARNINGS_UPCOMING, "AAPL")
    ]
    assert run.success is True
