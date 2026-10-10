"""`EarningsWindowService` context behaviour: statuses, hot refresh, truncation, staleness.

Hermetic: the client is a fake passed as a keyword. See test_earnings_window_service.py
for the boost/dedup/cancellation contract this builds on.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import earnings_window_service as ews
from app.services.earnings_window_service import (
    EARNINGS_DUE_TODAY,
    EARNINGS_REPORTED,
    EARNINGS_UPCOMING,
    EarningsWindowService,
    earnings_statuses_for,
    get_earnings_window_service,
    symbols_in_earnings_window,
)

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 10)                                   # Thursday, ORCL report day
NOON = datetime(2026, 9, 10, 12, 0, tzinfo=ET)


@pytest.fixture(autouse=True)
def _fresh_singleton():
    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()


class _DayFake:
    """Per-day calendar: ``days[date_str] -> rows``; records calls and peak concurrency."""

    def __init__(self, days=None, delay=0.0, fail_days=()):
        self.days = dict(days or {})
        self.delay = delay
        self.fail_days = set(fail_days)
        self.calls = []
        self.active = 0
        self.peak = 0

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.calls.append((from_date, to_date))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if from_date in self.fail_days:
                raise RuntimeError(f"FMP 503 for {from_date}")
            return list(self.days.get(from_date, []))
        finally:
            self.active -= 1


def _row(d, symbol="ORCL", eps=None):
    return {"symbol": symbol, "date": d, "epsActual": eps, "revenueActual": None}


# ── statuses through the service ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_statuses_are_filtered_to_the_requested_symbols():
    fake = _DayFake({
        "2026-09-10": [_row("2026-09-10"), _row("2026-09-10", "ADBE", eps=5.0)],
        "2026-09-12": [_row("2026-09-12", "MU")],
    })
    svc = EarningsWindowService()
    out = await svc.statuses(NOON, fmp=fake, symbols=["orcl", "MU", "AAPL"])
    assert set(out) == {"ORCL", "MU"}
    assert out["ORCL"].status == EARNINGS_DUE_TODAY
    assert out["MU"].status == EARNINGS_UPCOMING


@pytest.mark.asyncio
async def test_concurrency_is_bounded():
    fake = _DayFake(delay=0.01)
    await EarningsWindowService().statuses(NOON, fmp=fake, symbols=["ORCL"])
    assert fake.peak <= ews._FETCH_CONCURRENCY
    assert len(fake.calls) == 8


@pytest.mark.asyncio
async def test_the_boost_and_statuses_share_one_round():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    await symbols_in_earnings_window(NOON, fmp=fake)
    out = await earnings_statuses_for(NOON, fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_DUE_TODAY
    assert len(fake.calls) == 8


@pytest.mark.asyncio
async def test_peak_season_truncation_regression_d_minus_2_keeps_its_boost():
    """The old single D-2..D+1 request kept only the NEWEST days once FMP hit its
    4,000-row cap. Per-day calls keep D-2 (the reaction days' boost)."""
    fake = _DayFake({
        "2026-09-08": [_row("2026-09-08", "OLD", eps=1.0)],
        "2026-09-11": [_row("2026-09-11", f"S{i}") for i in range(5)],
    })
    svc = EarningsWindowService()
    boosted = await svc.symbols_in_window(NOON, fmp=fake)
    assert "OLD" in boosted


@pytest.mark.asyncio
async def test_a_day_at_the_row_cap_logs_a_truncation_error(caplog):
    rows = [_row("2026-09-10", f"S{i}") for i in range(ews._TRUNCATION_ROWS)]
    fake = _DayFake({"2026-09-10": rows})
    with caplog.at_level(logging.ERROR):
        await EarningsWindowService().symbols_in_window(NOON, fmp=fake)
    assert "TRUNCATED" in caplog.text


@pytest.mark.asyncio
async def test_boost_membership_is_unchanged_by_the_wider_context_window():
    fake = _DayFake({
        "2026-09-06": [_row("2026-09-06", "D4", eps=1.0)],   # D-4: status, no boost
        "2026-09-13": [_row("2026-09-13", "P3")],            # D+3: status, no boost
        "2026-09-08": [_row("2026-09-08", "D2", eps=1.0)],   # D-2: boost
        "2026-09-11": [_row("2026-09-11", "P1")],            # D+1: boost
    })
    svc = EarningsWindowService()
    boosted = await svc.symbols_in_window(NOON, fmp=fake)
    assert boosted == frozenset({"D2", "P1"})
    out = await svc.statuses(NOON, fmp=fake, symbols=["D4", "P3", "D2", "P1"])
    assert set(out) == {"D4", "P3", "D2", "P1"}


# ── all-or-nothing + stale snapshot ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_failing_day_fails_the_round(caplog):
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]}, fail_days={"2026-09-09"})
    svc = EarningsWindowService()
    with caplog.at_level(logging.WARNING):
        assert await svc.statuses(NOON, fmp=fake, symbols=["ORCL"]) == {}
    assert "FMP 503" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_new_day_keeps_serving_yesterdays_rows_rederived():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    assert (await svc.statuses(NOON, fmp=fake, symbols=["ORCL"]))["ORCL"].status == (
        EARNINGS_DUE_TODAY
    )
    # Next morning every call fails: the previous snapshot is re-derived for the
    # new day — ORCL's date is now in the past with no actuals → no status (never
    # "late"), and its boost (D-1 of the new day) still holds.
    fake.fail_days = {f"2026-09-{d:02d}" for d in range(7, 15)}
    tomorrow = NOON + timedelta(days=1)
    assert await svc.statuses(tomorrow, fmp=fake, symbols=["ORCL"]) == {}
    assert "ORCL" in await svc.symbols_in_window(tomorrow, fmp=fake)


@pytest.mark.asyncio
async def test_the_deadline_degrades_without_hanging(monkeypatch):
    monkeypatch.setattr(ews, "_FETCH_DEADLINE_SECONDS", 0.05)
    fake = _DayFake(delay=1.0)
    svc = EarningsWindowService()
    out = await asyncio.wait_for(svc.statuses(NOON, fmp=fake, symbols=["ORCL"]), 2.0)
    assert out == {}
    assert svc._failed_until is not None


# ── hot refresh ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hot_refresh_turns_due_today_into_reported_and_records_the_moment():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    first = await svc.statuses(NOON, fmp=fake, symbols=["ORCL"])
    assert first["ORCL"].status == EARNINGS_DUE_TODAY
    assert len(fake.calls) == 8

    # Within 20 minutes: no refresh.
    fake.days["2026-09-10"] = [_row("2026-09-10", eps=1.92)]
    await svc.statuses(NOON + timedelta(minutes=10), fmp=fake, symbols=["ORCL"])
    assert len(fake.calls) == 8

    after = NOON + timedelta(minutes=21)
    out = await svc.statuses(after, fmp=fake, symbols=["ORCL"])
    assert sorted(fake.calls[8:]) == [
        ("2026-09-09", "2026-09-09"), ("2026-09-10", "2026-09-10"),
    ]
    assert out["ORCL"].status == EARNINGS_REPORTED
    assert out["ORCL"].reported_seen_at == after


@pytest.mark.asyncio
async def test_no_hot_refresh_when_nothing_in_the_universe_is_pending():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10", "ADBE")]})
    svc = EarningsWindowService()
    await svc.statuses(NOON, fmp=fake, symbols=["ORCL"])
    await svc.statuses(NOON + timedelta(hours=2), fmp=fake, symbols=["ORCL"])
    assert len(fake.calls) == 8


@pytest.mark.asyncio
async def test_the_boost_seam_never_triggers_a_hot_refresh():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    await svc.symbols_in_window(NOON, fmp=fake)
    await svc.symbols_in_window(NOON + timedelta(hours=2), fmp=fake)
    assert len(fake.calls) == 8


@pytest.mark.asyncio
async def test_monday_hot_refresh_covers_friday():
    monday = datetime(2026, 9, 14, 8, 0, tzinfo=ET)
    fake = _DayFake({"2026-09-11": [_row("2026-09-11")]})
    svc = EarningsWindowService()
    await svc.statuses(monday, fmp=fake, symbols=["ORCL"])
    fake.days["2026-09-11"] = [_row("2026-09-11", eps=1.0)]
    out = await svc.statuses(monday + timedelta(minutes=25), fmp=fake, symbols=["ORCL"])
    assert sorted(fake.calls[8:]) == [
        ("2026-09-11", "2026-09-11"), ("2026-09-14", "2026-09-14"),
    ]
    assert out["ORCL"].status == EARNINGS_REPORTED


@pytest.mark.asyncio
async def test_hot_refresh_never_replaces_rows_with_an_empty_answer(caplog):
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    await svc.statuses(NOON, fmp=fake, symbols=["ORCL"])
    fake.days["2026-09-10"] = []
    with caplog.at_level(logging.WARNING):
        out = await svc.statuses(NOON + timedelta(minutes=25), fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_DUE_TODAY
    assert "kept the previous rows" in caplog.text


@pytest.mark.asyncio
async def test_reported_is_sticky_when_a_later_response_drops_the_actuals():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    await svc.statuses(NOON, fmp=fake, symbols=["ORCL"])
    fake.days["2026-09-10"] = [_row("2026-09-10", eps=1.92)]
    t1 = NOON + timedelta(minutes=25)
    assert (await svc.statuses(t1, fmp=fake, symbols=["ORCL"]))["ORCL"].status == EARNINGS_REPORTED
    fake.days["2026-09-10"] = [_row("2026-09-10")]            # a stale replica
    t2 = t1 + timedelta(minutes=25)
    out = await svc.statuses(t2, fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_REPORTED
    assert out["ORCL"].reported_seen_at == t1, "the landing moment is recorded once"


@pytest.mark.asyncio
async def test_results_already_in_at_the_first_fetch_are_not_a_landing():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10", eps=1.92)]})
    out = await EarningsWindowService().statuses(NOON, fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_REPORTED
    assert out["ORCL"].reported_seen_at is None


@pytest.mark.asyncio
async def test_an_overnight_landing_is_seen_at_the_next_days_round():
    # After-close report whose actuals FMP fills after 20:00: yesterday's pending
    # key + today's reported row = one landing, so the morning card refreshes.
    evening = datetime(2026, 9, 10, 19, 0, tzinfo=ET)
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    await svc.statuses(evening, fmp=fake, symbols=["ORCL"])
    fake.days["2026-09-10"] = [_row("2026-09-10", eps=1.92)]
    morning = datetime(2026, 9, 11, 4, 5, tzinfo=ET)
    out = await svc.statuses(morning, fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_REPORTED
    assert out["ORCL"].reported_seen_at == morning


@pytest.mark.asyncio
async def test_a_failed_hot_refresh_waits_the_full_interval(caplog):
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    await svc.statuses(NOON, fmp=fake, symbols=["ORCL"])
    fake.fail_days = {"2026-09-10"}
    with caplog.at_level(logging.WARNING):
        out = await svc.statuses(NOON + timedelta(minutes=21), fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_DUE_TODAY
    assert "hot refresh failed" in caplog.text
    n = len(fake.calls)
    await svc.statuses(NOON + timedelta(minutes=30), fmp=fake, symbols=["ORCL"])
    assert len(fake.calls) == n


@pytest.mark.asyncio
async def test_a_now_before_the_last_refresh_counts_as_due():
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    svc = EarningsWindowService()
    await svc.statuses(NOON, fmp=fake, symbols=["ORCL"])
    await svc.statuses(NOON - timedelta(minutes=5), fmp=fake, symbols=["ORCL"])
    assert len(fake.calls) == 10


def test_reset_equals_a_fresh_instance():
    svc = EarningsWindowService()
    svc._day = TODAY
    svc._reported_keys.add(("ORCL", TODAY))
    svc._hot_fetched_at = NOON
    svc.reset()
    fresh = EarningsWindowService()
    assert vars(svc) == vars(fresh)


@pytest.mark.asyncio
async def test_a_lenient_upstream_cannot_file_rows_under_the_wrong_day():
    """Every call answering with EVERY day's rows (a lenient upstream) must not file a
    row under a day it is not dated: a pending D+2 row filed under TODAY would read as
    "results pending today" and drive a hot refresh every 20 minutes for nothing."""

    class _Lenient(_DayFake):
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            self.calls.append((from_date, to_date))
            return [_row("2026-09-12", "MU")]          # dated D+2, returned for every day

    fake = _Lenient()
    svc = EarningsWindowService()
    await svc.statuses(NOON, fmp=fake, symbols=["MU"])
    assert {d: len(rows) for d, rows in svc._rows_by_day.items() if rows} == {
        date(2026, 9, 12): 1,
    }
    await svc.statuses(NOON + timedelta(minutes=25), fmp=fake, symbols=["MU"])
    assert len(fake.calls) == 8, "an upcoming report must not trigger a hot refresh"


@pytest.mark.asyncio
async def test_future_dated_actuals_never_become_a_sticky_report():
    """Review 2026-09-27: garbage actuals on a FUTURE row, later corrected to pending,
    used to read "reported" on the date — before the release — and to suppress both the
    hot refresh and the landing trigger."""
    monday = datetime(2026, 9, 14, 12, 0, tzinfo=ET)
    fake = _DayFake({"2026-09-16": [_row("2026-09-16", "AAPL", eps=1.5)]})
    svc = EarningsWindowService()
    assert await svc.statuses(monday, fmp=fake, symbols=["AAPL"]) == {}
    fake.days["2026-09-16"] = [_row("2026-09-16", "AAPL")]           # FMP corrects it
    tuesday = monday + timedelta(days=1)
    out = await svc.statuses(tuesday, fmp=fake, symbols=["AAPL"])
    assert out["AAPL"].status == EARNINGS_UPCOMING
    wednesday = monday + timedelta(days=2)
    out = await svc.statuses(wednesday, fmp=fake, symbols=["AAPL"])
    assert out["AAPL"].status == EARNINGS_DUE_TODAY, "never 'reported' before the release"
    # ...and the real results landing later that day are a landing.
    fake.days["2026-09-16"] = [_row("2026-09-16", "AAPL", eps=1.6)]
    evening = wednesday.replace(hour=16, minute=40)
    out = await svc.statuses(evening, fmp=fake, symbols=["AAPL"])
    assert out["AAPL"].status == EARNINGS_REPORTED
    assert out["AAPL"].reported_seen_at == evening


@pytest.mark.asyncio
async def test_a_duplicate_pending_and_reported_pair_in_one_response_is_not_a_landing():
    """Review 2026-09-27: [pending, reported] rows for one (symbol, date) in a single
    response used to stamp a landing on the first fetch — so every restart re-fired the
    one-shot regeneration."""
    fake = _DayFake({"2026-09-10": [_row("2026-09-10"), _row("2026-09-10", eps=1.92)]})
    out = await EarningsWindowService().statuses(NOON, fmp=fake, symbols=["ORCL"])
    assert out["ORCL"].status == EARNINGS_REPORTED
    assert out["ORCL"].reported_seen_at is None


@pytest.mark.asyncio
async def test_fetch_calendar_days_refuses_datetime_days_before_any_call():
    """`fetch_calendar_days` is public since 2026-10-09 (the Home Earnings Shockers card
    uses it). A `datetime` is a `date` subclass: it would be sent as `…T00:00:00` and then
    never equal a row's date, so every row would vanish as if the day were empty."""
    fake = _DayFake({"2026-09-10": [_row("2026-09-10")]})
    with pytest.raises(TypeError, match="needs datetime.date days"):
        await ews.fetch_calendar_days(fake.get_earnings_calendar, [NOON])
    with pytest.raises(TypeError):
        await ews.fetch_calendar_days(fake.get_earnings_calendar, ["2026-09-10"])
    assert fake.calls == []
    out = await ews.fetch_calendar_days(fake.get_earnings_calendar, [TODAY])
    assert [r["symbol"] for r in out[TODAY]] == ["ORCL"]
