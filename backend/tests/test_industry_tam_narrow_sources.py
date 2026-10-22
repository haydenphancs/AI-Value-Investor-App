"""Industry-specific TAM sources for the FMP industries that used to resolve to a
whole-sector stand-in (2026-10-01).

The Moat card shows TAM/CAGR only for `source_grain == 'industry'` (owner decision
2026-10-01), so an industry with no narrow source shows "—". These tests pin the
narrow sources added for them:

  * every Census NAICS code in `INDUSTRY_TO_CENSUS` (each code of a composite too) was
    verified LIVE in BOTH the 2024 AIES (`/2024/aiesbasic`) and the 2017 Economic Census
    (`/2017/ecnbasic`) — the figures are recorded in
    `fixtures/industry_tam/verified_sources_2026_10_01.json` and `…_2026_10_22.json` (the
    Aerospace & Defense composite), so a new mapping without a recorded verification fails;
  * every FRED series in `FRED_SERIES_MATCHES_INDUSTRY` was verified live and carries
    the "BEA … (via FRED)" markers migration 188's purge of grounded rows keys off;
  * the TAM/CAGR math on those recorded figures, and the grain `_compute_one` gives
    each newly mapped industry (Census answering, and Census unconfigured);
  * an allow-listed BEA backup never flips the card's lifecycle label against the
    Census figure it stands in for;
  * the deliberately UNMAPPED industries stay unmapped, each with its reason.

Hermetic: the Census and FRED clients are fakes fed from the fixture.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

import app.services.industry_dossier_service as ids
import app.services.industry_tam_service as its
from app.integrations.census import CensusRevenuePoint, CensusRevenueSnapshot, CensusUnavailableException
from app.services.industry_dossier_service import IndustryDossierService, classify_lifecycle

_BACKEND = Path(__file__).resolve().parents[1]
_FIXTURES = _BACKEND / "tests" / "fixtures" / "industry_tam"
SOURCES = json.loads((_FIXTURES / "verified_sources_2026_10_01.json").read_text())
SOURCES_1022 = json.loads((_FIXTURES / "verified_sources_2026_10_22.json").read_text())
# The 10-22 file adds the A&D composite's five new codes; its census keys are disjoint
# from the 10-01 file's (pinned below), so a merge cannot silently overwrite a figure.
CENSUS = {**SOURCES["census"], **SOURCES_1022["census"]}
COMPOSITES = SOURCES_1022["census_composites"]
_AIES_2023 = {
    **{c: r["aies_2023_thousands"] for c, r in SOURCES_1022["census"].items()},
    **{c: r["aies_2023_thousands"] for c, r in SOURCES_1022["census_extra_years"].items()},
}
_UNIVERSE_FILE = _BACKEND / "data" / "industry_universe.json"

# Mapped industries that are not in today's FMP universe file (kept from earlier
# FMP taxonomies; harmless, and removing them is a separate decision).
_MAPPED_OUTSIDE_UNIVERSE = {
    "Semiconductor Equipment & Materials", "Electronic Components", "Internet Retail",
}


def _universe() -> Dict[str, str]:
    """industry → sector from the FMP-derived universe file. Read directly: the
    `universe_data` loader may fall back to Storage, which the hermetic guard
    blocks. The file may move out of the repo (FMP ToS §2.6.1) → skip, not fail."""
    if not _UNIVERSE_FILE.exists():
        pytest.skip("industry_universe.json is not in this checkout")
    rows = json.loads(_UNIVERSE_FILE.read_text())["industries"]
    return {r["industry"]: r["sector"] for r in rows}


def _snapshot(naics: str) -> CensusRevenueSnapshot:
    rec = CENSUS[naics]
    return CensusRevenueSnapshot(
        revenue_usd=rec["aies_2024_thousands"] * 1000.0,
        year=2024,
        naics=naics,
        naics_label=rec["label"],
        revenue_usd_baseline=rec["ecn_2017_thousands"] * 1000.0,
        baseline_year=2017,
    )


class _FakeCensus:
    """Single codes through `get_industry_revenue_snapshot`; composites through the thin
    per-year reads. AIES 2025 is unpublished; 2024 / 2023 / ECN 2017 come from the
    fixtures. `missing` = {(code, year)} answered as "no data" (suppressed/unpublished);
    `blips` = {(code, year)} raise CensusUnavailableException."""

    def __init__(self, configured: bool = True) -> None:
        self.is_configured = configured
        self.calls: List[str] = []
        self.aies_calls: List[tuple] = []
        self.ecn_calls: List[str] = []
        self.missing: set = set()
        self.blips: set = set()

    async def get_industry_revenue_snapshot(self, naics: str) -> Optional[CensusRevenueSnapshot]:
        self.calls.append(naics)
        return _snapshot(naics)

    def aies_probe_years(self) -> List[int]:
        return [2025, 2024, 2023, 2022]

    async def get_aies_revenue(self, naics: str, year: int):
        self.aies_calls.append((naics, year))
        if (naics, year) in self.blips:
            raise CensusUnavailableException(f"Census HTTP 503 for {naics} {year}")
        if (naics, year) in self.missing:
            return None
        thousands = {2024: CENSUS[naics]["aies_2024_thousands"], 2023: _AIES_2023.get(naics)}.get(year)
        if thousands is None:
            return None
        return CensusRevenuePoint(revenue_usd=thousands * 1000.0, year=year, naics=naics)

    async def get_ecn_revenue(self, naics: str, year: int = 2017):
        self.ecn_calls.append(naics)
        if (naics, 2017) in self.blips:
            raise CensusUnavailableException(f"Census HTTP 503 for {naics} 2017")
        if (naics, 2017) in self.missing:
            return None
        rec = CENSUS[naics]
        return CensusRevenuePoint(revenue_usd=rec["ecn_2017_thousands"] * 1000.0, year=2017,
                                  naics=naics, naics_label=rec["label"])


class _FakeFred:
    is_configured = True

    def __init__(self) -> None:
        self.calls: List[str] = []

    async def get_observations(self, series_id: str, limit: int = 8):
        self.calls.append(series_id)
        obs = SOURCES["fred"][series_id]["observations_millions"]
        newest_first = sorted(obs.items(), key=lambda kv: kv[0], reverse=True)[:limit]
        return [SimpleNamespace(date=f"{year}-01-01", value=value) for year, value in newest_first]


@pytest.fixture
def census(monkeypatch) -> _FakeCensus:
    fake = _FakeCensus()
    monkeypatch.setattr(its, "get_census_client", lambda: fake)
    return fake


@pytest.fixture
def fred(monkeypatch) -> _FakeFred:
    fake = _FakeFred()
    monkeypatch.setattr(its, "get_fred_client", lambda: fake)
    return fake


# ── The mapping tables are backed by live verification ─────────────────


def test_every_census_code_was_verified_live_in_both_datasets():
    missing = sorted(
        f"{ind} → {code}" for ind, mapping in its.INDUSTRY_TO_CENSUS.items()
        for code in its.census_codes(mapping)
        if code not in CENSUS
    )
    assert not missing, (
        "NAICS codes with no recorded live verification (re-run the AIES 2024 + "
        f"ECN 2017 check and add them to the fixture): {missing}"
    )
    for code, rec in CENSUS.items():
        # Both vintages must answer: AIES alone gives a TAM with no CAGR.
        assert rec["aies_2024_thousands"] > 0 and rec["ecn_2017_thousands"] > 0, code
    # A composite must have its recorded sums, equal to its components'.
    for industry, mapping in its.INDUSTRY_TO_CENSUS.items():
        if isinstance(mapping, its.CensusComposite):
            rec = COMPOSITES[industry]
            assert tuple(rec["codes"]) == mapping.codes and rec["label"] == mapping.label
            assert rec["aies_2024_thousands"] == sum(CENSUS[c]["aies_2024_thousands"] for c in mapping.codes)
            assert rec["aies_2023_thousands"] == sum(_AIES_2023[c] for c in mapping.codes)
            assert rec["ecn_2017_thousands"] == sum(CENSUS[c]["ecn_2017_thousands"] for c in mapping.codes)


def test_the_two_fixture_files_record_disjoint_codes():
    assert not set(SOURCES["census"]) & set(SOURCES_1022["census"])


def test_every_mapped_industry_is_an_exact_fmp_industry_string():
    # A typo'd key silently maps nothing — the industry keeps showing "—".
    universe = _universe()
    mapped = set(its.INDUSTRY_TO_CENSUS) | set(its.FRED_SERIES_MATCHES_INDUSTRY)
    unknown = sorted(mapped - set(universe) - _MAPPED_OUTSIDE_UNIVERSE)
    assert not unknown, f"not an FMP industry in industry_universe.json: {unknown}"


def test_allow_listed_fred_series_were_verified_and_carry_us_source_markers():
    for industry in its.FRED_SERIES_MATCHES_INDUSTRY:
        series = its.INDUSTRY_TO_FRED_SERIES[industry]
        assert series in SOURCES["fred"], f"{industry}: {series} has no recorded live check"
        # An explicit label, not the generic `BEA <id>` fallback …
        assert series in its._FRED_SOURCE_LABELS, series
        label = its._fred_source_label(series)
        # … with the markers migration 188's purge reads to tell a Phase A row from a
        # retired grounded one.
        assert label.startswith("BEA ") and "(via FRED)" in label, label


# ── TAM / CAGR math on the verified figures ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(its.INDUSTRY_TO_CENSUS))
async def test_census_tam_and_cagr_from_the_verified_figures(census, industry):
    mapping = its.INDUSTRY_TO_CENSUS[industry]
    tam = await its._try_census_tam(industry)
    assert tam is not None
    if isinstance(mapping, its.CensusComposite):
        rec = COMPOSITES[industry]
        assert census.calls == []                    # composites never use the snapshot call
        assert {c for c, y in census.aies_calls if y == 2024} == set(mapping.codes)
        assert set(census.ecn_calls) == set(mapping.codes)
        assert all(code in tam.source_label for code in mapping.codes) and mapping.label in tam.source_label
    else:
        rec = CENSUS[mapping]
        assert census.calls == [mapping]
        assert f"(NAICS {mapping})" in tam.source_label and rec["label"] in tam.source_label
    assert tam.current_tam == round(rec["aies_2024_thousands"] / 1e6, 1)
    expected_cagr = (rec["aies_2024_thousands"] / rec["ecn_2017_thousands"]) ** (1 / 7) - 1
    assert tam.cagr_5y_pct == pytest.approx(round(expected_cagr * 100, 1))
    assert (tam.current_year, tam.future_year) == ("2024", "2029")
    assert tam.cagr_start_year == 2017               # the growth window the card labels
    assert math.isfinite(tam.future_tam) and tam.future_tam > 0


class _OneSnapshotCensus:
    is_configured = True

    def __init__(self, snap: CensusRevenueSnapshot) -> None:
        self.snap = snap

    async def get_industry_revenue_snapshot(self, naics: str) -> Optional[CensusRevenueSnapshot]:
        return self.snap


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline,baseline_year", [(None, None), (0.0, 2017), (100e9, 2024)])
async def test_a_missing_census_baseline_warns_and_gives_no_cagr(monkeypatch, caplog, baseline, baseline_year):
    """No usable 2017 baseline (missing, suppressed to 0, or not older): the TAM still
    shows, with no growth rate and no window — and a WARNING naming the NAICS, never a
    silent flat 'forecast'. The legacy future_tam stays flat and > 0 (old builds render a 0
    future as '$X → $0B')."""
    snap = CensusRevenueSnapshot(revenue_usd=195.0e9, year=2024, naics="335", naics_label="",
                                 revenue_usd_baseline=baseline, baseline_year=baseline_year)
    monkeypatch.setattr(its, "get_census_client", lambda: _OneSnapshotCensus(snap))
    with caplog.at_level("WARNING", logger="app.services.industry_tam_service"):
        tam = await its._try_census_tam("Electrical Equipment & Parts")
    assert tam.current_tam == 195.0
    assert tam.cagr_5y_pct is None and tam.cagr_start_year is None
    assert tam.future_tam == tam.current_tam > 0
    assert "NAICS 335" in caplog.text and "no usable 2017 Economic Census baseline" in caplog.text


@pytest.mark.asyncio
async def test_a_near_zero_census_rate_is_positive_zero_and_an_exact_zero_is_kept(monkeypatch):
    """−0.04%/yr rounds to −0.0, which iOS printed as '+-0.0%'; an exact 0.0 used to
    become None through `if cagr_decimal else None`."""
    near = CensusRevenueSnapshot(revenue_usd=99.72e9, year=2024, naics="335", naics_label="",
                                 revenue_usd_baseline=100.0e9, baseline_year=2017)
    monkeypatch.setattr(its, "get_census_client", lambda: _OneSnapshotCensus(near))
    tam = await its._try_census_tam("Electrical Equipment & Parts")
    assert tam.cagr_5y_pct == 0.0 and math.copysign(1.0, tam.cagr_5y_pct) == 1.0

    flat = CensusRevenueSnapshot(revenue_usd=100.0e9, year=2024, naics="335", naics_label="",
                                 revenue_usd_baseline=100.0e9, baseline_year=2017)
    monkeypatch.setattr(its, "get_census_client", lambda: _OneSnapshotCensus(flat))
    tam = await its._try_census_tam("Electrical Equipment & Parts")
    assert tam.cagr_5y_pct == 0.0 and tam.cagr_start_year == 2017


@pytest.mark.parametrize("industry,code", [
    # One representative per family; the NAICS argument lives next to each mapping.
    ("Banks - Regional", "522110"),                     # commercial banking, not all of finance
    ("Medical - Healthcare Plans", "524114"),           # direct health & medical insurers
    ("Trucking", "484"),
    ("Discount Stores", "4523"),                        # warehouse clubs, supercenters, dollar stores
    ("Oil & Gas Refining & Marketing", "324110"),       # refineries — manufacturing, not mining
    ("Internet Content & Information", "519130"),       # internet publishing & web search portals
    ("Restaurants", "7225"),                            # Census revenue now leads BEA value added
])
def test_representative_mappings(industry, code):
    assert its.INDUSTRY_TO_CENSUS[industry] == code


@pytest.mark.parametrize("code,cagr", [
    # Real declines (2017 ECN → 2024 AIES), kept: the card's lifecycle says "declining"
    # (every row is at or below the −1%/yr edge of the 2026-10-22 dead band).
    ("4522", -5.0),   # department stores
    ("5111", -3.0),   # newspaper, periodical, book & directory publishers
    ("313", -2.0),    # textile mills
])
def test_negative_census_cagrs_are_real_declines(code, cagr):
    rec = SOURCES["census"][code]
    got = ((rec["aies_2024_thousands"] / rec["ecn_2017_thousands"]) ** (1 / 7) - 1) * 100
    assert round(got, 1) == cagr


# ── Grain through `_compute_one` ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_census_mapped_industry_resolves_to_industry_grain(census, fred):
    universe = _universe()
    svc = IndustryDossierService()
    for industry in sorted(set(its.INDUSTRY_TO_CENSUS) & set(universe)):
        d = await svc._compute_one(industry, universe[industry], [], {})
        assert d.source_grain == "industry", industry
        assert d.source_label.startswith("US Census AIES"), industry
    assert fred.calls == [], "Census answered — no FRED tier may run"


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(its.FRED_SERIES_MATCHES_INDUSTRY))
async def test_census_unconfigured_falls_back_to_the_narrow_bea_series(monkeypatch, fred, industry):
    # The July 2026 failure mode: no CENSUS_API_KEY on the server. An allow-listed
    # industry must still show an industry-grain figure, from its own BEA series.
    monkeypatch.setattr(its, "get_census_client", lambda: _FakeCensus(configured=False))
    d = await IndustryDossierService()._compute_one(industry, "Industrials", [], {})
    series = its.INDUSTRY_TO_FRED_SERIES[industry]
    assert d.source_grain == "industry"
    assert fred.calls == [series]
    assert d.source_label == its._fred_source_label(series)
    latest = SOURCES["fred"][series]["observations_millions"]["2025"]
    assert d.current_tam == round(latest / 1000.0, 1)


@pytest.mark.asyncio
async def test_railroads_have_no_census_code_and_resolve_via_bea_rail_482(fred):
    # The Economic Census does not cover NAICS 482, so AIES has no rail row either.
    assert "Railroads" not in its.INDUSTRY_TO_CENSUS
    assert its.INDUSTRY_TO_FRED_SERIES["Railroads"] == "USRAILTRANNGSP"
    d = await IndustryDossierService()._compute_one("Railroads", "Industrials", [], {})
    assert d.source_grain == "industry"
    assert d.current_tam == 50.6
    assert d.cagr_5y_pct == 1.2   # 2018 → 2025, not from the 2020 trough (3.9%)
    assert "Rail Transportation" in d.source_label


def _census_rate(industry: str) -> float:
    mapping = its.INDUSTRY_TO_CENSUS[industry]
    rec = COMPOSITES[industry] if isinstance(mapping, its.CensusComposite) else CENSUS[mapping]
    return round(((rec["aies_2024_thousands"] / rec["ecn_2017_thousands"]) ** (1 / 7) - 1) * 100, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(
    set(its.FRED_SERIES_MATCHES_INDUSTRY) & set(its.INDUSTRY_TO_CENSUS)
))
async def test_bea_backup_keeps_the_census_lifecycle_label(fred, industry):
    # On a Census outage the BEA backup takes over. BEA is value added (it moves
    # with margins, not sales), so its rate differs — but it must not flip the
    # card's lifecycle label ("declining" at or below −1%/yr, "secular growth" above 15%).
    backup = await its.fred_tam_for_series(its.INDUSTRY_TO_FRED_SERIES[industry])
    assert backup.cagr_5y_pct is not None
    assert classify_lifecycle(backup.cagr_5y_pct, 50) == classify_lifecycle(_census_rate(industry), 50), (
        industry, backup.cagr_5y_pct, _census_rate(industry),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("industry,series,bea_label,census_label", [
    ("Oil & Gas Equipment & Services", "USSAMINNGSP", "declining", "mature"),        # −1.2% vs +1.9%
])
async def test_label_flipping_backups_stay_off_the_allow_list(
    fred, industry, series, bea_label, census_label,
):
    # Mapped to their own BEA series (an accurate stored label), but NOT industry
    # grain: the label flip below is the reason, recorded 2026-10-01. Under the ±1%/yr
    # dead band (2026-10-22) this is the only backup that still flips.
    assert its.INDUSTRY_TO_FRED_SERIES[industry] == series
    assert industry not in its.FRED_SERIES_MATCHES_INDUSTRY
    backup = await its.fred_tam_for_series(series)
    assert classify_lifecycle(backup.cagr_5y_pct, 50) == bea_label
    assert classify_lifecycle(_census_rate(industry), 50) == census_label


@pytest.mark.asyncio
@pytest.mark.parametrize("industry,series", [
    # Under the ±1%/yr dead band the labels now AGREE (BEA −0.6% vs Census +4.0% → both
    # mature; BEA +3.4% vs Census → both mature), so the label flip no longer keeps these
    # off the allow-list. SCOPE does: USPETCOALMANNGSP is all of NAICS 324 (coal products
    # included) as value added, against Census refineries 324110; USOTRANEQMANNGSP is value
    # added for 3364-3369 (rail, ships, boats too) and lacks 334511 / 3329xx of the A&D
    # composite.
    ("Oil & Gas Refining & Marketing", "USPETCOALMANNGSP"),
    ("Aerospace & Defense", "USOTRANEQMANNGSP"),
])
async def test_backups_that_no_longer_flip_stay_off_the_allow_list_for_scope(fred, industry, series):
    assert its.INDUSTRY_TO_FRED_SERIES[industry] == series
    assert industry not in its.FRED_SERIES_MATCHES_INDUSTRY
    backup = await its.fred_tam_for_series(series)
    assert classify_lifecycle(backup.cagr_5y_pct, 50) == classify_lifecycle(_census_rate(industry), 50) == "mature"


def test_a_flat_census_rate_is_mature():
    """NAICS 3364 at −0.16%/yr (2017 ECN → 2024 AIES; −1.1% over seven years) is flat,
    not a decline: 'mature' under both lifecycle mirrors (owner decision 2026-10-22)."""
    from app.services.agents.ticker_report_data_collector import _classify_lifecycle

    rec = SOURCES["census"]["3364"]
    rate = round(((rec["aies_2024_thousands"] / rec["ecn_2017_thousands"]) ** (1 / 7) - 1) * 100, 1)
    assert rate == -0.2
    assert classify_lifecycle(rate) == _classify_lifecycle(rate) == "mature"


# ── Deliberately unmapped ───────────────────────────────────────────────

# An industry here has no NAICS code that measures it, so it shows "—". Mapping one
# needs a new argument, not just a code.
_UNMAPPED_ON_PURPOSE = {
    "Computer Hardware": "NAICS 3341 US plant shipments ($30.5B) are below Dell's US revenue — the market is imported",
    "Consumer Electronics": "NAICS 3343 is $5.5B of US plant shipments for Apple's industry",
    "Communication Equipment": "NAICS 3342 ($37.9B) barely exceeds Cisco's US revenue — imports missing",
    "Apparel - Manufacturers": "NAICS 315 counts US cut-and-sew plants; these brands source abroad",
    "Apparel - Footwear & Accessories": "NAICS 3162 footwear plants are $1.8B against Nike's $19B US revenue",
    "Beverages - Non-Alcoholic": "NAICS 31211 bottler shipments omit concentrate (311930) and imports",
    "Beverages - Alcoholic": "NAICS 31212 US breweries omit imported beer (Modelo, Corona, Heineken)",
    "Household & Personal Products": "NAICS 3256 falls −0.6%/yr 2017→2024 (toilet preparations −2.9%/yr "
                                     "nominal): an AIES/ECN comparability break that would label P&G's "
                                     "industry 'declining'",
    "Oil & Gas Midstream": "NAICS 486 pipeline tariffs ($72.6B) are below ET's commodity-marketing revenue",
    "Oil & Gas Integrated": "upstream + refining + chemicals: no single NAICS code",
    "Oil & Gas Drilling": "AIES 2024 publishes only NAICS 213 (all mining support, 4.5x drilling)",
    "Coal": "AIES 2024 publishes mining only at NAICS 211/212/213 — no 2121",
    "Gold": "AIES 2024 has no 2122 (metal ore) row; 212 is all non-oil mining",
    "Silver": "same as Gold",
    "Copper": "same as Gold",
    "Residential Construction": "AIES 2024 publishes only NAICS 236 (residential + nonresidential)",
    "Hardware, Equipment & Parts": "NAICS 3344 is half semiconductors, a separate FMP industry",
    "Chemicals - Specialty": "gases, paint, cleaning, flavors, lithium: 32512/3255/3256/3119/32518",
    "Gambling, Resorts & Casinos": "split between casino hotels (72112) and gambling (7132)",
    "Entertainment": "streaming (519130), studios (5121), live events (7113), TV (5151)",
    "Specialty Retail": "FMP files Amazon, Alibaba and auto-parts stores here",
    "Financial - Credit Services": "payment networks (522320) vs card issuers (522210)",
    "Real Estate - Services": "brokerage (5312) and property management (5313), equal halves",
    "REIT - Healthcare Facilities": "senior-housing operations (6233) vs net-lease rent (5311)",
}


def test_deliberately_unmapped_industries_stay_unmapped():
    universe = _universe()
    for industry, reason in _UNMAPPED_ON_PURPOSE.items():
        assert industry in universe, f"stale entry (FMP renamed it?): {industry}"
        assert not its.expects_industry_grain(industry), f"{industry}: {reason}"


def test_phase_a_coverage_of_the_universe():
    universe = _universe()
    covered = {i for i in universe if its.expects_industry_grain(i)}
    # 16 before 2026-10-01's narrow-source pass (Census + allow-listed BEA), 85 after.
    assert len(covered) >= 85, sorted(set(universe) - covered)


# ── The Aerospace & Defense composite (owner decision 2026-10-22) ──────────


AD = "Aerospace & Defense"
AD_CODES = ("3364", "334511", "336992", "332992", "332993", "332994")


def test_aerospace_maps_to_the_owner_composite():
    mapping = its.INDUSTRY_TO_CENSUS[AD]
    assert isinstance(mapping, its.CensusComposite)
    assert mapping.codes == AD_CODES
    assert "336611" not in mapping.codes            # ship building: outside the scope, suppressed
    assert AD not in its.FRED_SERIES_MATCHES_INDUSTRY
    assert its.expects_industry_grain(AD)


@pytest.mark.asyncio
async def test_the_composite_reads_342_8_and_0_9_percent(census):
    tam = await its._try_census_tam(AD)
    assert tam.current_tam == 342.8 and tam.current_year == "2024"
    assert tam.cagr_5y_pct == 0.9 and tam.cagr_start_year == 2017
    assert tam.future_tam == 359.3 and tam.future_year == "2029"   # legacy, for builds <= 1.1
    assert tam.source_label == (
        "US Census AIES — Aerospace & defense manufacturing "
        "(NAICS 3364, 334511, 336992, 332992, 332993, 332994)"
    )
    assert classify_lifecycle(tam.cagr_5y_pct) == "mature"


@pytest.mark.asyncio
async def test_composite_steps_back_a_whole_year_when_one_code_is_suppressed(census, caplog):
    census.missing = {("332994", 2024)}
    with caplog.at_level("WARNING", logger="app.services.industry_tam_service"):
        tam = await its._try_census_tam(AD)
    assert tam.current_year == "2023"
    assert tam.current_tam == round(COMPOSITES[AD]["aies_2023_thousands"] / 1e6, 1) == 329.0
    assert "332994" in caplog.text and "2024" in caplog.text and "steps back" in caplog.text
    expected = (COMPOSITES[AD]["aies_2023_thousands"] / COMPOSITES[AD]["ecn_2017_thousands"]) ** (1 / 6) - 1
    assert tam.cagr_5y_pct == round(expected * 100, 1)


@pytest.mark.asyncio
async def test_composite_missing_in_every_year_gives_no_census_figure(census, fred, caplog):
    census.missing = {("336992", y) for y in (2022, 2023, 2024, 2025)}
    with caplog.at_level("WARNING", logger="app.services.industry_tam_service"):
        assert await its._try_census_tam(AD) is None
    assert "no AIES year in which every code" in caplog.text
    # Through _compute_one the industry drops to its BEA backup — sector grain, hidden —
    # never a partial 'industry' sum.
    d = await IndustryDossierService()._compute_one(AD, "Industrials", [], {})
    assert d.source_grain == "sector"


@pytest.mark.asyncio
async def test_composite_missing_baseline_gives_tam_without_growth(census, caplog):
    census.missing = {("332993", 2017)}
    with caplog.at_level("WARNING", logger="app.services.industry_tam_service"):
        tam = await its._try_census_tam(AD)
    assert tam.current_tam == 342.8
    assert tam.cagr_5y_pct is None and tam.cagr_start_year is None
    assert tam.future_tam == tam.current_tam
    assert "332993" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("code", AD_CODES)
@pytest.mark.parametrize("year", [2025, 2024, 2017])
async def test_composite_blip_on_any_code_raises_then_recovers(census, code, year):
    census.blips = {(code, year)}
    with pytest.raises(CensusUnavailableException):
        await its._try_census_tam(AD)
    census.blips = set()
    assert (await its._try_census_tam(AD)).current_tam == 342.8


@pytest.mark.asyncio
async def test_composite_reads_are_concurrent(monkeypatch):
    """All six AIES reads of a year are in flight together (an Event released only once
    all six have started would deadlock a sequential loop)."""
    import asyncio

    started: List[str] = []
    all_started = asyncio.Event()

    class _Gate(_FakeCensus):
        async def get_aies_revenue(self, naics, year):
            if year == 2024:
                started.append(naics)
                if len(started) == len(AD_CODES):
                    all_started.set()
                await all_started.wait()
            return await super().get_aies_revenue(naics, year)

    gate = _Gate()
    monkeypatch.setattr(its, "get_census_client", lambda: gate)
    tam = await asyncio.wait_for(its._try_census_tam(AD), timeout=2.0)
    assert tam.current_tam == 342.8


@pytest.mark.parametrize("label,codes", [
    ("x", ("3364",)),                     # one code is not a composite
    ("x", ("3364", "3364")),              # duplicate
    ("x", ("3364", "336411")),            # nested: 336411 is inside 3364
    ("", ("3364", "334511")),             # no label
    ("x", ("3364", "33451a")),            # not a NAICS code
])
def test_census_composite_validation(label, codes):
    with pytest.raises(ValueError):
        its.CensusComposite(label=label, codes=codes)


def test_no_code_is_shared_partially_across_mappings():
    """A code inside a composite must not also be another industry's plain mapping (that
    figure would then be both a whole industry and a slice of another)."""
    composite_codes = {c for m in its.INDUSTRY_TO_CENSUS.values()
                       if isinstance(m, its.CensusComposite) for c in m.codes}
    plain = {m for m in its.INDUSTRY_TO_CENSUS.values() if isinstance(m, str)}
    assert not composite_codes & plain


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(
    i for i, m in its.INDUSTRY_TO_CENSUS.items() if isinstance(m, str)
))
async def test_single_code_source_labels_are_unchanged(census, industry):
    code = its.INDUSTRY_TO_CENSUS[industry]
    tam = await its._try_census_tam(industry)
    assert tam.source_label == f"US Census AIES — {CENSUS[code]['label']} (NAICS {code})"


@pytest.mark.asyncio
async def test_a_hidden_unexpected_composite_error_is_logged_even_when_a_blip_wins(monkeypatch, caplog):
    """One code raises a transient 503 (which wins the raise) while another raises a parsing
    bug: the bug must still reach the logs with its stack."""
    class _Mixed(_FakeCensus):
        async def get_aies_revenue(self, naics, year):
            if year == 2025 and naics == "3364":
                raise CensusUnavailableException("Census HTTP 503")
            if year == 2025 and naics == "334511":
                raise TypeError("unexpected row shape")
            return await super().get_aies_revenue(naics, year)

    monkeypatch.setattr(its, "get_census_client", lambda: _Mixed())
    with caplog.at_level("ERROR", logger="app.services.industry_tam_service"):
        with pytest.raises(CensusUnavailableException):
            await its._try_census_tam(AD)
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors and "TypeError" in errors[0].getMessage() and "334511" in errors[0].getMessage()
    assert errors[0].exc_info is not None
