"""
Ticker search must show ONE row per company — `stock_search_service.refine_listings`.

THE BUG (2026-09-25, screenshot): "avgo" showed AVGO and AVGOP, both "Broadcom Inc.".
AVGOP is a preferred that converted in 2022; FMP still indexes it under the issuer's
exact name. A live sweep found it was one member of a family — dash preferreds, NASDAQ
5th-letter preferreds, notes, same-issuer twins, dead/renamed listings, mutual-fund
share classes — and that 51 of 98 rows people saw in 32 replayed queries were dead.

Every fixture row below is a REAL FMP search row captured live on 2026-09-25 (symbol,
name, exchange, and whether `/stable/actively-trading-list` held it). Scenario tests
replay a whole query, because the rules interact: a twin collapses only onto a live
base, a root twin needs a live base, ranking reads the survivors.

Hermetic: pure functions plus the handler with a stubbed FMP client and directory.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import pytest

import app.api.v1.endpoints.stocks as stocks_ep
from app.schemas.stock import StockSearchResult
from app.services import stock_search_service as svc
from app.services.stock_search_service import _issuer_key, refine_listings

# (symbol, name, exchange, type, active)
Row = Tuple[str, str, str, str, bool]


def _res(sym: str, name: str, ex: str = "NASDAQ", typ: str = "stock") -> StockSearchResult:
    return StockSearchResult(symbol=sym, name=name, currency="USD",
                             exchange_short_name=ex, exchange_full_name=ex, type=typ)


def _scenario(rows: List[Row], extra_dir: Optional[Dict[str, str]] = None):
    results = [_res(s, n, ex, t) for s, n, ex, t, _ in rows]
    directory = {s: n for s, n, _, _, active in rows if active}
    directory.update(extra_dir or {})
    return results, directory


def _syms(rows: List[StockSearchResult]) -> List[str]:
    return [r.symbol for r in rows]


def _refine(rows: List[Row], q: str, extra_dir=None, *, directory_available: bool = True):
    results, directory = _scenario(rows, extra_dir)
    return _syms(refine_listings(results, q.upper(), directory if directory_available else None))


# ── The reported case ─────────────────────────────────────────────────────────

AVGO_ROWS: List[Row] = [
    ("AVGO", "Broadcom Inc.", "NASDAQ", "stock", True),
    ("AVGOP", "Broadcom Inc.", "NASDAQ", "stock", False),
]


def test_avgo_shows_one_row():
    assert _refine(AVGO_ROWS, "avgo") == ["AVGO"]


def test_avgop_is_hidden_even_when_the_directory_is_unavailable():
    """The NASDAQ-preferred grammar needs no data, so an FMP outage cannot bring the
    reported duplicate back."""
    assert _refine(AVGO_ROWS, "avgo", directory_available=False) == ["AVGO"]


def test_a_dead_ticker_typed_exactly_is_still_shown():
    """Product decision 2026-09-25: the exact ticker always wins (same-day IPOs, and
    FMP's rare wrong 'inactive' flags). It ranks first; its live sibling follows."""
    assert _refine(AVGO_ROWS, "AVGOP") == ["AVGOP", "AVGO"]


# ── Whole-query replays (real FMP rows, 2026-09-25) ───────────────────────────

AGNC: List[Row] = [
    ("AGNC", "AGNC Investment Corp.", "NASDAQ", "stock", True),
    ("AGNCP", "AGNC Investment Corp.", "NASDAQ", "stock", True),
    ("AGNCM", "AGNC Investment Corp.", "NASDAQ", "stock", True),
    ("AGNCO", "AGNC Investment Corp.", "NASDAQ", "stock", True),
    ("AGNCL", "AGNC Investment Corp.", "NASDAQ", "stock", True),
    ("AGNCN", "AGNC Investment Corp.", "NASDAQ", "stock", True),
    ("AGNCZ", "AGNC Investment Corp. 8.75% Series H Fixed-Rate Cumulative Redeemable "
              "Preferred Stock", "NASDAQ", "stock", True),
]

BANK_OF_AMERICA: List[Row] = [
    ("BAC-PE", "Bank of America Corporation", "NYSE", "stock", True),
    ("BAC-PL", "Bank of America Corporation", "NYSE", "stock", True),
    ("BML-PH", "Bank of America Corporation", "NYSE", "stock", True),
    ("BML-PL", "Bank of America Corporation", "NYSE", "stock", True),
    ("BAC-PQ", "Bank of America Corporation", "NYSE", "stock", True),
    ("BAC-PK", "Bank of America Corporation", "NYSE", "stock", True),
    ("BAC", "Bank of America Corporation", "NYSE", "stock", True),
    ("BML-PJ", "Bank of America Corporation", "NYSE", "stock", True),
    ("BAC-PP", "Bank of America Corporation", "NYSE", "stock", True),
    ("BAC-PS", "Bank of America Corporation", "NYSE", "stock", True),
    ("MER-PK", "Bank of America Corp 6.45 % Notes 2018-15.12.66 Income Capital "
               "Obligations", "NYSE", "stock", True),
]

ATT: List[Row] = [
    ("T-PA", "AT&T Inc.", "NYSE", "stock", True),
    ("T", "AT&T Inc.", "NYSE", "stock", True),
    ("T-PC", "AT&T Inc.", "NYSE", "stock", True),
    ("TBB", "AT&T Inc. 5.35% GLB NTS 66", "NYSE", "stock", True),
    ("TBC", "AT&T Inc. 5.625% Global Notes d", "NYSE", "stock", False),
]

SOUTHERN: List[Row] = [
    ("SOLN", "Southern Company (The) 2019 Ser", "NYSE", "stock", False),
    ("SOJE", "Southern Company (The) Series 2", "NYSE", "stock", True),
    ("SOJD", "Southern Company (The) Series 2", "NYSE", "stock", True),
    ("SOJF", "Southern Company 6.5 % Notes 2025-15.03.85", "NYSE", "stock", True),
    ("SO", "The Southern Company", "NYSE", "stock", True),
    ("SOMN", "The Southern Company", "NYSE", "stock", True),
    ("SOJB", "The Southern Company JR SUB NT 76", "NYSE", "stock", False),
    ("SOJC", "The Southern Company JR 2017B NT 77", "NYSE", "stock", True),
    ("SOJA", "The Southern Company JR SUB NT 2015A", "NYSE", "stock", False),
]

GOOG: List[Row] = [
    ("GOOG", "Alphabet Inc.", "NASDAQ", "stock", True),
    ("GOOGN", "Alphabet Inc. Depository Shs Repr 1/20th Conv Pfd Registered Shs", "NASDAQ", "stock", True),
    ("GOOGM", "Alphabet Inc. Depository Shs Repr 1/20th Conv Pfd Registered Shs", "NASDAQ", "stock", True),
    ("GOOGL", "Alphabet Inc.", "NASDAQ", "stock", True),
]

BRK: List[Row] = [
    ("BRKS", "Brooks Automation, Inc.", "NASDAQ", "stock", False),
    ("BRKR", "Bruker Corporation", "NASDAQ", "stock", True),
    ("BRKH", "Burtech Acquisition Corp II", "NASDAQ", "stock", True),
    ("BRKY", "Direxion Breakfast Commodities Strategy ETF", "AMEX", "etf", False),
    ("BRKC", "YieldMax BRK.B Option Income Strategy ETF", "AMEX", "etf", True),
    ("BRK-B", "Berkshire Hathaway Inc.", "NYSE", "stock", True),
    ("BRKHW", "Burtech Acquisition Corp II Warrants", "NASDAQ", "stock", True),
    ("BRKRP", "Bruker Corporation", "NASDAQ", "stock", True),
    ("BRKHU", "Burtech Acquisition Corp II", "NASDAQ", "stock", True),
    ("BRK-A", "Berkshire Hathaway Inc.", "NYSE", "stock", True),
    ("RBRK", "Rubrik, Inc.", "NYSE", "stock", True),
]

JBT: List[Row] = [
    ("JBT", "JBT Marel Corporation", "NYSE", "stock", False),   # old ticker (renamed 2025-01)
    ("JBTM", "JBT Marel Corporation", "NYSE", "stock", True),
]

MET: List[Row] = [
    ("MET", "MetLife, Inc.", "NYSE", "stock", True),
    ("METU", "Direxion Daily META Bull 2X ETF", "NASDAQ", "etf", True),
    ("META", "Meta Platforms, Inc.", "NASDAQ", "stock", True),
    ("METC", "Ramaco Resources, Inc.", "NASDAQ", "stock", True),
    ("METCI", "Ramaco Resources, Inc.", "NASDAQ", "stock", True),
    ("METCB", "Ramaco Resources, Inc.", "NASDAQ", "stock", True),   # Class B COMMON
    ("METCZ", "Ramaco Resources, Inc.", "NASDAQ", "stock", True),
    ("METCL", "Ramaco Resources, Inc. - 9.00%", "NASDAQ", "stock", False),
    ("MET-PE", "MetLife, Inc.", "NYSE", "stock", True),
    ("MET-PA", "MetLife, Inc.", "NYSE", "stock", True),
]

UA: List[Row] = [
    ("UA", "Under Armour, Inc.", "NYSE", "stock", True),
    ("UAN", "CVR Partners, LP", "NYSE", "stock", True),
    ("UAE", "iShares MSCI UAE ETF", "NASDAQ", "etf", True),
    ("UAM", "Universal American Corporation", "NYSE", "stock", False),
    ("UAL", "United Airlines Holdings, Inc.", "NASDAQ", "stock", True),
    ("UAA", "Under Armour, Inc.", "NYSE", "stock", True),
]

RILY: List[Row] = [
    ("RILY", "BRC Group Holdings, Inc.", "NASDAQ", "stock", True),
    ("RILYT", "B. Riley Financial, Inc. 6.00% Senior Notes Due 2028", "NASDAQ", "stock", True),
    ("RILYP", "BRC Group Holdings, Inc.", "NASDAQ", "stock", True),
    ("RILYL", "BRC Group Holdings, Inc. - Depositary Shares, each representing a 1/1000th "
              "fractional interest in a share of Series B Cumulative Perpetual Preferred "
              "Stock", "NASDAQ", "stock", True),
    ("RILYM", "B. Riley Financial, Inc. - 6.37", "NASDAQ", "stock", False),
    ("RILYH", "B. Riley Financial, Inc.", "NASDAQ", "stock", False),
    ("RILYN", "BRC Group Holdings, Inc. 6.50% Senior Notes Due 2026", "NASDAQ", "stock", True),
    ("RILYK", "B. Riley Financial, Inc. 5.50% Senior Notes Due 2026", "NASDAQ", "stock", False),
    ("RILYG", "B. Riley Financial, Inc. 5.00% Senior Notes due 2026", "NASDAQ", "stock", True),
    ("RILYZ", "BRC Group Holdings Inc. 5.25% Sr. Notes due 2028", "NASDAQ", "stock", True),
]

DUK: List[Row] = [
    ("DUK", "Duke Energy Corporation", "NYSE", "stock", True),
    ("DUKQ", "Ocean Park Domestic ETF", "AMEX", "etf", True),
    ("DUKU", "Duke Energy Corporation Units 1.08.29", "NYSE", "stock", True),
    ("DUKB", "Duke Energy Corporation 5.625%", "NYSE", "stock", True),
    ("DUKR", "DUKE Robotics Corp.", "NASDAQ", "stock", True),
    ("DUKRW", "Duke Robotics Corp. C/wts Exp 06/05/2031(to Pur Com)", "NASDAQ", "stock", True),
    ("DUK-PA", "Duke Energy Corporation", "NYSE", "stock", True),
]

APO: List[Row] = [
    ("APO", "Apollo Global Management, Inc.", "NYSE", "stock", True),
    ("APOP", "Cellect Biotechnology Ltd.", "NASDAQ", "stock", False),
    ("APOS", "Apollo Global Management, Inc.", "NYSE", "stock", True),
    ("APOG", "Apogee Enterprises, Inc.", "NASDAQ", "stock", True),
    ("APOIX", "American Century Short Duration Inflation Protection Bond Fund Investor "
              "Class", "NASDAQ", "fund", True),
    ("APO-PB", "Apollo Global Management, Inc.", "NYSE", "stock", False),
]

OPEN_: List[Row] = [
    ("OPEN", "Opendoor Technologies Inc.", "NASDAQ", "stock", True),
    ("OPENL", "Opendoor Technologies Inc.", "NASDAQ", "stock", True),
    ("OPENZ", "Opendoor Technologies Inc.", "NASDAQ", "stock", True),
    ("OPENW", "Opendoor Technologies Inc.", "NASDAQ", "stock", True),
]

PADDED_WARRANTS: List[Row] = [
    ("VFS", "VinFast Auto Ltd.", "NASDAQ", "stock", True),
    ("VFSWW", "VinFast Auto Ltd.", "NASDAQ", "stock", True),
    ("PCT", "PureCycle Technologies, Inc.", "NASDAQ", "stock", True),
    ("PCTY", "Paylocity Holding Corporation", "NASDAQ", "stock", True),
    ("PCTTW", "PureCycle Technologies, Inc.", "NASDAQ", "stock", True),
    ("PCTTU", "PureCycle Technologies, Inc.", "NASDAQ", "stock", True),
    ("AUR", "Aurora Innovation, Inc.", "NASDAQ", "stock", True),
    ("AUROW", "Aurora Innovation, Inc.", "NASDAQ", "stock", True),
    ("BIOT", "Instinct Bio Technical Co. Holdings Inc.", "NASDAQ", "stock", True),
    ("BIOTW", "Instinct Bio Technical Company Holdings Inc. Warrants", "NASDAQ", "stock", True),
    ("JACS", "Jackson Acquisition Company II", "NYSE", "stock", True),
    ("JACS-RI", "Jackson Acquisition Company II", "NYSE", "stock", True),
    ("JACS-UN", "Jackson Acquisition Company II", "NYSE", "stock", False),
]

GLADSTONE: List[Row] = [
    ("LANDP", "Gladstone Land Corporation", "NASDAQ", "stock", True),
    ("LAND", "Gladstone Land Corporation", "NASDAQ", "stock", True),
    ("GAIN", "Gladstone Investment Corp.", "NASDAQ", "stock", True),
    ("LANDM", "Gladstone Land Corporation", "NASDAQ", "stock", False),
    ("LANDO", "Gladstone Land Corporation", "NASDAQ", "stock", True),
    ("GLAD", "Gladstone Capital Corporation", "NASDAQ", "stock", True),
    ("GLADL", "Gladstone Capital Corporation", "NASDAQ", "stock", False),
    ("GOODN", "Gladstone Commercial Corp Pref", "NASDAQ", "stock", True),
    ("GOODO", "Gladstone Commercial Corporation", "NASDAQ", "stock", True),
    ("GOOD", "Gladstone Commercial Corporation", "NASDAQ", "stock", True),
    ("GAINI", "Gladstone Investment Corporation", "NASDAQ", "stock", True),
    ("GLEE", "Gladstone Acquisition Corporation", "NASDAQ", "stock", False),
    ("GAING", "Gladstone Investment Corporation 7.125% Notes due 2031", "NASDAQ", "stock", True),
    ("GAINZ", "Gladstone Investment Corporation 4.875% Notes due 2028", "NASDAQ", "stock", True),
    ("PGC", "Peapack-Gladstone Financial Corporation", "NASDAQ", "stock", True),
]

BAC_SYMBOL: List[Row] = [
    ("BAC", "Bank of America Corporation", "NYSE", "stock", True),
    ("BACC", "Blue Acquisition Corp.", "NASDAQ", "stock", True),
    ("BACA", "Berenson Acquisition Corp. I", "NYSE", "stock", False),
    ("BACQ", "Inflection Point Acquisition Corp. IV", "NASDAQ", "stock", False),
    ("BACPX", "BlackRock 20/80 Target Allocation Inv A", "NASDAQ", "etf", True),
    ("BACCU", "Blue Acquisition Corp. Unit", "NASDAQ", "stock", True),
    ("BAC-PE", "Bank of America Corporation", "NYSE", "stock", True),
    ("BACA-UN", "Berenson Acquisition Corp. I", "NYSE", "stock", False),
    ("SBAC", "SBA Communications Corporation", "NASDAQ", "stock", True),
    ("IBAC", "IB Acquisition Corp. Common Stock", "NASDAQ", "stock", True),
    ("IBACR", "IB Acquisition Corp. Right", "NASDAQ", "stock", True),
    ("SBACX", "Touchstone Balanced Fund Class C", "NASDAQ", "fund", True),
]

JANUS: List[Row] = [
    ("JMGRX", "Janus Henderson Enterprise Fund", "NASDAQ", "fund", True),
    ("JDMRX", "Janus Henderson Enterprise Fund", "NASDAQ", "fund", True),
    ("JAENX", "Janus Henderson Enterprise Fund", "NASDAQ", "fund", True),
    ("JDMAX", "Janus Henderson Enterprise Fund Class A", "NASDAQ", "fund", True),
]


@pytest.mark.parametrize("rows,q,expected", [
    (AGNC, "agnc", ["AGNC"]),
    (BANK_OF_AMERICA, "bank of america", ["BAC"]),
    (ATT, "at&t", ["T"]),
    (SOUTHERN, "southern company", ["SO"]),
    (GOOG, "goog", ["GOOG", "GOOGL"]),
    (GOOG, "alphabet", ["GOOG", "GOOGL"]),
    (BRK, "brk", ["BRK-B", "BRK-A", "BRKR", "BRKH", "BRKC", "RBRK"]),
    (MET, "met", ["MET", "METU", "META", "METC", "METCB"]),
    (UA, "ua", ["UA", "UAA", "UAN", "UAE", "UAL"]),
    (RILY, "rily", ["RILY"]),
    (DUK, "duk", ["DUK", "DUKQ", "DUKR"]),
    (APO, "apo", ["APO", "APOG"]),
    (OPEN_, "open", ["OPEN"]),
    (GLADSTONE, "gladstone", ["LAND", "GAIN", "GLAD", "GOOD", "PGC"]),
    (BAC_SYMBOL, "bac", ["BAC", "BACC", "SBAC", "IBAC"]),
    (JANUS, "janus henderson enterprise", []),
])
def test_real_query_replays_show_one_row_per_security(rows, q, expected):
    assert _refine(rows, q) == expected


@pytest.mark.parametrize("q,expected", [
    ("vfs", ["VFS"]), ("pct", ["PCT", "PCTY"]), ("aur", ["AUR"]), ("biot", ["BIOT"]),
    ("jacs", ["JACS"]),
])
def test_padded_root_warrants_units_and_nyse_rights_collapse(q, expected):
    rows = [r for r in PADDED_WARRANTS if r[0].startswith(q.upper())]
    assert _refine(rows, q) == expected


def test_bank_of_america_puts_the_common_first_not_seventh():
    """FMP's own name ranking put six preferreds before BAC."""
    out = _refine(BANK_OF_AMERICA, "bank of america")
    assert out[0] == "BAC" and not any("-P" in s for s in out)


# ── Rename: a live ticker must not be hidden as the twin of its own dead old ticker ──

def test_renamed_live_ticker_survives_when_its_dead_old_ticker_is_typed():
    """JBT (dead) and JBTM (live) are both 'JBT Marel Corporation'. The exact-typed JBT
    is kept, and must NOT become the base that hides JBTM as a 'twin'."""
    assert _refine(JBT, "JBT") == ["JBT", "JBTM"]


def test_renamed_live_ticker_survives_a_name_query():
    assert _refine(JBT, "jbt marel") == ["JBTM"]


def test_renamed_live_ticker_survives_when_the_directory_is_unavailable():
    """With no directory the root-twin rule cannot know which base is live, so it must
    not run at all — the dead JBT would otherwise hide JBTM for EVERY query."""
    assert _refine(JBT, "jbt marel", directory_available=False) == ["JBT", "JBTM"]


# ── Must keep: real, distinct securities ──────────────────────────────────────

@pytest.mark.parametrize("rows,q", [
    # Dual-class commons with byte-identical FMP names.
    ([("FOX", "Fox Corporation", "NASDAQ", "stock", True),
      ("FOXA", "Fox Corporation", "NASDAQ", "stock", True)], "fox corp"),
    ([("NWS", "News Corporation", "NASDAQ", "stock", True),
      ("NWSA", "News Corporation", "NASDAQ", "stock", True)], "news corp"),
    ([("BBD", "Banco Bradesco S.A.", "NYSE", "stock", True),
      ("BBDO", "Banco Bradesco S.A.", "NYSE", "stock", True)], "bradesco"),
    ([("LILA", "Liberty Latin America Ltd.", "NASDAQ", "stock", True),
      ("LILAK", "Liberty Latin America Ltd.", "NASDAQ", "stock", True)], "liberty latin"),
    ([("UONE", "Urban One, Inc.", "NASDAQ", "stock", True),
      ("UONEK", "Urban One, Inc.", "NASDAQ", "stock", True)], "urban one"),
    ([("CENT", "Central Garden & Pet Company", "NASDAQ", "stock", True),
      ("CENTA", "Central Garden & Pet Company", "NASDAQ", "stock", True)], "central garden"),
    ([("RDI", "Reading International, Inc.", "NASDAQ", "stock", True),
      ("RDIB", "Reading International, Inc.", "NASDAQ", "stock", True)], "reading intern"),
    ([("WLY", "John Wiley & Sons, Inc.", "NYSE", "stock", True),
      ("WLYB", "John Wiley & Sons, Inc.", "NYSE", "stock", True)], "wiley"),
    ([("Z", "Zillow Group, Inc. Class C", "NASDAQ", "stock", True),
      ("ZG", "Zillow Group, Inc. Class A", "NASDAQ", "stock", True)], "zillow"),
    # Dash classes: never a preferred, never a twin.
    ([("BRK-A", "Berkshire Hathaway Inc.", "NYSE", "stock", True),
      ("BRK-B", "Berkshire Hathaway Inc.", "NYSE", "stock", True)], "berkshire"),
    ([("BF-A", "Brown-Forman Corporation", "NYSE", "stock", True),
      ("BF-B", "Brown-Forman Corporation", "NYSE", "stock", True)], "brown-forman"),
    ([("HEI", "HEICO Corporation", "NYSE", "stock", True),
      ("HEI-A", "HEICO Corporation", "NYSE", "stock", True)], "heico"),
    ([("LEN", "Lennar Corporation", "NYSE", "stock", True),
      ("LEN-B", "Lennar Corporation", "NYSE", "stock", True)], "lennar"),
    ([("MKC", "McCormick & Company, Incorporated", "NYSE", "stock", True),
      ("MKC-V", "McCormick & Company, Incorporated", "NYSE", "stock", True)], "mccormick"),
    ([("PBR", "Petróleo Brasileiro S.A. - Petrobras", "NYSE", "stock", True),
      ("PBR-A", "Petróleo Brasileiro S.A. - Petrobras", "NYSE", "stock", True)], "petrobras"),
    # 4-letter tickers ending in a preferred letter are commons.
    ([("SNAP", "Snap Inc.", "NYSE", "stock", True),
      ("RAMP", "LiveRamp Holdings, Inc.", "NYSE", "stock", True),
      ("NTBP", "NTBP Holdings Inc.", "NASDAQ", "stock", True)], "p"),
    # Same letters as a twin rule, but the base is a DIFFERENT company.
    ([("PG", "The Procter & Gamble Company", "NYSE", "stock", True),
      ("PGR", "The Progressive Corporation", "NYSE", "stock", True),
      ("V", "Visa Inc.", "NYSE", "stock", True),
      ("VRT", "Vertiv Holdings Co", "NYSE", "stock", True),
      ("VRTV", "Veritiv Corporation", "NYSE", "stock", True)], "x"),
    # Names that only LOOK like debt.
    ([("PFBC", "Preferred Bank", "NASDAQ", "stock", True),
      ("PFBX", "Preferred Bank Inc.", "NYSE", "stock", True),
      ("PRPI", "Perpetual Industries Inc.", "NASDAQ", "stock", True),
      ("BNMG", "Blue Note Mining Inc.", "NASDAQ", "stock", True),
      ("ARM", "Arm Holdings plc American Depositary Shares", "NASDAQ", "stock", True),
      ("SNDA", "Sonida Senior Living, Inc.", "NYSE", "stock", True)], "x"),
    # 4-letter X tickers and a 5-letter X outside NASDAQ are not mutual funds.
    ([("FOXX", "Foxx Development Holdings Inc.", "NASDAQ", "stock", True),
      ("BIOX", "Bioceres Crop Solutions Corp.", "NASDAQ", "stock", True),
      ("ABCDX", "Some NYSE Listing Inc.", "NYSE", "stock", True)], "x"),
    # The debt-name rule never reads ETF or fund rows; root twins are stock-only.
    # FFC carries BOTH a corporate marker ("Incorporated") and a debt marker ("Preferred
    # Securities") — only the stock-only scope keeps this closed-end fund.
    ([("FAMG", "Fidelity Asset Manager 20%", "NYSE", "etf", True),
      ("FFC", "Flaherty & Crumrine Preferred Securities Income Fund Incorporated",
       "NYSE", "fund", True),
      ("RSP", "Invesco S&P 500 Equal Weight ETF", "AMEX", "etf", True),
      ("RSPT", "Invesco S&P 500 Equal Weight ETF", "AMEX", "etf", True)], "x"),
])
def test_distinct_securities_are_never_hidden(rows, q):
    assert sorted(_refine(rows, q)) == sorted(r[0] for r in rows)


def test_googl_is_kept_only_because_it_is_allow_listed():
    """GOOGL has the SAME shape as a note twin: 'L' is a note letter (AGNCL), its base
    GOOG is live and byte-identical. The allow-list is the only thing keeping it."""
    rows = [("GOOG", "Alphabet Inc.", "NASDAQ", "stock", True),
            ("GOOGL", "Alphabet Inc.", "NASDAQ", "stock", True)]
    assert _refine(rows, "alphabet") == ["GOOG", "GOOGL"]
    assert "GOOGL" in svc._KNOWN_SHARE_CLASS_SYMBOLS


@pytest.mark.parametrize("sym", [
    "AVGOP", "AGNCP", "AGNCL", "AGNCZ", "BAC-PE", "BML-PH", "TBB", "APOS", "SOMN",
    "SOJD", "METCI", "VFSWW", "BIOTW", "JACS-RI", "JMGRX", "BRKS",
])
def test_every_rule_exempts_the_exact_typed_ticker(sym):
    rows = (AVGO_ROWS + AGNC + BANK_OF_AMERICA + ATT + APO + SOUTHERN + MET
            + PADDED_WARRANTS + JANUS + BRK)
    out = _refine(rows, sym)
    assert out[0] == sym


# ── Mutual funds (product decision: hidden) ───────────────────────────────────

def test_nasdaq_mutual_funds_are_hidden_even_when_typed_as_stock():
    """HEIFX / LZFOX / QLENX carry no 'Fund' word, so the classifier typed them 'stock'
    and they reached the company pickers."""
    rows = [("HEI", "HEICO Corporation", "NYSE", "stock", True),
            ("HEIFX", "Hennessy Equity and Income Investor", "NASDAQ", "stock", True),
            ("LZFOX", "Lazard Equity Franchise Portfolio Open Shares", "NASDAQ", "stock", True)]
    assert _refine(rows, "hei") == ["HEI"]


# ── Liveness ──────────────────────────────────────────────────────────────────

def test_dead_listings_are_dropped_but_only_with_a_directory():
    rows = [("FISV", "Fiserv, Inc.", "NASDAQ", "stock", True),
            ("FI", "Fiserv, Inc.", "NYSE", "stock", False),
            ("TWTR", "Twitter, Inc. (delisted)", "NYSE", "stock", False)]
    assert _refine(rows, "fiserv") == ["FISV"]
    # Fail OPEN: an outage can bring a dead row back, never hide a live one.
    assert _refine(rows, "fiserv", directory_available=False) == ["FISV", "FI", "TWTR"]


def test_corporate_action_twin_collapses_only_onto_a_live_base():
    rows = [("BACA", "Berenson Acquisition Corp. I", "NYSE", "stock", False),
            ("BACA-UN", "Berenson Acquisition Corp. I", "NYSE", "stock", False)]
    assert _refine(rows, "berenson") == []


# ── _issuer_key edges ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("The Southern Company", "Southern Company (The) Series 2"),
    ("The Southern Company", "The Southern Company JR SUB NT 76"),
    ("Instinct Bio Technical Co. Holdings Inc.",
     "Instinct Bio Technical Company Holdings Inc. Warrants"),
    ("Duke Energy Corporation", "Duke Energy Corporation Units 1.08.29"),
    ("DUKE Robotics Corp.", "Duke Robotics Corp. C/wts Exp 06/05/2031(to Pur Com)"),
    ("AT&T Inc.", "AT&T Inc. 5.35% GLB NTS 66"),
])
def test_issuer_key_matches_one_issuer_across_security_descriptions(a, b):
    assert _issuer_key(a) and _issuer_key(a) == _issuer_key(b)


@pytest.mark.parametrize("name", [None, "", "   ", "Inc.", "The Company", "3M Company",
                                  "Series A Preferred", "% notes"])
def test_issuer_key_too_short_or_empty_never_matches(name):
    assert _issuer_key(name) == ""


def test_issuer_keys_of_different_companies_differ():
    assert _issuer_key("MetLife, Inc.") != _issuer_key("Meta Platforms, Inc.")
    assert _issuer_key("The Procter & Gamble Company") != _issuer_key("The Progressive Corporation")


@pytest.mark.parametrize("name", [
    "%" * 10_000, "notes " * 2_000, "a" * 10_000, "Inc " * 3_000,
    "depositary " * 1_500 + "sh", "(the)" * 2_000, "jr" + " " * 10_000 + "sub",
])
def test_pathological_names_are_fast(name):
    """Search runs on the single uvicorn worker on every keystroke."""
    rows = [_res("AGNCL", name), _res("AGNC", name), _res("ABCDE", name)]
    started = time.perf_counter()
    for _ in range(5):
        refine_listings(rows, "X", {"AGNC": name, "AGNCL": name, "ABCDE": name})
        _issuer_key(name)
    assert time.perf_counter() - started < 0.25


# ── Ranking ───────────────────────────────────────────────────────────────────

def test_ranking_is_stable_within_a_tier_and_symbol_type_pairs_stay_unique():
    out = refine_listings(*_scenario(BRK)[:1], "BRK", _scenario(BRK)[1])
    assert _syms(out)[:2] == ["BRK-B", "BRK-A"]
    assert len({(r.symbol, r.type) for r in out}) == len(out)


def test_exact_symbol_ranks_first_even_when_fmp_lists_it_last():
    rows = [("UAA", "Under Armour, Inc.", "NYSE", "stock", True),
            ("UAL", "United Airlines Holdings, Inc.", "NASDAQ", "stock", True),
            ("UA", "Under Armour, Inc.", "NYSE", "stock", True)]
    assert _refine(rows, "UA") == ["UA", "UAA", "UAL"]


# ── Endpoint: wiring, fail-open, and the error contract ───────────────────────

def _fmp_row(sym, name, ex="NASDAQ"):
    return {"symbol": sym, "name": name, "currency": "USD", "exchange": ex,
            "exchangeFullName": ex}


class _FakeFMP:
    def __init__(self, rows):
        self.rows = rows
        self.calls: List[dict] = []

    async def search_stocks(self, query, limit=10, counts_as_symbol_match=None):
        self.calls.append({"query": query, "limit": limit,
                           "counts_as_symbol_match": counts_as_symbol_match})
        return self.rows


def _patch(monkeypatch, rows, directory):
    fake = _FakeFMP(rows)
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: fake)
    monkeypatch.setattr(svc, "get_active_listings", lambda: directory)
    return fake


@pytest.mark.asyncio
async def test_endpoint_avgo_returns_one_row(monkeypatch):
    _patch(monkeypatch, [
        _fmp_row("AVGO", "Broadcom Inc."),
        _fmp_row("AVGOP", "Broadcom Inc."),
        {"symbol": "AVGO.TO", "name": "Broadcom Inc.", "currency": "CAD", "exchange": "TSX",
         "exchangeFullName": "Toronto Stock Exchange"},
    ], {"AVGO": "Broadcom Inc."})
    out = await stocks_ep.search_stocks(q="avgo", limit=10)
    assert [(r.symbol, r.type) for r in out] == [("AVGO", "stock")]


@pytest.mark.asyncio
async def test_endpoint_exact_dead_ticker_is_shown(monkeypatch):
    _patch(monkeypatch, [_fmp_row("AVGOP", "Broadcom Inc."), _fmp_row("AVGO", "Broadcom Inc.")],
           {"AVGO": "Broadcom Inc."})
    out = await stocks_ep.search_stocks(q="avgop", limit=10)
    assert [r.symbol for r in out] == ["AVGOP", "AVGO"]


@pytest.mark.asyncio
async def test_endpoint_fails_open_without_a_directory(monkeypatch):
    _patch(monkeypatch, [_fmp_row("TWTR", "Twitter, Inc. (delisted)", "NYSE"),
                         _fmp_row("AVGOP", "Broadcom Inc.")], None)
    out = await stocks_ep.search_stocks(q="twitter", limit=10)
    assert [r.symbol for r in out] == ["TWTR"], "grammar still hides AVGOP; TWTR comes back"


@pytest.mark.asyncio
async def test_endpoint_widens_the_upstream_window_and_passes_the_ticker_rule(monkeypatch):
    fake = _patch(monkeypatch, [], None)
    await stocks_ep.search_stocks(q="bank", limit=10)
    await stocks_ep.search_stocks(q="bank", limit=50)
    assert [c["limit"] for c in fake.calls] == [100, 250]
    rule = fake.calls[0]["counts_as_symbol_match"]
    assert rule(_fmp_row("BANK", "Some Bank Inc.", "NYSE")) is True
    assert rule({"symbol": "BANK.L", "name": "x", "exchange": "LSE"}) is False
    assert rule({"symbol": "BANKUSD", "name": "x", "exchange": "CRYPTO"}) is False
    assert rule({"symbol": 7, "name": "x", "exchange": "NYSE"}) is False
    assert rule({"symbol": "BANK", "exchange": 12}) is False, "malformed row is not a hit"


@pytest.mark.asyncio
async def test_endpoint_rule_failure_serves_the_twin_dedupe_and_logs_once(monkeypatch, caplog):
    _patch(monkeypatch, [_fmp_row("SNDK", "Sandisk Corporation"),
                         _fmp_row("SNDKV", "Sandisk Corporation"),
                         _fmp_row("AVGOP", "Broadcom Inc.")], {"SNDK": "Sandisk Corporation"})

    def _boom(*a, **k):
        raise RuntimeError("rule bug")

    monkeypatch.setattr(svc, "refine_listings", _boom)
    with caplog.at_level(logging.DEBUG, logger="app.services.stock_search_service"):
        first = await stocks_ep.search_stocks(q="sandisk", limit=10)
        second = await stocks_ep.search_stocks(q="sandisk", limit=10)
    assert [r.symbol for r in first] == ["SNDK", "AVGOP"] == [r.symbol for r in second]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and errors[0].exc_info, "ERROR with a stack, once per type"


@pytest.mark.asyncio
async def test_endpoint_keeps_the_btc_coin_and_etf_twins(monkeypatch):
    _patch(monkeypatch, [_fmp_row("BTC", "Grayscale Bitcoin Mini Trust", "AMEX")],
           {"BTC": "Grayscale Bitcoin Mini Trust ETF"})
    out = await stocks_ep.search_stocks(q="BTC", limit=10)
    assert [(r.symbol, r.type) for r in out if r.symbol == "BTC"] == [("BTC", "crypto"), ("BTC", "etf")]


@pytest.mark.asyncio
async def test_endpoint_toyota_adrhedged_is_an_etf(monkeypatch):
    _patch(monkeypatch, [_fmp_row("TM", "Toyota Motor Corporation", "NYSE"),
                         _fmp_row("TMH", "Toyota Motor Corporation ADRhedged", "AMEX")],
           {"TM": "Toyota Motor Corporation", "TMH": "Toyota Motor Corporation ADRhedged"})
    out = await stocks_ep.search_stocks(q="toyota", limit=10)
    assert [(r.symbol, r.type) for r in out] == [("TM", "stock"), ("TMH", "etf")]


# ── Review fixes (2026-09-25 adversarial pass) ────────────────────────────────

from app.integrations.fmp import FMPClient  # noqa: E402
from app.services.stock_search_service import would_keep  # noqa: E402


class _RecordingFMP:
    """The REAL `FMPClient.search_stocks` over canned endpoint answers, recording calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: List[str] = []

    async def _make_request(self, endpoint, params=None):
        self.calls.append(endpoint)
        return self.responses[endpoint]

    search_stocks = FMPClient.search_stocks
    _has_symbol_prefix_match = staticmethod(FMPClient._has_symbol_prefix_match)


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", [None, {"V": "Visa Inc.", "VISAX": "Virtus KAR"}])
async def test_visa_is_found_when_the_only_prefix_hit_is_a_hidden_fund(monkeypatch, directory):
    """REGRESSION caught in review: 'visa' returned NOTHING. search-symbol's only US row
    is VISAX (a NASDAQ mutual fund); it counted as a ticker hit, so the name search never
    ran, and then the mutual-fund rule hid it. A prefix hit only counts if it survives."""
    client = _RecordingFMP({
        "search-symbol": [_fmp_row("VISAX", "Virtus KAR International Small-Mid Cap Fund - Class A"),
                          {"symbol": "VISA.TO", "name": "Visa", "exchange": "TSX"}],
        "search-name": [_fmp_row("V", "Visa Inc.", "NYSE")],
    })
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: client)
    monkeypatch.setattr(svc, "get_active_listings", lambda: directory)
    out = await stocks_ep.search_stocks(q="visa", limit=10)
    assert client.calls == ["search-symbol", "search-name"]
    assert [r.symbol for r in out] == ["V"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix_row,directory", [
    (_fmp_row("XYZ-PA", "Xyz Holdings Corp", "NYSE"), None),              # dash preferred
    (_fmp_row("XYZAP", "Xyz Holdings Corp"), None),                       # NASDAQ preferred
    (_fmp_row("XYZA", "Xyz Old Corp", "NYSE"), {"XYZ": "Xyz Holdings Corp"}),  # dead
])
async def test_a_prefix_hit_the_rules_hide_does_not_skip_the_name_search(
    monkeypatch, prefix_row, directory
):
    client = _RecordingFMP({"search-symbol": [prefix_row],
                            "search-name": [_fmp_row("XYZ", "Xyz Holdings Corp", "NYSE")]})
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: client)
    monkeypatch.setattr(svc, "get_active_listings", lambda: directory)
    out = await stocks_ep.search_stocks(q="xyz", limit=10)
    assert client.calls == ["search-symbol", "search-name"]
    assert "XYZ" in [r.symbol for r in out]


@pytest.mark.asyncio
async def test_an_exact_typed_hidden_ticker_still_costs_one_call(monkeypatch):
    client = _RecordingFMP({"search-symbol": [_fmp_row("AVGOP", "Broadcom Inc.")]})
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: client)
    monkeypatch.setattr(svc, "get_active_listings", lambda: {"AVGO": "Broadcom Inc."})
    out = await stocks_ep.search_stocks(q="AVGOP", limit=10)
    assert client.calls == ["search-symbol"] and [r.symbol for r in out] == ["AVGOP"]


@pytest.mark.parametrize("row,q,directory,expected", [
    (_res("VISAX", "Virtus KAR Fund", "NASDAQ", "fund"), "VISA", None, False),
    (_res("VISAX", "Virtus KAR Fund", "NASDAQ", "fund"), "VISAX", None, True),     # exact
    (_res("FI", "Fiserv, Inc.", "NYSE"), "F", {"FISV": "Fiserv, Inc."}, False),    # dead
    (_res("FI", "Fiserv, Inc.", "NYSE"), "F", None, True),                         # fail open
    (_res("AGNCL", "AGNC Investment Corp."), "AGNC",
     {"AGNC": "AGNC Investment Corp.", "AGNCL": "AGNC Investment Corp."}, False),   # root twin
    (_res("GOOGL", "Alphabet Inc."), "GOOG",
     {"GOOG": "Alphabet Inc.", "GOOGL": "Alphabet Inc."}, True),                    # class share
    (_res("AAPL", "Apple Inc."), "AAP", {"AAPL": "Apple Inc."}, True),
])
def test_would_keep_matches_the_listing_rules(row, q, directory, expected):
    assert would_keep(row, q, directory) is expected


DASH_TWINS: List[Row] = [
    ("IONQ", "IonQ, Inc.", "NYSE", "stock", True),
    ("IONQ-WT", "IonQ, Inc. WT", "NYSE", "stock", True),
    ("BBAI", "BigBear.ai Holdings, Inc.", "NYSE", "stock", True),
    ("BBAI-WT", "BigBear.ai Holdings, Inc. WT", "NYSE", "stock", True),
    ("AAC", "Ares Acquisition Corp. III Class A", "NYSE", "stock", True),
    ("AAC-UN", "Ares Acquisition Corporation III", "NYSE", "stock", True),
    ("SKYH", "Sky Harbour Group Corp", "NYSE", "stock", True),
    ("SKYH-WT", "Sky Harbour Group Corporation", "NYSE", "stock", True),
]


@pytest.mark.parametrize("q,expected", [
    ("ionq", ["IONQ"]), ("bbai", ["BBAI"]), ("aac", ["AAC"]), ("skyh", ["SKYH"]),
    ("IONQ-WT", ["IONQ-WT", "IONQ"]),
])
def test_dash_warrant_and_unit_twins_collapse_onto_the_same_issuer(q, expected):
    """IONQ-WT 'IonQ, Inc. WT' and AAC-UN 'Ares Acquisition Corporation III' did not
    match their base's raw name, so they showed as a second company row."""
    rows = [r for r in DASH_TWINS if r[0].startswith(q.upper()[:3])]
    assert _refine(rows, q) == expected
    assert _refine(rows, q, directory_available=False) == expected, "no data needed"


def test_the_dash_twin_rule_does_not_touch_dash_classes_or_etfs():
    rows = [("BRK-A", "Berkshire Hathaway Inc.", "NYSE", "stock", True),
            ("BRK-B", "Berkshire Hathaway Inc. Class B", "NYSE", "stock", True),
            ("MKC", "McCormick & Company, Incorporated", "NYSE", "stock", True),
            ("MKC-V", "McCormick & Company, Incorporated", "NYSE", "stock", True),
            # The issuer-level match is STOCK-only: on this ETF pair only it would pair
            # the two ("…Trust WT" vs "…Trust"), so the ETF twin must survive it.
            ("ABCD", "Alpha Beta ETF Trust", "AMEX", "etf", True),
            ("ABCD-WT", "Alpha Beta ETF Trust WT", "AMEX", "etf", True)]
    assert sorted(_refine(rows, "x")) == sorted(r[0] for r in rows)


DEBT_ISSUERS: List[Row] = [
    ("BIP", "Brookfield Infrastructure Partners L.P.", "NYSE", "stock", True),
    ("BIPH", "Brookfield Infrastructure Finance ULC 5 % Notes 2021-24.05.81 Global", "NYSE", "stock", True),
    ("DDS", "Dillard's, Inc.", "NYSE", "stock", True),
    ("DDT", "Dillards Capital Trust I CAP SECS 7.5%", "NYSE", "stock", True),
    ("CTSE", "Corgi U.S. Equities 30% Structu", "NASDAQ", "stock", True),
]


def test_finance_vehicle_notes_are_hidden_and_structured_etfs_are_not():
    """BIPH (a ULC) and DDT (a Capital Trust) carry a debt marker but no corporate word,
    so the debt-name rule never looked at them. CTSE — a structured ETF FMP types as a
    stock — carries a '%' and no issuer word, and must stay."""
    assert _refine(DEBT_ISSUERS, "x") == ["BIP", "DDS", "CTSE"]
    assert _refine(DEBT_ISSUERS, "BIPH")[0] == "BIPH"
    assert _refine(DEBT_ISSUERS, "DDT")[0] == "DDT"
