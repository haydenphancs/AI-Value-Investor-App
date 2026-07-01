"""
Per-ticker signal drill-down — aggregation + registry-match + degradation tests.

No network / Supabase: a fake FMP client + a tiny chainable Supabase shim feed
the pure logic. Run via `python -m pytest` (needs cwd on path — no conftest).
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.signals_detail import (
    SignalHolderResponse,
    SignalTickerDetailResponse,
)
from app.services import signals_service as ssvc
from app.services.signals_service import SignalsService, _norm_name, _congress_role


# ── Fakes ──────────────────────────────────────────────────────────────


class _FakeQuery:
    """Ignores filters (the tests supply already-filtered canned rows) and
    returns the table's data on execute()."""

    def __init__(self, data):
        self._data = data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        class _R:
            pass
        r = _R()
        r.data = self._data
        return r


class _FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


class _FakeFMP:
    def __init__(self, senate=None, house=None, profile=None):
        self._senate = senate or []
        self._house = house or []
        self._profile = profile or {}

    async def get_senate_latest(self, limit=1000):
        return self._senate

    async def get_house_latest(self, limit=1000):
        return self._house

    async def get_company_profile(self, ticker):
        return self._profile


def _svc():
    SignalsService._detail_cache.clear()
    SignalsService._detail_inflight.clear()
    return SignalsService()


# ── Whale drill-down ───────────────────────────────────────────────────


def test_whale_rows_join_registry_all_tappable_with_amount_and_ranking(monkeypatch):
    tables = {
        "whales": [
            {"id": "w1", "name": "Citadel Advisors", "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w2", "name": "Renaissance Tech", "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w3", "name": "AQR Capital", "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "w1", "ticker": "TSM", "allocation": 3.1, "change_percent": 1.2},
            {"whale_id": "w2", "ticker": "TSM", "allocation": 5.0, "change_percent": 0.8},
            {"whale_id": "w3", "ticker": "TSM", "allocation": 2.0, "change_percent": -0.5},  # trimmed → excluded
            {"whale_id": "wX", "ticker": "TSM", "allocation": 9.0, "change_percent": 4.0},   # not a 13F registry whale → excluded
        ],
        "whale_trades": [
            {"whale_id": "w1", "ticker": "TSM", "action": "BOUGHT", "trade_type": "Increased",
             "amount": 2_400_000, "date": "2026-03-31", "disclosure_date": "2026-05-15"},
            # w2 has no BOUGHT trade row → amount_est stays None
        ],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, as_of = _svc()._detail_whale_rows("TSM")

    assert [r.name for r in rows] == ["Citadel Advisors", "Renaissance Tech"]  # w3 trimmed, wX non-registry
    assert all(r.whale_id is not None for r in rows)          # every whale is a registry fund → tappable
    # w1 ranks first ($ est present); w2 has no $ est.
    assert rows[0].whale_id == "w1" and rows[0].amount_est == 2_400_000
    assert rows[0].allocation_percent == 3.1 and rows[0].allocation_change == 1.2
    assert rows[1].whale_id == "w2" and rows[1].amount_est is None
    assert as_of == "2026-03-31"


def test_whale_rows_dedup_shared_cik_person_and_fund(monkeypatch):
    # A fund registered under BOTH a person and a firm name shares ONE CIK →
    # must appear once (matches the card's distinct-fund count).
    tables = {
        "whales": [
            {"id": "wp", "name": "Ray Dalio", "cik": "CIK1", "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "wf", "name": "Bridgewater Associates", "cik": "CIK1", "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w2", "name": "Citadel", "cik": "CIK2", "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "wp", "ticker": "TSM", "allocation": 1.63, "change_percent": 1.63},
            {"whale_id": "wf", "ticker": "TSM", "allocation": 1.63, "change_percent": 1.63},
            {"whale_id": "w2", "ticker": "TSM", "allocation": 0.9, "change_percent": 0.2},
        ],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")
    assert len(rows) == 2                                   # CIK1 collapsed, CIK2 separate
    names = {r.name for r in rows}
    assert "Citadel" in names
    assert "Bridgewater Associates" in names               # tie-break name asc wins the shared CIK
    assert "Ray Dalio" not in names


def test_whale_rows_empty_when_nothing_adding(monkeypatch):
    tables = {
        "whales": [{"id": "w1", "name": "Citadel", "last_hydrated_at": "2026-03-31T00:00:00Z"}],
        "whale_holdings": [],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")
    assert rows == []


def test_whale_rows_degrade_to_empty_on_supabase_error(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(ssvc, "get_supabase", boom)
    rows, as_of = _svc()._detail_whale_rows("TSM")
    assert rows == [] and as_of is None


# ── Congress drill-down ────────────────────────────────────────────────


def _congress_dates():
    today = datetime.now(timezone.utc)
    return today.strftime("%Y-%m-%d"), (today - timedelta(days=40)).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_congress_rows_filters_and_registry_match(monkeypatch):
    recent, old = _congress_dates()
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent, "transactionDate": recent,
         "firstName": "Nancy", "lastName": "Pelosi", "district": "CA", "owner": "Self",
         "amount": "$1,001 - $15,000"},
        {"symbol": "NVDA", "type": "Sale", "disclosureDate": recent,
         "firstName": "Sell", "lastName": "Only", "district": "TX"},          # sale → excluded
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": old,
         "firstName": "Too", "lastName": "Old", "district": "NY"},            # out of 30d window → excluded
        {"symbol": "AAPL", "type": "Purchase", "disclosureDate": recent,
         "firstName": "Other", "lastName": "Ticker", "district": "FL"},       # different ticker → excluded
    ]
    house = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent, "transactionDate": recent,
         "firstName": "Random", "lastName": "Member", "district": "OH11", "owner": "Spouse",
         "amount": "$15,001 - $50,000"},
    ]
    s = _svc()
    s.fmp = _FakeFMP(senate=senate, house=house)
    # Registry: only Pelosi is tracked → only she is tappable.
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {_norm_name("Nancy Pelosi"): "whale-pelosi"})

    rows, as_of = await s._detail_congress_rows("NVDA")

    names = [r.name for r in rows]
    assert "Nancy Pelosi" in names and "Random Member" in names
    assert "Sell Only" not in names and "Too Old" not in names and "Other Ticker" not in names
    pelosi = next(r for r in rows if r.name == "Nancy Pelosi")
    assert pelosi.whale_id == "whale-pelosi"          # in registry → tappable
    assert pelosi.subtitle == "Senator (CA)" and pelosi.owner == "Self"
    assert pelosi.amount_range == "$1K – $15K"        # filed range, not a midpoint
    member = next(r for r in rows if r.name == "Random Member")
    assert member.whale_id is None                     # not tracked → plain row
    assert member.subtitle == "Representative (OH-11)"
    assert as_of == recent


@pytest.mark.asyncio
async def test_congress_rows_empty_when_no_buyers(monkeypatch):
    s = _svc()
    s.fmp = _FakeFMP(senate=[], house=[])
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    rows, as_of = await s._detail_congress_rows("NVDA")
    assert rows == [] and as_of is None


# ── get_ticker_detail (end to end w/ fakes) + degradation ──────────────


@pytest.mark.asyncio
async def test_get_ticker_detail_congress_wraps_header_and_holders(monkeypatch):
    recent, _ = _congress_dates()
    senate = [{"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent,
               "firstName": "Nancy", "lastName": "Pelosi", "district": "CA",
               "amount": "$1,001 - $15,000"}]
    s = _svc()
    s.fmp = _FakeFMP(senate=senate, house=[],
                     profile={"companyName": "NVIDIA Corp", "price": 170.0, "marketCap": 4.2e12})
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    resp = await s.get_ticker_detail("congress", "NVDA")
    assert isinstance(resp, SignalTickerDetailResponse)
    assert resp.symbol == "NVDA" and resp.kind == "congress"
    assert resp.company_name == "NVIDIA Corp" and resp.price == 170.0 and resp.market_cap == 4.2e12
    assert len(resp.holders) == 1 and resp.holders[0].name == "Nancy Pelosi"


@pytest.mark.asyncio
async def test_get_ticker_detail_degrades_on_fmp_error(monkeypatch):
    class _BoomFMP:
        async def get_company_profile(self, t): return {}
        async def get_senate_latest(self, limit=1000): raise RuntimeError("fmp down")
        async def get_house_latest(self, limit=1000): return []
    s = _svc()
    s.fmp = _BoomFMP()
    resp = await s.get_ticker_detail("congress", "NVDA")
    assert resp.symbol == "NVDA" and resp.holders == []   # degraded, not a 500


@pytest.mark.asyncio
async def test_get_ticker_detail_unknown_kind_is_empty(monkeypatch):
    s = _svc()
    s.fmp = _FakeFMP(profile={})
    resp = await s.get_ticker_detail("bogus", "NVDA")
    assert resp.kind == "bogus" and resp.holders == []


# ── Helpers ────────────────────────────────────────────────────────────


def test_norm_name_is_order_insensitive():
    assert _norm_name("Pelosi, Nancy") == _norm_name("Nancy Pelosi")
    assert _norm_name("Sheldon Whitehouse") != _norm_name("Nancy Pelosi")


def test_congress_role_formatting():
    assert _congress_role("KY", "senate") == "Senator (KY)"
    assert _congress_role("TX11", "house") == "Representative (TX-11)"
    assert _congress_role("", "house") == "Representative"


# ── Schema parity (backend ↔ iOS SignalHolderDTO / SignalTickerDetailDTO) ──

_HOLDER_KEYS = {
    "whale_id", "name", "subtitle", "transaction_date", "disclosure_date",
    "allocation_percent", "allocation_change", "is_new_position", "amount_est",
    "amount_range", "owner", "action",
}
_DETAIL_KEYS = {
    "symbol", "kind", "company_name", "price", "market_cap", "as_of_date", "holders",
}


def test_signal_detail_schema_keys_match_ios_dto():
    holder = SignalHolderResponse(name="Citadel", subtitle="13F fund")
    assert set(holder.model_dump().keys()) == _HOLDER_KEYS
    detail = SignalTickerDetailResponse(symbol="TSM", kind="whale")
    assert set(detail.model_dump().keys()) == _DETAIL_KEYS
    # A fully-populated payload round-trips (worst-case-ish nullability).
    SignalTickerDetailResponse.model_validate({
        "symbol": "TSM", "kind": "whale", "company_name": "", "price": None,
        "market_cap": None, "as_of_date": None,
        "holders": [{"name": "X", "subtitle": "", "action": "BOUGHT"}],
    })
