"""The pinned projection tiers ARE the owner's rule (2026-10-22), re-run on every test pass.

Rule: for each industry, the most specific public source whose 2017–24 growth stayed within
2 pts/yr of Census's — BLS sector output, then CMS health spending (matched 2018–24 window),
then US nominal GDP — else measured only. Owner decisions: "i pick option 1" (the GDP path for
the 12 that track the economy) and "Measured, Health Plans via CMS" (the one exception).

Census comes from the verified fixtures; the BLS / CMS / GDP history from the committed table.
A refresh that moves an industry across a tier fails here until the owner decides it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.industry_tam_service import INDUSTRY_TO_CENSUS, census_codes
from app.services.industry_tam_projection import _DATA_PATH, validate_table
from scripts.refresh_industry_tam_projections import (
    load_census_fixtures,
    newest_census_year,
    rule_for_industry,
    tier_changes,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "industry_tam"

_GDP_TIER = {
    "Restaurants", "Airlines, Airports & Air Services", "Insurance - Property & Casualty",
    "REIT - Industrial", "REIT - Office", "REIT - Retail", "Agricultural Inputs",
    "Insurance - Diversified", "Auto - Dealerships", "Paper, Lumber & Forest Products",
    "Advertising Agencies", "Staffing & Employment Services",
}
_CMS_TIER = {"Medical - Distribution", "Medical - Healthcare Plans"}
_MEASURED_ONLY = {
    "Banks", "Banks - Diversified", "Banks - Regional", "Financial - Mortgages",
    "Insurance - Reinsurance", "Travel Lodging", "REIT - Hotel & Motel", "Steel",
    "Technology Distributors", "Apparel - Retail", "Department Stores", "Internet Retail",
    "Internet Content & Information", "Publishing", "Broadcasting",
}


@pytest.fixture(scope="module")
def table() -> dict:
    return json.loads(Path(_DATA_PATH).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def census() -> dict:
    return load_census_fixtures(_FIXTURES)


def test_the_table_is_valid_and_pins_exactly_the_census_mapped_industries(table):
    assert validate_table(table) == []
    assert set(table["industries"]) == set(INDUSTRY_TO_CENSUS)
    for name, ind in table["industries"].items():
        assert tuple(ind["naics_2017"]) == tuple(census_codes(INDUSTRY_TO_CENSUS[name])), name


def test_tier_counts_and_the_owners_lists(table):
    by_tier: dict = {}
    for name, ind in table["industries"].items():
        by_tier.setdefault(ind["tier"], set()).add(name)
    assert {k: len(v) for k, v in by_tier.items()} == {
        "bls_output": 58, "cms_nhe": 2, "gdp_path": 12, "measured_only": 15}
    assert by_tier["gdp_path"] == _GDP_TIER
    assert by_tier["cms_nhe"] == _CMS_TIER
    assert by_tier["measured_only"] == _MEASURED_ONLY


def test_the_rule_reproduces_every_pinned_tier(table, census):
    assert tier_changes(table, census) == []


def test_the_only_exception_is_health_plans_and_it_is_still_needed(table, census):
    exceptions = {n: i["exception"] for n, i in table["industries"].items() if i.get("exception")}
    assert set(exceptions) == {"Medical - Healthcare Plans"}
    exc = exceptions["Medical - Healthcare Plans"]
    assert exc["owner_decision"] == "Measured, Health Plans via CMS" and exc["date"] == "2026-10-22"
    rule, gaps = rule_for_industry(table, "Medical - Healthcare Plans", census)
    assert rule == exc["rule_tier"] == "measured_only"
    assert -2.5 < gaps["cms"] < -2.0          # misses the 2-pt line by a quarter point


def test_spot_gaps(table, census):
    _, ad = rule_for_industry(table, "Aerospace & Defense", census)
    assert ad["bls"] == pytest.approx(0.73, abs=0.01)
    _, rx = rule_for_industry(table, "Medical - Distribution", census)
    assert rx["bls"] < -2.0 and rx["cms"] == pytest.approx(-1.78, abs=0.01)
    tier, banks = rule_for_industry(table, "Banks - Diversified", census)
    assert tier == "measured_only" and abs(banks["bls"]) > 2 and abs(banks["gdp"]) > 2
    tier, dept = rule_for_industry(table, "Department Stores", census)
    assert tier == "measured_only" and dept["census"] < 0      # a shrinking industry never projects


def test_the_composite_weights_are_the_newest_verified_census_figures(table, census):
    """The runtime projects a composite only when its weights are for the dossier's measured
    year; the refresh re-weights from the newest verified Census year covering every code."""
    ind = table["industries"]["Aerospace & Defense"]
    year = newest_census_year(census, ind["naics_2017"])
    assert ind["composite"]["weights_year"] == year == 2024
    for code, part in ind["composite"]["codes"].items():
        assert part["census_thousands"] == census[code][year], code


def test_the_gdp_path_is_one_vintage(table):
    """BLS Table 4.3 and CMS Table 1 print the same BEA history (2015 here) — one vintage — and
    CMS's own base-year figure is near BLS's (a splice check: a unit or column change)."""
    g = table["gdp"]
    assert g["vintage_overlap"], "no overlap year recorded"
    for year, pair in g["vintage_overlap"].items():
        assert abs(pair["bls"] - pair["cms"]) <= 0.001 * pair["bls"], year
    assert abs(g["cms_base_year_value"] - g["base_value"]) / g["base_value"] <= 0.03


def test_the_table_is_refreshed_yearly(table):
    """BLS releases each August. Past ~15 months the table is a year stale: run
    scripts/refresh_industry_tam_projections.py (documents/OWNER_TASKS.md, yearly row)."""
    released = date.fromisoformat(table["sources"]["bls_output"]["released"])
    assert date.today() <= released + timedelta(days=456), (
        f"projection table built from the BLS release of {released}: refresh it")
