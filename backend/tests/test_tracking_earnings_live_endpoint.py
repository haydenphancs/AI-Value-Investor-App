"""`GET /api/v1/tracking/earnings/{ticker}/live` — the Alerts sheet's live results.

Through FastAPI's real serialisation (`response_model`), with the identity overridden and
every upstream faked: today's row from the Tracking memo, the previous day's from the live
records, the year-ago / last-4 extras injected. Pins the input wall, the report-day choice,
the "none" answer and the wire keys iOS decodes, what the route answers with the live switch
off, and that a day nobody has read is never answered "none".
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pytest

import app.services.earnings_live_service as els
import app.services.tracking_service as ts
from app.config import settings
from app.services.earnings_filing_service import FilingFigures
from app.utils.market_hours import ET

REPORT = date(2026, 10, 29)
PREV = date(2026, 10, 28)

EXPECTED_KEYS = {
    "ticker", "company_name", "report_date", "status", "checked_at", "preliminary", "eps",
    "revenue", "filing", "guidance", "year_ago", "last_quarters", "poll_seconds",
}
METRIC_KEYS = {"estimate", "actual", "surprise_pct", "source", "basis", "checking"}


class _FMP:
    def __init__(self, rows: List[dict]):
        self.rows = rows

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        return [dict(r) for r in self.rows if from_date <= r["date"] <= to_date]


def _row(symbol, day, **kw):
    base = {"symbol": symbol, "date": day.isoformat(), "epsEstimated": 0.41,
            "revenueEstimated": 16.6e9, "epsActual": None, "revenueActual": None,
            "companyName": "Intel Corporation"}
    base.update(kw)
    return base


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.security import rate_limiter
    from app.dependencies import get_current_user_id, get_watchlist_identity
    from app.main import app

    # One shared bucket of 60 a minute, and this file makes more than that in a run.
    rate_limiter.clear()
    monkeypatch.setattr(ts, "_et_today", lambda: REPORT)
    els.get_earnings_live_service().reset()
    ts.reset_earnings_calendar_cache()
    fmp = _FMP([_row("INTC", REPORT), _row("AAPL", REPORT + timedelta(days=4))])
    monkeypatch.setattr(ts, "get_fmp_client", lambda: fmp)

    async def _no_extras(self, symbol, report_day, *, loader=None):
        return None

    monkeypatch.setattr(els.EarningsLiveService, "history_extras", _no_extras)
    app.dependency_overrides[get_current_user_id] = lambda: "u-1"
    app.dependency_overrides[get_watchlist_identity] = lambda: {"id": "u-1", "tier": "free"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_watchlist_identity, None)
        els.get_earnings_live_service().reset()
        ts.reset_earnings_calendar_cache()


def test_todays_report_is_answered_with_every_wire_key(client):
    resp = client.get("/api/v1/tracking/earnings/intc/live")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == EXPECTED_KEYS
    assert set(body["eps"]) == METRIC_KEYS and set(body["revenue"]) == METRIC_KEYS
    assert body["ticker"] == "INTC" and body["report_date"] == "2026-10-29"
    assert body["eps"]["estimate"] == 0.41 and body["revenue"]["estimate"] == 16.6e9
    assert body["company_name"] == "Intel Corporation"


def test_a_filing_figure_shows_preliminary_with_its_link(client):
    live = els.get_earnings_live_service()
    at = datetime(2026, 10, 29, 16, 3, tzinfo=ET).astimezone(timezone.utc)
    live.observe_rows(REPORT, [_row("INTC", REPORT)], at)
    live.absorb_filing("INTC", REPORT, FilingFigures(
        accession="0000050863-26-000123", accepted_at=at, url="https://www.sec.gov/idx",
        revenue=17.1e9, guidance="Fourth-quarter revenue of $17.5–18.5 billion."), at)
    body = client.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-10-29").json()
    assert body["revenue"]["actual"] == 17.1e9 and body["revenue"]["source"] == "filing"
    assert body["preliminary"] is True
    assert body["filing"]["url"] == "https://www.sec.gov/idx"
    assert body["guidance"].startswith("Fourth-quarter")


def test_without_a_date_the_previous_trading_days_report_is_chosen_when_today_has_none(client):
    live = els.get_earnings_live_service()
    live.observe_rows(PREV, [_row("MSFT", PREV, epsActual=3.1, revenueActual=7e10,
                                  epsEstimated=3.0, revenueEstimated=6.9e10)],
                      datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    body = client.get("/api/v1/tracking/earnings/MSFT/live").json()
    assert body["report_date"] == "2026-10-28" and body["status"] == "reported"


@pytest.mark.parametrize("query", ["", "?report_date=2026-10-24"], ids=["no_date", "by_date"])
def test_a_report_dated_on_a_closed_day_is_answered_on_the_next_trading_day(
        client, monkeypatch, query):
    saturday, monday = date(2026, 10, 24), date(2026, 10, 26)
    monkeypatch.setattr(ts, "_et_today", lambda: monday)
    els.get_earnings_live_service().observe_rows(
        saturday, [_row("MSFT", saturday, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                        revenueEstimated=6.9e10)],
        datetime(2026, 10, 24, 15, tzinfo=timezone.utc))
    body = client.get(f"/api/v1/tracking/earnings/MSFT/live{query}").json()
    assert (body["report_date"], body["status"]) == ("2026-10-24", "reported")


@pytest.mark.parametrize("reported_first", [False, True],
                         ids=["pending_first", "reported_first"])
def test_a_duplicate_pair_answers_with_the_reported_rows_figures(client, reported_first):
    pair = [_row("MSFT", REPORT, epsEstimated=0.35, revenueEstimated=15.9e9),
            _row("MSFT", REPORT, epsActual=0.44, revenueActual=17.1e9)]
    ts.get_fmp_client().rows[:] = pair[::-1] if reported_first else pair
    body = client.get("/api/v1/tracking/earnings/MSFT/live").json()
    assert (body["eps"]["estimate"], body["eps"]["surprise_pct"]) == (0.41, 7.32)
    assert (body["revenue"]["estimate"], body["revenue"]["surprise_pct"]) == (16.6e9, 3.01)


@pytest.mark.parametrize("reported_first", [False, True],
                         ids=["pending_first", "reported_first"])
def test_a_figure_held_by_a_twins_consensus_reaches_the_sheet_as_checking(client, reported_first):
    """No figure, no estimate, `checking` true: iOS draws "—" and "Confirming…". The twin's
    consensus held the figure and must not show."""
    pair = [_row("AVGO", REPORT, epsEstimated=1.65),
            _row("AVGO", REPORT, epsActual=0.169, epsEstimated=None, revenueEstimated=None)]
    ts.get_fmp_client().rows[:] = pair[::-1] if reported_first else pair
    body = client.get("/api/v1/tracking/earnings/AVGO/live").json()
    assert set(body["eps"]) == METRIC_KEYS and set(body["revenue"]) == METRIC_KEYS
    assert body["eps"]["checking"] is True
    assert (body["eps"]["estimate"], body["eps"]["actual"]) == (None, None)
    assert body["eps"]["surprise_pct"] is None
    assert body["revenue"]["estimate"] is None


@pytest.mark.parametrize("newest_first", [False, True])
def test_a_future_pair_shows_the_estimates_the_card_picks(client, newest_first):
    """No record exists yet, so the row is the sheet's only consensus: it must be the row the
    card picks, not whichever FMP listed first."""
    day = REPORT + timedelta(days=4)
    stale = _row("MSFT", day, epsEstimated=0.35, lastUpdated="2026-10-01")
    fresh = _row("MSFT", day, epsEstimated=0.41, lastUpdated="2026-10-20")
    ts.get_fmp_client().rows[:] = [fresh, stale] if newest_first else [stale, fresh]
    body = client.get("/api/v1/tracking/earnings/MSFT/live").json()
    assert body["status"] == "upcoming" and body["report_date"] == day.isoformat()
    assert body["eps"]["estimate"] == 0.41


def test_a_ticker_with_no_report_in_the_window_answers_none(client):
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live").json()
    assert body["status"] == "none"


def test_an_upcoming_report_in_the_window_is_answered_upcoming(client):
    body = client.get("/api/v1/tracking/earnings/AAPL/live").json()
    assert body["status"] == "upcoming" and body["report_date"] == "2026-11-02"


@pytest.mark.parametrize("path", [
    "/api/v1/tracking/earnings/IN%20TC/live",
    "/api/v1/tracking/earnings/TOOLONGTICKER1/live",
    "/api/v1/tracking/earnings/INTC/live?report_date=yesterday",
    "/api/v1/tracking/earnings/INTC/live?report_date=2026-12-31",
    "/api/v1/tracking/earnings/INTC/live?report_date=2026-10-01",
])
def test_bad_input_is_refused_before_any_read(client, path):
    resp = client.get(path)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error_code"] == "INVALID_INPUT"


def test_the_route_is_sign_in_only():
    """FMP's Order Form licenses display on the authenticated app alone."""
    import inspect

    from app.api.v1.endpoints import tracking as ep

    sig = inspect.signature(ep.get_earnings_live)
    dep = sig.parameters["user"].default
    from app.dependencies import get_watchlist_identity

    assert getattr(dep, "dependency", None) is get_watchlist_identity


def _the_loop_loads(day: date) -> None:
    """One real refresh, as the live loop runs it, 10:00 ET on `day`."""
    live = els.get_earnings_live_service()
    asyncio.run(live.refresh(_et(day, 10), fmp=ts.get_fmp_client(), symbols=["INTC"]))


def _the_loop_reads_the_previous_day() -> None:
    """The first load, with an unwatched row keeping that day from being blank, as a real
    calendar's trading day is."""
    ts.get_fmp_client().rows.append(_row("ZZZZ", PREV))
    _the_loop_loads(REPORT)


# A report FMP moved: INTC is listed for today and AAPL for 2026-11-02.


def test_a_pinned_sheet_follows_a_report_moved_off_its_date(client):
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-29").json()
    assert body["status"] == "upcoming" and body["report_date"] == "2026-11-02"


@pytest.mark.parametrize("pinned", ["2026-10-28", "2026-10-30", "2026-11-05"],
                         ids=["before_today", "before_the_new_date", "after_the_new_date"])
def test_a_pinned_date_fmp_no_longer_lists_follows_from_either_side(client, pinned):
    _the_loop_reads_the_previous_day()
    body = client.get(f"/api/v1/tracking/earnings/AAPL/live?report_date={pinned}").json()
    assert (body["status"], body["report_date"]) == ("upcoming", "2026-11-02")


def test_a_moved_report_answers_with_its_nearest_date(client):
    ts.get_fmp_client().rows.append(_row("AAPL", REPORT + timedelta(days=9)))
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-29").json()
    assert body["report_date"] == "2026-11-02"


def test_a_moved_report_carries_its_new_rows_consensus(client):
    ts.get_fmp_client().rows[1] = _row("AAPL", REPORT + timedelta(days=4), epsEstimated=1.9,
                                       companyName="Apple Inc.")
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-29").json()
    assert (body["eps"]["estimate"], body["company_name"]) == (1.9, "Apple Inc.")


def test_a_pinned_date_fmp_still_lists_stays_pinned(client):
    ts.get_fmp_client().rows.append(_row("AAPL", REPORT + timedelta(days=9)))
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-11-07").json()
    assert (body["status"], body["report_date"]) == ("upcoming", "2026-11-07")


def test_a_record_keeps_the_sheet_on_its_date_whatever_fmp_lists_now(client):
    """A record is what this process saw on that date, figures included. Only a date with
    neither a record nor a row has moved."""
    els.get_earnings_live_service().observe_rows(
        PREV, [_row("AAPL", PREV, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                    revenueEstimated=6.9e10)],
        datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-28").json()
    assert (body["status"], body["report_date"]) == ("reported", "2026-10-28")


def test_a_moved_report_is_followed_past_an_older_report_still_held(client):
    """The sheet was opened for one report, so it follows that one, never another the unpinned
    sheet would name."""
    els.get_earnings_live_service().observe_rows(
        PREV, [_row("AAPL", PREV)], datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-29").json()
    assert (body["status"], body["report_date"]) == ("upcoming", "2026-11-02")


def test_a_pinned_date_with_nothing_listed_still_answers_none(client):
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-10-29").json()
    assert (body["status"], body["report_date"]) == ("none", "2026-10-29")


def test_a_failed_calendar_read_never_moves_a_pinned_sheet(client, monkeypatch):
    async def _down(fmp, today, *, later_days_since=None):
        raise RuntimeError("FMP 503")

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _down)
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-11-02").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", "2026-11-02", 30)


# The live switch off. The fixture freezes only `_et_today`: `now` must be frozen too, or the
# report day is long past on the real clock and a sheet with the live state on is not LIVE.


class _FrozenDatetime:
    """`datetime` stand-in for tracking_service: `now()` is frozen, the rest delegates."""

    def __init__(self, at: datetime):
        self._at = at

    def now(self, tz=None):
        return self._at.astimezone(tz) if tz else self._at.replace(tzinfo=None)

    def __getattr__(self, name):
        return getattr(datetime, name)

    def __call__(self, *a, **kw):
        return datetime(*a, **kw)


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture
def extras_calls(client, monkeypatch) -> List[tuple]:
    """Every history build the route starts, in place of `client`'s stub."""
    calls: List[tuple] = []

    async def _spy(self, symbol, report_day, *, loader=None):
        calls.append((symbol, report_day))
        return None

    monkeypatch.setattr(els.EarningsLiveService, "history_extras", _spy)
    return calls


@pytest.fixture
def live_off(client, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", False)
    return client


_AT_THE_CLOSE = _et(REPORT, 16, 30)
_MEMO_ROWS = {
    "pending": {},
    "eps_in": {"epsActual": 0.30},
    "both_in": {"epsActual": 0.30, "revenueActual": 17.1e9},
}


def _estimate_only(estimate: Optional[float]) -> dict:
    return {"estimate": estimate, "actual": None, "surprise_pct": None, "source": None,
            "basis": None, "checking": False}


@pytest.mark.parametrize("query", ["", "?report_date=2026-10-29"], ids=["no_date", "by_date"])
@pytest.mark.parametrize("figures", _MEMO_ROWS.values(), ids=_MEMO_ROWS)
def test_live_off_answers_estimates_only(live_off, monkeypatch, extras_calls, figures, query):
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_AT_THE_CLOSE))
    ts.get_fmp_client().rows[:] = [_row("INTC", REPORT, **figures)]
    resp = live_off.get(f"/api/v1/tracking/earnings/INTC/live{query}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == EXPECTED_KEYS
    assert (body["status"], body["report_date"]) == ("upcoming", "2026-10-29")
    assert body["company_name"] == "Intel Corporation"
    assert body["eps"] == _estimate_only(0.41) and body["revenue"] == _estimate_only(16.6e9)
    assert body["poll_seconds"] is None and body["preliminary"] is False
    assert body["checked_at"] is None and body["guidance"] is None
    assert body["filing"] is None and body["year_ago"] is None and body["last_quarters"] == []
    assert extras_calls == [], "the spend the switch stops"
    assert els.get_earnings_live_service()._records == {}, "records made here are never pruned"


@pytest.mark.parametrize("figures,status,poll", [
    (_MEMO_ROWS["pending"], "live", 30),
    (_MEMO_ROWS["eps_in"], "live", 30),
    (_MEMO_ROWS["both_in"], "reported", None),
], ids=_MEMO_ROWS)
def test_the_same_read_with_live_on_answers_from_the_record(
        client, monkeypatch, extras_calls, figures, status, poll):
    """The other side of the switch at the same frozen moment: without this, the test above
    would hold on a sheet that was never live."""
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_AT_THE_CLOSE))
    ts.get_fmp_client().rows[:] = [_row("INTC", REPORT, **figures)]
    body = client.get("/api/v1/tracking/earnings/INTC/live").json()
    assert (body["status"], body["poll_seconds"]) == (status, poll)
    assert body["eps"]["actual"] == figures.get("epsActual")
    assert extras_calls == [("INTC", REPORT)]
    assert ("INTC", REPORT) in els.get_earnings_live_service()._records


@pytest.mark.parametrize("query,day", [("?report_date=2026-10-28", "2026-10-28"),
                                       ("", "2026-10-29")], ids=["by_date", "no_date"])
def test_live_off_never_reads_a_held_record(live_off, monkeypatch, query, day):
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_AT_THE_CLOSE))
    els.get_earnings_live_service().observe_rows(
        PREV, [_row("MSFT", PREV, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                    revenueEstimated=6.9e10)], _et(REPORT, 8))
    body = live_off.get(f"/api/v1/tracking/earnings/MSFT/live{query}").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == ("none", day, None)
    assert body["eps"]["actual"] is None and body["revenue"]["actual"] is None


def test_live_off_never_shows_a_filing_figure_held_for_today(live_off, monkeypatch):
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_AT_THE_CLOSE))
    live = els.get_earnings_live_service()
    live.observe_rows(REPORT, [_row("INTC", REPORT)], _et(REPORT, 16, 3))
    live.absorb_filing("INTC", REPORT, FilingFigures(
        accession="0000050863-26-000123", accepted_at=_et(REPORT, 16, 2),
        url="https://www.sec.gov/idx", revenue=17.1e9, guidance="Revenue of $18 billion."),
        _et(REPORT, 16, 3))
    body = live_off.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-10-29").json()
    assert (body["status"], body["poll_seconds"]) == ("upcoming", None)
    assert body["revenue"] == _estimate_only(16.6e9) and body["preliminary"] is False
    assert body["filing"] is None and body["guidance"] is None and body["checked_at"] is None


@pytest.mark.parametrize("ticker,query,status,day", [
    ("AAPL", "", "upcoming", "2026-11-02"),
    ("AAPL", "?report_date=2026-11-02", "upcoming", "2026-11-02"),
    ("AAPL", "?report_date=2026-10-29", "upcoming", "2026-11-02"),
    ("ZZZZ", "", "none", "2026-10-29"),
    ("ZZZZ", "?report_date=2026-11-02", "none", "2026-11-02"),
    ("ZZZZ", "?report_date=2026-10-28", "none", "2026-10-28"),
], ids=["later_row", "pinned_row", "pinned_sheet_follows", "no_row", "no_row_pinned_later",
        "no_row_pinned_before_today"])
def test_live_off_answers_the_memo_row_or_none(live_off, ticker, query, status, day):
    body = live_off.get(f"/api/v1/tracking/earnings/{ticker}/live{query}").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (status, day, None)


# A day nobody has read is unknown, never "none": iOS prints "No earnings report for X in the
# next two weeks" on `none`, and its poll stops for good on a null `poll_seconds`.



def _fmp_is_down(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise ts.FMPUnavailableException("cold and failing")

    monkeypatch.setattr(ts.get_fmp_client(), "get_earnings_calendar", _boom)


def test_a_previous_day_report_the_loop_has_not_loaded_is_awaiting_and_retried(
        client, extras_calls):
    """A deploy at 08:00 ET and a push tap at 08:00:30: the sheet must not say no report."""
    body = client.get("/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-28").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "awaiting", "2026-10-28", 30)
    assert body["eps"]["actual"] is None and body["year_ago"] is None
    assert extras_calls == []


def test_the_loops_first_load_ends_the_wait_with_the_report(client, monkeypatch):
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 10)))
    path = "/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-28"
    assert client.get(path).json()["poll_seconds"] == 30
    ts.get_fmp_client().rows.append(_row("MSFT", PREV, epsActual=0.44, revenueActual=17.1e9))
    _the_loop_loads(REPORT)
    body = client.get(path).json()
    assert (body["status"], body["poll_seconds"], body["eps"]["actual"]) == ("reported", None, 0.44)


def test_once_the_day_is_loaded_a_missing_report_still_answers_none(client):
    _the_loop_reads_the_previous_day()
    body = client.get("/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-28").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "none", "2026-10-28", None)


@pytest.mark.parametrize("today,asked", [
    (date(2026, 10, 25), date(2026, 10, 24)),
    (date(2026, 10, 26), date(2026, 10, 24)),
    (date(2026, 10, 26), date(2026, 10, 25)),
], ids=["saturday_on_sunday", "saturday_on_monday", "sunday_on_monday"])
def test_a_closed_day_the_loop_will_load_is_awaited_and_then_settled(
        client, monkeypatch, today, asked):
    """`live_days` holds every calendar day back to the previous trading day, so the loop does
    read these, and a single load is what ends the wait: no date is polled for ever."""
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    path = f"/api/v1/tracking/earnings/MSFT/live?report_date={asked.isoformat()}"
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "awaiting", asked.isoformat(), 30)
    _the_loop_loads(today)
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "none", asked.isoformat(), None)


def test_a_failed_load_keeps_the_previous_day_awaited(client, monkeypatch):
    _fmp_is_down(monkeypatch)
    _the_loop_loads(REPORT)
    body = client.get("/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-28").json()
    assert (body["status"], body["poll_seconds"]) == ("awaiting", 30)


def test_a_blank_first_load_of_a_trading_day_keeps_the_sheet_waiting(client, monkeypatch):
    """A plain 200 `[]`, no outage flag. The other side of the settled closed days above: a
    market with no report on a trading day is a blip, and it reads nothing."""
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 8)))
    path = "/api/v1/tracking/earnings/INTC/live?report_date=2026-10-28"
    _the_loop_loads(REPORT)
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "awaiting", "2026-10-28", 30)
    ts.get_fmp_client().rows.append(_row("INTC", PREV, epsActual=0.44, revenueActual=17.1e9))
    _the_loop_loads(REPORT)
    body = client.get(path).json()
    assert (body["status"], body["poll_seconds"]) == ("reported", None)
    assert body["eps"]["actual"] == 0.44 and body["revenue"]["actual"] == 17.1e9


def test_a_pinned_day_the_loop_has_not_loaded_holds_against_a_later_report(client, monkeypatch):
    """The stale row a company reporting earlier leaves behind: its consensus is another
    report's, and the sheet would stop on it for good with the figures one load away."""
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 8)))
    later = _row("MSFT", REPORT + timedelta(days=5), epsEstimated=0.99, revenueEstimated=20e9)
    ts.get_fmp_client().rows.append(later)
    path = "/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-28"
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "awaiting", "2026-10-28", 30)
    assert body["eps"] == _estimate_only(None) and body["revenue"] == _estimate_only(None)
    ts.get_fmp_client().rows.append(_row("MSFT", PREV, epsActual=0.44, revenueActual=17.1e9))
    _the_loop_loads(REPORT)
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "reported", "2026-10-28", None)
    assert body["eps"]["actual"] == 0.44 and body["eps"]["estimate"] == 0.41


def test_a_pinned_day_the_loop_has_not_loaded_then_follows_a_report_that_truly_moved(client):
    path = "/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-28"
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "awaiting", "2026-10-28", 30)
    _the_loop_reads_the_previous_day()
    body = client.get(path).json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", "2026-11-02", None)
    assert body["eps"]["estimate"] == 0.41


def test_live_off_a_pinned_previous_day_never_waits(live_off):
    """Nothing loads it with the switch off, so the sheet follows the memo's row as it did."""
    body = live_off.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-28").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", "2026-11-02", None)


@pytest.mark.parametrize("query,day", [
    ("?report_date=2026-10-29", "2026-10-29"),
    ("?report_date=2026-11-02", "2026-11-02"),
    ("?report_date=2026-11-12", "2026-11-12"),
    ("", "2026-10-29"),
], ids=["today", "days_out", "last_day_of_the_window", "no_date"])
def test_a_cold_calendar_failure_is_unknown_not_none(client, monkeypatch, extras_calls, query, day):
    _fmp_is_down(monkeypatch)
    resp = client.get(f"/api/v1/tracking/earnings/INTC/live{query}")
    assert resp.status_code == 200, "an outage is not a 502: the sheet just asks again"
    body = resp.json()
    assert set(body) == EXPECTED_KEYS
    assert (body["status"], body["report_date"], body["poll_seconds"]) == ("upcoming", day, 30)
    assert body["eps"] == _estimate_only(None) and body["revenue"] == _estimate_only(None)
    assert body["year_ago"] is None and body["last_quarters"] == []
    assert extras_calls == [], "no history fan-out during an outage"


def test_one_failed_day_of_a_cold_round_is_unknown_for_a_day_that_did_answer(client, monkeypatch):
    """One failed day fails the round, so the memo holds nothing, even for INTC's healthy day."""
    fmp = ts.get_fmp_client()
    healthy = fmp.get_earnings_calendar

    async def _one_day_down(from_date=None, to_date=None):
        if from_date == "2026-11-05":
            raise ts.FMPUnavailableException("one day down")
        return await healthy(from_date=from_date, to_date=to_date)

    monkeypatch.setattr(fmp, "get_earnings_calendar", _one_day_down)
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-10-29").json()
    assert (body["status"], body["poll_seconds"]) == ("upcoming", 30)


def test_a_cold_empty_window_is_unknown_not_none(client, monkeypatch, extras_calls):
    monkeypatch.setattr(ts.get_fmp_client(), "rows", [])
    body = client.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-10-29").json()
    assert (body["status"], body["poll_seconds"]) == ("upcoming", 30)
    assert extras_calls == []


def _a_blank_today(monkeypatch, today: date = REPORT) -> None:
    """A cold round: today comes back a plain `[]`, while the days after it hold rows."""
    monkeypatch.setattr(ts, "_et_today", lambda: today)
    ts.get_fmp_client().rows[:] = [_row("AAPL", today + timedelta(days=4))]


@pytest.mark.parametrize("query", ["", "?report_date=2026-10-29"], ids=["no_date", "by_date"])
def test_a_blank_today_on_a_trading_day_is_unknown_not_none(
        client, monkeypatch, extras_calls, query):
    """The memo stores a blank day, and it has no way to tell this one from a blip: a whole
    market reporting nothing on a trading day is not an answer about INTC."""
    _a_blank_today(monkeypatch)
    body = client.get(f"/api/v1/tracking/earnings/INTC/live{query}").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", "2026-10-29", 30)
    assert body["eps"] == _estimate_only(None) and body["revenue"] == _estimate_only(None)
    assert extras_calls == []


def test_a_blank_today_ends_with_the_memos_next_round_either_way(client, monkeypatch):
    _a_blank_today(monkeypatch)
    for ticker in ("INTC", "ZZZZ"):
        path = f"/api/v1/tracking/earnings/{ticker}/live?report_date=2026-10-29"
        assert client.get(path).json()["poll_seconds"] == 30
    ts.get_fmp_client().rows.append(_row("INTC", REPORT))
    ts.reset_earnings_calendar_cache()
    body = client.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-10-29").json()
    assert body["eps"]["estimate"] == 0.41
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-10-29").json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)


def test_a_blank_today_never_moves_a_pinned_sheet_to_a_later_report(client, monkeypatch):
    _a_blank_today(monkeypatch)
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-29").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", "2026-10-29", 30)
    assert body["eps"] == _estimate_only(None)


def test_a_blank_closed_today_is_a_read(client, monkeypatch, extras_calls):
    """A weekend holds no reports, so its blank is the honest answer."""
    saturday = date(2026, 10, 31)
    _a_blank_today(monkeypatch, saturday)
    body = client.get(f"/api/v1/tracking/earnings/INTC/live?report_date={saturday}").json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)
    assert extras_calls == []


def test_a_blank_day_after_today_is_a_read(client, extras_calls):
    """Only today: FMP can honestly list nothing on a trading day a week out."""
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-11-04").json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)


def test_live_off_a_blank_today_answers_none_without_a_poll(live_off, monkeypatch):
    _a_blank_today(monkeypatch)
    body = live_off.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-10-29").json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)


def test_a_day_the_memo_read_and_found_empty_stays_none(client, extras_calls):
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-11-03").json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)
    assert extras_calls == []


def test_a_day_a_failed_round_left_out_is_unknown_and_a_held_day_is_not(client, monkeypatch):
    """The rollover: a held window served after a failed round has no row for its new tail."""
    client.get("/api/v1/tracking/earnings/ZZZZ/live")
    tomorrow = REPORT + timedelta(days=1)
    monkeypatch.setattr(ts, "_et_today", lambda: tomorrow)
    _fmp_is_down(monkeypatch)
    tail, held = tomorrow + timedelta(days=14), tomorrow + timedelta(days=3)
    body = client.get(f"/api/v1/tracking/earnings/ZZZZ/live?report_date={tail}").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", tail.isoformat(), 30)
    body = client.get(f"/api/v1/tracking/earnings/ZZZZ/live?report_date={held}").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "none", held.isoformat(), None)


def test_a_day_a_failed_round_left_out_holds_a_pinned_sheet_against_a_held_row(
        client, monkeypatch):
    """No answer covers that day yet, so nothing says the report left it. AAPL's row is on a day
    the memo did read, and it is not the report this sheet was opened for."""
    client.get("/api/v1/tracking/earnings/ZZZZ/live")
    tomorrow = REPORT + timedelta(days=1)
    monkeypatch.setattr(ts, "_et_today", lambda: tomorrow)
    _fmp_is_down(monkeypatch)
    tail = tomorrow + timedelta(days=14)
    body = client.get(f"/api/v1/tracking/earnings/AAPL/live?report_date={tail}").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", tail.isoformat(), 30)
    assert body["eps"] == _estimate_only(None)


def test_the_first_read_after_an_outage_ends_the_recheck_either_way(client, monkeypatch):
    fmp = ts.get_fmp_client()
    healthy = fmp.get_earnings_calendar
    _fmp_is_down(monkeypatch)
    for ticker in ("AAPL", "ZZZZ"):
        assert client.get(f"/api/v1/tracking/earnings/{ticker}/live").json()["poll_seconds"] == 30
    monkeypatch.setattr(fmp, "get_earnings_calendar", healthy)
    ts.reset_earnings_calendar_cache()
    body = client.get("/api/v1/tracking/earnings/AAPL/live").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "upcoming", "2026-11-02", None)
    body = client.get("/api/v1/tracking/earnings/ZZZZ/live").json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)


def test_an_outage_never_hides_a_report_held_in_a_record(client, monkeypatch, extras_calls):
    monkeypatch.setattr(ts, "datetime", _FrozenDatetime(_et(REPORT, 10)))
    els.get_earnings_live_service().observe_rows(
        PREV, [_row("MSFT", PREV, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                    revenueEstimated=6.9e10)], _et(REPORT, 8))
    _fmp_is_down(monkeypatch)
    body = client.get("/api/v1/tracking/earnings/MSFT/live").json()
    assert (body["status"], body["report_date"], body["poll_seconds"]) == (
        "reported", "2026-10-28", None)
    assert extras_calls == [("MSFT", PREV)]


def test_a_report_with_a_row_never_rechecks(client):
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-11-02").json()
    assert (body["status"], body["poll_seconds"]) == ("upcoming", None)


def test_a_report_with_a_row_and_no_record_builds_its_history(client, extras_calls):
    """The year-ago block and the last four quarters are on every upcoming sheet: only an
    answer that found nothing goes without them."""
    client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-11-02")
    assert extras_calls == [("AAPL", date(2026, 11, 2))]


@pytest.mark.parametrize("query", ["", "?report_date=2026-10-29", "?report_date=2026-11-02",
                                   "?report_date=2026-10-28"],
                         ids=["no_date", "today", "days_out", "before_today"])
def test_the_off_switch_wins_over_an_unknown_answer(live_off, monkeypatch, extras_calls, query):
    """The loop is idle: nothing a poll could wait for is coming."""
    _fmp_is_down(monkeypatch)
    resp = live_off.get(f"/api/v1/tracking/earnings/INTC/live{query}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)
    assert extras_calls == []


def test_a_date_before_the_sheet_days_is_still_refused(client):
    resp = client.get("/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-26")
    assert resp.status_code == 400 and resp.json()["error_code"] == "INVALID_INPUT"
    assert "2026-10-27" in resp.json()["message"], "it names the first day it does take"


# ── the day after the live days, for a push held overnight ───────────────────

OLDER = date(2026, 10, 27)
OLDER_PATH = "/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-27"


def test_a_report_a_trading_day_past_the_live_days_still_answers_its_figures(client):
    """A result held for the morning can wake here, and the push opens this date."""
    assert OLDER < els.live_days(REPORT)[0]
    at = datetime(2026, 10, 27, 22, tzinfo=timezone.utc)
    els.get_earnings_live_service().observe_rows(
        OLDER, [_row("MSFT", OLDER, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                     revenueEstimated=6.9e10)], at, fetched=True)
    resp = client.get(OLDER_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["status"], body["report_date"]) == ("reported", "2026-10-27")
    assert body["eps"]["actual"] == 3.1 and body["revenue"]["actual"] == 7e10
    assert body["poll_seconds"] is None


def test_with_no_date_the_sheet_never_reaches_past_the_live_days(client):
    """Only a date asks for that day: it holds no card, so nothing else may open it."""
    at = datetime(2026, 10, 27, 22, tzinfo=timezone.utc)
    els.get_earnings_live_service().observe_rows(
        OLDER, [_row("MSFT", OLDER, epsActual=3.1, revenueActual=7e10)], at, fetched=True)
    body = client.get("/api/v1/tracking/earnings/MSFT/live").json()
    assert (body["status"], body["eps"]["actual"]) == ("none", None)


def test_after_a_restart_that_day_waits_for_its_first_look_and_never_says_none(client):
    """The record went with the process. `none` would tell someone just buzzed that results are
    out that INTC has no report, so the sheet asks again, and the loop's next round answers."""
    body = client.get(OLDER_PATH).json()
    assert (body["status"], body["poll_seconds"]) == ("awaiting", els.EARNINGS_LIVE_POLL_SECONDS)
    assert body["report_date"] == "2026-10-27"


def test_a_first_look_that_found_nothing_on_that_day_is_a_plain_none(client):
    live = els.get_earnings_live_service()
    live._fetched[OLDER] = 1.0
    body = client.get(OLDER_PATH).json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)


def test_with_the_loop_off_that_day_is_a_plain_none(live_off):
    body = live_off.get(OLDER_PATH).json()
    assert (body["status"], body["poll_seconds"]) == ("none", None)
