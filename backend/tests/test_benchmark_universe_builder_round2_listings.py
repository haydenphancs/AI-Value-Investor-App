"""Only operating companies' COMMON shares vote in the benchmark universe, once per issuer.

Review 2026-10-07 (round 2) of `scripts/build_benchmark_universe.py`:

  UNI-1  The symbol-shape regex let notes on bare tickers (SOJC, TBB, PMTU, MFAN, RWTN),
         NASDAQ 5th-letter preferreds (FCNCN, AGNCP, NHPAP), warrants (CCXIW), units
         (NOVTU), when-issued rows (HONIV) and one-letter dash actions (ABC-U) through. FMP
         lists each under the ISSUER's name and prices it with the issuer's share count
         (SOJC $20.7B, TBB $126B), so the June file carried ~15 Southern / Duke / CMS / DTE /
         Entergy notes among its 52 Regulated Electric names — each a second, junk vote in
         the issuer's industry median.
  UNI-3  Dual-class commons (GOOG/GOOGL, BRK-A/BRK-B, FOX/FOXA …) each carry the issuer's
         fundamentals, so the issuer voted twice.
  UNI-5  Closed-end funds come back `isFund=false` (FMP's flag marks open-end funds).

The classifier REUSES the ticker-search grammar (`stock_search_service`), whose rows were
captured live from FMP on 2026-09-25 — the names below marked "captured" are those rows,
copied from tests/test_stock_search_listing_rules.py. Rows marked "pattern" are the review's
June-file symbols with a name in FMP's form for that family ("<issuer> <coupon>% Senior Notes
due <year>", as RILYN / GAING were captured); for preferred symbols the SYMBOL alone decides
and the name is just the issuer's. Fund names are the funds' public names.

Hermetic: pure functions, plus `main()` against a fake FMP client.
"""
from __future__ import annotations

import ast
import json
import logging
import math
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import scripts.build_benchmark_universe as bu
from app.services import stock_search_service as search

FLOOR = 500_000_000


def _row(sym: str, name: Any = None, ex: str = "NYSE", cap: Any = 2e9, *,
         price: Any = 50.0, avg_volume: Any = 1_000_000, **over: Any) -> Dict[str, Any]:
    """A /stable company-screener row (FMP's field names)."""
    row = {
        "symbol": sym, "companyName": f"{sym} Corp" if name is None else name,
        "marketCap": cap, "sector": "Utilities", "industry": "Regulated Electric",
        "price": price, "volume": avg_volume, "avgVolume": avg_volume,
        "exchange": ex, "exchangeShortName": ex, "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _kept(rows: List[Dict[str, Any]]) -> List[str]:
    kept, _, _ = bu._filter_rows(rows, FLOOR)
    return sorted(r["symbol"] for r in kept)


# ── UNI-1: per-row — a non-common listing by its own symbol or name ────────────────────

NOT_COMMON_ROWS = [
    # Notes on bare tickers, named as notes (captured).
    ("SOJC", "The Southern Company JR 2017B NT 77", "NYSE"),
    ("SOJF", "Southern Company 6.5 % Notes 2025-15.03.85", "NYSE"),
    ("TBB", "AT&T Inc. 5.35% GLB NTS 66", "NYSE"),
    ("DUKB", "Duke Energy Corporation 5.625%", "NYSE"),
    ("RILYN", "BRC Group Holdings, Inc. 6.50% Senior Notes Due 2026", "NASDAQ"),
    ("RILYG", "B. Riley Financial, Inc. 5.00% Senior Notes due 2026", "NASDAQ"),
    ("GAING", "Gladstone Investment Corporation 7.125% Notes due 2031", "NASDAQ"),
    ("AGNCZ", "AGNC Investment Corp. 8.75% Series H Fixed-Rate Cumulative Redeemable "
              "Preferred Stock", "NASDAQ"),
    ("RILYL", "BRC Group Holdings, Inc. - Depositary Shares, each representing a 1/1000th "
              "fractional interest in a share of Series B Cumulative Perpetual Preferred "
              "Stock", "NASDAQ"),
    # Notes (pattern) — the June file's REIT - Mortgage / Insurance rows. PMTU's issuer is a
    # TRUST: no Inc/Corp, so the search's issuer gate alone would have kept it.
    ("PMTU", "PennyMac Mortgage Investment Trust 8.50% Senior Notes due 2028", "NYSE"),
    ("MFAN", "MFA Financial, Inc. 8.875% Senior Notes due 2029", "NYSE"),
    ("RWTN", "Redwood Trust, Inc. 9.00% Senior Notes due 2029", "NYSE"),
    ("CGABL", "The Carlyle Group Inc. 4.625% Subordinated Notes due 2061", "NASDAQ"),
    # NASDAQ 5th-letter preferreds P/O/N/M: the symbol alone decides.
    ("AGNCP", "AGNC Investment Corp.", "NASDAQ"),                         # captured
    ("AVGOP", "Broadcom Inc.", "NASDAQ"),                                 # captured
    ("LANDO", "Gladstone Land Corporation", "NASDAQ"),                    # captured
    ("GOOGN", "Alphabet Inc. Depository Shs Repr 1/20th Conv Pfd Registered Shs",
     "NASDAQ"),                                                           # captured
    ("FCNCN", "First Citizens BancShares, Inc.", "NASDAQ"),
    ("NHPAP", "National Healthcare Properties, Inc.", "NASDAQ"),
    ("LILAP", "Liberty Latin America Ltd.", "NASDAQ"),
    ("BPYPO", "Brookfield Property Partners L.P.", "NASDAQ"),
    # NASDAQ 5th-letter warrant / unit / right / when-issued whose NAME says so.
    ("BIOTW", "Instinct Bio Technical Company Holdings Inc. Warrants", "NASDAQ"),  # captured
    ("BACCU", "Blue Acquisition Corp. Unit", "NASDAQ"),                   # captured
    ("IBACR", "IB Acquisition Corp. Right", "NASDAQ"),                    # captured
    ("DUKRW", "Duke Robotics Corp. C/wts Exp 06/05/2031(to Pur Com)", "NASDAQ"),  # captured
    ("CCXIW", "Churchill Capital Corp IX Warrants", "NASDAQ"),            # pattern
    ("HONIV", "Honeywell International Inc. When Issued", "NASDAQ"),      # pattern
    # One-letter dash actions: unit / right / warrant.
    ("ABC-U", "ABC Acquisition Corp", "NYSE"),
    ("ABC-R", "ABC Acquisition Corp", "NYSE"),
    ("ABC-W", "ABC Acquisition Corp", "NYSE"),
    # Already caught by the symbol shape — still the same reason.
    ("EP-PC", "El Paso Energy Capital Trust I", "NYSE"),
    ("MER-PK", "Bank of America Corp 6.45 % Notes 2018-15.12.66 Income Capital Obligations",
     "NYSE"),
]


@pytest.mark.parametrize("sym,name,ex", NOT_COMMON_ROWS)
def test_a_non_common_listing_is_dropped_on_its_own_symbol_or_name(sym, name, ex):
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) == "not_common_share"


MUST_KEEP_ROWS = [
    # Commons whose shape looks like a twin or a preferred (captured names).
    ("GOOGL", "Alphabet Inc.", "NASDAQ"), ("GOOG", "Alphabet Inc.", "NASDAQ"),
    ("CMCSA", "Comcast Corporation", "NASDAQ"),
    ("BRK-B", "Berkshire Hathaway Inc.", "NYSE"), ("BRK-A", "Berkshire Hathaway Inc.", "NYSE"),
    ("BF-B", "Brown-Forman Corporation", "NYSE"),
    ("MKC-V", "McCormick & Company, Incorporated", "NYSE"),
    ("PBR-A", "Petróleo Brasileiro S.A. - Petrobras", "NYSE"),
    ("FOXA", "Fox Corporation", "NASDAQ"), ("LILAK", "Liberty Latin America Ltd.", "NASDAQ"),
    ("FWONK", "Liberty Media Corporation", "NASDAQ"),
    ("RYAAY", "Ryanair Holdings plc", "NASDAQ"),           # a 5-letter, non-fund common (ADR)
    ("SNAP", "Snap Inc.", "NYSE"), ("RAMP", "LiveRamp Holdings, Inc.", "NYSE"),
    # Ends in W / U / R / V but is not a 5-letter NASDAQ action code.
    ("V", "Visa Inc.", "NYSE"), ("PGR", "The Progressive Corporation", "NYSE"),
    ("NOW", "ServiceNow, Inc.", "NYSE"), ("SNOW", "Snowflake Inc.", "NYSE"),
    ("ROIV", "Roivant Sciences Ltd.", "NASDAQ"), ("NU", "Nu Holdings Ltd.", "NYSE"),
    ("TROW", "T. Rowe Price Group, Inc.", "NASDAQ"),
    # Names that only LOOK like debt, a unit, or a fund.
    ("PFBC", "Preferred Bank", "NASDAQ"), ("SNDA", "Sonida Senior Living, Inc.", "NYSE"),
    ("ARM", "Arm Holdings plc American Depositary Shares", "NASDAQ"),
    # Round 3 (UB3-4): an ADR / GDS is the foreign issuer's common in every spelling — the
    # shared grammar spared only "American Depositary Shares" (pattern names).
    ("BABA", "Alibaba Group Holding Limited American Depository Shares", "NYSE"),
    ("GDS", "GDS Holdings Limited Global Depositary Shares", "NASDAQ"),
    ("TSM", "Taiwan Semiconductor Manufacturing Global Depository Shares", "NYSE"),
    ("UNT", "Unit Corporation", "NYSE"),
    ("FGF", "Fundamental Global Inc.", "NASDAQ"),           # "fund" only as a word part
    # Operating trusts: REITs, a royalty trust, a bank.
    ("FRT", "Federal Realty Investment Trust", "NYSE"),
    ("UHT", "Universal Health Realty Income Trust", "NYSE"),
    ("SBR", "Sabine Royalty Trust", "NYSE"),
    ("NTRS", "Northern Trust Corporation", "NASDAQ"),
    ("RWT", "Redwood Trust, Inc.", "NYSE"),
    # Operating asset managers sit in the same 'Asset Management' industry as the funds.
    ("BEN", "Franklin Resources, Inc.", "NYSE"), ("IVZ", "Invesco Ltd.", "NYSE"),
]


@pytest.mark.parametrize("sym,name,ex", MUST_KEEP_ROWS)
def test_a_real_common_is_kept_on_its_own(sym, name, ex):
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) is None


@pytest.mark.parametrize("sym,name,ex", [
    ("ETY", "Eaton Vance Tax-Managed Diversified Equity Income Fund", "NYSE"),
    ("EVV", "Eaton Vance Limited Duration Income Fund", "AMEX"),
    ("NIE", "Virtus Equity & Convertible Income Fund", "NYSE"),
    ("IGR", "CBRE Global Real Estate Income Fund", "NYSE"),
    ("DNP", "DNP Select Income Fund Inc.", "NYSE"),
    ("ADX", "Adams Diversified Equity Fund, Inc.", "NYSE"),
    # Captured: carries a corporate marker AND a preferred marker — a fund first.
    ("FFC", "Flaherty & Crumrine Preferred Securities Income Fund Incorporated", "NYSE"),
    ("DJP", "iPath Bloomberg Commodity Index Total Return ETN", "NYSE"),
    ("XYZQ", "Some Covered Call ETF", "NASDAQ"),
])
def test_a_closed_end_fund_or_etp_named_as_such_is_dropped(sym, name, ex):
    """UNI-5: FMP's `isFund=false` let these through (it flags open-end funds)."""
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) == "fund_name"
    assert "fund_name" not in bu._UNEXPECTED_DROP_REASONS     # INFO: no filter was ignored


@pytest.mark.parametrize("name", [None, 42, 3.5, [], {}, "", "   ", True])
def test_a_missing_or_wrong_type_name_never_drops_or_raises(name):
    row = _row("ABC")
    row["companyName"] = name
    assert bu._drop_reason(row, FLOOR) is None
    assert _kept([row]) == ["ABC"]


def test_cap_checks_still_follow_the_listing_checks():
    """A note under the floor is still counted as what it IS; a common under it as drift."""
    assert bu._drop_reason(_row("TBB", "AT&T Inc. 5.35% GLB NTS 66", cap=1e8), FLOOR) \
        == "not_common_share"
    assert bu._drop_reason(_row("T", "AT&T Inc.", cap=1e8), FLOOR) == "below_floor"


@pytest.mark.parametrize("name", [
    "%" * 100_000, "notes " * 20_000, "a" * 100_000, "Fund " * 20_000,
    "depositary " * 15_000 + "sh", "(the)" * 20_000, "jr" + " " * 100_000 + "sub",
    "Inc " * 30_000, "trust income " * 10_000,
])
def test_pathological_names_are_capped_and_fast(name):
    rows = [_row("AGNC", name, "NASDAQ"), _row("AGNCL", name, "NASDAQ"),
            _row("ABCDE", name, "NASDAQ"), _row("ABCDW", name, "NASDAQ")]
    started = time.perf_counter()
    kept, _, examples = bu._filter_rows(rows, FLOOR)
    bu._log_suspect_rows({"X": kept})
    assert time.perf_counter() - started < 0.5
    assert all(len(label) < 200 for sample in examples.values() for label in sample)


# ── UNI-1: industry-level twins of a base listing ───────────────────────────────────────


def test_regulated_electric_keeps_the_issuers_not_their_notes():
    """The June file's Regulated Electric shape: each issuer plus its notes, all priced off
    the issuer's share count."""
    rows = [
        _row("SO", "The Southern Company", cap=1.05e11, price=90, avg_volume=4e6),
        _row("SOJC", "The Southern Company JR 2017B NT 77", cap=2.067e10, price=21,
             avg_volume=4e4),
        _row("SOJD", "Southern Company (The) Series 2", cap=2.1e10, price=20, avg_volume=3e4),
        _row("SOJE", "Southern Company (The) Series 2", cap=2.0e10, price=19, avg_volume=3e4),
        _row("SOMN", "The Southern Company", cap=2.2e10, price=24, avg_volume=2e4),
        _row("DUK", "Duke Energy Corporation", cap=9e10, price=115, avg_volume=3e6),
        _row("DUKB", "Duke Energy Corporation 5.625%", cap=1.8e10, price=24, avg_volume=5e4),
        _row("DUKU", "Duke Energy Corporation Units 1.08.29", cap=8e9, price=50,
             avg_volume=1e4),
        _row("NEE", "NextEra Energy, Inc.", cap=1.5e11, price=75, avg_volume=9e6),
    ]
    kept, dropped, examples = bu._filter_rows(rows, FLOOR)
    assert sorted(r["symbol"] for r in kept) == ["DUK", "NEE", "SO"]
    assert dropped == {"not_common_share": 6}
    # Named by what proved them: their own name first, then the twins (capped at 5).
    assert examples["not_common_share"] == [
        'SOJC "The Southern Company JR 2017B NT 77"',
        'DUKB "Duke Energy Corporation 5.625%"',
        "DUKU (twin of DUK)", "SOJD (twin of SO)", "SOJE (twin of SO)",
    ]


@pytest.mark.parametrize("rows,expected", [
    # REIT - Mortgage: identical issuer names — only the twin rule can tell.
    ([_row("PMT", "PennyMac Mortgage Investment Trust"),
      _row("PMTU", "PennyMac Mortgage Investment Trust")], ["PMT"]),
    ([_row("AGNC", "AGNC Investment Corp.", "NASDAQ"),
      _row("AGNCL", "AGNC Investment Corp.", "NASDAQ"),
      _row("AGNCP", "AGNC Investment Corp.", "NASDAQ")], ["AGNC"]),
    ([_row("MFA", "MFA Financial, Inc."), _row("MFAN", "MFA Financial, Inc.")], ["MFA"]),
    # Conglomerates: the when-issued row at $122.7B.
    ([_row("HON", "Honeywell International Inc.", "NASDAQ"),
      _row("HONIV", "Honeywell International Inc.", "NASDAQ", cap=1.227e11)], ["HON"]),
    # Banks - Regional: FCNCN at $22.6B beside FCNCA.
    ([_row("FCNCA", "First Citizens BancShares, Inc.", "NASDAQ", cap=2.6e10),
      _row("FCNCN", "First Citizens BancShares, Inc.", "NASDAQ", cap=2.26e10)], ["FCNCA"]),
    # A warrant and a unit with a plain name, beside their SPAC common.
    ([_row("CCXI", "Churchill Capital Corp IX", "NASDAQ"),
      _row("CCXIW", "Churchill Capital Corp IX", "NASDAQ"),
      _row("CCXIU", "Churchill Capital Corp IX", "NASDAQ")], ["CCXI"]),
    ([_row("NOVT", "Novanta Inc.", "NASDAQ"), _row("NOVTU", "Novanta Inc.", "NASDAQ")],
     ["NOVT"]),
    # Notes listed as a bare ticker plus letters under the issuer's exact name.
    ([_row("XEL", "Xcel Energy Inc.", "NASDAQ"), _row("XELLL", "Xcel Energy Inc.", "NASDAQ")],
     ["XEL"]),
    ([_row("APO", "Apollo Global Management, Inc."),
      _row("APOS", "Apollo Global Management, Inc.")], ["APO"]),
])
def test_a_twin_of_a_base_listing_in_the_same_industry_is_dropped(rows, expected):
    assert _kept(rows) == expected


@pytest.mark.parametrize("twin", ["RFACU", "RFACR", "RFACW"])
def test_a_corporate_action_twin_is_dropped_when_only_the_base_names_its_share_type(twin):
    """FMP names a SPAC's common "RF Acquisition Corp II Ordinary Shares" and its unit /
    right / warrant just "RF Acquisition Corp II" (stock_search_service records the shape).
    The two issuer keys differ, so the root-twin rule cannot pair them; the corporate-action
    rule strips the share-type words and does."""
    common = _row("RFAC", "RF Acquisition Corp II Ordinary Shares", "NASDAQ")
    action = _row(twin, "RF Acquisition Corp II", "NASDAQ")
    assert search._issuer_key(common["companyName"]) != search._issuer_key(action["companyName"])
    kept, dropped, examples = bu._filter_rows([action, common], FLOOR)
    assert [r["symbol"] for r in kept] == ["RFAC"]
    assert examples["not_common_share"] == [f"{twin} (twin of RFAC)"]


def test_the_name_is_capped_before_any_rule_reads_it():
    """Every name regex reads at most `_MAX_NAME_CHARS` (shared with the search), folded
    onto one line — a 100k-character name costs no more than a normal one."""
    assert bu._row_name({"companyName": "x" * 100_000}) == "x" * bu._MAX_NAME_CHARS
    assert bu._row_name({"companyName": "Foo\n\tHoldings   Inc."}) == "Foo Holdings Inc."
    # A fund word past the cap is not read at all.
    assert bu._drop_reason(_row("ABC", "x" * bu._MAX_NAME_CHARS + " Fund"), FLOOR) is None


def test_a_twin_whose_base_is_just_under_the_floor_is_still_a_twin():
    """A SPAC's unit can sit over the floor while its common sits under it: the common is
    still a live listing, so it still proves the unit a twin."""
    rows = [_row("XYZA", "XYZ Acquisition Corp", "NASDAQ", cap=4.8e8),
            _row("XYZAU", "XYZ Acquisition Corp", "NASDAQ", cap=5.1e8)]
    kept, dropped, examples = bu._filter_rows(rows, FLOOR)
    assert kept == []
    assert dropped == {"below_floor": 1, "not_common_share": 1}
    assert examples["not_common_share"] == ["XYZAU (twin of XYZA)"]


def test_a_dropped_non_common_row_is_never_a_twin_base():
    """Only a common (dropped for its cap at most) can prove a twin — a fund or a note that
    shares a prefix and a name must not take a real listing down with it."""
    rows = [_row("ABC", "ABC Holdings Inc.", isFund=True),
            _row("ABCD", "ABC Holdings Inc.")]
    assert _kept(rows) == ["ABCD"]


@pytest.mark.parametrize("rows", [
    # Class letters A/B/C/J/K, and the allow-listed GOOGL, are never twins.
    [_row("FOX", "Fox Corporation", "NASDAQ"), _row("FOXA", "Fox Corporation", "NASDAQ")],
    [_row("LILA", "Liberty Latin America Ltd.", "NASDAQ"),
     _row("LILAK", "Liberty Latin America Ltd.", "NASDAQ")],
    [_row("GOOG", "Alphabet Inc.", "NASDAQ"), _row("GOOGL", "Alphabet Inc.", "NASDAQ")],
    [_row("BRK-A", "Berkshire Hathaway Inc."), _row("BRK-B", "Berkshire Hathaway Inc.")],
    [_row("MKC", "McCormick & Company, Incorporated"),
     _row("MKC-V", "McCormick & Company, Incorporated")],
    [_row("Z", "Zillow Group, Inc. Class C", "NASDAQ"),
     _row("ZG", "Zillow Group, Inc. Class A", "NASDAQ")],
])
def test_a_second_share_class_is_never_called_a_non_common_listing(rows):
    """It is deduped as a SECOND CLASS (`same_issuer`) — never mislabelled a note."""
    kept, dropped, _ = bu._filter_rows(rows, FLOOR)
    assert len(kept) == 1
    assert dropped == {"same_issuer": 1}


@pytest.mark.parametrize("rows", [
    [_row("PG", "The Procter & Gamble Company"), _row("PGR", "The Progressive Corporation")],
    [_row("V", "Visa Inc."), _row("VRT", "Vertiv Holdings Co"),
     _row("VRTV", "Veritiv Corporation")],
    [_row("DUK", "Duke Energy Corporation"), _row("DUKR", "DUKE Robotics Corp.", "NASDAQ")],
    [_row("MET", "MetLife, Inc."), _row("META", "Meta Platforms, Inc.", "NASDAQ")],
])
def test_different_companies_sharing_letters_are_all_kept(rows):
    assert _kept(rows) == sorted(r["symbol"] for r in rows)


# ── UNI-3: one vote per issuer ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("liquid,thin", [("GOOGL", "GOOG"), ("GOOG", "GOOGL")])
def test_the_more_liquid_class_is_the_issuers_one_vote(liquid, thin):
    rows = [_row(thin, "Alphabet Inc.", "NASDAQ", cap=2.1e12, price=170, avg_volume=1e7),
            _row(liquid, "Alphabet Inc.", "NASDAQ", cap=2.1e12, price=170, avg_volume=3e7)]
    kept, dropped, examples = bu._filter_rows(rows, FLOOR)
    assert [r["symbol"] for r in kept] == [liquid]
    assert dropped == {"same_issuer": 1}
    assert examples["same_issuer"] == [f"{thin} (kept {liquid})"]


def test_berkshire_votes_once_by_dollar_volume_not_share_count():
    """BRK-A trades a few thousand shares at ~$700k; BRK-B millions at ~$470. Liquidity is
    DOLLAR volume, so the share price does not decide it on its own."""
    a = _row("BRK-A", "Berkshire Hathaway Inc.", cap=1.0e12, price=700_000, avg_volume=3_000)
    b = _row("BRK-B", "Berkshire Hathaway Inc.", cap=1.0e12, price=470, avg_volume=4_000_000)
    assert _kept([a, b]) == ["BRK-A"]                       # $2.10B > $1.88B a day
    b["avgVolume"] = 5_000_000                              # $2.35B
    assert _kept([a, b]) == ["BRK-B"]


def test_entertainment_four_dual_class_issuers_vote_four_times_not_eight():
    """The June file's Entertainment industry (32 rows) held all four pairs."""
    pairs = [("FOX", "FOXA", "Fox Corporation"), ("NWS", "NWSA", "News Corporation"),
             ("BATRA", "BATRK", "Atlanta Braves Holdings, Inc."),
             ("FWONA", "FWONK", "Liberty Media Corporation")]
    rows = [_row("DIS", "The Walt Disney Company")]
    for a, b, name in pairs:
        rows += [_row(a, name, "NASDAQ", avg_volume=1e6), _row(b, name, "NASDAQ", avg_volume=2e6)]
    kept, dropped, _ = bu._filter_rows(rows, FLOOR)
    assert sorted(r["symbol"] for r in kept) == ["BATRK", "DIS", "FOXA", "FWONK", "NWSA"]
    assert dropped == {"same_issuer": 4}


def test_two_banks_with_one_normalised_name_both_vote():
    """First Bancorp (FBNC, North Carolina) and First BanCorp. (FBP, Puerto Rico): one
    normalised name, two companies, both in Banks - Regional and both over $1B. Their
    symbols do not read as share classes of one listing, so neither is dropped."""
    rows = [_row("FBNC", "First Bancorp", "NASDAQ"), _row("FBP", "First BanCorp.", "NYSE")]
    assert search._issuer_key("First Bancorp") == search._issuer_key("First BanCorp.")
    assert _kept(rows) == ["FBNC", "FBP"]


@pytest.mark.parametrize("a,b,siblings", [
    ("BRK-A", "BRK-B", True), ("PBR", "PBR-A", True), ("GOOG", "GOOGL", True),
    ("Z", "ZG", True), ("BATRA", "BATRK", True), ("FWONA", "FWONK", True),
    ("UA", "UAA", True), ("WLY", "WLYB", True),
    ("FBNC", "FBP", False), ("FWONK", "LSXMK", False), ("SO", "SOMN", False),
    ("AB", "AC", False),                                     # too short to read as classes
])
def test_share_class_sibling_symbols(a, b, siblings):
    assert bu._share_class_siblings(a, b) is siblings
    assert bu._share_class_siblings(b, a) is siblings


@pytest.mark.parametrize("bad", [None, "N/A", "3,000", math.nan, math.inf, -5.0, 0, True])
def test_unusable_liquidity_falls_back_to_cap_then_symbol(bad):
    """Garbage price / volume never decides the vote, and never raises."""
    small = _row("GOOG", "Alphabet Inc.", "NASDAQ", cap=2.0e12, price=bad, avg_volume=bad,
                 volume=bad)
    large = _row("GOOGL", "Alphabet Inc.", "NASDAQ", cap=2.1e12, price=bad, avg_volume=bad,
                 volume=bad)
    assert _kept([small, large]) == ["GOOGL"]               # the larger cap
    large["marketCap"] = 2.0e12
    assert _kept([large, small]) == ["GOOG"]                # a tie: the shorter symbol


def test_avg_volume_missing_falls_back_to_the_days_volume():
    a = _row("GOOG", "Alphabet Inc.", "NASDAQ", price=170, avg_volume=None, volume=9e7)
    b = _row("GOOGL", "Alphabet Inc.", "NASDAQ", price=170, avg_volume=3e7)
    assert _kept([a, b]) == ["GOOG"]


def test_a_thin_listing_that_slipped_through_loses_to_the_common_despite_a_bigger_cap():
    """A row no rule caught (a class letter, the issuer's name) but FMP priced with the
    issuer's share count: liquidity, not the reported cap, picks the issuer's vote."""
    common = _row("XYZ", "XYZ Holdings Inc.", cap=1e10, price=50, avg_volume=2e6)
    junk = _row("XYZB", "XYZ Holdings Inc.", cap=2e10, price=25, avg_volume=1e3)
    assert _kept([junk, common]) == ["XYZ"]


def test_the_vote_does_not_depend_on_the_order_fmp_listed_the_rows_in():
    rows = [_row("GOOG", "Alphabet Inc.", "NASDAQ", avg_volume=1e7),
            _row("GOOGL", "Alphabet Inc.", "NASDAQ", avg_volume=3e7),
            _row("FOX", "Fox Corporation", "NASDAQ", avg_volume=1e6),
            _row("FOXA", "Fox Corporation", "NASDAQ", avg_volume=2e6),
            _row("SO", "The Southern Company"), _row("SOMN", "The Southern Company"),
            _row("BRK-A", "Berkshire Hathaway Inc.", price=7e5, avg_volume=3e3),
            _row("BRK-B", "Berkshire Hathaway Inc.", price=470, avg_volume=4e6)]
    expected = _kept(rows)
    assert expected == ["BRK-A", "FOXA", "GOOGL", "SO"]
    rng = random.Random(7)
    for _ in range(20):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        assert _kept(shuffled) == expected


def test_a_name_too_short_to_prove_one_issuer_is_never_grouped():
    rows = [_row("MMM", "3M Company"), _row("MMMA", "3M Company")]
    assert search._issuer_key("3M Company") == ""
    assert _kept(rows) == ["MMM", "MMMA"]


def test_a_symbol_repeated_across_a_page_boundary_still_votes_once():
    rows = [_row("AAA", "AAA Corp", cap=5e9), _row("aaa", "AAA Corp", cap=6e9)]
    kept, dropped, _ = bu._filter_rows(rows, FLOOR)
    assert [(r["symbol"], r["marketCap"]) for r in kept] == [("AAA", 5e9)]
    assert dropped == {}


# ── UNI-5: a trust that only reads like a fund is NAMED, never dropped ─────────────────


def test_suspect_rows_are_named_at_warning_and_kept(caplog):
    by_industry = {
        "Asset Management - Bonds": [
            _row("BBN", "BlackRock Taxable Municipal Bond Trust"),
            _row("BOE", "BlackRock Enhanced Global Dividend Trust"),
        ],
        "REIT - Healthcare Facilities": [_row("UHT", "Universal Health Realty Income Trust")],
        "REIT - Retail": [_row("FRT", "Federal Realty Investment Trust")],
        "Oil & Gas E&P": [_row("SBR", "Sabine Royalty Trust")],
        "Banks - Regional": [_row("NTRS", "Northern Trust Corporation", "NASDAQ")],
        "Shell Companies": [_row("ABCDW", "Foo Acquisition Corp", "NASDAQ")],
    }
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows(by_industry)
    trusts = [r.getMessage() for r in caplog.records if "closed-end fund" in r.getMessage()]
    actions = [r.getMessage() for r in caplog.records if "W/U/R/V" in r.getMessage()]
    assert len(trusts) == 1 and len(actions) == 1
    for sym in ("BBN", "BOE", "UHT"):
        assert f"{sym} " in trusts[0]
    for sym in ("FRT", "SBR", "NTRS", "ABCDW"):
        assert f"{sym} " not in trusts[0]
    assert 'ABCDW "Foo Acquisition Corp" [Shell Companies]' in actions[0]
    assert all(r.levelno == logging.WARNING for r in caplog.records)


def test_no_suspect_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"Banks - Regional": [_row("FITB", "Fifth Third Bancorp")]})
    assert caplog.records == []


def test_a_long_suspect_list_is_capped(caplog):
    rows = [_row(f"Q{chr(65 + i // 26)}{chr(65 + i % 26)}X", "Some Municipal Income Trust")
            for i in range(bu._SUSPECTS_NAMED + 7)]
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"Asset Management - Income": rows})
    (msg,) = [r.getMessage() for r in caplog.records]
    assert f"{bu._SUSPECTS_NAMED + 7} kept row(s)" in msg and "(+7 more)" in msg


# ── end to end through main(): the file written and the log lines ───────────────────────


class _FakeFMP:
    def __init__(self, screener: Dict[str, List[Dict[str, Any]]]):
        self.screener = screener

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        if endpoint == "available-industries":
            return [{"industry": n} for n in self.screener]
        rows = self.screener[params["industry"]]
        limit, page = int(params["limit"]), int(params.get("page", 0))
        return rows[page * limit:(page + 1) * limit]

    async def close(self) -> None:  # pragma: no cover — an injected client is not closed
        pass


async def _no_sleep(_delay: float) -> None:  # pragma: no cover — no 429 here
    pass


@pytest.mark.asyncio
async def test_main_writes_one_common_per_issuer_and_logs_why(tmp_path, caplog):
    out = tmp_path / "u.json"
    fmp = _FakeFMP({
        "Regulated Electric": [
            _row("SO", "The Southern Company", price=90, avg_volume=4e6),
            _row("SOJC", "The Southern Company JR 2017B NT 77", price=21, avg_volume=4e4),
            _row("SOMN", "The Southern Company", price=24, avg_volume=2e4),
            _row("DUK", "Duke Energy Corporation"),
        ],
        "Internet Content & Information": [
            _row("GOOG", "Alphabet Inc.", "NASDAQ", avg_volume=1e7, sector="Communication Services"),
            _row("GOOGL", "Alphabet Inc.", "NASDAQ", avg_volume=3e7, sector="Communication Services"),
        ],
        "Asset Management - Income": [
            _row("TROW", "T. Rowe Price Group, Inc.", "NASDAQ", sector="Financial Services"),
            _row("EVV", "Eaton Vance Limited Duration Income Fund", "AMEX",
                 sector="Financial Services"),
            _row("BBN", "BlackRock Taxable Municipal Bond Trust", sector="Financial Services"),
        ],
    })
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    written = json.loads(out.read_text(encoding="utf-8"))
    tickers = {e["industry"]: e["tickers"] for e in written["industries"]}
    assert tickers == {"Regulated Electric": ["DUK", "SO"],
                       "Internet Content & Information": ["GOOGL"],
                       "Asset Management - Income": ["BBN", "TROW"]}

    levels = {r.getMessage().split(" row(s)")[0].rsplit(" ", 1)[-1]: r.levelno
              for r in caplog.records if " row(s) of " in r.getMessage()}
    assert levels == {"not_common_share": logging.INFO, "fund_name": logging.INFO,
                      "same_issuer": logging.INFO}
    text = caplog.text
    assert 'SOJC "The Southern Company JR 2017B NT 77"' in text
    assert "SOMN (twin of SO)" in text
    assert "GOOG (kept GOOGL)" in text
    assert 'EVV "Eaton Vance Limited Duration Income Fund"' in text
    assert "not honoured" not in text                  # nothing here is an ignored filter
    assert "BBN" in text and "closed-end fund" in text  # kept, and named at WARNING


# ── the grammar is REUSED, not copied ─────────────────────────────────────────────────


def test_the_search_grammar_is_the_same_object_not_a_copy():
    assert bu._grammar_drop_reason is search._grammar_drop_reason
    assert bu._DEBT_PREF_NAME_RE is search._DEBT_PREF_NAME_RE
    assert bu._DASH_ACTION_SUFFIX_RE is search._DASH_ACTION_SUFFIX_RE
    assert bu._issuer_key is search._issuer_key
    assert bu._is_root_twin is search._is_root_twin
    assert bu._dedupe_secondary_listings is search._dedupe_secondary_listings


def test_the_fund_name_patterns_equal_the_search_endpoints():
    """Copied (so the script does not import the endpoint layer) — kept equal here."""
    import app.api.v1.endpoints.stocks as stocks_ep

    for name in ("_FUND_NAME_RE", "_ETF_NAME_RE"):
        mine, theirs = getattr(bu, name), getattr(stocks_ep, name)
        assert (mine.pattern, mine.flags) == (theirs.pattern, theirs.flags), name


def test_every_search_grammar_verdict_is_accounted_for():
    """The builder drops every verdict of `_grammar_drop_reason` but `mutual_fund` (FMP's
    isFund flag decides funds here). A NEW verdict is dropped too — this test makes someone
    confirm that is right for the benchmark universe."""
    src = Path(search.__file__).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_grammar_drop_reason")
    verdicts = {n.value.value for n in ast.walk(fn)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)}
    assert verdicts == {"dash_preferred", "nasdaq_preferred", "mutual_fund",
                        "debt_or_preferred_name"}
    assert bu._SEARCH_GRAMMAR_KEPT == {"mutual_fund"}


def test_an_unflagged_nasdaq_mutual_fund_symbol_is_still_kept_and_named():
    """The search hides ABCDX; this builder keeps FMP's flag as the authority for funds."""
    assert bu._drop_reason(_row("ABCDX", "Some Equity Income Portfolio", "NASDAQ"), FLOOR) \
        is None
