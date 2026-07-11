"""
Tracking service — alert roll-up correctness (analyst / whale / insider).

Guards three fixes:
1. Analyst: EVERY material grade change per ticker surfaces (not just the
   first), so "N rating changes" counts changes, not tickers.
2. Whale: institutional (13F, exact) and congressional (STOCK Act range) trades
   on the same ticker/action stay in SEPARATE items — a precise 13F figure is
   never flipped into a fuzzy range or mislabeled is_congress.
3. Insider: items order by the exact raw_amount, not a rounding-lossy label.

Inline fakes only — no network / Supabase.
Run: cd backend && ./venv/bin/pytest tests/test_tracking_alert_rollups.py -x
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import tracking_service as tsvc
from app.services.tracking_service import TrackingService


_RECENT = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")


# ════════════════════════════ Analyst rollup ═════════════════════════════


class _GradesFMP:
    def __init__(self, grades):
        self._grades = grades

    async def get_grades(self, ticker, limit=20):
        return self._grades.get(ticker.upper(), [])


def _grade(firm, action, prev, new):
    return {
        "publishedDate": _RECENT,
        "gradingCompany": firm,
        "action": action,
        "previousGrade": prev,
        "newGrade": new,
        "priceTarget": 200.0,
        "previousPriceTarget": 180.0,
    }


@pytest.mark.asyncio
async def test_analyst_surfaces_every_material_change_per_ticker():
    svc = TrackingService()
    svc.fmp = _GradesFMP({
        "CRM": [
            _grade("Goldman Sachs", "downgrade", "Buy", "Neutral"),
            _grade("Morgan Stanley", "upgrade", "Neutral", "Overweight"),
        ]
    })
    alerts = await svc._get_analyst_rating_alerts(["CRM"])
    assert len(alerts) == 1
    items = alerts[0].analyst_rating_items
    assert len(items) == 2, "both firms' actions must surface, not just the first"
    assert {it.firm_name for it in items} == {"Goldman Sachs", "Morgan Stanley"}
    assert {it.rating_action for it in items} == {"upgrade", "downgrade"}
    assert "2 rating changes" in alerts[0].description
    assert "CRM" in alerts[0].description


@pytest.mark.asyncio
async def test_analyst_dedups_same_firm_multiple_rows():
    svc = TrackingService()
    svc.fmp = _GradesFMP({
        "CRM": [
            _grade("Goldman Sachs", "upgrade", "Neutral", "Buy"),
            _grade("Goldman Sachs", "upgrade", "Neutral", "Buy"),  # dup firm
        ]
    })
    alerts = await svc._get_analyst_rating_alerts(["CRM"])
    assert len(alerts) == 1
    assert len(alerts[0].analyst_rating_items) == 1
    assert "1 rating change" in alerts[0].description


@pytest.mark.asyncio
async def test_analyst_maintains_are_filtered_out():
    svc = TrackingService()
    svc.fmp = _GradesFMP({
        "CRM": [_grade("Barclays", "maintain", "Buy", "Buy")]  # non-material
    })
    alerts = await svc._get_analyst_rating_alerts(["CRM"])
    assert alerts == []


# ════════════════════════════ Whale rollup ═══════════════════════════════


class _WhaleTable:
    def __init__(self, rows):
        self._rows = rows

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
        r.data = self._rows
        return r


class _WhaleSupabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return _WhaleTable(self._rows if name == "whale_trades" else [])


def _wrow(action, amount, amount_range, whale_id, name):
    return {
        "ticker": "CRM", "company_name": "Salesforce", "action": action,
        "amount": amount, "amount_range": amount_range,
        "date": _RECENT, "created_at": datetime.now().isoformat(),
        "whale_id": whale_id,
        "whales": {"name": name, "avatar_url": None, "firm_name": None},
    }


@pytest.mark.asyncio
async def test_whale_13f_and_congress_on_same_ticker_stay_separate(monkeypatch):
    rows = [
        _wrow("BOUGHT", 2_000_000_000.0, None, "inst1", "Citadel"),          # 13F exact
        _wrow("BOUGHT", 8_000.0, "$1,001 - $15,000", "rep1", "Ro Khanna"),   # congress range
    ]
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _WhaleSupabase(rows))
    svc = TrackingService()
    alerts = await svc._get_whale_trade_alerts(["CRM"])

    assert len(alerts) == 1
    bought = alerts[0]
    assert bought.action == "bought"
    items = {it.is_congress: it for it in bought.whale_trade_items}
    assert set(items.keys()) == {True, False}, "13F and congress must be distinct items"

    inst = items[False]
    assert inst.is_congress is False
    assert inst.amount == "$2.00B"            # exact, never a fuzzy range
    assert inst.raw_amount == pytest.approx(2_000_000_000.0)

    cong = items[True]
    assert cong.is_congress is True
    assert "–" in cong.amount                  # honest STOCK Act range
    assert cong.raw_amount == pytest.approx(8_000.0)

    # Largest first, and the ticker appears once in the sentence despite 2 items.
    assert bought.whale_trade_items[0].raw_amount >= bought.whale_trade_items[1].raw_amount
    assert bought.description.count("CRM") == 1


# ════════════════════════════ Insider rollup ═════════════════════════════


class _InsiderFMP:
    def __init__(self, trades):
        self._trades = trades

    async def get_insider_trading(self, ticker, limit=30):
        return self._trades.get(ticker.upper(), [])


def _tx(shares, price, name):
    return {
        "transactionDate": _RECENT,
        "transactionType": "S-Sale",
        "securitiesTransacted": shares,
        "price": price,
        "reportingName": name,
        "typeOfOwner": "CFO",
    }


@pytest.mark.asyncio
async def test_insider_items_order_by_exact_amount_not_label():
    # X = $999,900 and Y = $1,040,000 BOTH format to "$1.0M", so a label-based
    # sort would tie and keep input order [X, Y]; sorting by raw_amount must put
    # the genuinely-larger Y first.
    svc = TrackingService()
    svc.fmp = _InsiderFMP({
        "X": [_tx(9_999, 100.0, "Alice Smith")],     # 999_900
        "Y": [_tx(10_400, 100.0, "Bob Jones")],      # 1_040_000
    })
    alerts = await svc._get_insider_transaction_alerts(["X", "Y"])
    sold = [a for a in alerts if a.action == "sold"]
    assert len(sold) == 1
    items = sold[0].insider_transaction_items
    assert [it.ticker for it in items] == ["Y", "X"]
    assert items[0].raw_amount > items[1].raw_amount


@pytest.mark.asyncio
async def test_insider_below_threshold_is_dropped():
    svc = TrackingService()
    svc.fmp = _InsiderFMP({"Z": [_tx(100, 100.0, "Tiny Trader")]})  # $10K < $100K
    alerts = await svc._get_insider_transaction_alerts(["Z"])
    assert alerts == []
