"""
Whale-trade alerts — first-hydration backfill guard.

The query windows on created_at, but a newly added whale's FIRST hydration
inserts months-old 13F filings with created_at=now. Without the guard, the
"Whales Bought this week" alert would present May filings as this week's
activity (guaranteed once for every whale added by the registry expansion).

⚠️ A 13F row's `date` is the QUARTER END its filing describes — the hydrators
write FMP's `institutional-ownership/dates` date — NOT the filing date. The guard
used to require it inside the 7-day window, and since a 13F can only be filed
after its quarter ends (usually weeks after), EVERY 13F row failed it and only
congressional rows ever reached the card (C14, 2026-09-25). This file used to
build 13F rows dated TODAY — a shape production never writes — so it passed
against the broken guard. The fixtures below use quarter ends.

13F rows are now gated on "belongs to the latest filed quarter": date on/after the
first day of the previous calendar quarter. Congress rows keep the created_at
window — their `date` is the TRANSACTION date, which legitimately lags the
disclosure that makes the trade news.

Run via `python -m pytest` from backend/.
"""

import asyncio
from datetime import date, datetime

import pytest

from app.services import tracking_service as tsvc
from app.services.tracking_service import TrackingService

# Q3 2026: the latest quarter a 13F can describe is Q2 (ended 06-30, due 08-14).
TODAY = datetime(2026, 8, 10, 12, 0)
PREV_Q_END = "2026-06-30"
TWO_Q_AGO_END = "2026-03-31"


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        class _R:
            pass
        r = _R()
        r.data = self._data
        return r


class _FakeSupabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return _FakeQuery(self._rows if name == "whale_trades" else [])


def _frozen(now):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.replace(tzinfo=tz)
    return _Clock


def _alerts(monkeypatch, rows, *, today=TODAY):
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(rows))
    monkeypatch.setattr(tsvc, "datetime", _frozen(today))
    # `asyncio.run`, not `get_event_loop()` — see the note in test_whale_activity_feed.py.
    # This helper is the single choke point for the file, so the stale-loop failure takes
    # every test in it down at once and none of them reach an assertion.
    return asyncio.run(
        TrackingService()._get_whale_trade_alerts(["ORCL"])
    )


def _row(**over):
    base = {
        "ticker": "ORCL", "company_name": "Oracle", "action": "BOUGHT",
        "amount": 2_400_000.0, "amount_range": None,
        # What production writes for a 13F: the quarter END, hydrated weeks later.
        "date": PREV_Q_END,
        "created_at": TODAY.isoformat(),
        "whale_id": "w1",
        "whales": {"name": "Ray Dalio", "avatar_url": None,
                   "firm_name": "Bridgewater Associates"},
    }
    base.update(over)
    return base


def test_the_latest_quarters_13f_filing_is_included(monkeypatch):
    """THE regression: a Q2 filing (date 06-30) hydrated on 08-10 is this week's news.
    The 7-day window on its quarter-end date dropped it (06-30 < 08-03)."""
    alerts = _alerts(monkeypatch, [_row()])
    assert len(alerts) == 1, "a fresh 13F filing must produce the weekly alert"
    assert {i.ticker for i in alerts[0].whale_trade_items} == {"ORCL"}


def test_a_deadline_day_filing_is_included(monkeypatch):
    """Q2 is due 08-14; a deadline-day filer is hydrated that night and read on 08-15."""
    alerts = _alerts(monkeypatch, [_row()], today=datetime(2026, 8, 15, 12, 0))
    assert len(alerts) == 1


def test_backfilled_older_quarter_is_excluded(monkeypatch):
    # created_at = now (just hydrated) but the filing is for Q1 — a first hydration's
    # history, which must NOT surface as "this week" activity.
    assert _alerts(monkeypatch, [_row(date=TWO_Q_AGO_END)]) == []


@pytest.mark.parametrize("today,kept,dropped", [
    # Year boundary: in Q1 the latest filed quarter is last year's Q4.
    (datetime(2026, 2, 17, 12, 0), "2025-12-31", "2025-09-30"),
    # The hydrators' fallback date is `{year}-{q*3:02d}-30`: 12-30 for Q4, 03-30 for Q1 —
    # a day before the true end. A quarter-END floor would drop exactly these.
    (datetime(2026, 2, 17, 12, 0), "2025-12-30", "2025-09-30"),
    (datetime(2026, 5, 20, 12, 0), "2026-03-30", "2025-12-31"),
    # Last day of a quarter: still the same floor.
    (datetime(2026, 9, 30, 23, 0), "2026-06-30", "2026-03-31"),
])
def test_the_floor_is_the_previous_quarters_first_day(monkeypatch, today, kept, dropped):
    alerts = _alerts(monkeypatch, [
        _row(date=kept, ticker="KEEP", company_name="Keep"),
        _row(date=dropped, ticker="DROP", company_name="Drop"),
    ], today=today)
    assert len(alerts) == 1
    assert {i.ticker for i in alerts[0].whale_trade_items} == {"KEEP"}


def test_old_congress_transaction_date_still_included(monkeypatch):
    # Congress: traded in March, DISCLOSED this week (created_at=now). The
    # date-guard must NOT drop it — disclosure recency is what matters.
    alerts = _alerts(monkeypatch, [_row(
        date="2026-03-02", amount_range="$50,001 - $100,000", amount=75_000.0,
    )])
    assert len(alerts) == 1


def test_13f_missing_date_degrades_to_created_at_window(monkeypatch):
    # Unparseable/blank date → keep the row (old created_at-only behavior),
    # never drop data on a formatting hiccup.
    alerts = _alerts(monkeypatch, [_row(date=None)])
    assert len(alerts) == 1


def test_mixed_backfill_and_fresh_keeps_only_fresh(monkeypatch):
    # One backfilled older-quarter filing + one fresh filing on different tickers —
    # only the fresh one survives into the rolled-up alert.
    alerts = _alerts(monkeypatch, [
        _row(date=TWO_Q_AGO_END, ticker="AAPL", company_name="Apple"),
        _row(),
    ])
    assert len(alerts) == 1
    tickers = {i.ticker for i in alerts[0].whale_trade_items}
    assert tickers == {"ORCL"}
    # The lead whale's firm rides along on the surviving item.
    assert alerts[0].whale_trade_items[0].lead_whale_firm == "Bridgewater Associates"


def test_the_helper_matches_the_documented_examples():
    assert tsvc._thirteen_f_floor(date(2026, 8, 10)) == "2026-04-01"
    assert tsvc._thirteen_f_floor(date(2026, 1, 1)) == "2025-10-01"
    assert tsvc._thirteen_f_floor(date(2026, 3, 31)) == "2025-10-01"
    assert tsvc._thirteen_f_floor(date(2026, 4, 1)) == "2026-01-01"
    assert tsvc._thirteen_f_floor(date(2026, 12, 31)) == "2026-07-01"
