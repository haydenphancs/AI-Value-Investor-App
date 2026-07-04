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


def test_whale_rows_subtitle_carries_firm_name(monkeypatch):
    # Post-merge (migration 080): person-fronted whales carry firm_name, and the
    # drill-down row's subtitle shows the FIRM so the name never appears alone.
    # Whales without a firm (true institutions) keep the "13F fund" fallback.
    tables = {
        "whales": [
            {"id": "wp", "name": "Ray Dalio", "cik": "CIK1",
             "firm_name": "Bridgewater Associates",
             "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w2", "name": "Renaissance Technologies", "cik": "CIK2",
             "firm_name": None,
             "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "wp", "ticker": "TSM", "allocation": 1.6, "change_percent": 1.6},
            {"whale_id": "w2", "ticker": "TSM", "allocation": 0.9, "change_percent": 0.2},
        ],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")

    by_name = {r.name: r for r in rows}
    assert by_name["Ray Dalio"].subtitle == "Bridgewater Associates"
    assert by_name["Renaissance Technologies"].subtitle == "13F fund"


def test_whale_rows_firm_edge_cases_whitespace_and_unicode(monkeypatch):
    # Whitespace-only firm (bad row edit) → generic fallback, never a blank
    # subtitle; ampersand firm names (live registry data) pass through intact.
    tables = {
        "whales": [
            {"id": "w1", "name": "Broken Row", "cik": "C1", "firm_name": "   ",
             "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w2", "name": "Duan Yongping", "cik": "C2",
             "firm_name": "H&H International Investment",
             "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "w1", "ticker": "TSM", "allocation": 1.0, "change_percent": 0.5},
            {"whale_id": "w2", "ticker": "TSM", "allocation": 2.0, "change_percent": 1.1},
        ],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")

    by_name = {r.name: r for r in rows}
    assert by_name["Broken Row"].subtitle == "13F fund"
    assert by_name["Duan Yongping"].subtitle == "H&H International Investment"


def test_whale_rows_empty_when_nothing_adding(monkeypatch):
    tables = {
        "whales": [{"id": "w1", "name": "Citadel", "last_hydrated_at": "2026-03-31T00:00:00Z"}],
        "whale_holdings": [],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")
    assert rows == []


def test_whale_rows_reraise_on_supabase_error(monkeypatch):
    # A Supabase failure must PROPAGATE (not swallow to []) so get_ticker_detail
    # returns an uncached empty response and the next tap retries.
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(ssvc, "get_supabase", boom)
    with pytest.raises(RuntimeError):
        _svc()._detail_whale_rows("TSM")


@pytest.mark.asyncio
async def test_get_ticker_detail_does_not_cache_whale_transient_failure(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(ssvc, "get_supabase", boom)
    s = _svc()
    s.fmp = _FakeFMP(profile={})   # profile empty too → totally empty result
    resp = await s.get_ticker_detail("whale", "TSM")
    assert resp.holders == []                                    # degraded, not a 500
    assert "whale:TSM" not in SignalsService._detail_cache       # NOT pinned for 10 min


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
    # Registry: only Pelosi (senate) is tracked → only she is tappable. Keyed by
    # (chamber, normalized-name) so a same-named House member can't false-match.
    monkeypatch.setattr(
        s, "_congress_registry_map",
        lambda: {("senate", _norm_name("Nancy Pelosi")): "whale-pelosi"},
    )

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


def test_norm_name_is_order_sensitive():
    # Order-PRESERVING: "First Last" matches itself (case / whitespace / punctuation
    # insensitive) but permutations must NOT collide (a collision could deep-link a
    # tap to the wrong politician's profile).
    assert _norm_name("Nancy Pelosi") == _norm_name("nancy  pelosi!")
    assert _norm_name("Robert J. Smith") != _norm_name("J. Robert Smith")
    assert _norm_name("Pelosi, Nancy") != _norm_name("Nancy Pelosi")
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


def test_signal_holder_fully_populated_variants_round_trip():
    # Both kinds' full field sets validate (pins the wire shape the iOS DTO decodes).
    whale = SignalHolderResponse.model_validate({
        "whale_id": "w1", "name": "Citadel", "subtitle": "13F fund",
        "transaction_date": "2026-03-31", "disclosure_date": None,
        "allocation_percent": 3.1, "allocation_change": 1.2, "is_new_position": False,
        "amount_est": 2_400_000.0, "amount_range": None, "owner": None, "action": "BOUGHT",
    })
    assert whale.whale_id == "w1" and whale.amount_est == 2_400_000.0
    congress = SignalHolderResponse.model_validate({
        "whale_id": None, "name": "Nancy Pelosi", "subtitle": "Senator (CA)",
        "transaction_date": "2026-03-15", "disclosure_date": "2026-05-15",
        "amount_range": "$1K – $15K", "owner": "Self", "action": "BOUGHT",
    })
    assert congress.amount_range == "$1K – $15K" and congress.owner == "Self"


# ── Outlier / boundary hardening (deep-review additions) ────────────────


def test_whale_row_uses_most_recent_trade_date_not_largest_amount(monkeypatch):
    # A whale with a big OLD buy and a smaller RECENT buy: show the RECENT date +
    # its amount (coherent + fresh), not the older larger-amount trade's date.
    tables = {
        "whales": [
            {"id": "w1", "name": "Citadel", "cik": "C1", "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "w1", "ticker": "TSM", "allocation": 2.0, "change_percent": 1.0},
        ],
        "whale_trades": [
            {"whale_id": "w1", "ticker": "TSM", "action": "BOUGHT", "trade_type": "Increased",
             "amount": 5_000_000, "date": "2025-12-31"},   # big, OLD
            {"whale_id": "w1", "ticker": "TSM", "action": "BOUGHT", "trade_type": "Increased",
             "amount": 900_000, "date": "2026-03-31"},      # small, RECENT
        ],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")
    assert len(rows) == 1
    assert rows[0].transaction_date == "2026-03-31"     # most recent, not 2025-12-31
    assert rows[0].amount_est == 900_000                # that trade's amount (coherent)


def test_whale_rows_null_cik_stay_distinct(monkeypatch):
    tables = {
        "whales": [
            {"id": "w1", "name": "A Fund", "cik": None, "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w2", "name": "B Fund", "cik": "", "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "w1", "ticker": "TSM", "allocation": 1.0, "change_percent": 1.0},
            {"whale_id": "w2", "ticker": "TSM", "allocation": 1.0, "change_percent": 1.0},
        ],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")
    assert len(rows) == 2   # null/blank CIK → sentinel per whale, NOT collapsed


def test_whale_rows_change_percent_zero_excluded(monkeypatch):
    tables = {
        "whales": [
            {"id": "w1", "name": "Adder", "cik": "C1", "last_hydrated_at": "2026-03-31T00:00:00Z"},
            {"id": "w2", "name": "Flat", "cik": "C2", "last_hydrated_at": "2026-03-31T00:00:00Z"},
        ],
        "whale_holdings": [
            {"whale_id": "w1", "ticker": "TSM", "allocation": 2.0, "change_percent": 1.5},
            {"whale_id": "w2", "ticker": "TSM", "allocation": 3.0, "change_percent": 0.0},  # not adding
        ],
        "whale_trades": [],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("TSM")
    assert [r.name for r in rows] == ["Adder"]   # change_percent==0 excluded


def test_whale_rows_match_class_share_variant(monkeypatch):
    # Holdings stored as "BRK.B" must match a request canonicalized to "BRK-B".
    tables = {
        "whales": [{"id": "w1", "name": "Berkshire Fund", "cik": "C1", "last_hydrated_at": "2026-03-31T00:00:00Z"}],
        "whale_holdings": [{"whale_id": "w1", "ticker": "BRK.B", "allocation": 2.5, "change_percent": 0.5}],
        "whale_trades": [{"whale_id": "w1", "ticker": "BRK.B", "action": "BOUGHT", "trade_type": "Increased",
                          "amount": 1_200_000, "date": "2026-03-31"}],
    }
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    rows, _ = _svc()._detail_whale_rows("BRK-B")
    assert len(rows) == 1 and rows[0].name == "Berkshire Fund" and rows[0].amount_est == 1_200_000


@pytest.mark.asyncio
async def test_congress_dedups_multiple_filings_per_member_keeps_latest(monkeypatch):
    recent, _ = _congress_dates()
    from datetime import datetime, timedelta, timezone
    earlier = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": earlier, "transactionDate": earlier,
         "firstName": "Nancy", "lastName": "Pelosi", "district": "CA", "amount": "$1,001 - $15,000"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent, "transactionDate": recent,
         "firstName": "Nancy", "lastName": "Pelosi", "district": "CA", "amount": "$15,001 - $50,000"},
    ]
    s = _svc()
    s.fmp = _FakeFMP(senate=senate, house=[])
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    rows, as_of = await s._detail_congress_rows("NVDA")
    assert len(rows) == 1                          # ONE row per member (matches the card count)
    assert rows[0].disclosure_date == recent       # most recent filing kept
    assert rows[0].amount_range == "$15K – $50K"


@pytest.mark.asyncio
async def test_congress_registry_chamber_scoped_no_false_match(monkeypatch):
    # A HOUSE member with the SAME name as a tracked SENATE member must NOT inherit
    # the senator's whale_id (chamber-scoped key).
    recent, _ = _congress_dates()
    house = [{"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent, "transactionDate": recent,
              "firstName": "Nancy", "lastName": "Pelosi", "district": "CA1", "amount": "$1,001 - $15,000"}]
    s = _svc()
    s.fmp = _FakeFMP(senate=[], house=house)
    monkeypatch.setattr(s, "_congress_registry_map",
                        lambda: {("senate", _norm_name("Nancy Pelosi")): "whale-senate-pelosi"})
    rows, _ = await s._detail_congress_rows("NVDA")
    assert len(rows) == 1 and rows[0].whale_id is None   # house member ≠ senate registry entry


@pytest.mark.asyncio
async def test_congress_handles_missing_owner_and_district(monkeypatch):
    recent, _ = _congress_dates()
    senate = [{"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent, "transactionDate": recent,
               "firstName": "Charlie", "lastName": "Minimal", "amount": "$1,001 - $15,000"}]  # no owner/district
    s = _svc()
    s.fmp = _FakeFMP(senate=senate, house=[])
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    rows, _ = await s._detail_congress_rows("NVDA")
    assert len(rows) == 1
    assert rows[0].subtitle == "Senator"   # no district suffix
    assert rows[0].owner is None
