"""
Per-ticker signal drill-down — aggregation + registry-match + degradation tests.

No network / Supabase: a fake FMP client + a tiny chainable Supabase shim feed
the pure logic. Run via `python -m pytest` (needs cwd on path — no conftest).
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.fmp import FMPPartialPageException
from app.schemas.signals_detail import (
    SignalHolderResponse,
    SignalTickerDetailResponse,
)
from app.services import signals_service as ssvc
from app.services.signals_service import SignalsService, _norm_name, _congress_role
from _price_fakes import PriceFromFMPFake


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
    def gte(self, *a, **k): return self
    def limit(self, *a, **k): return self
    # These two arrived with `fetch_all_rows` (2026-09-12): the reads are PAGED now,
    # because `.limit(5000)` never lifted PostgREST's ~1,000-row server cap and the
    # "N funds adding" count was silently truncated. A fake that lacks them makes every
    # caller degrade through its `except` and the tests pass for the wrong reason.
    def order(self, *a, **k): return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        class _R:
            pass
        r = _R()
        start, end = getattr(self, "_range", (0, len(self._data)))
        r.data = self._data[start:end + 1]
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


# ── Whale drill-down: share increases in each fund's latest 13F ─────────
#
# Since 2026-10-09 the card and this list read `whale_trades` (BOUGHT New / Increased in the
# fund's latest, current 13F group) — not `whale_holdings.change_percent`, a WEIGHT change.
# The fake ignores filters, so the Python re-checks are what these tests exercise.
# At _DNOW (2026-06-30) the expected 13F quarter is 2026-Q1.

_DNOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
_Q1 = "2026-03-31"


def _w(wid, name, cik=None, firm=None, filed="2026-Q1"):
    return {"id": wid, "name": name, "cik": cik, "firm_name": firm,
            "last_hydrated_at": "2026-04-02T00:00:00Z", "last_filing_period": filed}


def _g(wid, date=_Q1):
    return {"id": f"g-{wid}-{date}", "whale_id": wid, "date": date}


def _t(wid, ticker, *, action="BOUGHT", trade_type="Increased", amount=1_000_000.0,
       date=_Q1, alloc=1.0, name=None):
    return {"id": f"t-{wid}-{ticker}-{action}-{date}", "whale_id": wid,
            "trade_group_id": f"g-{wid}-{date}", "ticker": ticker,
            "company_name": name or ticker, "action": action, "trade_type": trade_type,
            "amount": amount, "new_allocation": alloc, "date": date}


def _tables(whales, trades, groups=None, holdings=None):
    return {
        "whales": whales,
        "whale_trade_groups": groups if groups is not None else [_g(w["id"]) for w in whales],
        "whale_trades": trades,
        "whale_holdings": holdings or [],
    }


def _rows(monkeypatch, tables, sym="TSM"):
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    return _svc()._detail_whale_rows(sym, now=_DNOW)


def test_whale_rows_join_registry_all_tappable_with_amount_and_ranking(monkeypatch):
    tables = _tables(
        [_w("w1", "Citadel Advisors", "C1"), _w("w2", "Renaissance Tech", "C2"),
         _w("w3", "AQR Capital", "C3")],
        [
            _t("w1", "TSM", amount=2_400_000, alloc=3.1),
            _t("w2", "TSM", trade_type="New", amount=800_000, alloc=5.0),
            _t("w3", "TSM", action="SOLD", trade_type="Decreased"),     # trimmed → excluded
            _t("wX", "TSM", amount=9_000_000),                          # not a registry whale
        ],
        groups=[_g("w1"), _g("w2"), _g("w3"), _g("wX")],
    )
    rows, as_of = _rows(monkeypatch, tables)

    assert [r.name for r in rows] == ["Citadel Advisors", "Renaissance Tech"]
    assert all(r.whale_id is not None for r in rows)          # every whale is a registry fund → tappable
    assert rows[0].whale_id == "w1" and rows[0].amount_est == 2_400_000
    assert rows[0].allocation_percent == 3.1 and rows[0].is_new_position is False
    assert rows[1].whale_id == "w2" and rows[1].is_new_position is True
    # No weight CHANGE is sent any more (it moves with price); the date is the quarter END.
    assert all(r.allocation_change is None for r in rows)
    assert all(r.transaction_date == _Q1 and r.disclosure_date is None for r in rows)
    assert as_of == "2026-04-02"


def test_whale_rows_dedup_shared_cik_person_and_fund(monkeypatch):
    # A fund registered under BOTH a person and a firm name shares ONE CIK → once,
    # matching the card's distinct-fund count; the larger add represents it.
    tables = _tables(
        [_w("wp", "Ray Dalio", "CIK1"), _w("wf", "Bridgewater Associates", "CIK1"),
         _w("w2", "Citadel", "CIK2")],
        [_t("wp", "TSM", amount=1_000_000), _t("wf", "TSM", amount=1_630_000),
         _t("w2", "TSM", amount=900_000)],
    )
    rows, _ = _rows(monkeypatch, tables)
    assert [r.name for r in rows] == ["Bridgewater Associates", "Citadel"]


def test_whale_rows_shared_cik_tie_is_resolved_the_same_way_every_time(monkeypatch):
    whales = [_w("wb", "Person", "CIK1"), _w("wa", "Firm", "CIK1")]
    trades = [_t("wb", "TSM"), _t("wa", "TSM")]                  # identical adds
    first, _ = _rows(monkeypatch, _tables(whales, trades))
    again, _ = _rows(monkeypatch, _tables(list(reversed(whales)), list(reversed(trades))))
    assert [r.whale_id for r in first] == [r.whale_id for r in again] == ["wa"]


def test_whale_rows_subtitle_carries_firm_name(monkeypatch):
    # Person-fronted whales carry firm_name; the row's subtitle shows the FIRM so the name
    # never appears alone. Whales without a firm keep the "13F fund" fallback.
    tables = _tables(
        [_w("wp", "Ray Dalio", "CIK1", firm="Bridgewater Associates"),
         _w("w2", "Renaissance Technologies", "CIK2", firm=None)],
        [_t("wp", "TSM"), _t("w2", "TSM")],
    )
    by_name = {r.name: r for r in _rows(monkeypatch, tables)[0]}
    assert by_name["Ray Dalio"].subtitle == "Bridgewater Associates"
    assert by_name["Renaissance Technologies"].subtitle == "13F fund"


def test_whale_rows_firm_edge_cases_whitespace_and_unicode(monkeypatch):
    tables = _tables(
        [_w("w1", "Broken Row", "C1", firm="   "),
         _w("w2", "Duan Yongping", "C2", firm="H&H International Investment")],
        [_t("w1", "TSM"), _t("w2", "TSM")],
    )
    by_name = {r.name: r for r in _rows(monkeypatch, tables)[0]}
    assert by_name["Broken Row"].subtitle == "13F fund"
    assert by_name["Duan Yongping"].subtitle == "H&H International Investment"


def test_whale_rows_empty_when_nothing_adding(monkeypatch):
    tables = _tables([_w("w1", "Citadel", "C1")],
                     [_t("w1", "TSM", action="SOLD", trade_type="Decreased")])
    assert _rows(monkeypatch, tables)[0] == []
    # No current group at all (a late filer) → empty, not an error.
    late = _tables([_w("w1", "Citadel", "C1", filed="2025-Q4")], [_t("w1", "TSM", date="2025-12-31")],
                   groups=[_g("w1", "2025-12-31")])
    assert _rows(monkeypatch, late)[0] == []


def test_a_weight_that_rose_on_price_alone_is_not_listed(monkeypatch):
    # The defect this replaced: Flat's holding weight rose (change_percent > 0) with no
    # share bought; Adder bought shares while its weight FELL.
    tables = _tables(
        [_w("w1", "Adder", "C1"), _w("w2", "Flat", "C2")],
        [_t("w1", "TSM", alloc=0.8)],
        holdings=[{"whale_id": "w1", "ticker": "TSM", "allocation": 0.8, "change_percent": -0.4},
                  {"whale_id": "w2", "ticker": "TSM", "allocation": 3.0, "change_percent": 2.0}],
    )
    assert [r.name for r in _rows(monkeypatch, tables)[0]] == ["Adder"]


def test_a_groups_read_failure_raises_on_card_and_detail(monkeypatch):
    class _Boom(_FakeSupabase):
        def table(self, name):
            if name == "whale_trade_groups":
                raise RuntimeError("supabase down")
            return super().table(name)

    tables = _tables([_w("w1", "A", "C1"), _w("w2", "B", "C2")], [_t("w1", "TSM"), _t("w2", "TSM")])
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _Boom(tables))
    with pytest.raises(RuntimeError):
        _svc()._detail_whale_rows("TSM", now=_DNOW)
    with pytest.raises(RuntimeError):
        _svc()._query_and_aggregate_whale(now=_DNOW)


# ── The card (same reads, same functions) ───────────────────────────────


def _card(monkeypatch, tables):
    monkeypatch.setattr(ssvc, "get_supabase", lambda: _FakeSupabase(tables))
    return _svc()._query_and_aggregate_whale(now=_DNOW)


def _parity_tables():
    whales = [_w(f"w{i}", f"Fund {i}", f"C{i}") for i in range(1, 7)]
    whales.append(_w("w1b", "Fund 1 (person)", "C1"))           # shares C1 with w1
    whales.append(_w("wnull", "No CIK Fund", None))
    trades = [
        _t("w1", "NVDA"), _t("w1b", "NVDA"), _t("w2", "NVDA"), _t("w3", "NVDA", trade_type="New"),
        _t("w4", "NVDA", action="SOLD", trade_type="Decreased"),
        _t("w2", "BRK.B"), _t("w5", "BRK-B"), _t("wnull", "BRK.B"),
        _t("w6", "TSM"), _t("w6", "TSM", action="SOLD", trade_type="Decreased"),   # stale pair
        _t("w5", "TSM"),
        _t("w1", "AAPL"),                                                            # one fund only
    ]
    holdings = [{"id": "h1", "whale_id": "w2", "ticker": "NVDA", "company_name": "NVIDIA Corporation"}]
    return _tables(whales, trades, holdings=holdings)


def test_the_card_counts_distinct_funds_that_added_shares(monkeypatch):
    g = _card(monkeypatch, _parity_tables())
    assert [(e.symbol, e.value) for e in g.entries] == [
        ("BRK-B", 3.0), ("NVDA", 3.0), ("AAPL", 1.0), ("TSM", 1.0),
    ]
    assert g.entries[1].name == "NVIDIA Corporation"     # holdings name over the bare ticker
    assert g.as_of_date == "2026-04-02"


def test_every_card_count_equals_its_drill_down_list(monkeypatch):
    tables = _parity_tables()
    g = _card(monkeypatch, tables)
    for e in g.entries:
        rows, _ = _rows(monkeypatch, tables, sym=e.symbol)
        assert len(rows) == e.value, e.symbol


def test_no_current_fund_omits_the_card_honestly(monkeypatch):
    tables = _tables([_w("w1", "A", "C1", filed="2025-Q4"), _w("w2", "B", "C2", filed="2025-Q4")],
                     [_t("w1", "TSM", date="2025-12-31"), _t("w2", "TSM", date="2025-12-31")],
                     groups=[_g("w1", "2025-12-31"), _g("w2", "2025-12-31")])
    assert _card(monkeypatch, tables) is None


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
    s.price = PriceFromFMPFake(s.fmp)
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
    s.price = PriceFromFMPFake(s.fmp)
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
    s.price = PriceFromFMPFake(s.fmp)
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
    s.price = PriceFromFMPFake(s.fmp)
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
    s.price = PriceFromFMPFake(s.fmp)
    resp = await s.get_ticker_detail("congress", "NVDA")
    assert resp.symbol == "NVDA" and resp.holders == []   # degraded, not a 500


class _FlakyCongressFMP(_FakeFMP):
    """Either chamber can be made to raise; clearing the error models FMP recovering."""

    def __init__(self, senate_exc=None, house_exc=None, **kw):
        super().__init__(**kw)
        self.senate_exc = senate_exc
        self.house_exc = house_exc

    async def get_senate_latest(self, limit=1000):
        if self.senate_exc is not None:
            raise self.senate_exc
        return await super().get_senate_latest(limit)

    async def get_house_latest(self, limit=1000):
        if self.house_exc is not None:
            raise self.house_exc
        return await super().get_house_latest(limit)


def _partial(endpoint):
    return FMPPartialPageException("x", endpoint=endpoint, pages_total=4, pages_failed=1)


@pytest.mark.parametrize("chamber", ["senate", "house"])
@pytest.mark.asyncio
async def test_congress_rows_reraise_on_an_incomplete_feed(monkeypatch, chamber):
    # An incomplete chamber must PROPAGATE (not return `([], None)` normally), or
    # get_ticker_detail caches the empty result for the 10-min TTL.
    recent, _ = _congress_dates()
    buy = {"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent,
           "firstName": "Nancy", "lastName": "Pelosi", "district": "CA"}
    exc = _partial(f"{chamber}-latest")
    s = _svc()
    # The OTHER chamber is healthy and even has a buyer — a short list is still wrong.
    s.fmp = _FlakyCongressFMP(
        senate_exc=exc if chamber == "senate" else None,
        house_exc=exc if chamber == "house" else None,
        senate=[buy], house=[buy],
    )
    s.price = PriceFromFMPFake(s.fmp)
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    with pytest.raises(FMPPartialPageException):
        await s._detail_congress_rows("NVDA")


@pytest.mark.asyncio
async def test_a_congress_detail_failure_is_not_cached_and_the_next_tap_retries(monkeypatch):
    recent, _ = _congress_dates()
    senate = [{"symbol": "NVDA", "type": "Purchase", "disclosureDate": recent,
               "firstName": "Nancy", "lastName": "Pelosi", "district": "CA",
               "amount": "$1,001 - $15,000"}]
    s = _svc()
    fmp = _FlakyCongressFMP(house_exc=_partial("house-latest"), senate=senate, house=[])
    s.fmp = fmp
    s.price = PriceFromFMPFake(s.fmp)
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})

    resp = await s.get_ticker_detail("congress", "NVDA")
    assert resp.holders == []                                          # degraded, not a 500
    assert "congress:NVDA" not in SignalsService._detail_cache, "a failure must not pin an empty screen"

    fmp.house_exc = None                                               # FMP recovers
    resp = await s.get_ticker_detail("congress", "nvda")
    assert [h.name for h in resp.holders] == ["Nancy Pelosi"]           # retried, not pinned
    assert "congress:NVDA" in SignalsService._detail_cache


@pytest.mark.asyncio
async def test_an_honest_empty_congress_detail_is_cached(monkeypatch):
    s = _svc()
    s.fmp = _FakeFMP(senate=[], house=[])
    s.price = PriceFromFMPFake(s.fmp)
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    resp = await s.get_ticker_detail("congress", "NVDA")
    assert resp.holders == []
    assert "congress:NVDA" in SignalsService._detail_cache


@pytest.mark.asyncio
async def test_get_ticker_detail_unknown_kind_is_empty(monkeypatch):
    s = _svc()
    s.fmp = _FakeFMP(profile={})
    s.price = PriceFromFMPFake(s.fmp)
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
    "amount_range", "owner", "action", "shares",
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


def test_whale_row_reads_only_the_latest_quarter_not_a_bigger_older_buy(monkeypatch):
    # A big OLD buy and a smaller RECENT one: only the fund's latest 13F counts, so the row
    # shows the recent quarter and that trade's amount (coherent + current).
    tables = _tables(
        [_w("w1", "Citadel", "C1")],
        [_t("w1", "TSM", amount=5_000_000, date="2025-12-31"), _t("w1", "TSM", amount=900_000)],
        groups=[_g("w1", "2025-12-31"), _g("w1")],
    )
    rows, _ = _rows(monkeypatch, tables)
    assert len(rows) == 1
    assert rows[0].transaction_date == _Q1 and rows[0].amount_est == 900_000


def test_whale_rows_null_cik_stay_distinct(monkeypatch):
    tables = _tables([_w("w1", "A Fund", None), _w("w2", "B Fund", "")],
                     [_t("w1", "TSM"), _t("w2", "TSM")])
    assert len(_rows(monkeypatch, tables)[0]) == 2   # null/blank CIK → sentinel per whale


def test_whale_rows_match_class_share_variant(monkeypatch):
    # Trades stored as "BRK.B" must match a request canonicalized to "BRK-B".
    tables = _tables([_w("w1", "Berkshire Fund", "C1")], [_t("w1", "BRK.B", amount=1_200_000)])
    rows, _ = _rows(monkeypatch, tables, sym="BRK-B")
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
    s.price = PriceFromFMPFake(s.fmp)
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
    s.price = PriceFromFMPFake(s.fmp)
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
    s.price = PriceFromFMPFake(s.fmp)
    monkeypatch.setattr(s, "_congress_registry_map", lambda: {})
    rows, _ = await s._detail_congress_rows("NVDA")
    assert len(rows) == 1
    assert rows[0].subtitle == "Senator"   # no district suffix
    assert rows[0].owner is None
