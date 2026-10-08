"""Owner calls of 2026-10-08 on `scripts/build_benchmark_universe.py`, made from a read-only
FMP probe the same day.

  D1  BDCs named "Lending Fund" vote. FMP profiles: MSDL "Morgan Stanley Direct Lending
      Fund" (Financial - Conglomerates, isFund false, NYSE, $1.1B) and BXSL "Blackstone
      Secured Lending Fund" (Asset Management, isFund false, $5.6B) are listed BDCs. Their
      peers (ARCC, OBDC, FSK, MAIN) already vote, but the fund-name rule dropped these two.
      The exemption covers the whole-word phrase only. Closed-end funds ("Cohen & Steers
      Infrastructure Fund", "Eaton Vance Tax-Advantaged Dividend Income Fund") still drop.
  D2  Brazilian (and any) preferred-class ADRs are equity and vote once per issuer. FMP's
      companyName never says "Preferred" for them (ITUB "Itaú Unibanco Holding S.A."; BBD
      and BBDO both "Banco Bradesco S.A."; PBR and PBR-A both "Petróleo Brasileiro S.A. -
      Petrobras", PBR-A isAdr false). BBD + BBDO and PBR + PBR-A each collapse to one vote
      when FMP files both classes under ONE industry (the vote is per industry; a pair in
      two industries is kept twice and named at WARNING — round 2, B2, in
      test_owner_decisions_builder_round2_2026_10_08.py),
      and the round-2 guard still keeps distinct companies with one normalised name apart:
      First Bancorp vs First BanCorp., and tracking stocks with unrelated symbols.
      A depositary description ("each representing one Preferred Share") ENDING the name no
      longer drops the row either; its fixed-income twins still do (round 2, B1: words
      after the description, or a fixed-income word anywhere, refuse the cut).

Names marked "captured" are the probe's FMP strings. "pattern" rows use the issuer's
legal name, or a depositary's description form; FMP's exact string for them was not
recorded. Each test docstring gives the mutation that turns it red. Hermetic: pure
functions, plus `main()` against a fake FMP client.
"""
from __future__ import annotations

import zlib
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

import pytest

import scripts.build_benchmark_universe as bu
from app.services import stock_search_service as search

FLOOR = 500_000_000


def _row(sym: str, name: str, ex: str = "NYSE", cap: Any = 5e9, *,
         industry: str = "Asset Management", sector: str = "Financial Services",
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


def _syms(rows: List[Dict[str, Any]]) -> List[str]:
    return sorted(r["symbol"] for r in rows)


def _kept(rows: List[Dict[str, Any]]) -> List[str]:
    return _syms(bu._filter_rows(rows, FLOOR)[0])


# ══ D1: a listed BDC named "Lending Fund" is an operating company ══════════════════════

LENDING_FUND_BDCS = [
    # captured (FMP profile, 2026-10-08)
    ("MSDL", "Morgan Stanley Direct Lending Fund", 1.1e9, "Financial - Conglomerates"),
    ("BXSL", "Blackstone Secured Lending Fund", 5.6e9, "Asset Management"),
]


@pytest.mark.parametrize("sym,name,cap,industry", LENDING_FUND_BDCS)
def test_a_listed_bdc_named_lending_fund_is_kept(sym, name, cap, industry):
    """Mutation: drop the `_is_listed_lending_fund` clause from `_listing_reason` → both are
    `fund_name` again (the 2026-10-07 behaviour)."""
    assert bu._FUND_NAME_RE.search(name), "the row must reach the fund-name rule"
    row = _row(sym, name, cap=cap, industry=industry)
    assert bu._drop_reason(row, FLOOR) is None
    assert _kept([row]) == [sym]


@pytest.mark.parametrize("name", [
    "BLACKSTONE SECURED LENDING FUND",
    "blackstone secured lending fund",
    "Blackstone Secured   Lending\tFund",
    "Blackstone Secured Lending Fund",
    "Morgan Stanley Direct Lending Fund Common Shares of Beneficial Interest",
])
def test_the_exemption_survives_case_spacing_and_a_share_description(name):
    """FMP's casing and spacing drift. Mutation: make `_LENDING_FUND_RE` case-sensitive →
    red. (Spacing holds because `_row_name` folds every whitespace run, NBSP included,
    before any regex reads the name — a literal single space in the pattern is equivalent.)"""
    assert bu._drop_reason(_row("BXSL", name), FLOOR) is None


MUST_STAY_FUND_NAME = [
    # The task's twins: closed-end funds named "… Fund" (public names) — no exemption.
    ("UTF", "Cohen & Steers Infrastructure Fund", "NYSE"),
    ("EVT", "Eaton Vance Tax-Advantaged Dividend Income Fund", "NYSE"),
    # Interval fund with the very phrase, on NASDAQ's fifth-letter-X fund symbol
    # (mutation: drop the `_fund_like_symbol` gate → kept).
    ("CCLFX", "Cliffwater Corporate Lending Fund", "NASDAQ"),
    # The phrase is not adjacent (mutation: `lending.*fund` → kept).
    ("LNDR", "Alternative Lending Risk Premium Fund", "NYSE"),
    # Plural "Funds" names a fund family (mutation: `funds?` in `_LENDING_FUND_RE` → kept).
    ("LNDF", "XYZ Direct Lending Funds", "NYSE"),
    # A second fund / ETF word once the phrase is cut out (mutation: drop the fund check
    # on `rest` → LNDI kept; drop the ETF check on `rest` → LNDE kept).
    ("LNDI", "XYZ Lending Fund Income Fund", "NYSE"),
    ("LNDE", "XYZ Direct Lending Fund ETF", "NYSE"),
    # "lending" must be a whole word (mutation: drop the leading `\b` → kept).
    ("MCLF", "Global Microlending Fund", "NYSE"),
]


@pytest.mark.parametrize("sym,name,ex", MUST_STAY_FUND_NAME)
def test_every_other_fund_name_still_drops(sym, name, ex):
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) == "fund_name"
    assert not bu._is_listed_lending_fund(_row(sym, name, ex), sym, name)


@pytest.mark.parametrize("flag,expected", [
    (False, None), ("false", None), (" FALSE ", None),
    (True, "fund"),                       # FMP says fund: the server filter was ignored
    ("true", "fund"),
    (None, "fund_name"), (0, "fund_name"), ("", "fund_name"), ("no", "fund_name"),
    ([], "fund_name"),
])
def test_the_exemption_needs_fmp_to_say_isfund_false(flag, expected):
    """A missing or unreadable `isFund` proves nothing, so the name rule decides.
    Mutation: `not _flag_true(row.get("isFund"))` in place of `_flag_false(...)` → the
    None / 0 / "" / "no" / [] rows are kept."""
    row = _row("MSDL", "Morgan Stanley Direct Lending Fund", isFund=flag)
    assert bu._drop_reason(row, FLOOR) == expected


def test_a_missing_isfund_key_falls_back_to_the_name_rule():
    row = _row("MSDL", "Morgan Stanley Direct Lending Fund")
    del row["isFund"]
    assert bu._drop_reason(row, FLOOR) == "fund_name"


def test_the_exemption_never_keeps_a_lending_funds_note():
    """The exemption only skips the fund-name rule; the debt rules still read the name.
    Mutation: return None from `_listing_reason` right after the exemption → kept."""
    row = _row("BXSLZ", "Blackstone Secured Lending Fund 5.25% Notes due 2030")
    assert bu._drop_reason(row, FLOOR) == "not_common_share"


def test_bdcs_vote_beside_their_peers_and_their_sponsors_across_the_market():
    """The whole market: the two BDCs vote beside ARCC / OBDC / FSK / MAIN, closed-end funds
    in the same industry still drop, and neither BDC is taken for a twin of its sponsor's
    ticker (BX "Blackstone Inc.", MS "Morgan Stanley": the issuer names differ).
    Mutation: drop the exemption → MSDL and BXSL leave, and "Financial - Conglomerates"
    loses its only row."""
    market = {
        "Asset Management": [
            _row("ARCC", "Ares Capital Corporation", "NASDAQ", cap=1.5e10),
            _row("OBDC", "Blue Owl Capital Corporation", cap=5.5e9),
            _row("FSK", "FS KKR Capital Corp.", cap=5e9),
            _row("MAIN", "Main Street Capital Corporation", cap=5e9),
            _row("BXSL", "Blackstone Secured Lending Fund", cap=5.6e9),
            _row("BX", "Blackstone Inc.", cap=1.7e11),
            _row("UTF", "Cohen & Steers Infrastructure Fund", cap=2.4e9),
            _row("EVT", "Eaton Vance Tax-Advantaged Dividend Income Fund", cap=1.7e9),
        ],
        "Financial - Conglomerates": [
            _row("MSDL", "Morgan Stanley Direct Lending Fund", cap=1.1e9,
                 industry="Financial - Conglomerates"),
        ],
        "Financial - Capital Markets": [
            _row("MS", "Morgan Stanley", cap=2.2e11, industry="Financial - Capital Markets"),
        ],
    }
    result = bu._filter_market(market, FLOOR)
    assert {k: _syms(v) for k, v in result.kept.items()} == {
        "Asset Management": ["ARCC", "BX", "BXSL", "FSK", "MAIN", "OBDC"],
        "Financial - Conglomerates": ["MSDL"],
        "Financial - Capital Markets": ["MS"],
    }
    assert result.dropped == {"fund_name": 2}
    assert result.cross_industry_twins == []


def test_the_kept_bdcs_are_named_at_info_and_never_as_suspects(caplog):
    """Each build names what the exemption let in. Mutation: delete the
    `_log_owner_call_keeps` lender branch → no line."""
    by_industry = {
        "Asset Management": [_row("BXSL", "Blackstone Secured Lending Fund"),
                             _row("ARCC", "Ares Capital Corporation", "NASDAQ")],
        "Financial - Conglomerates": [_row("MSDL", "Morgan Stanley Direct Lending Fund")],
    }
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        bu._log_owner_call_keeps(by_industry)
        bu._log_suspect_rows(by_industry)
    (line,) = [r for r in caplog.records if "Lending Fund" in r.getMessage()]
    assert line.levelno == logging.INFO
    msg = line.getMessage()
    assert "2 listed BDC(s)" in msg
    assert 'BXSL "Blackstone Secured Lending Fund" [Asset Management]' in msg
    assert 'MSDL "Morgan Stanley Direct Lending Fund" [Financial - Conglomerates]' in msg
    assert "ARCC" not in msg
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_nothing_to_name_logs_nothing(caplog):
    with caplog.at_level(logging.DEBUG, logger=bu.__name__):
        bu._log_owner_call_keeps({"Banks - Regional": [_row("FITB", "Fifth Third Bancorp")]})
        bu._log_owner_call_keeps({})
    assert caplog.records == []


# ══ D2: preferred-class ADRs are equity, one vote per issuer ══════════════════════════

BRAZIL_ADRS = [
    # captured (FMP profile companyName, 2026-10-08)
    ("ITUB", "Itaú Unibanco Holding S.A.", "Banks"),
    ("BBD", "Banco Bradesco S.A.", "Banks - Regional"),
    ("BBDO", "Banco Bradesco S.A.", "Banks - Regional"),
    ("PBR", "Petróleo Brasileiro S.A. - Petrobras", "Oil & Gas Integrated"),
    ("PBR-A", "Petróleo Brasileiro S.A. - Petrobras", "Oil & Gas Integrated"),
    # pattern (issuer's legal name): the probe found single listings for these
    ("GGB", "Gerdau S.A.", "Steel"),
    ("CIG", "Companhia Energética de Minas Gerais", "Regulated Electric"),
    ("ELP", "Companhia Paranaense de Energia - COPEL", "Regulated Electric"),
    ("SBS", "Companhia de Saneamento Básico do Estado de São Paulo - SABESP",
     "Regulated Water"),
    ("BSBR", "Banco Santander (Brasil) S.A.", "Banks - Regional"),
    ("ABEV", "Ambev S.A.", "Beverages - Alcoholic"),
]


@pytest.mark.parametrize("sym,name,industry", BRAZIL_ADRS)
def test_each_brazilian_adr_is_kept_on_its_own(sym, name, industry):
    """A regression pin on the probe's real strings: none names a class, so no listing rule
    may read them as a non-common. Mutation: read `_ADR_NON_COMMON_RE` / the dash-class
    symbol (PBR-A) as a preferred, or treat " - " in a name as a security description for
    the debt rules → red."""
    row = _row(sym, name, industry=industry, isAdr=(sym != "PBR-A"))
    assert bu._drop_reason(row, FLOOR) is None
    assert _kept([row]) == [sym]


@pytest.mark.parametrize("common,pref,liquid", [
    ("BBDO", "BBD", "BBD"), ("BBDO", "BBD", "BBDO"),
    ("PBR", "PBR-A", "PBR"), ("PBR", "PBR-A", "PBR-A"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_each_issuer_votes_once_with_its_more_liquid_class(common, pref, liquid, reverse):
    """Both directions, in both input orders: the more liquid class is the vote whichever
    it is. Mutation: drop the one-letter-added rule from `_share_class_siblings` → Bradesco
    votes twice. (PBR/PBR-A is held by two rules — one dash root, and equal 3-letter roots —
    so dropping either alone leaves it merged; dropping both turns it red.)"""
    name = ("Banco Bradesco S.A." if common == "BBDO"
            else "Petróleo Brasileiro S.A. - Petrobras")
    rows = [_row(s, name, industry="Banks - Regional", price=3.0,
                 avg_volume=4e7 if s == liquid else 2e6) for s in (common, pref)]
    if reverse:
        rows.reverse()
    kept, dropped, examples = bu._filter_rows(rows, FLOOR)
    assert [r["symbol"] for r in kept] == [liquid]
    assert dropped == {"same_issuer": 1}
    other = pref if liquid == common else common
    assert examples["same_issuer"] == [f"{other} (kept {liquid})"]


def test_the_brazilian_pairs_read_as_one_issuer_and_as_classes():
    """Pins why the merge happens: one issuer key AND sibling symbols, for each pair."""
    assert search._issuer_key("Banco Bradesco S.A.") == "banco bradesco s a"
    assert (search._issuer_key("Petróleo Brasileiro S.A. - Petrobras")
            == "petróleo brasileiro s a")
    for a, b in (("BBD", "BBDO"), ("PBR", "PBR-A")):
        assert bu._share_class_siblings(a, b) and bu._share_class_siblings(b, a)
    # BBDO is the search's allow-listed common ADR, so it is never a root twin of BBD —
    # it is deduped as a second CLASS (`same_issuer`), not mislabelled a note.
    assert "BBDO" in search._KNOWN_SHARE_CLASS_SYMBOLS


def test_a_distinct_company_with_a_sibling_symbol_still_votes():
    """BBDC (Barings BDC) reads as a class of BBD by symbol alone; only the issuer name
    keeps it apart. Mutation: group by `_share_class_siblings` without the issuer key →
    BBDC is folded into Bradesco."""
    assert bu._share_class_siblings("BBD", "BBDC")
    rows = [_row("BBD", "Banco Bradesco S.A.", avg_volume=4e7),
            _row("BBDO", "Banco Bradesco S.A.", avg_volume=2e6),
            _row("BBDC", "Barings BDC, Inc.", cap=1e9)]
    kept, dropped, _ = bu._filter_rows(rows, FLOOR)
    assert _syms(kept) == ["BBD", "BBDC"]
    assert dropped == {"same_issuer": 1}


@pytest.mark.parametrize("rows,expected", [
    # Round 2: two banks, one normalised name, symbols that are not classes of one listing.
    ([_row("FBNC", "First Bancorp", "NASDAQ"), _row("FBP", "First BanCorp.")],
     ["FBNC", "FBP"]),
    # Tracking stocks under one issuer name with unrelated symbols.
    ([_row("FWONK", "Liberty Media Corporation", "NASDAQ"),
      _row("LSXMK", "Liberty Media Corporation", "NASDAQ")], ["FWONK", "LSXMK"]),
])
def test_distinct_companies_with_one_name_are_never_merged(rows, expected):
    """Mutation: make `_share_class_siblings` always True → FBNC/FBP and FWONK/LSXMK each
    collapse to one vote."""
    assert _kept(rows) == expected


def test_the_vote_does_not_depend_on_fmp_row_order():
    rows = [_row(s, n, industry=i, avg_volume=v) for s, n, i, v in [
        ("BBD", "Banco Bradesco S.A.", "Banks - Regional", 4e7),
        ("BBDO", "Banco Bradesco S.A.", "Banks - Regional", 2e6),
        ("BBDC", "Barings BDC, Inc.", "Banks - Regional", 1e6),
        ("PBR", "Petróleo Brasileiro S.A. - Petrobras", "Banks - Regional", 3e7),
        ("PBR-A", "Petróleo Brasileiro S.A. - Petrobras", "Banks - Regional", 1e7),
        ("ITUB", "Itaú Unibanco Holding S.A.", "Banks - Regional", 2e7),
    ]]
    expected = _kept(rows)
    assert expected == ["BBD", "BBDC", "ITUB", "PBR"]
    rng = random.Random(1008)
    for _ in range(25):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        assert _kept(shuffled) == expected


# ── a depositary's "each representing one Preferred Share" is the issuer's class ───────

PREFERRED_CLASS_ADRS = [
    # Moved here from round 6's must-drop list (it was its last row).
    ("BXYZ", "Banco XYZ S.A. American Depositary Shares, each representing one Preferred "
             "Share"),
    ("ITUB", "Itaú Unibanco Holding S.A. American Depositary Shares, each representing 1 "
             "Preferred Share"),
    ("GGB", "Gerdau S.A. American Depositary Shares, each representing one preferred share"),
    ("EBR-B", "Centrais Elétricas Brasileiras S.A. - Eletrobras American Depositary Shares, "
              "each representing one Class B Preferred Share"),
    ("CIB", "Bancolombia S.A. American Depositary Shares, each representing four Preferred "
            "Shares"),
    ("XYZG", "XYZ S.A. Global Depository Shares, each representing a Preferred Share"),
    ("XYZR", "XYZ S.A. American Depositary Receipts each representing 2 PREFERRED SHARES"),
]


@pytest.mark.parametrize("sym,name", PREFERRED_CLASS_ADRS)
def test_an_adr_over_a_preferred_class_is_kept(sym, name):
    """Every row here reached the old drop: without the class cut, `_ADR_NON_COMMON_RE`
    matches. Mutation: remove the `_without_preferred_class` call in `_listing_reason`
    → each is `not_common_share` again."""
    cut, is_adr = bu._without_adr_phrase(bu._row_name({"companyName": name}))
    assert is_adr and bu._ADR_NON_COMMON_RE.search(cut), "must have reached the old drop"
    rest, was_cut = bu._without_preferred_class(cut)
    assert was_cut and not bu._ADR_NON_COMMON_RE.search(rest)
    assert bu._drop_reason(_row(sym, name), FLOOR) is None


PREFERRED_FIXED_INCOME = [
    # A qualifier between the count and "preferred share" — the class regex cannot match.
    ("XYZN", "XYZ Bank plc American Depositary Shares, each representing one Non-Cumulative "
             "Preferred Share"),
    ("XYZC", "XYZ Bank Ltd. American Depositary Shares, each representing one 6.50% "
             "Preferred Share"),
    ("XYZI", "XYZ Corp American Depositary Shares, each representing a 1/40th interest in a "
             "share of Preferred Stock"),
    # A series anywhere cancels the cut (mutation: drop the `_SERIES_WORD_RE` guard → kept).
    ("XYZS", "XYZ Bank plc American Depositary Shares, each representing one Preferred "
             "Share, Series A"),
    ("XYZT", "XYZ Bank plc American Depositary Shares Series 2, each representing one "
             "Preferred Share"),
    # The class is cut, but a fixed-income marker is left over.
    ("XYZP", "XYZ Ltd. American Depositary Shares, each representing one Preferred Share "
             "Pfd"),
    ("XYZK", "XYZ Ltd. American Depositary Shares, each representing one Preferred Share "
             "Non-Cumulative"),
    # "Preference share" is the UK fixed-income form: not the class regex's word.
    ("XYZU", "XYZ Bank plc American Depositary Shares, each representing one Preference "
             "Share"),
    # No ADR phrase at all: the cut is never applied (mutation: cut without `is_adr` → kept).
    ("XYZQ", "XYZ Corp., each representing one Preferred Share"),
    # A preferred symbol is dropped on its symbol before any name is read.
    ("XYZ-PA", "XYZ S.A. American Depositary Shares, each representing one Preferred Share"),
    ("XYZBP", "XYZ S.A. American Depositary Shares, each representing one Preferred Share"),
]


@pytest.mark.parametrize("sym,name", PREFERRED_FIXED_INCOME)
def test_a_fixed_income_preferred_adr_is_still_dropped(sym, name):
    ex = "NASDAQ" if sym == "XYZBP" else "NYSE"
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) == "not_common_share"


def test_the_class_regex_reads_only_a_count_before_preferred():
    rx = bu._ADR_PREFERRED_CLASS_RE
    for text in ("representing one Preferred Share", "representing 10 preferred shares",
                 "representing a Class B Preferred Share", "representing an preferred share",
                 "REPRESENTING TWO PREFERRED SHARES"):
        assert rx.search(text), text
    for text in ("representing one Non-Cumulative Preferred Share",
                 "representing 1/40th of a Preferred Share",
                 "representing one 5% Preferred Share",
                 "representing one Series A Preferred Share",
                 "representing one Preference Share",
                 "representing one Preferred Stock",
                 "misrepresenting one Preferred Share",
                 "representing 12345 Preferred Shares"):
        assert not rx.search(text), text


def test_the_class_cut_is_fast_on_pathological_names():
    names = ["representing " * 30_000, "representing one " * 20_000 + "Preferred Share",
             "American Depositary Shares " * 10_000, "1 " * 100_000 + "preferred shares"]
    started = time.perf_counter()
    for name in names:
        bu._drop_reason(_row("XYZ", name), FLOOR)
        bu._without_preferred_class(name)
    assert time.perf_counter() - started < 0.5


def test_a_described_preferred_class_and_its_common_vote_once():
    """Eletrobras: the common (EBR) under its plain name and the class B preferred ADR
    (EBR-B) under a depositary description cut at " - " to one issuer key → one vote."""
    rows = [_row("EBR", "Centrais Elétricas Brasileiras S.A. - Eletrobras", avg_volume=3e6),
            _row("EBR-B", PREFERRED_CLASS_ADRS[3][1], avg_volume=5e5)]
    kept, dropped, examples = bu._filter_rows(rows, FLOOR)
    assert _syms(kept) == ["EBR"]
    assert dropped == {"same_issuer": 1}
    assert examples["same_issuer"] == ["EBR-B (kept EBR)"]


def test_a_kept_preferred_class_adr_is_named_at_info(caplog):
    """Mutation: delete the preferred-class branch of `_log_owner_call_keeps` → no line.
    FMP's real names (no description) produce no line."""
    by_industry = {
        "Banks - Regional": [_row("BXYZ", PREFERRED_CLASS_ADRS[0][1]),
                             _row("BBD", "Banco Bradesco S.A.")],
        "Steel": [_row("GGB", "Gerdau S.A.")],
    }
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        bu._log_owner_call_keeps(by_industry)
    (line,) = [r for r in caplog.records if "preferred share class" in r.getMessage()]
    assert line.levelno == logging.INFO
    assert "1 ADR(s)" in line.getMessage() and "BXYZ " in line.getMessage()
    assert "BBD " not in line.getMessage() and "GGB " not in line.getMessage()


# ══ end to end through main() ═══════════════════════════════════════════════════════════



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
async def test_main_writes_both_owner_calls(tmp_path, caplog):
    """The file the owner uploads: MSDL / BXSL beside their peers, each Brazilian issuer
    once, the closed-end fund out — and the log says why for every one."""
    out = tmp_path / "u.json"
    fmp = _FakeFMP({
        "Asset Management": [
            _row("ARCC", "Ares Capital Corporation", "NASDAQ"),
            _row("BXSL", "Blackstone Secured Lending Fund", cap=5.6e9),
            _row("UTF", "Cohen & Steers Infrastructure Fund", cap=2.4e9),
        ],
        "Financial - Conglomerates": [
            _row("MSDL", "Morgan Stanley Direct Lending Fund", cap=1.1e9),
        ],
        "Banks - Regional": [
            _row("BBD", "Banco Bradesco S.A.", price=3.0, avg_volume=4e7),
            _row("BBDO", "Banco Bradesco S.A.", price=3.0, avg_volume=2e6),
            _row("BBDC", "Barings BDC, Inc.", cap=1e9),
        ],
        "Banks": [_row("ITUB", "Itaú Unibanco Holding S.A.", cap=7e10)],
        "Oil & Gas Integrated": [
            _row("PBR", "Petróleo Brasileiro S.A. - Petrobras", sector="Energy",
                 price=13.0, avg_volume=2e7),
            _row("PBR-A", "Petróleo Brasileiro S.A. - Petrobras", sector="Energy",
                 price=12.0, avg_volume=1e7, isAdr=False),
        ],
    })
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    written = json.loads(out.read_text(encoding="utf-8"))
    assert {e["industry"]: e["tickers"] for e in written["industries"]} == {
        "Asset Management": ["ARCC", "BXSL"],
        "Financial - Conglomerates": ["MSDL"],
        "Banks - Regional": ["BBD", "BBDC"],
        "Banks": ["ITUB"],
        "Oil & Gas Integrated": ["PBR"],
    }
    assert written["ticker_count"] == 7
    text = caplog.text
    assert "BBDO (kept BBD)" in text and "PBR-A (kept PBR)" in text
    assert 'UTF "Cohen & Steers Infrastructure Fund"' in text
    assert "2 listed BDC(s)" in text and "MSDL " in text and "BXSL " in text
    assert "preferred share class" not in text          # FMP's real names carry none
    assert "not honoured" not in text
