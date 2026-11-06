"""`GET /api/v1/tracking/earnings/{ticker}/live` — the Alerts sheet's live results.

Through FastAPI's real serialisation (`response_model`), with the identity overridden and
every upstream faked: today's row from the Tracking memo, the previous day's from the live
records, the year-ago / last-4 extras injected. Pins the input wall, the report-day choice,
the "none" answer, which only a read that worked may give, and the wire keys iOS decodes.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import List

import pytest

import app.services.earnings_live_service as els
import app.services.tracking_service as ts
from app.api.v1.endpoints import tracking as endpoint
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


@pytest.mark.parametrize("query", ["", "?report_date=2026-10-29"], ids=["no_date", "by_date"])
def test_a_leftover_row_answers_with_the_report_the_trading_day_before(client, query):
    """Today's row has nothing on it and the report came out yesterday, so the sheet must not
    say the results are not in."""
    live = els.get_earnings_live_service()
    at = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    live.observe_rows(PREV, [_row("INTC", PREV)], at)
    live.absorb_filing("INTC", PREV, FilingFigures(
        accession="0000050863-26-000123", accepted_at=at - timedelta(hours=14),
        url="https://www.sec.gov/idx", revenue=17.1e9, eps_adjusted=0.44), at)
    body = client.get(f"/api/v1/tracking/earnings/INTC/live{query}").json()
    assert (body["report_date"], body["status"]) == ("2026-10-29", "reported")
    assert (body["eps"]["actual"], body["revenue"]["actual"]) == (0.44, 17.1e9)
    assert body["preliminary"] is True and body["filing"]["url"] == "https://www.sec.gov/idx"


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
])
def test_bad_input_is_refused_before_any_read(client, path):
    resp = client.get(path)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error_code"] == "INVALID_INPUT"


def test_a_date_past_the_calendar_says_which_window(client):
    resp = client.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-12-31")
    assert (resp.status_code, resp.json()["error_code"]) == (400, "INVALID_INPUT")
    assert "2026-12-31 is outside 2026-10-28..2026-11-12" in resp.json()["message"]


def test_the_last_day_of_the_calendar_is_still_answered(client):
    resp = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-11-12")
    assert (resp.status_code, resp.json()["status"]) == (200, "none")


# An inbox row keeps "See live results" and its report date for 90 days, and that report's record
# leaves with the live days: Thursday the 29th has the 28th and itself.


@pytest.mark.parametrize("old", ["2026-10-27", "2026-10-26", "2026-09-01", "2016-01-04"])
def test_an_old_inbox_row_is_answered_and_never_refused(client, old):
    """A 400 here is for good: the row's date never comes back into the window, so its Retry can
    never work, and the screen has no figures of its own to fall back on."""
    resp = client.get(f"/api/v1/tracking/earnings/ZZZZ/live?report_date={old}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "none", "a report still to come is the one thing it must not say"
    assert body["poll_seconds"] is None


def test_an_old_inbox_row_answers_like_an_open_sheet_when_a_report_is_up(client):
    """Nothing can be read for the old date, so the sheet says what is true now."""
    pinned = client.get("/api/v1/tracking/earnings/INTC/live?report_date=2026-10-26").json()
    opened = client.get("/api/v1/tracking/earnings/INTC/live").json()
    assert pinned == opened and pinned["report_date"] == "2026-10-29"


def test_an_old_inbox_row_is_answered_from_the_report_held_since(client):
    els.get_earnings_live_service().observe_rows(
        PREV, [_row("MSFT", PREV, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                    revenueEstimated=6.9e10)],
        datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    body = client.get("/api/v1/tracking/earnings/MSFT/live?report_date=2026-10-26").json()
    assert (body["status"], body["report_date"]) == ("reported", "2026-10-28")


def test_the_first_day_of_the_live_days_stays_pinned(client):
    """The floor itself is inside: a report held on it keeps its date."""
    els.get_earnings_live_service().observe_rows(
        PREV, [_row("AAPL", PREV, epsActual=3.1, revenueActual=7e10, epsEstimated=3.0,
                    revenueEstimated=6.9e10)],
        datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    body = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-10-28").json()
    assert (body["status"], body["report_date"]) == ("reported", "2026-10-28")


def test_an_old_inbox_row_during_an_outage_is_an_error_the_client_can_retry(client, monkeypatch):
    _calendar_down(monkeypatch)
    resp = client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-10-26")
    assert (resp.status_code, resp.json()["error_code"]) == (502, "FMP_UNAVAILABLE")


def test_an_old_date_says_so_in_the_log_with_the_parsed_date(client, caplog):
    with caplog.at_level(logging.INFO, logger=ts.logger.name):
        client.get("/api/v1/tracking/earnings/ZZZZ/live?report_date=2026-10-26%0AFORGED-LINE")
    (line,) = [r.getMessage() for r in caplog.records if "left the live days" in r.getMessage()]
    assert "ZZZZ" in line and "2026-10-26" in line and "FORGED" not in line


@pytest.mark.parametrize("estimate", [0, 0.0, -1.0, "0", None])
def test_a_zero_revenue_estimate_is_no_consensus_on_the_sheet(client, estimate):
    """A record built from this row would carry none, so the sheet before it must not print
    "$0", and the EPS consensus beside it still shows."""
    day = REPORT + timedelta(days=4)
    ts.get_fmp_client().rows[:] = [
        _row("JOBY", day, revenueEstimated=estimate, epsEstimated=-0.05),
        _row("ZZZY", REPORT),
    ]
    body = client.get("/api/v1/tracking/earnings/JOBY/live").json()
    assert (body["status"], body["report_date"]) == ("upcoming", day.isoformat())
    assert (body["eps"]["estimate"], body["revenue"]["estimate"]) == (-0.05, None)


def test_a_real_revenue_estimate_still_shows_on_the_sheet(client):
    day = REPORT + timedelta(days=4)
    ts.get_fmp_client().rows[:] = [_row("JOBY", day, revenueEstimated=23.5e6)]
    body = client.get("/api/v1/tracking/earnings/JOBY/live").json()
    assert body["revenue"]["estimate"] == 23.5e6


def _logged(caplog) -> List[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == endpoint.logger.name and r.levelno >= logging.ERROR]


def test_a_response_shape_bug_is_logged_and_never_answers_bad_input(client, monkeypatch, caplog):
    """pydantic's `ValidationError` is a `ValueError`, so a catch of `ValueError` around the
    service would turn this bug into a 400 blaming the caller, with nothing in the logs."""
    real = ts.live_payload

    def _no_status(*args, **kwargs):
        payload = real(*args, **kwargs)
        del payload["status"]
        return payload

    monkeypatch.setattr(ts, "live_payload", _no_status)
    with caplog.at_level(logging.ERROR, logger=endpoint.logger.name):
        resp = client.get("/api/v1/tracking/earnings/INTC/live")
    assert resp.status_code >= 500 and resp.json()["error_code"] != "INVALID_INPUT"
    (line,) = _logged(caplog)
    assert "INTC" in line.getMessage() and "ValidationError" in line.getMessage()
    assert line.exc_info, "the stack comes with it"


def test_any_other_value_error_from_the_service_reaches_the_logged_branch(
        client, monkeypatch, caplog):
    async def _bug(self, symbol, requested=None):
        raise ValueError("not a date problem")

    monkeypatch.setattr(ts.TrackingService, "get_earnings_live", _bug)
    with caplog.at_level(logging.ERROR, logger=endpoint.logger.name):
        resp = client.get("/api/v1/tracking/earnings/INTC/live"
                          "?report_date=2026-10-29%0AFORGED-LOG-LINE")
    assert resp.status_code >= 500 and resp.json()["error_code"] != "INVALID_INPUT"
    (line,) = _logged(caplog)
    assert "INTC" in line.getMessage() and REPORT.isoformat() in line.getMessage()
    assert "FORGED" not in line.getMessage(), "the parsed date, never the caller's text"


def test_a_failed_calendar_read_warns_with_the_ticker_and_the_day(client, monkeypatch, caplog):
    async def _down(fmp, today, *, later_days_since=None):
        raise RuntimeError("FMP 503")

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _down)
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        client.get("/api/v1/tracking/earnings/INTC/live")
    lines = [r.getMessage() for r in caplog.records
             if r.levelno == logging.WARNING and "calendar unavailable" in r.getMessage()]
    assert lines and all("INTC" in line and REPORT.isoformat() in line
                         and "RuntimeError: FMP 503" in line for line in lines)


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


def _calendar_down(monkeypatch) -> None:
    """The memo's own read fails, as after a restart in an FMP outage: no day of it is held."""
    async def _down(from_date=None, to_date=None):
        raise RuntimeError("FMP 503")

    monkeypatch.setattr(ts.get_fmp_client(), "get_earnings_calendar", _down)


@pytest.mark.parametrize("query", ["", "?report_date=2026-10-29", "?report_date=2026-11-02"],
                         ids=["no_date", "today", "a_later_date"])
def test_a_failed_calendar_read_is_an_error_never_a_report_nobody_scheduled(
        client, monkeypatch, query):
    """`none` is "No earnings report in the next two weeks", and this read cannot know that.
    An error is what shows the card's retry notice."""
    _calendar_down(monkeypatch)
    resp = client.get(f"/api/v1/tracking/earnings/INTC/live{query}")
    body = resp.json()
    assert (resp.status_code, body["error_code"]) == (502, "FMP_UNAVAILABLE")
    assert body["user_message"], "the body is the iOS error shape"
    assert body["details"]["ticker"] == "INTC" and body["details"]["step"] == "earnings_live"


def test_a_failed_calendar_read_is_handled_so_it_warns_and_opens_no_error(
        client, monkeypatch, caplog):
    """A sheet polled through an outage must not open one Sentry issue per read."""
    _calendar_down(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=endpoint.logger.name):
        client.get("/api/v1/tracking/earnings/INTC/live"
                   "?report_date=2026-11-02%0AFORGED-LOG-LINE")
    assert _logged(caplog) == []
    (line,) = [r.getMessage() for r in caplog.records if r.name == endpoint.logger.name]
    assert "INTC" in line and "2026-11-02" in line
    assert "FORGED" not in line and "\n" not in line, "the parsed date, never the caller's text"


def test_a_failed_calendar_read_still_answers_a_report_a_record_holds(client, monkeypatch):
    live = els.get_earnings_live_service()
    live.observe_rows(PREV, [_row("MSFT", PREV, epsActual=3.1, revenueActual=7e10,
                                  epsEstimated=3.0, revenueEstimated=6.9e10)],
                      datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    _calendar_down(monkeypatch)
    resp = client.get("/api/v1/tracking/earnings/MSFT/live")
    assert resp.status_code == 200, resp.text
    assert (resp.json()["status"], resp.json()["report_date"]) == ("reported", "2026-10-28")


def test_the_retry_after_an_outage_finds_the_report(client, monkeypatch):
    fmp = ts.get_fmp_client()
    with monkeypatch.context() as outage:
        _calendar_down(outage)
        assert client.get("/api/v1/tracking/earnings/AAPL/live").status_code == 502
    assert fmp.rows, "the outage is over"
    ts.reset_earnings_calendar_cache()                          # and so is the memo's wait
    body = client.get("/api/v1/tracking/earnings/AAPL/live").json()
    assert (body["status"], body["report_date"]) == ("upcoming", "2026-11-02")


def test_a_failed_calendar_read_never_moves_a_pinned_sheet(client, monkeypatch):
    async def _down(fmp, today, *, later_days_since=None):
        raise RuntimeError("FMP 503")

    monkeypatch.setattr(ts, "_cached_earnings_calendar", _down)
    resp = client.get("/api/v1/tracking/earnings/AAPL/live?report_date=2026-11-02")
    assert (resp.status_code, resp.json()["error_code"]) == (502, "FMP_UNAVAILABLE")
