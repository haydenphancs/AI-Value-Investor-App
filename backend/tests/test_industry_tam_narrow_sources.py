"""Industry-specific TAM sources for the FMP industries that used to resolve to a
whole-sector stand-in (2026-10-01).

The Moat card shows TAM/CAGR only for `source_grain == 'industry'` (owner decision
2026-10-01), so an industry with no narrow source shows "—". These tests pin the
narrow sources added for them:

  * every Census NAICS code in `INDUSTRY_TO_CENSUS` was verified LIVE in BOTH the
    2024 AIES (`/2024/aiesbasic`) and the 2017 Economic Census (`/2017/ecnbasic`) —
    the figures are recorded in `fixtures/industry_tam/verified_sources_2026_10_01.json`,
    so a new mapping without a recorded verification fails here;
  * every FRED series in `FRED_SERIES_MATCHES_INDUSTRY` was verified live and carries
    the "BEA … (via FRED)" markers Phase B's floor-skip and scope backfill key off;
  * the TAM/CAGR math on those recorded figures, and the grain `_compute_one` gives
    each newly mapped industry (Census answering, and Census unconfigured);
  * an allow-listed BEA backup never flips the card's lifecycle label against the
    Census figure it stands in for;
  * the deliberately UNMAPPED industries stay unmapped, each with its reason;
  * Phase B's "global ≥ US Census" floor for the two curated global industries that
    now carry a Census figure.

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
from app.integrations.census import CensusRevenueSnapshot
from app.services.industry_dossier_service import IndustryDossierService, classify_lifecycle
from app.services.industry_override_service import (
    CURATED_OVERRIDE_INDUSTRIES,
    IndustryOverrideService,
)

_BACKEND = Path(__file__).resolve().parents[1]
SOURCES = json.loads(
    (_BACKEND / "tests" / "fixtures" / "industry_tam" / "verified_sources_2026_10_01.json").read_text()
)
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
    rec = SOURCES["census"][naics]
    return CensusRevenueSnapshot(
        revenue_usd=rec["aies_2024_thousands"] * 1000.0,
        year=2024,
        naics=naics,
        naics_label=rec["label"],
        revenue_usd_baseline=rec["ecn_2017_thousands"] * 1000.0,
        baseline_year=2017,
    )


class _FakeCensus:
    def __init__(self, configured: bool = True) -> None:
        self.is_configured = configured
        self.calls: List[str] = []

    async def get_industry_revenue_snapshot(self, naics: str) -> Optional[CensusRevenueSnapshot]:
        self.calls.append(naics)
        return _snapshot(naics)


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
        f"{ind} → {code}" for ind, code in its.INDUSTRY_TO_CENSUS.items()
        if code not in SOURCES["census"]
    )
    assert not missing, (
        "NAICS codes with no recorded live verification (re-run the AIES 2024 + "
        f"ECN 2017 check and add them to the fixture): {missing}"
    )
    for code, rec in SOURCES["census"].items():
        # Both vintages must answer: AIES alone gives a TAM with no CAGR.
        assert rec["aies_2024_thousands"] > 0 and rec["ecn_2017_thousands"] > 0, code


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
        # … with the markers Phase B's floor-skip and `_backfill_global_scope` read.
        assert label.startswith("BEA ") and "(via FRED)" in label, label


# ── TAM / CAGR math on the verified figures ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(its.INDUSTRY_TO_CENSUS))
async def test_census_tam_and_cagr_from_the_verified_figures(census, industry):
    code = its.INDUSTRY_TO_CENSUS[industry]
    rec = SOURCES["census"][code]
    tam = await its._try_census_tam(industry)

    assert tam is not None
    assert census.calls == [code]
    assert tam.current_tam == round(rec["aies_2024_thousands"] / 1e6, 1)
    expected_cagr = (rec["aies_2024_thousands"] / rec["ecn_2017_thousands"]) ** (1 / 7) - 1
    assert tam.cagr_5y_pct == pytest.approx(round(expected_cagr * 100, 1))
    assert (tam.current_year, tam.future_year) == ("2024", "2029")
    assert f"(NAICS {code})" in tam.source_label and rec["label"] in tam.source_label
    assert math.isfinite(tam.future_tam) and tam.future_tam > 0


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
    # Real declines (2017 ECN → 2024 AIES), kept: the card's lifecycle says "declining".
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
    rec = SOURCES["census"][its.INDUSTRY_TO_CENSUS[industry]]
    return round(((rec["aies_2024_thousands"] / rec["ecn_2017_thousands"]) ** (1 / 7) - 1) * 100, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(
    set(its.FRED_SERIES_MATCHES_INDUSTRY) & set(its.INDUSTRY_TO_CENSUS)
))
async def test_bea_backup_keeps_the_census_lifecycle_label(fred, industry):
    # On a Census outage the BEA backup takes over. BEA is value added (it moves
    # with margins, not sales), so its rate differs — but it must not flip the
    # card's lifecycle label ("declining" below 0%, "secular growth" above 15%).
    backup = await its.fred_tam_for_series(its.INDUSTRY_TO_FRED_SERIES[industry])
    assert backup.cagr_5y_pct is not None
    assert classify_lifecycle(backup.cagr_5y_pct, 50) == classify_lifecycle(_census_rate(industry), 50), (
        industry, backup.cagr_5y_pct, _census_rate(industry),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("industry,series,bea_label,census_label", [
    ("Oil & Gas Refining & Marketing", "USPETCOALMANNGSP", "declining", "mature"),   # −0.6% vs +4.0%
    ("Oil & Gas Equipment & Services", "USSAMINNGSP", "declining", "mature"),        # −1.2% vs +1.9%
    ("Aerospace & Defense", "USOTRANEQMANNGSP", "mature", "declining"),             # +3.4% vs −0.2%
])
async def test_label_flipping_backups_stay_off_the_allow_list(
    fred, industry, series, bea_label, census_label,
):
    # Mapped to their own BEA series (an accurate stored label), but NOT industry
    # grain: the label flip below is the reason, recorded 2026-10-01.
    assert its.INDUSTRY_TO_FRED_SERIES[industry] == series
    assert industry not in its.FRED_SERIES_MATCHES_INDUSTRY
    backup = await its.fred_tam_for_series(series)
    assert classify_lifecycle(backup.cagr_5y_pct, 50) == bea_label
    assert classify_lifecycle(_census_rate(industry), 50) == census_label


# ── Deliberately unmapped ───────────────────────────────────────────────

# An industry here has no NAICS code that measures it, so it shows "—" (or Phase B's
# global figure when curated). Mapping one needs a new argument, not just a code.
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


# ── Phase B floor for curated global industries that now have a Census figure ──

# Phase B global rows as stored on 2026-10-01 (industry_dossier, tam_scope='global').
_GLOBAL_ROW_2026_10_01 = {
    "Aerospace & Defense": 904.29,
    "Internet Content & Information": 1050.64,
}


def _payload(current_tam_b: float) -> dict:
    return {
        "current_tam_b": current_tam_b,
        "future_tam_b": current_tam_b * 1.4,
        "current_year": "2025",
        "future_year": "2030",
        "cagr_5y_pct": 7.0,
        "source_label": "Research synthesis",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("industry", sorted(_GLOBAL_ROW_2026_10_01))
async def test_phase_b_census_floor_for_newly_census_mapped_curated_industries(census, industry):
    assert industry in {ind for ind, _ in CURATED_OVERRIDE_INDUSTRIES}
    # Unshared code → the floor APPLIES (a shared one, like 5112, is skipped).
    assert not its.census_naics_is_shared(industry)
    us = await its._try_census_tam(industry)
    global_now = _GLOBAL_ROW_2026_10_01[industry]
    # Global ≥ US must leave room for an honest global estimate.
    assert us.current_tam * 2 <= global_now, (us.current_tam, global_now)

    svc = IndustryOverrideService()
    ok = svc._validate_response(_payload(global_now), us.current_tam, us.source_label, industry)
    assert ok["status"] == "ok"
    low = svc._validate_response(_payload(us.current_tam - 1), us.current_tam, us.source_label, industry)
    assert low["status"] == "rejected_below_phase_a"

    # Whatever Phase A stores while Census is down carries a BEA label → the floor
    # is skipped, never applied to value added.
    fred_label = its._fred_source_label(its.INDUSTRY_TO_FRED_SERIES[industry])
    skip = svc._validate_response(_payload(1.5), 500.0, fred_label, industry)
    assert skip["status"] == "ok"
