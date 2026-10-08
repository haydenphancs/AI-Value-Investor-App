"""Round 2 of the 2026-10-08 owner calls: the verifier's findings B1, B2 and B4 against
`scripts/build_benchmark_universe.py` and the search route's `_get_asset_type`.

  B1  The ADR "each representing N preferred share(s)" cut kept fixed-income preferreds whose
      qualifier comes AFTER the description ("… Preferred Share, Cumulative", "… Liquidation
      Preference $25") or sits in the issuer's name ("XYZ Capital Funding Trust"). The cut now
      needs the description to END the name, and is refused when the name carries a
      fixed-income word anywhere. The Brazilian common / preferred-class ADR names stay kept.
  B2  One vote per issuer runs per industry. One issuer's share classes filed under DIFFERENT
      industries (the June file: BBDO in "Banks", BBD in "Banks - Regional") are both kept —
      and now named at WARNING by a market-wide pass, for the owner to read before upload.
  B4  The search route typed MSDL "Morgan Stanley Direct Lending Fund" and BXSL "Blackstone
      Secured Lending Fund" as "fund" (iOS folds "fund" into the ETF screen). The builder's
      narrow "Lending Fund" exemption now applies there too; every other fund name is
      unchanged.

Each test docstring names the mutation that turns it red (hand-checked). Hermetic: pure
functions, `main()` over a fake FMP client, and the search route over canned FMP answers.
"""
from __future__ import annotations

import zlib
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

import pytest

import app.api.v1.endpoints.stocks as stocks_ep
import scripts.build_benchmark_universe as bu
from app.integrations.fmp import FMPClient
from app.services import stock_search_service as search

FLOOR = 500_000_000


def _row(sym: str, name: str, ex: str = "NYSE", cap: Any = 5e9, *,
         industry: str = "Banks - Regional", sector: str = "Financial Services",
         price: Any = 20.0, avg_volume: Any = 1_000_000, **over: Any) -> Dict[str, Any]:
    """A /stable company-screener row (FMP's field names); a liquid common by default."""
    row = {
        "symbol": sym, "companyName": name, "marketCap": cap,
        "sector": sector, "industry": industry,
        "price": price, "volume": avg_volume, "avgVolume": avg_volume,
        "exchange": ex, "exchangeShortName": ex, "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _drop(sym: str, name: str) -> Optional[str]:
    return bu._drop_reason(_row(sym, name), FLOOR)


# ══ B1: the preferred-class cut never keeps a fixed-income preferred ═══════════════════

_DESC = "XYZ S.A. American Depositary Shares, each representing one Preferred Share"

# The verifier's endings (each returned None — kept — before this fix).
VERIFIER_ENDINGS = [
    " Perpetual", " Redeemable", " Callable", " Cumulative", " Convertible",
    " Mandatory Convertible", " Fixed-to-Floating Rate", " Dollar Preference",
    " Liquidation Preference $25", ", Cumulative",
]
# Words AFTER the description that name no fixed-income word: only the END anchor drops them.
ANCHOR_ONLY_ENDINGS = [" Step-Up Rate", " Dollar Denominated", " (Brazil)", " Sub Voting"]
# The description ENDS the name, but the issuer's part carries a fixed-income word: only the
# word guard drops them.
WORD_ONLY_NAMES = [
    "XYZ Capital Funding Trust American Depositary Shares, each representing one Preferred "
    "Share",
    "XYZ Perpetual Capital S.A. American Depositary Shares, each representing one Preferred "
    "Share",
    "XYZ Convertible Holdings plc American Depositary Shares, each representing 2 Preferred "
    "Shares",
    "XYZ Floating Rate Capital Ltd. Global Depositary Shares, each representing a Preferred "
    "Share",
    "XYZ Redeemable S.A. American Depositary Receipts each representing one Class B "
    "Preferred Share",
    "XYZ Bank $25 S.A. American Depositary Shares, each representing one Preferred Share",
    "XYZ Bank Exchangeable S.A. American Depositary Shares, each representing one Preferred "
    "Share",
]


def _reached_the_old_cut(name: str) -> bool:
    """The pre-fix rule: an ADR phrase, then a count-only description anywhere — no end
    anchor, no word guard (the series guard is the only one it had)."""
    old = r"\brepresenting\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|an?|\d{1,4})" \
          r"\s+(?:class\s+[a-z]\s+)?preferred\s+shares?\b"
    import re
    cut, is_adr = bu._without_adr_phrase(bu._row_name({"companyName": name}))
    return is_adr and not bu._SERIES_WORD_RE.search(cut) and bool(
        re.search(old, cut, re.IGNORECASE))


@pytest.mark.parametrize("ending", VERIFIER_ENDINGS)
def test_the_verifiers_fixed_income_endings_drop(ending):
    """Each was KEPT before (the description was cut wherever it sat). Mutation: remove BOTH
    the end anchor and the word guard → each is kept again. (One guard alone still drops
    them: every ending here is both after the description and a fixed-income word.)"""
    name = _DESC + ending
    assert _reached_the_old_cut(name)
    assert _drop("XYZ", name) == "not_common_share"
    assert _drop("XYZ-A", name) == "not_common_share"         # a one-letter dash class too


@pytest.mark.parametrize("ending", ANCHOR_ONLY_ENDINGS)
def test_a_description_that_does_not_end_the_name_is_not_cut(ending):
    """Mutation: drop the `[\\s,.;)]*\\Z` anchor from `_ADR_PREFERRED_CLASS_RE` → kept."""
    name = _DESC + ending
    assert not bu._PREFERRED_FIXED_INCOME_WORD_RE.search(name), "the anchor alone must decide"
    cut, _ = bu._without_adr_phrase(bu._row_name({"companyName": name}))
    assert bu._without_preferred_class(cut) == (cut, False)
    assert _drop("XYZ", name) == "not_common_share"


@pytest.mark.parametrize("name", WORD_ONLY_NAMES)
def test_a_fixed_income_word_anywhere_refuses_the_cut(name):
    """Mutation: drop the `_PREFERRED_FIXED_INCOME_WORD_RE` check from
    `_without_preferred_class` → each is kept (the description ends the name)."""
    cut, is_adr = bu._without_adr_phrase(bu._row_name({"companyName": name}))
    assert is_adr and bu._ADR_PREFERRED_CLASS_RE.search(cut), "the anchor alone would cut it"
    assert bu._without_preferred_class(cut) == (cut, False)
    assert _drop("XYZ", name) == "not_common_share"


@pytest.mark.parametrize("name", [
    # The Brazilian preferred-class description forms, with and without trailing punctuation.
    "Gerdau S.A. American Depositary Shares, each representing one preferred share",
    "Gerdau S.A. American Depositary Shares, each representing one preferred share.",
    "Itaú Unibanco Holding S.A. American Depositary Shares, each representing 1 Preferred "
    "Share;",
    "Centrais Elétricas Brasileiras S.A. - Eletrobras American Depositary Shares, each "
    "representing one Class B Preferred Share",
    "Companhia Energética de Minas Gerais American Depositary Shares, each representing one "
    "Preferred Share ",
    "Companhia Paranaense de Energia - COPEL American Depositary Shares (each representing "
    "one Preferred Share)",
    # Whole words only: a fixed-income word INSIDE another word never refuses the cut.
    "Companhia Refunding S.A. American Depositary Shares, each representing one Preferred "
    "Share",
    "Trustworthy Bank S.A. American Depositary Shares, each representing one Preferred Share",
    "Fixedline Telecom S.A. American Depositary Shares, each representing one Preferred Share",
    "Convertibles Brasil S.A. American Depositary Shares, each representing one Preferred "
    "Share",
])
def test_a_brazilian_preferred_class_adr_is_still_kept(name):
    """Mutation: drop the `\\b` around the word list → the substring rows drop; drop the
    `[\\s,.;)]*` before the anchor → the punctuated rows drop."""
    assert _drop("XYZ", name) is None


@pytest.mark.parametrize("sym,name", [
    # The probe's real FMP strings (no class, no description): untouched by either guard.
    ("ITUB", "Itaú Unibanco Holding S.A."), ("BBD", "Banco Bradesco S.A."),
    ("BBDO", "Banco Bradesco S.A."), ("PBR", "Petróleo Brasileiro S.A. - Petrobras"),
    ("PBR-A", "Petróleo Brasileiro S.A. - Petrobras"), ("GGB", "Gerdau S.A."),
    ("CIG", "Companhia Energética de Minas Gerais"),
    ("ELP", "Companhia Paranaense de Energia - COPEL"),
])
def test_the_probe_names_stay_kept(sym, name):
    assert _drop(sym, name) is None


def test_the_word_guard_is_whole_word_and_names_each_family():
    rx = bu._PREFERRED_FIXED_INCOME_WORD_RE
    for word in ("Cumulative", "Non-Cumulative", "PERPETUAL", "redeemable", "Callable",
                 "Convertible", "Exchangeable", "Liquidation", "Floating", "Fixed",
                 "Fixed-to-Floating", "Variable", "Adjustable", "Reset", "Auction",
                 "Funding", "Trust", "Preference", "$25", "$ 25"):
        assert rx.search(f"XYZ {word} S.A."), word
    for text in ("Refunding", "Trustee", "Trustworthy", "Fixedline", "Convertibles",
                 "Preferences", "Cumulatively", "US$", "$ ", "Banco Bradesco S.A."):
        assert not rx.search(text), text


def test_a_refused_cut_is_never_named_as_an_owner_call_keep(caplog):
    """The INFO line names only what the exemption KEPT; a refused name is not one.
    Mutation: compute the line from the description alone (no guards) → named."""
    rows = [_row("XYZ", WORD_ONLY_NAMES[0]), _row("ABC", _DESC + " Cumulative")]
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        bu._log_owner_call_keeps({"Banks": rows})
    assert "preferred share class" not in caplog.text


def test_the_anchored_cut_is_fast_on_pathological_names():
    """The `[\\s,.;)]*\\Z` tail backtracks over a punctuation run once per description."""
    names = [("representing one preferred share" + "," * 50_000 + "x") * 3,
             "representing one preferred share" + " ;" * 50_000 + "x",
             "Fixed " * 50_000 + "representing one preferred share",
             "$" * 100_000 + "1"]
    started = time.perf_counter()
    for name in names:
        bu._without_preferred_class(name)
        bu._drop_reason(_row("XYZ", "American Depositary Shares " + name), FLOOR)
    assert time.perf_counter() - started < 0.5


# ══ B2: one issuer's classes in DIFFERENT industries are named at WARNING ════════════════

BBD = dict(sym="BBD", name="Banco Bradesco S.A.")
BBDO = dict(sym="BBDO", name="Banco Bradesco S.A.")


def _market(**industries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    return {k.replace("_", " "): v for k, v in industries.items()}


def test_bradesco_across_banks_and_banks_regional_is_kept_twice_and_named():
    """The June file's shape. `_filter_market` keeps both (one vote per issuer is per
    industry, by design); the market-wide pass names the pair."""
    market = {"Banks": [_row("BBDO", "Banco Bradesco S.A.", industry="Banks")],
              "Banks - Regional": [_row("BBD", "Banco Bradesco S.A.")]}
    result = bu._filter_market(market, FLOOR)
    assert {i: [r["symbol"] for r in rows] for i, rows in result.kept.items()} == {
        "Banks": ["BBDO"], "Banks - Regional": ["BBD"]}
    assert result.dropped == {}
    assert bu._cross_industry_share_classes(result.kept) == [
        'BBDO [Banks] + BBD [Banks - Regional] "Banco Bradesco S.A."']


@pytest.mark.parametrize("market,expected", [
    # Petrobras across two energy industries; a dash class.
    ({"Oil & Gas Integrated": [_row("PBR", "Petróleo Brasileiro S.A. - Petrobras")],
      "Oil & Gas E&P": [_row("PBR-A", "Petróleo Brasileiro S.A. - Petrobras")]},
     ['PBR-A [Oil & Gas E&P] + PBR [Oil & Gas Integrated] "Petróleo Brasileiro S.A. - '
      'Petrobras"']),
    # Round 4 keeps FOX / FOXA in two industries (each votes in its own) — now also named.
    ({"Entertainment": [_row("FOX", "Fox Corporation", "NASDAQ")],
      "Broadcasting": [_row("FOXA", "Fox Corporation", "NASDAQ")]},
     ['FOXA [Broadcasting] + FOX [Entertainment] "Fox Corporation"']),
    # Three classes in three industries: every pair.
    ({"A": [_row("BRK-A", "Berkshire Hathaway Inc.")],
      "B": [_row("BRK-B", "Berkshire Hathaway Inc.")],
      "C": [_row("BRK-C", "Berkshire Hathaway Inc.")]},
     ['BRK-A [A] + BRK-B [B] "Berkshire Hathaway Inc."',
      'BRK-A [A] + BRK-C [C] "Berkshire Hathaway Inc."',
      'BRK-B [B] + BRK-C [C] "Berkshire Hathaway Inc."']),
])
def test_every_cross_industry_class_pair_is_named(market, expected):
    kept = bu._filter_market(market, FLOOR).kept
    assert sum(len(v) for v in kept.values()) == sum(len(v) for v in market.values())
    assert bu._cross_industry_share_classes(kept) == expected


@pytest.mark.parametrize("market", [
    # Same industry: the per-industry vote already merged them — nothing left to name.
    {"Banks - Regional": [_row("BBD", "Banco Bradesco S.A.", avg_volume=4e7),
                          _row("BBDO", "Banco Bradesco S.A.", avg_volume=2e6)]},
    # A sibling SYMBOL under another issuer (Barings BDC vs Bradesco).
    {"Banks - Regional": [_row("BBD", "Banco Bradesco S.A.")],
     "Asset Management": [_row("BBDC", "Barings BDC, Inc.", cap=1e9)]},
    # One normalised name, two banks: the symbols are not classes of one listing.
    {"Banks - Regional": [_row("FBNC", "First Bancorp", "NASDAQ")],
     "Banks - Diversified": [_row("FBP", "First BanCorp.")]},
    # Tracking stocks with unrelated symbols.
    {"Entertainment": [_row("FWONK", "Liberty Media Corporation", "NASDAQ")],
     "Broadcasting": [_row("LSXMK", "Liberty Media Corporation", "NASDAQ")]},
    # A name too short to prove one issuer has no key.
    {"Conglomerates": [_row("MMM", "3M Company")],
     "Specialty Industrial Machinery": [_row("MMMA", "3M Company")]},
    # Distinct companies, sibling-looking symbols.
    {"Restaurants": [_row("YUM", "Yum! Brands, Inc.")],
     "Restaurants - China": [_row("YUMC", "Yum China Holdings, Inc.")]},
])
def test_nothing_else_is_named(market):
    """Mutations: group every row under one key → BBD/BBDC and YUM/YUMC named; drop the
    `_share_class_siblings` check → FBNC/FBP and FWONK/LSXMK named."""
    kept = bu._filter_market(market, FLOOR).kept
    assert bu._cross_industry_share_classes(kept) == []


def test_two_classes_in_one_industry_are_never_named_even_unfiltered():
    """The pass reads only CROSS-industry pairs. Mutation: drop `industry_a != industry_b`
    → this same-industry pair (never merged, as the input skips `_filter_market`) is named."""
    assert bu._cross_industry_share_classes({"Banks - Regional": [
        _row("BBD", "Banco Bradesco S.A."), _row("BBDO", "Banco Bradesco S.A.")]}) == []


def test_the_named_pairs_do_not_depend_on_fmp_order():
    market = {
        "Banks": [_row("BBDO", "Banco Bradesco S.A."), _row("ITUB", "Itaú Unibanco Holding S.A.")],
        "Banks - Regional": [_row("BBD", "Banco Bradesco S.A."),
                             _row("FBNC", "First Bancorp", "NASDAQ")],
        "Banks - Diversified": [_row("FBP", "First BanCorp.")],
        "Oil & Gas E&P": [_row("PBR-A", "Petróleo Brasileiro S.A. - Petrobras")],
        "Oil & Gas Integrated": [_row("PBR", "Petróleo Brasileiro S.A. - Petrobras")],
    }
    expected = bu._cross_industry_share_classes(bu._filter_market(market, FLOOR).kept)
    assert len(expected) == 2
    rng = random.Random(20261008)
    names = list(market)
    for _ in range(20):
        rng.shuffle(names)
        shuffled = {n: rng.sample(market[n], len(market[n])) for n in names}
        assert bu._cross_industry_share_classes(
            bu._filter_market(shuffled, FLOOR).kept) == expected


def test_the_log_line_is_a_warning_that_names_every_pair(caplog):
    entries = [f"E{i}" for i in range(bu._SUSPECTS_NAMED + 3)]
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        bu._log_cross_industry_share_classes([])
        assert caplog.records == []                       # nothing to name, nothing said
        bu._log_cross_industry_share_classes(entries)
    (line,) = caplog.records
    assert line.levelno == logging.WARNING
    msg = line.getMessage()
    assert f"{len(entries)} pair(s)" in msg and "DIFFERENT industries" in msg
    assert "check before uploading" in msg and "(+3 more)" in msg
    # Kept apart from the twin line, which a round-4 test unpacks by "ANOTHER industry".
    assert "ANOTHER industry" not in msg


def test_the_docstrings_no_longer_claim_a_market_wide_guarantee():
    """B2: the module and `_one_vote_per_issuer` docstrings said BBD + BBDO "each vote once"
    unconditionally."""
    assert "INSIDE AN INDUSTRY" in bu.__doc__ and "_cross_industry_share_classes" in bu.__doc__
    doc = bu._one_vote_per_issuer.__doc__
    assert "per industry" in doc and "_cross_industry_share_classes" in doc
    assert "same industry" in bu._EXPECTED_DROP_NOTES["same_issuer"]



def _distinct_ratios(sym: str) -> List[Dict[str, Any]]:
    """A `ratios-ttm` answer unique to `sym`: every listing reports its own statements, so
    the statement-twin pass (2026-10-08) drops nothing these tests did not ask for. The
    twin pass itself is pinned in test_benchmark_universe_builder_twins_2026_10_09.py."""
    seed = zlib.crc32(sym.encode("utf-8")) + 1
    return [{"grossProfitMarginTTM": 0.3 + seed / 2**34, "operatingProfitMarginTTM": 0.1 + seed / 2**35,
             "netProfitMarginTTM": 0.05 + seed / 2**36, "currentRatioTTM": 1.5,
             "debtToEquityRatioTTM": 0.8}]

class _FakeFMP:
    def __init__(self, screener: Dict[str, List[Dict[str, Any]]]):
        self.screener = screener

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        if endpoint == "available-industries":
            return [{"industry": n} for n in self.screener]
        if endpoint == "ratios-ttm":     # the statement-twin pass: one set per listing
            return _distinct_ratios(params["symbol"])
        rows = self.screener[params["industry"]]
        limit, page = int(params["limit"]), int(params.get("page", 0))
        return rows[page * limit:(page + 1) * limit]

    async def close(self) -> None:  # pragma: no cover — an injected client is not closed
        pass


async def _no_sleep(_delay: float) -> None:  # pragma: no cover — no 429 here
    pass


@pytest.mark.asyncio
async def test_main_keeps_both_bradesco_classes_and_warns_before_upload(tmp_path, caplog):
    """The file the owner uploads holds BBD and BBDO (FMP's industries are not this
    builder's call), and the build log names the pair at WARNING. Mutation: remove the
    `_log_cross_industry_share_classes(...)` call from `main()` → no line."""
    out = tmp_path / "u.json"
    fmp = _FakeFMP({
        "Banks": [_row("BBDO", "Banco Bradesco S.A.", industry="Banks", price=3.0,
                       avg_volume=2e6),
                  _row("ITUB", "Itaú Unibanco Holding S.A.", industry="Banks", cap=7e10)],
        "Banks - Regional": [_row("BBD", "Banco Bradesco S.A.", price=3.0, avg_volume=4e7),
                             _row("BBDC", "Barings BDC, Inc.", cap=1e9)],
    })
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    written = json.loads(out.read_text(encoding="utf-8"))
    assert {e["industry"]: e["tickers"] for e in written["industries"]} == {
        "Banks": ["BBDO", "ITUB"], "Banks - Regional": ["BBD", "BBDC"]}
    (line,) = [r for r in caplog.records if "DIFFERENT industries" in r.getMessage()]
    assert line.levelno == logging.WARNING
    assert 'BBDO [Banks] + BBD [Banks - Regional] "Banco Bradesco S.A."' in line.getMessage()
    assert "BBDC" not in line.getMessage() and "ITUB" not in line.getMessage()
    assert "same_issuer" not in caplog.text                 # nothing was merged


# ══ B4: the search route types a listed "Lending Fund" BDC as a stock ════════════════════

def _item(sym: str, name: str, ex: str = "NYSE", **over: Any) -> Dict[str, Any]:
    """A /stable search-symbol / search-name row (no isFund / isEtf keys)."""
    item = {"symbol": sym, "name": name, "currency": "USD", "exchange": ex,
            "exchangeFullName": ex}
    item.update(over)
    return item


@pytest.mark.parametrize("sym,name", [
    ("MSDL", "Morgan Stanley Direct Lending Fund"),     # captured (FMP, 2026-10-08)
    ("BXSL", "Blackstone Secured Lending Fund"),        # captured (FMP, 2026-10-08)
    ("MSDL", "Morgan Stanley Direct  LENDING   FUND"),
    ("BXSL", "Blackstone Secured Lending Fund"),
])
def test_a_listed_lending_fund_bdc_is_a_stock(sym, name):
    """Mutation: drop the `_is_listed_lending_fund` branch in `_get_asset_type` → "fund"
    (iOS: the ETF screen)."""
    assert stocks_ep._FUND_NAME_RE.search(name), "the row must reach the fund-name rule"
    assert stocks_ep._get_asset_type(_item(sym, name)) == "stock"


@pytest.mark.parametrize("flags", [{"isFund": False}, {"isFund": "false"},
                                   {"isFund": False, "isEtf": False}, {"isFund": None}])
def test_an_explicitly_false_or_absent_flag_keeps_the_exemption(flags):
    assert stocks_ep._get_asset_type(
        _item("MSDL", "Morgan Stanley Direct Lending Fund", **flags)) == "stock"


@pytest.mark.parametrize("flags", [
    {"isFund": True}, {"isFund": "true"}, {"isFund": " TRUE "}, {"isFund": 1},
    {"isFund": "yes"}, {"isFund": ""}, {"isEtf": True}, {"isEtf": "true"},
])
def test_a_true_or_unreadable_flag_keeps_the_row_a_fund(flags):
    """Mutation: read only `isFund is True` (or skip the flags) → the junk/isEtf rows type
    "stock"."""
    assert stocks_ep._get_asset_type(
        _item("MSDL", "Morgan Stanley Direct Lending Fund", **flags)) == "fund"


MUST_STAY_FUND = [
    # (symbol, name, exchange, expected type) — what each typed BEFORE this change.
    ("CCLFX", "Cliffwater Corporate Lending Fund", "NASDAQ", "fund"),   # interval fund
    ("UTF", "Cohen & Steers Infrastructure Fund", "NYSE", "fund"),
    ("EVT", "Eaton Vance Tax-Advantaged Dividend Income Fund", "NYSE", "fund"),
    ("XYZ", "XYZ Lending Funds", "NYSE", "fund"),                       # plural
    ("XYZ", "XYZ Microlending Fund", "NYSE", "fund"),                   # not the whole word
    ("XYZ", "XYZ Lending Risk Premium Fund", "NYSE", "fund"),           # not the phrase
    ("XYZ", "XYZ Lending Fund Income Fund", "NYSE", "fund"),            # another fund word
    ("XYZ", "XYZ Lending Fund Funds Trust", "NYSE", "fund"),
    ("XYZ", "XYZ Lending Fund ETF", "NYSE", "etf"),                     # ETF wins first
    ("XYZ", "XYZ Lending Fund ETN", "NYSE", "etf"),
    ("XYZ", "XYZ Lending, Fund", "NYSE", "fund"),                       # not adjacent
    ("XYZ", "Invesco Bond Fund", "NYSE", "fund"),
    ("XYZ", "Grayscale Funds Trust", "NYSE", "fund"),
]


@pytest.mark.parametrize("sym,name,ex,expected", MUST_STAY_FUND)
def test_every_other_fund_name_is_unchanged(sym, name, ex, expected):
    """Mutations: drop the rest-of-name fund / ETF check → "Lending Fund Income Fund" types
    "stock"; drop the fifth-letter-X gate → CCLFX types "stock"; loosen the phrase to
    `lending.*fund` or `funds?` → "Lending Risk Premium Fund" / "Lending Funds" type "stock"."""
    assert stocks_ep._get_asset_type(_item(sym, name, ex)) == expected


def test_international_and_crypto_rows_never_reach_the_exemption():
    assert stocks_ep._get_asset_type(
        _item("MSDL.TO", "Morgan Stanley Direct Lending Fund", "TSX")) is None
    assert stocks_ep._get_asset_type({"symbol": "BTC", "name": "Bitcoin Lending Fund",
                                      "exchangeShortName": "CRYPTO"}) == "crypto"


@pytest.mark.parametrize("item", [
    {"symbol": None, "name": "Morgan Stanley Direct Lending Fund", "exchange": "NYSE"},
    {"symbol": 12345, "name": "Morgan Stanley Direct Lending Fund", "exchange": "NYSE"},
    {"name": "Morgan Stanley Direct Lending Fund", "exchange": "NYSE"},
])
def test_a_malformed_symbol_never_crashes_the_exemption(item):
    """The route skips (and logs) a row without a string symbol before or around
    `_get_asset_type`; the exemption itself must not be what raises."""
    assert stocks_ep._is_listed_lending_fund(item, item["name"]) is True
    assert stocks_ep._is_listed_lending_fund({**item, "symbol": "cclfx"}, item["name"]) \
        is False                                    # the X gate reads the symbol uppercased


NAME_TABLE = [n for _, n in [
    ("MSDL", "Morgan Stanley Direct Lending Fund"),
    ("BXSL", "Blackstone Secured Lending Fund"),
    ("X", "Cohen & Steers Infrastructure Fund"),
    ("X", "XYZ Lending Funds"),
    ("X", "XYZ Microlending Fund"),
    ("X", "XYZ Lending Risk Premium Fund"),
    ("X", "XYZ Lending Fund Income Fund"),
    ("X", "XYZ Lending Fund ETF"),
    ("X", "XYZ Lending Fund ETN"),
    ("X", "Blackstone Secured Lending Fund, Shares of Beneficial Interest"),
]]


@pytest.mark.parametrize("name", NAME_TABLE)
@pytest.mark.parametrize("sym", ["MSDL", "CCLFX"])
def test_the_builder_and_the_search_agree_on_every_name(name, sym):
    """One owner call, two readers: on a row FMP flags isFund=false, the universe counts a
    name as an operating company exactly when the search types it a stock."""
    builder = bu._is_listed_lending_fund(_row(sym, name), sym, bu._row_name(
        {"companyName": name}))
    endpoint = stocks_ep._is_listed_lending_fund(_item(sym, name, isFund=False), name)
    assert builder == endpoint


def test_the_lending_fund_pattern_equals_the_builders():
    assert (stocks_ep._LENDING_FUND_RE.pattern, stocks_ep._LENDING_FUND_RE.flags) == \
        (bu._LENDING_FUND_RE.pattern, bu._LENDING_FUND_RE.flags)


class _RecordingFMP:
    """The REAL `FMPClient.search_stocks` over canned endpoint answers."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: List[str] = []

    async def _make_request(self, endpoint, params=None):
        self.calls.append(endpoint)
        return self.responses[endpoint]

    search_stocks = FMPClient.search_stocks
    _has_symbol_prefix_match = staticmethod(FMPClient._has_symbol_prefix_match)


@pytest.mark.asyncio
@pytest.mark.parametrize("q,sym,name", [
    ("MSDL", "MSDL", "Morgan Stanley Direct Lending Fund"),
    ("BXSL", "BXSL", "Blackstone Secured Lending Fund"),
])
async def test_the_search_route_returns_them_as_stocks(monkeypatch, q, sym, name):
    """Through the real route: the ticker search answers a "stock" row, so iOS opens the
    company screen (`AssetDetailRouter` folds only "fund" into the ETF screen)."""
    client = _RecordingFMP({"search-symbol": [_item(sym, name)], "search-name": []})
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: client)
    monkeypatch.setattr(search, "get_active_listings", lambda: {sym: name})
    out = await stocks_ep.search_stocks(q=q, limit=10)
    assert [(r.symbol, r.type) for r in out] == [(sym, "stock")]
    assert client.calls == ["search-symbol"]          # a kept ticker hit: no name search


@pytest.mark.asyncio
async def test_a_name_search_types_only_the_bdcs_as_stocks(monkeypatch):
    client = _RecordingFMP({
        "search-symbol": [],
        "search-name": [_item("MSDL", "Morgan Stanley Direct Lending Fund"),
                        _item("BXSL", "Blackstone Secured Lending Fund"),
                        _item("XYZ", "XYZ Lending Fund Income Fund"),
                        _item("UTF", "Cohen & Steers Infrastructure Fund")],
    })
    monkeypatch.setattr(stocks_ep, "get_fmp_client", lambda: client)
    monkeypatch.setattr(search, "get_active_listings", lambda: None)
    out = await stocks_ep.search_stocks(q="lending fund", limit=10)
    assert {r.symbol: r.type for r in out} == {
        "MSDL": "stock", "BXSL": "stock", "XYZ": "fund", "UTF": "fund"}
