"""Industry TAM lookup with cascading sources.

Used as a secondary source for `market_dynamics.{current_tam, future_tam,
cagr_5yr}` in the ticker-report Moat section. AI extraction from the
earnings transcript is the primary source (highest trust — explicit
company-quoted figure); this module fills in when AI didn't extract one.

Priority chain (best precision first):
  1. **Census Bureau** (NAICS 4-digit) — e.g., NAICS 5112 Software
     Publishers ≈ $300B. Requires CENSUS_API_KEY.
  2. **FRED** (BEA GDP-by-industry, 2-digit NAICS) — e.g., Information
     sector (51) ≈ $1.7T. Requires FRED_API_KEY. Always available as
     fallback when Census isn't configured or doesn't cover the industry.

Returned values are industry-level proxies, not company-specific TAM.
The iOS UI shows a small attribution caption so users know which source
produced the figure.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.integrations.census import get_census_client
from app.integrations.fred import get_fred_client

logger = logging.getLogger(__name__)


# ── FRED sector-level mapping ─────────────────────────────────────────
#
# FMP `profile.industry` → FRED series ID for "Gross Domestic Product by
# Industry" (BEA, annual, in MILLIONS USD). Series naming pattern is
# `US<INDUSTRY>NGSP` (US + industry abbreviation + N=nominal + GSP).
#
# These were verified against the live FRED API on 2026-05-21 — the
# earlier `VAPGDP*` family used here returned percent-of-GDP ratios, not
# dollar amounts, which is why TAM rendered as 0 in production.
INDUSTRY_TO_FRED_SERIES: Dict[str, str] = {
    # Information / Software / Tech services (NAICS 51) ≈ $1.7T
    "Software - Infrastructure": "USINFONGSP",
    "Software - Application": "USINFONGSP",
    "Information Technology Services": "USINFONGSP",
    "Internet Content & Information": "USINFONGSP",
    "Communication Equipment": "USINFONGSP",
    "Telecom Services": "USINFONGSP",
    # Manufacturing (NAICS 31-33) — semis, pharma, devices, autos
    "Semiconductors": "USMANNGSP",
    "Semiconductor Equipment & Materials": "USMANNGSP",
    "Electronic Components": "USMANNGSP",
    "Drug Manufacturers - General": "USMANNGSP",
    "Drug Manufacturers - Specialty & Generic": "USMANNGSP",
    "Biotechnology": "USMANNGSP",
    "Medical Devices": "USMANNGSP",
    "Auto Manufacturers": "USMANNGSP",
    # Finance & Insurance (NAICS 52)
    "Banks - Diversified": "USFININSNGSP",
    "Banks - Regional": "USFININSNGSP",
    "Capital Markets": "USFININSNGSP",
    "Insurance - Diversified": "USFININSNGSP",
    "Asset Management": "USFININSNGSP",
    # Health Care & Social Assistance (NAICS 62) ≈ $2.4T
    "Healthcare Plans": "USHLTHSOCASSNGSP",
    "Medical Care Facilities": "USHLTHSOCASSNGSP",
    "Health Information Services": "USHLTHSOCASSNGSP",
    # Mining, Quarrying, Oil & Gas (NAICS 21)
    "Oil & Gas Integrated": "USMINNGSP",
    "Oil & Gas E&P": "USMINNGSP",
    "Oil & Gas Refining & Marketing": "USMINNGSP",
    # Retail Trade (NAICS 44-45) ≈ $1.9T
    "Internet Retail": "USRETAILNGSP",
    "Specialty Retail": "USRETAILNGSP",
    "Discount Stores": "USRETAILNGSP",
    "Apparel Retail": "USRETAILNGSP",
    "Home Improvement Retail": "USRETAILNGSP",
    # Wholesale Trade (NAICS 42) ≈ $1.9T
    "Specialty Industrial Machinery": "USWHOLENGSP",
    # Food Services (NAICS 722)
    "Restaurants": "USFOODDPNGSP",
}


# ── Census 4-digit NAICS mapping (more precise than FRED) ──────────────
#
# FMP industry → (Census survey, NAICS code). When CENSUS_API_KEY is set
# and the survey/NAICS combo returns data, this is preferred over FRED
# because it pinpoints the actual industry (e.g., "Software Publishers"
# instead of "all of Information sector").

INDUSTRY_TO_CENSUS: Dict[str, Tuple[str, str]] = {
    # Software Publishers (NAICS 5112) ≈ $300B — far more precise than
    # the Information sector aggregate at FRED.
    "Software - Infrastructure": ("sas", "5112"),
    "Software - Application": ("sas", "5112"),
    # Computer Systems Design and Related Services (NAICS 5415) ≈ $450B
    "Information Technology Services": ("sas", "5415"),
    # Internet publishing has shifted to NAICS 519 under 2022 — use 5191
    "Internet Content & Information": ("sas", "5191"),
    # Semiconductor and Other Electronic Component Manufacturing (NAICS 3344)
    "Semiconductors": ("asm", "3344"),
    "Semiconductor Equipment & Materials": ("asm", "3344"),
    "Electronic Components": ("asm", "3344"),
    # Pharmaceutical and Medicine Manufacturing (NAICS 3254)
    "Drug Manufacturers - General": ("asm", "3254"),
    "Drug Manufacturers - Specialty & Generic": ("asm", "3254"),
    "Biotechnology": ("asm", "3254"),
    # Medical Equipment and Supplies Manufacturing (NAICS 3391)
    "Medical Devices": ("asm", "3391"),
    # Motor Vehicle Manufacturing (NAICS 3361)
    "Auto Manufacturers": ("asm", "3361"),
    # Electronic Shopping and Mail-Order Houses (NAICS 4541)
    "Internet Retail": ("arts", "4541"),
    # Full-Service Restaurants (NAICS 7221) — closest to "Restaurants"
    "Restaurants": ("sas", "7221"),
}


@dataclass
class IndustryTAM:
    """Industry-size projection from a public-data source.

    `current_tam` and `future_tam` are in **billions USD** (already
    normalized from the source's native unit — FRED reports in $M, Census
    in $K, both get converted here). `cagr_5y_pct` is the realized 5-year
    CAGR from the underlying source, exposed so the response's
    `market_dynamics.cagr_5yr` can fall back to it when the SectorAggregates
    batch hasn't run. `source_label` is shown verbatim under the TAM row.
    """
    current_tam: float
    future_tam: float
    current_year: str
    future_year: str
    source_label: str
    cagr_5y_pct: Optional[float] = None


_FRED_SOURCE_LABELS: Dict[str, str] = {
    "USINFONGSP": "BEA Information Sector GDP (via FRED)",
    "USMANNGSP": "BEA Manufacturing GDP (via FRED)",
    "USFININSNGSP": "BEA Finance & Insurance GDP (via FRED)",
    "USHLTHSOCASSNGSP": "BEA Health Care & Social Assistance GDP (via FRED)",
    "USMINNGSP": "BEA Mining (oil & gas) GDP (via FRED)",
    "USRETAILNGSP": "BEA Retail Trade GDP (via FRED)",
    "USWHOLENGSP": "BEA Wholesale Trade GDP (via FRED)",
    "USFOODDPNGSP": "BEA Food Services GDP (via FRED)",
}


def _fred_source_label(series_id: str) -> str:
    return _FRED_SOURCE_LABELS.get(series_id, f"BEA {series_id} (via FRED)")


def _census_source_label(survey: str, naics: str) -> str:
    survey_name = {
        "sas": "Service Annual Survey",
        "asm": "Annual Survey of Manufactures",
        "arts": "Annual Retail Trade Survey",
    }.get(survey, survey.upper())
    return f"US Census {survey_name} (NAICS {naics})"


def _project_5y(latest_value: float, cagr_decimal: float) -> float:
    """Project a value 5 years forward at a clamped CAGR. Clamping keeps
    a one-off BEA / Census revision from blowing up the future TAM.
    """
    clamped = max(-0.20, min(0.20, cagr_decimal))
    return latest_value * math.pow(1.0 + clamped, 5)


async def _try_fred_tam(industry: str) -> Optional[IndustryTAM]:
    """FRED branch: BEA GDP-by-industry series. Returns None when the
    industry isn't mapped, the FRED key isn't set, or the series has
    insufficient observations.
    """
    series_id = INDUSTRY_TO_FRED_SERIES.get(industry)
    if not series_id:
        return None

    client = get_fred_client()
    if not client.is_configured:
        return None

    obs = await client.get_observations(series_id, limit=8)
    if len(obs) < 2:
        logger.warning(
            f"FRED series {series_id} returned {len(obs)} obs for industry "
            f"{industry!r} — check series exists and FRED_API_KEY is set"
        )
        return None
    latest = obs[0]
    if latest.value <= 0:
        return None

    try:
        current_year = latest.date.split("-", 1)[0]
        int(current_year)
    except (AttributeError, ValueError, IndexError):
        return None

    # Realized 5y CAGR (or shorter window when we don't have 6 obs yet).
    cagr_decimal = 0.0
    if len(obs) >= 6 and obs[5].value > 0:
        cagr_decimal = (latest.value / obs[5].value) ** (1.0 / 5) - 1.0
    elif len(obs) >= 2 and obs[-1].value > 0:
        years = len(obs) - 1
        cagr_decimal = (latest.value / obs[-1].value) ** (1.0 / years) - 1.0

    # FRED returns values in millions USD — convert to billions for iOS
    # (matches the formatter's $B / $T scale).
    current_b = latest.value / 1000.0
    future_b = _project_5y(current_b, cagr_decimal)

    return IndustryTAM(
        current_tam=round(current_b, 1),
        future_tam=round(future_b, 1),
        current_year=current_year,
        future_year=str(int(current_year) + 5),
        source_label=_fred_source_label(series_id),
        cagr_5y_pct=round(cagr_decimal * 100, 1),
    )


async def _try_census_tam(industry: str) -> Optional[IndustryTAM]:
    """Census branch: NAICS-precise revenue from SAS / ASM / ARTS.

    Returns None when the industry isn't mapped, the Census API key isn't
    set (Census requires a key for every request, even on the free tier),
    or the survey/NAICS combo returns no data.
    """
    lookup = INDUSTRY_TO_CENSUS.get(industry)
    if not lookup:
        return None
    survey, naics = lookup

    client = get_census_client()
    if not client.is_configured:
        return None

    snapshot = await client.get_industry_revenue_snapshot(survey, naics)
    if snapshot is None:
        return None

    cagr_decimal = 0.0
    if snapshot.revenue_usd_5y_ago and snapshot.revenue_usd_5y_ago > 0:
        cagr_decimal = (
            (snapshot.revenue_usd / snapshot.revenue_usd_5y_ago) ** (1.0 / 5)
            - 1.0
        )

    current_b = snapshot.revenue_usd / 1e9
    future_b = _project_5y(current_b, cagr_decimal)

    return IndustryTAM(
        current_tam=round(current_b, 1),
        future_tam=round(future_b, 1),
        current_year=str(snapshot.year),
        future_year=str(snapshot.year + 5),
        source_label=_census_source_label(survey, naics),
        cagr_5y_pct=round(cagr_decimal * 100, 1) if cagr_decimal else None,
    )


async def get_industry_tam(industry: Optional[str]) -> Optional[IndustryTAM]:
    """Resolve TAM for the given FMP industry using the cascading chain
    Census (NAICS-precise) → FRED (sector-level) → None.

    Returns None when no source can produce a positive TAM value.
    """
    if not industry:
        return None

    census_tam = await _try_census_tam(industry)
    if census_tam is not None and census_tam.current_tam > 0:
        return census_tam

    return await _try_fred_tam(industry)
