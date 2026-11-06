"""`GET /api/v1/tracking/earnings/{ticker}/live` — the Alerts sheet's live results.

Through FastAPI's real serialisation (`response_model`), with the identity overridden and
every upstream faked: today's row from the Tracking memo, the previous day's from the live
records, the year-ago / last-4 extras injected. Pins the input wall, the report-day choice,
the "none" answer and the wire keys iOS decodes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List

import pytest

import app.services.earnings_live_service as els
import app.services.tracking_service as ts
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

    from app.dependencies import get_current_user_id, get_watchlist_identity
    from app.main import app

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


# A report FMP moved: INTC is listed for today and AAPL for 2026-11-02.


def test_a_pinned_sheet_follows_a_report_moved_off_its_date(client):
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-29").json()
    assert body["status"] == "upcoming" and body["report_date"] == "2026-11-02"


@pytest.mark.parametrize("pinned", ["2026-10-28", "2026-10-30", "2026-11-05"],
                         ids=["before_today", "before_the_new_date", "after_the_new_date"])
def test_a_pinned_date_fmp_no_longer_lists_follows_from_either_side(client, pinned):
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
    assert (body["status"], body["report_date"]) == ("none", "2026-11-02")
