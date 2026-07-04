"""
Whale-trade alerts — first-hydration backfill guard.

The query windows on created_at, but a newly added whale's FIRST hydration
inserts months-old 13F filings with created_at=now. Without the guard, the
"Whales Bought this week" alert would present May filings as this week's
activity (guaranteed once for every whale added by the registry expansion).
13F rows are therefore also gated on their own `date` (the filing date);
congress rows keep the created_at window — their `date` is the TRANSACTION
date, which legitimately lags the disclosure that makes the trade news.

Run via `python -m pytest` from backend/.
"""

import asyncio
from datetime import datetime, timedelta

from app.services import tracking_service as tsvc
from app.services.tracking_service import TrackingService


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


def _alerts(monkeypatch, rows):
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(rows))
    return asyncio.get_event_loop().run_until_complete(
        TrackingService()._get_whale_trade_alerts(["ORCL"])
    )


def _row(**over):
    base = {
        "ticker": "ORCL", "company_name": "Oracle", "action": "BOUGHT",
        "amount": 2_400_000.0, "amount_range": None,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "created_at": datetime.now().isoformat(),
        "whale_id": "w1",
        "whales": {"name": "Ray Dalio", "avatar_url": None,
                   "firm_name": "Bridgewater Associates"},
    }
    base.update(over)
    return base


def test_backfilled_old_13f_filing_is_excluded(monkeypatch):
    # created_at = now (just hydrated) but the filing itself is 2 months old —
    # must NOT surface as "this week" activity.
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    assert _alerts(monkeypatch, [_row(date=old)]) == []


def test_fresh_13f_filing_is_included(monkeypatch):
    alerts = _alerts(monkeypatch, [_row()])
    assert len(alerts) == 1, "fresh filing must produce the weekly alert"


def test_old_congress_transaction_date_still_included(monkeypatch):
    # Congress: traded in May, DISCLOSED this week (created_at=now). The
    # date-guard must NOT drop it — disclosure recency is what matters.
    old = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    alerts = _alerts(monkeypatch, [_row(
        date=old, amount_range="$50,001 - $100,000", amount=75_000.0,
    )])
    assert len(alerts) == 1


def test_13f_missing_date_degrades_to_created_at_window(monkeypatch):
    # Unparseable/blank date → keep the row (old created_at-only behavior),
    # never drop data on a formatting hiccup.
    alerts = _alerts(monkeypatch, [_row(date=None)])
    assert len(alerts) == 1


def test_mixed_backfill_and_fresh_keeps_only_fresh(monkeypatch):
    # One old backfilled filing + one fresh filing on different tickers —
    # only the fresh one survives into the rolled-up alert.
    old = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    alerts = _alerts(monkeypatch, [
        _row(date=old, ticker="AAPL", company_name="Apple"),
        _row(),
    ])
    assert len(alerts) == 1
    tickers = {i.ticker for i in alerts[0].whale_trade_items}
    assert tickers == {"ORCL"}
    # The lead whale's firm rides along on the surviving item.
    assert alerts[0].whale_trade_items[0].lead_whale_firm == "Bridgewater Associates"
