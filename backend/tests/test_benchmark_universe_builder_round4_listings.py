"""Round-3 review fixes (2026-10-08) to `scripts/build_benchmark_universe.py` — listings.

  UB3-1  Notes filed under a SUBSIDIARY's or a financing vehicle's name, or under another
         FMP industry than their issuer, were kept and never named: the twin rules read
         only the industry's own rows, the shared grammar has no "bonds" term, and no
         WARNING list looked at liquidity. The June file held ELC $37.75B, EAI $0.94B,
         PPLC $35.98B, DTW $3.75B (Regulated Electric), CGABL (Financial - Credit
         Services; CG sits in Asset Management) and HONAV (Aerospace & Defense; HON sits
         in Conglomerates). Fixed three ways, each pinned below:
           (a) the twin rules read a MARKET-WIDE directory, the issuer-name guard intact;
           (b) "bonds" / "first mortgage" / "collateral trust" are builder-local debt words;
           (c) a kept row trading under 0.02% of its reported cap a day is named at WARNING —
               the only rule that sees FMP's ~31-character truncated names ("Entergy
               Louisiana, LLC Collater").
  UB3-2  Closed-end and physical-metal trusts without a fund word were never named.
  UB3-4  "American Depository Shares" and "Global Depositary / Depository Shares" dropped a
         foreign issuer's common as `not_common_share`.

Names marked "review" are the review's probe strings (FMP's real screener companyName for
these rows is unknown — the review rates it PLAUSIBLE); "pattern" rows follow FMP's form for
the family. Hermetic: pure functions, plus `main()` against a fake FMP client.
"""
from __future__ import annotations

import json
import logging
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import scripts.build_benchmark_universe as bu
from app.services import stock_search_service as search

FLOOR = 500_000_000


def _row(sym: str, name: Any = None, ex: str = "NYSE", cap: Any = 2e9, *,
         price: Any = 50.0, avg_volume: Any = 1_000_000, **over: Any) -> Dict[str, Any]:
    """A /stable company-screener row (FMP's field names). The default trades 2.5% of its
    cap a day — a liquid common."""
    row = {
        "symbol": sym, "companyName": f"{sym} Corp" if name is None else name,
        "marketCap": cap, "sector": "Utilities", "industry": "Regulated Electric",
        "price": price, "volume": avg_volume, "avgVolume": avg_volume,
        "exchange": ex, "exchangeShortName": ex, "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _syms(rows: List[Dict[str, Any]]) -> List[str]:
    return sorted(r["symbol"] for r in rows)


def _warnings(caplog, marker: str) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and marker in r.getMessage()]


# ── UB3-4: an ADR / GDS is common equity in every spelling ─────────────────────────────

ADR_COMMONS = [
    ("XYZH", "XYZ Holdings Ltd. American Depository Shares", "NYSE"),       # review
    ("ABCD", "ABCD Ltd. Global Depositary Shares", "NASDAQ"),               # review
    ("ARM", "Arm Holdings plc American Depositary Shares", "NASDAQ"),       # captured
    ("GDSX", "Some Holdings Limited Global Depository Shares", "NASDAQ"),
    ("SONY", "Sony Group Corporation American Depository Receipts", "NYSE"),
    ("ADRS", "Foo Ltd. American Depositary Share", "NYSE"),                  # singular
    ("SHS", "Foo Ltd. American Depository Shs", "NYSE"),
    ("LWR", "foo holdings ltd. AMERICAN   depository\tshares", "NYSE"),      # case, spacing
    # No issuer marker: the ungated debt rule is the one that used to drop it.
    ("NOMK", "Taiwan Semiconductor Manufacturing Global Depository Shares", "NYSE"),
]


@pytest.mark.parametrize("sym,name,ex", ADR_COMMONS)
def test_an_adr_or_gds_is_kept_in_every_spelling(sym, name, ex):
    """Mutation: drop the `_without_adr_phrase` cut and every row but ARM (and the receipts
    row) is `not_common_share` again — the shared grammar's depository / global branches."""
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) is None
    assert _syms(bu._filter_rows([_row(sym, name, ex)], FLOOR)[0]) == [sym]


def test_the_adr_rows_really_hit_the_shared_grammar():
    """The test above is not vacuous: without the cut, the shared rule matches these names."""
    hits = [name for _, name, _ in ADR_COMMONS if search._DEBT_PREF_NAME_RE.search(name)]
    assert len(hits) >= 5, hits


ADR_NON_COMMON = [
    # A preferred marker next to the ADR phrase: `_ADR_NON_COMMON_RE` alone. A bare
    # "Series B" is NOT one (round 5, B4-1: a Mexican / Chilean ordinary class) — its
    # must-keep rows are in test_benchmark_universe_builder_round6_adr_series.py.
    ("XYZS", "XYZ Ltd. American Depositary Shares Series B Pref", "NYSE"),
    ("XYZP", "XYZ Ltd. Global Depository Shares Pref", "NYSE"),
    ("XYZQ", "XYZ Ltd. American Depository Shares Preferred", "NYSE"),
    # A coupon / notes / pfd left over: the shared rule on the cut name.
    ("XYZC", "XYZ Bank Ltd. 6.5% American Depositary Shares", "NYSE"),
    ("XYZN", "XYZ Ltd. Global Depositary Shares Notes", "NYSE"),
    ("XYZF", "XYZ Ltd. American Depository Shs Repr 1/4th Pfd", "NYSE"),
    # Plain depositary shares are a PREFERRED's wrapper — still dropped (RILYL / GOOGN).
    ("XYZD", "XYZ Corp. Depositary Shares", "NYSE"),
    ("XYZE", "XYZ Corp. Depository Shares", "NYSE"),
]


@pytest.mark.parametrize("sym,name,ex", ADR_NON_COMMON)
def test_an_adr_phrase_never_hides_a_preferred_or_note(sym, name, ex):
    assert bu._drop_reason(_row(sym, name, ex), FLOOR) == "not_common_share"


@pytest.mark.parametrize("name", ["XYZ Ltd. American Depositary Shares Series B Pref",
                                  "XYZ Ltd. Global Depository Shares Pref"])
def test_the_pref_markers_decide_on_their_own(name):
    """Row weight: only `_ADR_NON_COMMON_RE` drops these (mutation: remove it → kept)."""
    cut, is_adr = bu._without_adr_phrase(name)
    assert is_adr
    assert not search._DEBT_PREF_NAME_RE.search(cut)
    assert bu._ADR_NON_COMMON_RE.search(cut)


@pytest.mark.parametrize("sym,name", [
    ("FWONK", "Liberty Media Corporation Series C Liberty Formula One Common Stock"),
    ("PFBC", "Preferred Bank"),
])
def test_series_or_preferred_without_an_adr_phrase_drops_nothing(sym, name):
    """The marker is read only beside an ADR / GDS phrase."""
    assert bu._drop_reason(_row(sym, name, "NASDAQ"), FLOOR) is None


# ── UB3-1 (b): the builder's own debt words ────────────────────────────────────────────

BUILDER_DEBT_ROWS = [
    ("EAI", "Entergy Arkansas, LLC First Mortgage Bonds"),                       # review
    ("ELC", "Entergy Louisiana, LLC Collateral Trust Mortgage Bonds"),           # pattern
    ("EAIX", "Entergy Arkansas, LLC First Mortgage"),                            # pattern
    ("ELCT", "Entergy Louisiana, LLC Collateral Trust"),                         # pattern
    ("DTWB", "DTE Electric Company 2017 Series E Bonds"),                         # pattern
]


@pytest.mark.parametrize("sym,name", BUILDER_DEBT_ROWS)
def test_a_subsidiarys_mortgage_bonds_are_not_a_common(sym, name):
    """Mutation: remove `_BUILDER_DEBT_NAME_RE` and every row here is kept — none of them
    carries a word the shared grammar knows (asserted, so the row weighs)."""
    assert not search._DEBT_PREF_NAME_RE.search(name)
    assert bu._BUILDER_DEBT_NAME_RE.search(name)
    assert bu._drop_reason(_row(sym, name), FLOOR) == "not_common_share"


def test_debentures_are_already_caught_by_the_shared_grammar():
    name = "PPL Capital Funding, Inc. Junior Subordinated Debentures"
    assert search._DEBT_PREF_NAME_RE.search(name)
    assert bu._drop_reason(_row("PPLX", name), FLOOR) == "not_common_share"


@pytest.mark.parametrize("sym,name", [
    ("BBN", "BlackRock Taxable Municipal Bond Trust"),     # singular: a CEF, named not dropped
    ("FMBH", "First Mid Bancshares, Inc."),
    ("AGM", "Federal Agricultural Mortgage Corporation"),
    ("FR", "First Industrial Realty Trust, Inc."),
    ("FAF", "First American Financial Corporation"),
    ("NTRS", "Northern Trust Corporation"),
    ("ABR", "Arbor Realty Trust, Inc."),
])
def test_names_that_only_share_a_debt_word_are_kept(sym, name):
    assert bu._drop_reason(_row(sym, name, "NASDAQ"), FLOOR) is None


# ── UB3-1 (a): the twin rules read the whole market ────────────────────────────────────


def _market(**industries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    return {k.replace("_", " "): v for k, v in industries.items()}


CROSS_INDUSTRY_CASES = [
    # (industries, the twin, its base, the base's industry)
    ({"Asset Management": [_row("CG", "The Carlyle Group Inc.", "NASDAQ", cap=1.8e10)],
      "Financial - Credit Services": [_row("CGABL", "The Carlyle Group Inc.", "NASDAQ",
                                           cap=5.85e9),
                                      _row("OBDC", "Blue Owl Capital Corporation")]},
     "CGABL", "CG", "Asset Management"),
    ({"Conglomerates": [_row("HON", "Honeywell International Inc.", "NASDAQ", cap=1.3e11)],
      "Aerospace & Defense": [_row("HONAV", "Honeywell International Inc.", "NASDAQ",
                                   cap=7.764e10),
                              _row("RTX", "RTX Corporation")]},
     "HONAV", "HON", "Conglomerates"),
    ({"Utilities - Regulated Electric": [_row("SO", "The Southern Company")],
      "Utilities - Diversified": [_row("SOJD", "Southern Company (The) Series 2")]},
     "SOJD", "SO", "Utilities - Regulated Electric"),
]


@pytest.mark.parametrize("industries,twin,base,base_industry", CROSS_INDUSTRY_CASES)
def test_a_twin_filed_under_another_industry_is_dropped(industries, twin, base, base_industry):
    result = bu._filter_market(industries, FLOOR)
    kept = {s for rows in result.kept.values() for s in _syms(rows)}
    assert twin not in kept and base in kept
    assert f"{twin} (twin of {base} in {base_industry})" in result.examples["not_common_share"]
    (entry,) = result.cross_industry_twins
    assert entry.startswith(f'{twin} "') and entry.endswith(f"→ {base} [{base_industry}]")


@pytest.mark.parametrize("industries,twin,base,base_industry", CROSS_INDUSTRY_CASES)
def test_an_industry_local_directory_would_have_kept_it(industries, twin, base, base_industry):
    """What round 2 did — each industry alone. The twin survives there, so the test above
    fails if `_filter_market` goes back to per-industry directories (mutation-checked)."""
    (twin_industry,) = [i for i, rows in industries.items() if twin in _syms(rows)]
    kept, _, _ = bu._filter_rows(industries[twin_industry], FLOOR)
    assert twin in _syms(kept)


@pytest.mark.parametrize("industries", [
    # Distinct companies: the issuer-name guard holds across industries.
    {"Beverages - Non-Alcoholic": [_row("KO", "The Coca-Cola Company")],
     "Beverages - Bottlers": [_row("KOF", "Coca-Cola FEMSA, S.A.B. de C.V.")]},
    {"Insurance - Property & Casualty": [_row("PGR", "The Progressive Corporation")],
     "Household & Personal Products": [_row("PG", "The Procter & Gamble Company")]},
    {"Insurance - Life": [_row("MET", "MetLife, Inc.")],
     "Internet Content & Information": [_row("META", "Meta Platforms, Inc.", "NASDAQ")]},
    {"Banks - Regional": [_row("FBNC", "First Bancorp", "NASDAQ")],
     "Banks - Diversified": [_row("FBP", "First BanCorp.")]},
    {"Restaurants": [_row("YUM", "Yum! Brands, Inc.")],
     "Restaurants - China": [_row("YUMC", "Yum China Holdings, Inc.")]},
    # A class letter is never a twin, in any industry (each industry keeps its own vote).
    {"Entertainment": [_row("FOX", "Fox Corporation", "NASDAQ")],
     "Broadcasting": [_row("FOXA", "Fox Corporation", "NASDAQ")]},
    # A name too short to prove one issuer is never paired.
    {"Conglomerates": [_row("MMM", "3M Company")],
     "Specialty Industrial Machinery": [_row("MMMA", "3M Company")]},
])
def test_distinct_companies_in_different_industries_are_all_kept(industries):
    result = bu._filter_market(industries, FLOOR)
    assert {s for rows in result.kept.values() for s in _syms(rows)} == \
        {r["symbol"] for rows in industries.values() for r in rows}
    assert result.cross_industry_twins == []


def test_a_base_under_the_floor_in_another_industry_still_proves_a_twin():
    result = bu._filter_market({
        "Shell Companies": [_row("XYZA", "XYZ Acquisition Corp", "NASDAQ", cap=4.8e8)],
        "Capital Markets": [_row("XYZAU", "XYZ Acquisition Corp", "NASDAQ", cap=5.1e8)],
    }, FLOOR)
    assert result.kept == {"Capital Markets": [], "Shell Companies": []}
    assert result.dropped == {"below_floor": 1, "not_common_share": 1}
    assert "XYZAU (twin of XYZA in Shell Companies)" in result.examples["not_common_share"]


def test_a_non_common_row_in_another_industry_is_never_a_base():
    """A fund (or a note) sharing a prefix and a name must not take a real listing down."""
    result = bu._filter_market({
        "Asset Management": [_row("ABC", "ABC Holdings Inc.", isFund=True)],
        "Banks - Regional": [_row("ABCD", "ABC Holdings Inc.")],
    }, FLOOR)
    assert _syms(result.kept["Banks - Regional"]) == ["ABCD"]


def test_a_twin_repeated_in_two_industries_is_dropped_from_both():
    result = bu._filter_market({
        "A Industry": [_row("SO", "The Southern Company")],
        "B Industry": [_row("SOMN", "The Southern Company")],
        "C Industry": [_row("SOMN", "The Southern Company")],
    }, FLOOR)
    assert result.kept == {"A Industry": [result.kept["A Industry"][0]],
                           "B Industry": [], "C Industry": []}
    assert result.dropped == {"not_common_share": 2}
    assert result.drops_by_industry == {"B Industry": 1, "C Industry": 1}


def test_drop_counts_are_kept_per_industry_and_empty_industries_survive():
    result = bu._filter_market({
        "Shell Companies": [],
        "Regulated Electric": [_row("SO", "The Southern Company"),
                               _row("SOJC", "The Southern Company JR 2017B NT 77"),
                               _row("SOMN", "The Southern Company")],
        "Entertainment": [_row("GOOG", "Alphabet Inc.", "NASDAQ", avg_volume=1e6),
                          _row("GOOGL", "Alphabet Inc.", "NASDAQ", avg_volume=2e6)],
    }, FLOOR)
    assert result.kept["Shell Companies"] == []
    assert result.drops_by_industry == {"Regulated Electric": 2, "Entertainment": 1}
    assert result.dropped == {"not_common_share": 2, "same_issuer": 1}


def test_the_result_does_not_depend_on_the_order_industries_arrived_in():
    """`main()` fills its dict in request-completion order; the filter walks it sorted."""
    industries = {name: rows for case in CROSS_INDUSTRY_CASES for name, rows in case[0].items()}
    industries["Regulated Electric"] = [_row("DUK", "Duke Energy Corporation"),
                                        _row("DUKB", "Duke Energy Corporation 5.625%"),
                                        _row("DUKU", "Duke Energy Corporation Units 1.08.29")]
    expected = bu._filter_market(industries, FLOOR)
    rng = random.Random(11)
    names = list(industries)
    for _ in range(15):
        rng.shuffle(names)
        got = bu._filter_market({n: industries[n] for n in names}, FLOOR)
        assert got == expected


# ── UB3-1 (c): a listing priced with the issuer's share count is named ────────────────

# The review's truncated probe strings (FMP cuts this family at ~31 characters), with a
# note's liquidity: a sliver of the issuer-sized cap FMP reports.
TRUNCATED_NOTES = [
    ("ELC", "Entergy Louisiana, LLC Collater", 3.775e10, 25.0, 2e4),
    ("EAI", "Entergy Arkansas, LLC First Mor", 9.4e8, 24.0, 5e3),
    ("PPLC", "PPL Capital Funding, Inc. 2007 ", 3.598e10, 24.0, 3e4),
    ("DTW", "DTE Electric Company 2017 Seri", 3.75e9, 22.0, 1.5e4),
]


def test_truncated_subsidiary_notes_pass_every_name_rule():
    """Honest about the limit: no name rule reads these, so they are KEPT — the turnover
    WARNING below is what names them."""
    rows = [_row(s, n, cap=c, price=p, avg_volume=v) for s, n, c, p, v in TRUNCATED_NOTES]
    rows += [_row("ETR", "Entergy Corporation", cap=3.3e10, price=80, avg_volume=2.5e6),
             _row("PPL", "PPL Corporation", cap=2.4e10, price=33, avg_volume=4e6),
             _row("DTE", "DTE Energy Company", cap=2.7e10, price=130, avg_volume=1.2e6)]
    kept, dropped, _ = bu._filter_rows(rows, FLOOR)
    assert _syms(kept) == sorted(["ELC", "EAI", "PPLC", "DTW", "ETR", "PPL", "DTE"])
    assert dropped == {}


def test_thin_turnover_rows_are_named_most_suspicious_first(caplog):
    rows = [_row(s, n, cap=c, price=p, avg_volume=v) for s, n, c, p, v in TRUNCATED_NOTES]
    rows += [_row("ETR", "Entergy Corporation", cap=3.3e10, price=80, avg_volume=2.5e6),
             _row("DUK", "Duke Energy Corporation", cap=9e10, price=115, avg_volume=3e6)]
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"Regulated Electric": rows})
    (msg,) = _warnings(caplog, "of their reported market cap a day")
    assert "4 kept row(s)" in msg
    assert "ETR " not in msg and "DUK " not in msg
    order = [msg.index(f"{s} ") for s in ("ELC", "PPLC", "DTW", "EAI")]
    assert order == sorted(order)              # 0.0013% < 0.0020% < 0.0088% < 0.0128%
    assert 'ELC "Entergy Louisiana, LLC Collater" [Regulated Electric] 0.0013%/day' in msg
    # Not mistaken for the other two lists.
    assert "closed-end fund" not in msg and "W/U/R/V" not in msg


def test_sojc_shape_is_named_and_duk_is_not():
    """The review's measured pair: SOJC $0.84M a day on $20.7B (0.004%), DUK ~0.38%."""
    assert bu._daily_turnover(_row("SOJC", cap=2.067e10, price=21, avg_volume=4e4)) < \
        bu._THIN_DAILY_TURNOVER
    assert bu._daily_turnover(_row("DUK", cap=9e10, price=115, avg_volume=3e6)) > \
        10 * bu._THIN_DAILY_TURNOVER


@pytest.mark.parametrize("avg_volume,named", [(100_000, False), (99_999, True)])
def test_the_turnover_bar_is_strict(caplog, avg_volume, named):
    """$20 × 100,000 on a $10B cap is exactly 0.02%: not under the bar."""
    row = _row("ABC", "ABC Holdings Inc.", cap=1e10, price=20.0, avg_volume=avg_volume)
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"X": [row]})
    assert bool(_warnings(caplog, "of their reported market cap a day")) is named


@pytest.mark.parametrize("field,bad", [
    ("price", None), ("price", "N/A"), ("price", math.nan), ("price", math.inf), ("price", 0),
    ("price", -3.0), ("price", True),
    ("avgVolume", None), ("avgVolume", "12"), ("avgVolume", math.nan), ("avgVolume", 0),
    ("avgVolume", False),
    ("marketCap", None), ("marketCap", math.inf), ("marketCap", -1e9), ("marketCap", "1e9"),
])
def test_unusable_inputs_never_name_a_row_or_raise(caplog, field, bad):
    row = _row("ABC", "ABC Holdings Inc.", cap=1e10, price=20.0, avg_volume=10)  # would be thin
    row[field] = bad
    assert bu._daily_turnover(row) is None
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"X": [row]})
    assert _warnings(caplog, "of their reported market cap a day") == []


def test_only_the_average_volume_counts_not_the_days():
    """The day's volume is one session's noise; the rule reads avgVolume only."""
    row = _row("ABC", cap=1e10, price=20.0, avg_volume=None, volume=10)
    assert bu._daily_turnover(row) is None


def test_an_overflowing_product_is_not_a_turnover():
    assert bu._daily_turnover(_row("ABC", cap=1e10, price=1e200, avg_volume=1e200)) is None


def test_a_long_thin_list_is_capped(caplog):
    rows = [_row(f"T{chr(65 + i // 26)}{chr(65 + i % 26)}", cap=1e10, price=1.0,
                 avg_volume=1 + i) for i in range(bu._SUSPECTS_NAMED + 4)]
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"X": rows})
    (msg,) = _warnings(caplog, "of their reported market cap a day")
    assert f"{bu._SUSPECTS_NAMED + 4} kept row(s)" in msg and "(+4 more)" in msg


# ── UB3-2: closed-end and physical-metal trusts are named (never dropped) ─────────────


@pytest.mark.parametrize("sym,name", [
    ("RVT", "Royce Value Trust Inc."),
    ("GAB", "The Gabelli Equity Trust Inc."),
    ("BST", "BlackRock Science and Technology Trust"),
    ("BCAT", "BlackRock Capital Allocation Term Trust"),
    ("CEF", "Sprott Physical Gold and Silver Trust"),
    ("PHYS", "Sprott Physical Gold Trust"),
    ("RMT", "Royce Micro-Cap Trust, Inc."),
])
def test_a_closed_end_or_physical_trust_is_kept_and_named(caplog, sym, name):
    assert bu._drop_reason(_row(sym, name), FLOOR) is None
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"Asset Management": [_row(sym, name)]})
    (msg,) = _warnings(caplog, "closed-end fund")
    assert f"{sym} " in msg


@pytest.mark.parametrize("name", [
    "Federal Realty Investment Trust", "Sabine Royalty Trust", "Northern Trust Corporation",
    "Permian Basin Royalty Trust", "Starwood Property Trust, Inc.", "Blackstone Mortgage Trust",
    "Mesabi Trust", "Pebblebrook Hotel Trust", "Brandywine Realty Trust", "RLJ Lodging Trust",
    "Healthcare Realty Trust Incorporated", "Washington Trust Bancorp, Inc.",
])
def test_operating_trusts_are_still_not_named(caplog, name):
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        bu._log_suspect_rows({"X": [_row("ABC", name)]})
    assert _warnings(caplog, "closed-end fund") == []


def test_the_documented_bdc_policy():
    """Docstring: a BDC votes as an operating company, except one whose name says Fund,
    which the name rule cannot tell from a closed-end fund. Pinned so a change to either
    half is deliberate (and updates the docstring + OWNER_TASKS)."""
    assert bu._drop_reason(_row("ARCC", "Ares Capital Corporation", "NASDAQ"), FLOOR) is None
    assert bu._drop_reason(_row("OBDC", "Blue Owl Capital Corporation"), FLOOR) is None
    assert bu._drop_reason(_row("MSDL", "Morgan Stanley Direct Lending Fund"), FLOOR) \
        == "fund_name"
    assert bu._drop_reason(_row("BXSL", "Blackstone Secured Lending Fund"), FLOOR) \
        == "fund_name"


# ── end to end through main() ─────────────────────────────────────────────────────────


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
async def test_main_drops_cross_industry_twins_and_names_every_suspect(tmp_path, caplog):
    out = tmp_path / "u.json"
    fmp = _FakeFMP({
        "Asset Management": [_row("CG", "The Carlyle Group Inc.", "NASDAQ", cap=1.8e10,
                                  sector="Financial Services")],
        "Financial - Credit Services": [
            _row("CGABL", "The Carlyle Group Inc.", "NASDAQ", cap=5.85e9,
                 sector="Financial Services"),
            _row("OBDC", "Blue Owl Capital Corporation", sector="Financial Services"),
        ],
        "Conglomerates": [_row("HON", "Honeywell International Inc.", "NASDAQ", cap=1.3e11,
                               sector="Industrials")],
        "Aerospace & Defense": [
            _row("HONAV", "Honeywell International Inc.", "NASDAQ", cap=7.764e10,
                 sector="Industrials"),
            _row("RTX", "RTX Corporation", sector="Industrials"),
        ],
        "Regulated Electric": [
            _row("ETR", "Entergy Corporation", cap=3.3e10, price=80, avg_volume=2.5e6),
            _row("EAI", "Entergy Arkansas, LLC First Mortgage Bonds", cap=9.4e8, price=24,
                 avg_volume=5e3),
            _row("ELC", "Entergy Louisiana, LLC Collater", cap=3.775e10, price=25,
                 avg_volume=2e4),
            _row("ARMX", "Foo Power Ltd. American Depository Shares", cap=4e9),
        ],
    })
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    written = json.loads(out.read_text(encoding="utf-8"))
    tickers = {e["industry"]: e["tickers"] for e in written["industries"]}
    assert tickers == {
        "Asset Management": ["CG"],
        "Financial - Credit Services": ["OBDC"],
        "Conglomerates": ["HON"],
        "Aerospace & Defense": ["RTX"],
        "Regulated Electric": ["ARMX", "ELC", "ETR"],      # ELC kept — but named below
    }
    (cross,) = _warnings(caplog, "ANOTHER industry")
    assert "CGABL" in cross and "HONAV" in cross and "check before uploading" in cross
    (thin,) = _warnings(caplog, "of their reported market cap a day")
    assert "ELC " in thin and "ETR " not in thin
    text = caplog.text
    assert 'EAI "Entergy Arkansas, LLC First Mortgage Bonds"' in text
    assert "HONAV (twin of HON in Conglomerates)" in text
    # The per-industry lines carry each industry's own drop count.
    assert any("Aerospace & Defense" in r.getMessage() and "1 tickers (1 dropped)"
               in r.getMessage() for r in caplog.records)
    # The drop counts stay INFO: nothing here is an ignored server-side filter.
    assert "not honoured" not in text
