"""Round-5 review fix B4-1 (2026-10-08) to `scripts/build_benchmark_universe.py`.

Round 4 dropped any ADR / GDS name whose remainder said "series" (`_ADR_NON_COMMON_RE` held
`\\bseries\\b`). A Mexican or Chilean issuer's ORDINARY voting class is named "Series B" /
"Series A" / "Series L", so the US common of PAC, ASR, OMAB, AMX, SQM, KOF and FMX went out
as `not_common_share` — logged at INFO among five examples, with no WARNING — whenever FMP's
`companyName` carries the ADS description. Round 3 kept them. The fix keeps only PREFERRED
markers beside an ADR phrase ("%", "pfd", "non-cum", "pref", "preferred", "preference
shares").

Names follow the depositary's own description form ("… American Depositary Shares, each
representing 10 Series B shares"); FMP's screener string for these rows is unproven (the
review rates the finding PLAUSIBLE), so each is a pattern, not a capture. Hermetic: pure
functions, plus `main()` against a fake FMP client.
"""
from __future__ import annotations

import zlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

import pytest

import scripts.build_benchmark_universe as bu
from app.services import stock_search_service as search

FLOOR = 500_000_000

# The round-4 rule, verbatim, so every must-keep row below provably reached the old drop.
_ROUND4_ADR_RULE = re.compile(r"\bseries\b|\bpref(?:erred)?\b", re.IGNORECASE)


def _row(sym: str, name: str, ex: str = "NYSE", cap: Any = 5e9, *,
         industry: str = "Airports & Air Services", **over: Any) -> Dict[str, Any]:
    """A /stable company-screener row (FMP's field names), a liquid common by default."""
    row = {
        "symbol": sym, "companyName": name, "marketCap": cap,
        "sector": "Industrials", "industry": industry,
        "price": 50.0, "volume": 1_000_000, "avgVolume": 1_000_000,
        "exchange": ex, "exchangeShortName": ex, "country": "MX",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _syms(rows: List[Dict[str, Any]]) -> List[str]:
    return sorted(r["symbol"] for r in rows)


# ── must KEEP: a Latin American ordinary class named "Series X" ───────────────────────

LATAM_ADR_COMMONS = [
    ("PAC", "Grupo Aeroportuario del Pacífico, S.A.B. de C.V. American Depositary Shares, "
            "each representing 10 Series B shares"),
    ("ASR", "Grupo Aeroportuario del Sureste, S.A.B. de C.V. American Depositary Shares, "
            "each representing 10 Series B shares"),
    ("OMAB", "Grupo Aeroportuario del Centro Norte, S.A.B. de C.V. American Depositary "
             "Shares, each representing 8 Series B shares"),
    ("AMX", "América Movil, S.A.B. de C.V. American Depositary Shares, each representing "
            "20 Series B shares"),
    ("SQM", "Sociedad Química y Minera de Chile S.A. American Depositary Shares, each "
            "representing one Series B share"),
    ("KOF", "Coca-Cola FEMSA, S.A.B. de C.V. American Depositary Shares, each representing "
            "10 Units, each Unit consisting of 3 Series B Shares and 5 Series L Shares"),
    ("KOF", "Coca-Cola FEMSA, S.A.B. de C.V. American Depositary Shares (Series L)"),
    ("FMX", "Fomento Económico Mexicano, S.A.B. de C.V. American Depositary Shares, each "
            "representing 10 BD Units, each consisting of one Series B Share, two Series D-B "
            "Shares and two Series D-L Shares"),
    ("AKO-A", "Embotelladora Andina S.A. American Depositary Shares, each representing 6 "
              "Series A shares"),
    ("AKO-B", "Embotelladora Andina S.A. American Depositary Shares, each representing 6 "
              "Series B shares"),
    # Round 4's synthetic must-drop row, now a must-keep: a bare "Series B" says nothing
    # about a preferred.
    ("ASR", "Grupo Aeroportuario del Sureste, S.A.B. de C.V. American Depositary Shares "
            "Series B"),
    ("XYZS", "XYZ Ltd. American Depository Shares Series B"),
    ("XYZG", "XYZ S.A. Global Depositary Shares, each representing 2 Series A shares"),
]


@pytest.mark.parametrize("sym,name", LATAM_ADR_COMMONS)
def test_a_series_named_ordinary_class_behind_an_adr_is_kept(sym, name):
    """Mutation: put `\\bseries\\b` back into `_ADR_NON_COMMON_RE` and every row here is
    `not_common_share` again (B4-1's regression)."""
    cut, is_adr = bu._without_adr_phrase(name)
    assert is_adr
    assert _ROUND4_ADR_RULE.search(cut), "the row must have reached round 4's drop"
    assert not bu._ADR_NON_COMMON_RE.search(cut)
    assert bu._drop_reason(_row(sym, name), FLOOR) is None
    assert _syms(bu._filter_rows([_row(sym, name)], FLOOR)[0]) == [sym]


@pytest.mark.parametrize("word", ["Series", "series", "SERIES", "Serie", "Series D-L"])
def test_the_adr_rule_never_reads_a_series_word(word):
    assert not bu._ADR_NON_COMMON_RE.search(f"XYZ S.A.B. de C.V. {word} B shares")


# ── must DROP: the preferred ADR twin of each shape ───────────────────────────────────

# (the name, whether the shared rule ALSO catches it on the cut name)
LATAM_ADR_PREFERREDS = [
    # Only `_ADR_NON_COMMON_RE` reads these (bare pref / preferred, "preference shares").
    ("PACP", "Grupo Aeroportuario del Pacífico, S.A.B. de C.V. American Depositary Shares "
             "Series B Pref", False),
    ("XYZA", "XYZ Ltd. American Depository Shares Series A Preferred", False),
    ("XYZB", "XYZ Bank plc American Depositary Shares Series 5 Preference Shares", False),
    ("XYZH", "XYZ Bank plc Global Depositary Shs Series 2 Preference Shs", False),
    # The shared rule reads these too; the ADR rule restates them (one vocabulary).
    ("XYZC", "XYZ Bank plc American Depositary Shares Series 5 Non-Cumulative Preference "
             "Shares", True),
    ("XYZD", "XYZ Bank Ltd. American Depositary Shares Series B 6.5%", True),
    ("XYZE", "XYZ Ltd. American Depositary Shares Series C Pfd", True),
    # "Banco XYZ S.A. American Depositary Shares, each representing one Preferred Share"
    # used to be the last row here. Owner call 2026-10-08: an ADR over a foreign issuer's
    # preferred share CLASS is its equity (Brazil: ITUB, BBD, PBR-A), so that row is a
    # must-KEEP now. It and its fixed-income twins (coupon, non-cumulative, series,
    # fractional interest — all still dropped) are in
    # test_benchmark_universe_builder_decisions_2026_10_08.py.
]


@pytest.mark.parametrize("sym,name,shared", LATAM_ADR_PREFERREDS)
def test_a_preferred_adr_is_still_dropped(sym, name, shared):
    """Row weight: the `False` rows are dropped by `_ADR_NON_COMMON_RE` ALONE (mutation:
    remove its pref / preferred / preference alternatives → those rows are kept)."""
    cut, is_adr = bu._without_adr_phrase(name)
    assert is_adr
    assert bu._ADR_NON_COMMON_RE.search(cut)
    assert bool(search._DEBT_PREF_NAME_RE.search(cut)) is shared
    assert not bu._BUILDER_DEBT_NAME_RE.search(cut)
    assert bu._drop_reason(_row(sym, name), FLOOR) == "not_common_share"


@pytest.mark.parametrize("name", [
    "Preference Holdings Ltd. American Depositary Shares",        # a word, not a security
    "XYZ Ltd. American Depositary Shares, each representing one ordinary share",
    "Grupo Televisa, S.A.B. Global Depositary Shares, each representing 5 Ordinary "
    "Participation Certificates (CPOs)",
])
def test_preference_is_read_only_as_preference_shares(name):
    assert bu._drop_reason(_row("XYZ", name), FLOOR) is None


# ── through the whole market: every class votes once, distinct issuers all vote ────────


def test_the_latam_adrs_survive_the_market_filter():
    """The twin and one-vote rules read the ADS description too: three airports (one
    industry) stay three issuers, KOF is not KO's twin, and Andina's two classes vote once.
    Mutation: re-adding "series" to the ADR rule empties four industries here."""
    andina_a, andina_b = (r for r in LATAM_ADR_COMMONS if r[0].startswith("AKO-"))
    market = {
        "Airports & Air Services": [_row(s, n) for s, n in LATAM_ADR_COMMONS[:3]],
        "Telecommunications Services": [
            _row("AMX", LATAM_ADR_COMMONS[3][1], industry="Telecommunications Services"),
            _row("T", "AT&T Inc.", cap=1.8e11, industry="Telecommunications Services")],
        "Chemicals - Specialty": [_row("SQM", LATAM_ADR_COMMONS[4][1],
                                       industry="Chemicals - Specialty")],
        "Beverages - Non-Alcoholic": [
            _row("KO", "The Coca-Cola Company", cap=2.9e11,
                 industry="Beverages - Non-Alcoholic"),
            _row("KOF", LATAM_ADR_COMMONS[5][1], industry="Beverages - Non-Alcoholic"),
            _row(andina_a[0], andina_a[1], avgVolume=10_000, volume=10_000,
                 industry="Beverages - Non-Alcoholic"),
            _row(andina_b[0], andina_b[1], avgVolume=200_000, volume=200_000,
                 industry="Beverages - Non-Alcoholic"),
            _row("PACP", LATAM_ADR_PREFERREDS[0][1], industry="Beverages - Non-Alcoholic"),
        ],
    }
    result = bu._filter_market(market, FLOOR)
    assert {k: _syms(v) for k, v in result.kept.items()} == {
        "Airports & Air Services": ["ASR", "OMAB", "PAC"],
        "Telecommunications Services": ["AMX", "T"],
        "Chemicals - Specialty": ["SQM"],
        "Beverages - Non-Alcoholic": ["AKO-B", "KO", "KOF"],
    }
    assert result.dropped == {"not_common_share": 1, "same_issuer": 1}
    assert result.examples["same_issuer"] == ["AKO-A (kept AKO-B)"]
    assert any(label.startswith("PACP") for label in result.examples["not_common_share"])
    assert result.cross_industry_twins == []


# ── end to end through main() ─────────────────────────────────────────────────────────



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
async def test_main_writes_the_series_b_adrs_into_their_industry(tmp_path, caplog):
    """The regression's real cost: the airport industry's median lost its members."""
    out = tmp_path / "u.json"
    fmp = _FakeFMP({
        "Airports & Air Services": [_row(s, n) for s, n in LATAM_ADR_COMMONS[:3]]
        + [_row("PACP", LATAM_ADR_PREFERREDS[0][1])],
    })
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    written = json.loads(out.read_text(encoding="utf-8"))
    assert {e["industry"]: e["tickers"] for e in written["industries"]} == {
        "Airports & Air Services": ["ASR", "OMAB", "PAC"],
    }
    assert "PACP" in caplog.text
