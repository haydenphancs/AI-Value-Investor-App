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
