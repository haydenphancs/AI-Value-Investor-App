"""
Regression tests for the "Select a Company" search screen (GET /stocks/search),
from the adversarial review triggered by the SNDK/SNDKV duplicate.

Two defects, both pure-function testable:

  1. Asset-type MISCLASSIFICATION dropped real companies. The iOS company picker
     filters to type == "stock". The old classifier flagged any name containing an
     issuer brand ("invesco", "schwab", "vanguard"…) as "etf", and any name with
     the substring "trust"/"fund"/"index" as "fund" — so IVZ ("Invesco Ltd."),
     SCHW ("The Charles Schwab Corporation"), NTRS ("Northern Trust Corporation"),
     and REITs like FRT ("Federal Realty Investment Trust") were mislabeled and
     silently DROPPED from company search. Fixed with a corporate-entity override.

  2. SECONDARY-LISTING duplicates. FMP returns when-issued ("V": SNDK→SNDKV) and
     warrant ("W": BGRY→BGRYW) rows sharing the exact company name, rendering as
     confusing identical rows. Fixed by dropping a V/W twin only when its base
     symbol is also present with the same name+exchange+type — without collapsing
     legitimate dual-class shares (GOOGL/GOOG).

Pure-function tests; no network.
"""

from __future__ import annotations

import pytest

from app.api.v1.endpoints.stocks import (
    _get_asset_type,
    _dedupe_secondary_listings,
    _normalize_company_name,
)
from app.schemas.stock import StockSearchResult


def _item(symbol: str, name: str, exchange: str = "NASDAQ") -> dict:
    return {"symbol": symbol, "name": name, "exchange": exchange}


# ── Classifier: real operating companies must be "stock" (the drop bug) ────────

@pytest.mark.parametrize("name", [
    "Invesco Ltd.",                        # IVZ — brand keyword "invesco"
    "The Charles Schwab Corporation",      # SCHW — brand keyword "schwab"
    "Northern Trust Corporation",          # NTRS — substring "trust"
    "Federal Realty Investment Trust",     # FRT — REIT ending in "Trust"
    "TrustCo Bank Corp NY",                # TRST — starts with "Trust"
    "BlackRock, Inc.",
    "State Street Corporation",
    "Alphabet Inc.",
    "Berkshire Hathaway Inc.",
    "Simon Property Group, Inc.",          # REIT with "Group"
])
def test_operating_companies_classify_as_stock(name):
    assert _get_asset_type(_item("X", name)) == "stock"


# ── Classifier: genuine ETFs/funds must NOT be "stock" ─────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Invesco Solar ETF", "etf"),
    ("Northern Trust US Equity ETF", "etf"),
    ("iShares Core S&P 500 ETF", "etf"),
    ("ProShares Short QQQ", "etf"),          # brand ETF WITHOUT "ETF" in name
    ("SPDR Gold Shares", "etf"),             # brand ETF WITHOUT "ETF" in name
    ("Direxion Daily Semiconductor Bull 3X", "etf"),
    ("Nuveen Core Plus Bond", "etf"),        # active ETF, issuer keyword
    ("Invesco Bond Fund", "fund"),           # closed-end fund
    ("Vanguard STAR Fund", "fund"),
    ("SPDR S&P 500 ETF Trust", "etf"),       # "ETF" word wins over "Trust"
    ("Invesco QQQ Trust, Series 1", "etf"),  # QQQ — brand trust, NO "ETF" word
    ("GraniteShares Gold Trust", "etf"),     # one-word brand
    ("Sprott Physical Gold Trust", "etf"),
    ("Grayscale Sui Trust", "etf"),
    ("Grayscale Funds Trust", "fund"),       # plural "Funds"
    ("UBS AG ETRACS Gold Covered Call ETNs", "etf"),  # plural "ETNs"
    ("BlackRock High Yield K", "etf"),       # mutual-fund class, issuer keyword
])
def test_funds_and_etfs_not_stock(name, expected):
    assert _get_asset_type(_item("X", name)) == expected


def test_qqq_is_not_stock_regression():
    # The exact FMP row: QQQ (3rd-largest ETF) must NOT reach the company picker.
    assert _get_asset_type(_item("QQQ", "Invesco QQQ Trust, Series 1")) == "etf"


# ── Classifier: exclusion + edge cases ─────────────────────────────────────────

def test_classifier_excludes_international_and_handles_crypto():
    assert _get_asset_type(_item("SNDK.TO", "Sandisk Corporation", "TSX")) is None
    assert _get_asset_type(_item("AAPL.MX", "Apple Inc.", "MEX")) is None
    assert _get_asset_type({"symbol": "BTC", "name": "Bitcoin",
                            "exchangeShortName": "CRYPTO"}) == "crypto"


def test_classifier_empty_or_missing_name_is_stock_not_crash():
    assert _get_asset_type(_item("X", "")) == "stock"
    assert _get_asset_type({"symbol": "X", "exchange": "NYSE"}) == "stock"  # no name key


def test_corporate_marker_is_whole_word_not_substring():
    # "Incyte" contains "inc" but not as a whole word — must not be forced to
    # stock via a bogus corp match (it's a real company anyway, ends up stock via
    # the fallthrough, but assert the CORP regex doesn't fire on the substring).
    from app.api.v1.endpoints.stocks import _CORP_ENTITY_RE
    assert _CORP_ENTITY_RE.search("Incyte Corporation")  # matches "Corporation"
    assert not _CORP_ENTITY_RE.search("Incyte Genomics")  # "Inc" inside "Incyte" ≠ word


# ── Secondary-listing dedup ────────────────────────────────────────────────────

def _res(symbol, name, exch="NASDAQ", typ="stock"):
    return StockSearchResult(symbol=symbol, name=name,
                             exchange_short_name=exch, type=typ)


def test_dedupe_drops_when_issued_twin():
    out = _dedupe_secondary_listings([
        _res("SNDK", "Sandisk Corporation"),
        _res("SNDKV", "Sandisk Corporation"),
    ])
    assert [r.symbol for r in out] == ["SNDK"]


def test_dedupe_is_order_independent():
    out = _dedupe_secondary_listings([
        _res("SNDKV", "Sandisk Corporation"),
        _res("SNDK", "Sandisk Corporation"),
    ])
    assert [r.symbol for r in out] == ["SNDK"]  # secondary dropped regardless of order


def test_dedupe_drops_warrant_twin():
    out = _dedupe_secondary_listings([
        _res("BGRY", "Berkshire Grey, Inc."),
        _res("BGRYW", "Berkshire Grey, Inc."),
    ])
    assert [r.symbol for r in out] == ["BGRY"]


def test_dedupe_drops_spac_unit_and_right_twins():
    # NASDAQ 5th-letter: unit "U", right "R" (SVNA→SVNAU, RFAC→RFACR).
    out = _dedupe_secondary_listings([
        _res("SVNA", "7 Acquisition Corporation"),
        _res("SVNAU", "7 Acquisition Corporation"),
        _res("SVNAW", "7 Acquisition Corporation"),
    ])
    assert [r.symbol for r in out] == ["SVNA"]
    out2 = _dedupe_secondary_listings([
        _res("RFAC", "RF Acquisition Corp."),
        _res("RFACR", "RF Acquisition Corp."),
        _res("RFACU", "RF Acquisition Corp."),
    ])
    assert [r.symbol for r in out2] == ["RFAC"]


def test_dedupe_drops_nyse_dash_action_twins():
    # NYSE dash form: warrant "-WT", unit "-UN" (APCA → APCA-WT / APCA-UN).
    out = _dedupe_secondary_listings([
        _res("APCA", "AP Acquisition Corp.", exch="NYSE"),
        _res("APCA-WT", "AP Acquisition Corp.", exch="NYSE"),
        _res("APCA-UN", "AP Acquisition Corp.", exch="NYSE"),
    ])
    assert [r.symbol for r in out] == ["APCA"]


def test_dedupe_matches_across_security_class_descriptor():
    # The common carries "Ordinary Shares" that the twins lack — must still match.
    out = _dedupe_secondary_listings([
        _res("RFAI", "RF Acquisition Corp II Ordinary Shares"),
        _res("RFAIU", "RF Acquisition Corp II"),
        _res("RFAIR", "RF Acquisition Corp II"),
    ])
    assert [r.symbol for r in out] == ["RFAI"]


def test_dedupe_does_not_cross_spac_series():
    # Corp II vs Corp III are DIFFERENT companies — never collapse across them.
    out = _dedupe_secondary_listings([
        _res("RFAI", "RF Acquisition Corp II Ordinary Shares"),
        _res("RFAM", "RF Acquisition Corp III Ordinary Shares"),
        _res("RFAMU", "RF Acquisition Corp III"),
    ])
    assert {r.symbol for r in out} == {"RFAI", "RFAM"}


def test_dedupe_keeps_class_c_dual_class_by_symbol_gate():
    # Zillow Z (Class C) / ZG (Class A): even after stripping "Class X" the names
    # match, but NEITHER symbol carries a V/W/U/R suffix, so both survive.
    out = _dedupe_secondary_listings([
        _res("ZG", "Zillow Group, Inc. Class A"),
        _res("Z", "Zillow Group, Inc. Class C"),
    ])
    assert {r.symbol for r in out} == {"ZG", "Z"}


def test_dedupe_does_not_drop_real_r_ending_common():
    # Progressive "PGR" ends in R but its base "PG" (P&G) is a different company.
    out = _dedupe_secondary_listings([
        _res("PG", "The Procter & Gamble Company"),
        _res("PGR", "The Progressive Corporation"),
    ])
    assert {r.symbol for r in out} == {"PG", "PGR"}
    # Baidu "BIDU": base "BID" different name → kept.
    out2 = _dedupe_secondary_listings([_res("BIDU", "Baidu, Inc.")])
    assert [r.symbol for r in out2] == ["BIDU"]


def test_dedupe_keeps_legitimate_dual_class():
    # GOOGL/GOOG differ by "L" (not a V/W secondary suffix) → both kept.
    out = _dedupe_secondary_listings([
        _res("GOOGL", "Alphabet Inc."),
        _res("GOOG", "Alphabet Inc."),
    ])
    assert [r.symbol for r in out] == ["GOOGL", "GOOG"]


def test_dedupe_keeps_standalone_v_ticker_without_base_twin():
    # Veritiv "VRTV" ends in V but its base "VRT" (Vertiv, a DIFFERENT company)
    # is not present under the same name → VRTV must survive.
    out = _dedupe_secondary_listings([
        _res("VRTV", "Veritiv Corporation"),
        _res("VRT", "Vertiv Holdings Co"),
    ])
    assert {r.symbol for r in out} == {"VRTV", "VRT"}


def test_dedupe_respects_exchange_and_name_boundaries():
    # Same base+V but DIFFERENT exchange → not a twin, keep both.
    out = _dedupe_secondary_listings([
        _res("SNDK", "Sandisk Corporation", exch="NASDAQ"),
        _res("SNDKV", "Sandisk Corporation", exch="NYSE"),
    ])
    assert len(out) == 2
    # Different company name → keep both.
    out2 = _dedupe_secondary_listings([
        _res("ABC", "Alpha Corp"),
        _res("ABCV", "Beta Corp"),
    ])
    assert len(out2) == 2


def test_dedupe_keep_symbol_protects_exact_typed_ticker():
    out = _dedupe_secondary_listings([
        _res("SNDK", "Sandisk Corporation"),
        _res("SNDKV", "Sandisk Corporation"),
    ], keep_symbol="SNDKV")
    assert {r.symbol for r in out} == {"SNDK", "SNDKV"}


def test_dedupe_single_char_symbol_not_dropped():
    # "V" (Visa) — len 1, no base → untouched.
    out = _dedupe_secondary_listings([_res("V", "Visa Inc.")])
    assert [r.symbol for r in out] == ["V"]


def test_normalize_company_name():
    assert _normalize_company_name("  Sandisk   Corporation ") == "sandisk corporation"
    assert _normalize_company_name(None) == ""
    # descriptors stripped so a twin keys to its base
    assert (_normalize_company_name("RF Acquisition Corp II Ordinary Shares")
            == _normalize_company_name("RF Acquisition Corp II"))


# ── Endpoint-level: crypto must not shadow a same-ticker stock; row resilience ─

import app.api.v1.endpoints.stocks as stocks_ep


class _FakeFMP:
    def __init__(self, rows):
        self._rows = rows

    async def search_stocks(self, query, limit=10):
        return self._rows


@pytest.mark.asyncio
async def test_crypto_does_not_shadow_same_ticker_stock(monkeypatch):
    # "STX" is BOTH Stacks (crypto in _CRYPTO_NAMES) and Seagate (NASDAQ stock).
    # The old code seeded seen_symbols with the crypto and skipped the stock, so
    # Seagate vanished from the company picker. It must now be present.
    monkeypatch.setattr(
        stocks_ep, "get_fmp_client",
        lambda: _FakeFMP([
            {"symbol": "STX", "name": "Seagate Technology Holdings plc",
             "exchange": "NASDAQ", "currency": "USD"},
        ]),
    )
    results = await stocks_ep.search_stocks(q="STX", limit=10)
    stocks = [r for r in results if r.type == "stock"]
    assert any(r.symbol == "STX" for r in stocks), "Seagate (STX stock) must survive"
    # The crypto STX must be dropped (it duplicates the stock symbol).
    assert not any(r.symbol == "STX" and r.type == "crypto" for r in results)


@pytest.mark.asyncio
async def test_malformed_fmp_rows_do_not_502_the_search(monkeypatch):
    # A non-dict element and a dict with a non-string name must be skipped, not
    # abort the whole search (which would lose the valid Apple row).
    monkeypatch.setattr(
        stocks_ep, "get_fmp_client",
        lambda: _FakeFMP([
            None,                                                   # non-dict
            "garbage",                                              # non-dict
            {"symbol": "BAD", "name": 12345, "exchange": "NASDAQ"},  # int name
            {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
        ]),
    )
    results = await stocks_ep.search_stocks(q="apple", limit=10)
    assert any(r.symbol == "AAPL" for r in results)


@pytest.mark.asyncio
async def test_whitespace_only_query_returns_empty(monkeypatch):
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: _FakeFMP([]))
    assert await stocks_ep.search_stocks(q="   ", limit=10) == []
