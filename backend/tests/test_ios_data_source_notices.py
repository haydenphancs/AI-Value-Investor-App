"""The Data Sources screen carries the notices the FRED and Census API terms ask for (2026-10-22).

FRED's API Terms of Use require, for an app other people use, the notice "This product uses
the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." and a
link to those terms; the Census Data API terms ask for the equivalent Census line. The Moat
card's measured market size and the macro readings come from these two APIs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VIEW = Path(__file__).resolve().parents[2] / "frontend/ios/ios/Views/Screens/DataSourcesView.swift"

_FRED_NOTICE = ("This product uses the FRED® API but is not endorsed or certified by the "
                "Federal Reserve Bank of St. Louis.")
_CENSUS_NOTICE = ("This product uses the Census Bureau Data API but is not endorsed or "
                  "certified by the Census Bureau.")


def _item(src: str, name: str) -> str:
    """The `DataSourceItem(...)` whose name is `name`, comments stripped, its string
    concatenations joined into one text."""
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))
    start = code.index(f'name: "{name}"')
    end = code.index("DataSourceItem(", start) if "DataSourceItem(" in code[start:] else len(code)
    body = code[start:end]
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', body))


@pytest.fixture(scope="module")
def src() -> str:
    if not _VIEW.exists():
        pytest.skip(f"{_VIEW} not present")
    return _VIEW.read_text(encoding="utf-8")


def test_fred_notice_and_terms_link(src):
    fred = _item(src, "Federal Reserve Bank of St. Louis (FRED)")
    assert _FRED_NOTICE in fred
    assert "https://fred.stlouisfed.org/docs/api/terms_of_use.html" in fred


def test_census_notice(src):
    assert _CENSUS_NOTICE in _item(src, "U.S. Census Bureau")


def test_bls_and_cms_credit_the_projection_sources(src):
    """The Moat row's 2026→2031 figures scale Census revenue along public BLS / CMS paths
    (2026-10-22): both agencies are listed, BLS with the citation it asks for."""
    bls = _item(src, "U.S. Bureau of Labor Statistics")
    assert "Source: U.S. Bureau of Labor Statistics." in bls
    assert "https://www.bls.gov/emp/" in bls
    cms = _item(src, "Centers for Medicare & Medicaid Services")
    assert "National Health Expenditure projections" in cms
    assert re.search(r"https://www\.cms\.gov/\S+", cms)


def test_cms_also_credits_the_gdp_history(src):
    """The GDP path's 2017–24 history is BEA data as printed in CMS NHE Table 1 (review 2026-10-22)."""
    cms = _item(src, "Centers for Medicare & Medicaid Services")
    assert "historical US nominal GDP" in cms and "Bureau of Economic Analysis" in cms


def test_the_global_market_sources_are_credited(src):
    """The global market rows (2026-10-24) come from an EUR-Lex document, a CC BY 4.0 JRC report
    and a US ITC report: each is listed, the JRC one with its licence and its copyright line, and
    each says Caydex changed the figures (CC BY: "any changes are indicated")."""
    ec = _item(src, "European Commission")
    assert "COM(2026) 504" in ec and "CELEX:52026PC0504" in ec
    jrc = _item(src, "European Commission, Joint Research Centre")
    assert "CC BY 4.0" in jrc and "© European Union, 2025" in jrc and "JRC141323" in jrc
    itc = _item(src, "U.S. International Trade Commission")
    assert "Publication 5643" in itc and "pub5643.pdf" in itc
    for text in (ec, jrc, itc):
        assert "Caydex fills" in text and "extends" in text


def test_every_global_market_source_is_credited(src):
    """A source added to the global-markets table needs its credit here too."""
    import json

    table = json.loads((Path(__file__).resolve().parents[1] / "data/industry_global_markets.json")
                       .read_text(encoding="utf-8"))
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))
    for name, source in table["sources"].items():
        assert source["url"] in code, f"{name}: {source['url']} is not on the Data Sources screen"
