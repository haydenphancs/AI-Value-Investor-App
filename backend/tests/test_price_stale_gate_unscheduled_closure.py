"""An unscheduled market closure must not read as a missed ingest the next morning.

`_snapshot_is_current` accepts a stored close only when `trade_date >=
previous_trading_day(session)`, and `previous_trading_day` knew only the scheduled holiday
table. A weekday closure not in the table (a national day of mourning, weather) made the
NEXT session's previous trading day the closure itself, so the stored close — the day
before it, exactly right — was judged stale for every symbol: tiles showed '—', Top Movers
emptied, %-move alerts never fired, all day. The ingest's bellwether probe already sees the
closure as "not a US session"; it now teaches the calendar.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.price_service import PriceService
from app.utils import market_hours as mh


@pytest.fixture(autouse=True)
def _clean():
    mh._OBSERVED_CLOSURES.clear()
    yield
    mh._OBSERVED_CLOSURES.clear()


def test_a_registered_closure_is_skipped_by_previous_trading_day():
    thu = date(2026, 9, 17)                       # a plain weekday, not in the table
    fri = date(2026, 9, 18)
    assert mh.is_trading_day(thu)
    assert mh.previous_trading_day(fri) == thu
    mh.register_market_closure(thu)
    assert not mh.is_trading_day(thu)
    assert mh.previous_trading_day(fri) == date(2026, 9, 16)


def test_weekends_are_never_registered():
    mh.register_market_closure(date(2026, 9, 19))     # Saturday
    assert mh._OBSERVED_CLOSURES == set()


def test_the_stored_close_survives_the_morning_after_an_unscheduled_closure(monkeypatch):
    friday_0900 = datetime(2026, 9, 18, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr("app.services.price_service.session_trading_date",
                        lambda now=None: date(2026, 9, 18))
    snap = {"close": 100.0, "previous_close": 99.0, "trade_date": "2026-09-16"}
    # Before the closure is known: Wednesday's row is one session short → stale → None.
    assert PriceService._pick_denominator(101.0, snap) is None
    # The ingest walk observed Thursday had no US session.
    mh.register_market_closure(date(2026, 9, 17))
    assert PriceService._pick_denominator(101.0, snap) == 100.0


@pytest.mark.asyncio
async def test_the_ingest_walk_registers_the_non_session_it_steps_over(monkeypatch):
    svc = PriceService.__new__(PriceService)
    calls = []

    async def _eod(fmp, target):
        calls.append(target)
        # Thursday: rows, but no bellwethers (an international-only day)
        return [{"symbol": "SAP.DE", "close": 1.0}] if target == "2026-09-17" else \
               [{"symbol": "AAPL", "close": 1.0}, {"symbol": "MSFT", "close": 1.0}, {"symbol": "SPY", "close": 1.0}]
    monkeypatch.setattr(PriceService, "_batch_eod_with_backoff", staticmethod(_eod))
    monkeypatch.setattr(PriceService, "_is_us_session", staticmethod(
        lambda rows: any(r.get("symbol") in {"AAPL", "MSFT", "SPY"} for r in rows)))
    monkeypatch.setattr("app.services.price_service.get_fmp_client", lambda: object())
    monkeypatch.setattr(PriceService, "_step_back", staticmethod(lambda t: "2026-09-16"))
    target, rows = await svc._fetch_latest_session("2026-09-17")
    assert target == "2026-09-16" and rows
    assert (2026, 9, 17) in mh._OBSERVED_CLOSURES
