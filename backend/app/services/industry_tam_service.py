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
from typing import Dict, Optional

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
    "Software - Services": "USINFONGSP",
    "Information Technology Services": "USINFONGSP",
    "Internet Content & Information": "USINFONGSP",
    "Communication Equipment": "USINFONGSP",
    "Telecommunications Services": "USINFONGSP",
    # Manufacturing (NAICS 31-33) — semis, pharma, devices, autos
    "Semiconductors": "USMANNGSP",
    "Semiconductor Equipment & Materials": "USMANNGSP",
    "Electronic Components": "USMANNGSP",
    "Drug Manufacturers - General": "USMANNGSP",
    "Drug Manufacturers - Specialty & Generic": "USMANNGSP",
    "Biotechnology": "USMANNGSP",
    "Medical - Devices": "USMANNGSP",
    "Medical - Instruments & Supplies": "USMANNGSP",
    "Auto - Manufacturers": "USMANNGSP",
    "Auto - Parts": "USMANNGSP",
    "Aerospace & Defense": "USMANNGSP",
    "Chemicals": "USMANNGSP",
    "Chemicals - Specialty": "USMANNGSP",
    "Industrial - Machinery": "USMANNGSP",
    "Agricultural - Machinery": "USMANNGSP",
    "Electrical Equipment & Parts": "USMANNGSP",
    "Computer Hardware": "USMANNGSP",
    "Consumer Electronics": "USMANNGSP",
    # Finance & Insurance (NAICS 52)
    "Banks - Diversified": "USFININSNGSP",
    "Banks - Regional": "USFININSNGSP",
    "Capital Markets": "USFININSNGSP",
    "Asset Management": "USFININSNGSP",
    "Financial - Capital Markets": "USFININSNGSP",
    "Investment - Banking & Investment Services": "USFININSNGSP",
    "Insurance - Diversified": "USFININSNGSP",
    "Insurance - Life": "USFININSNGSP",
    "Insurance - Property & Casualty": "USFININSNGSP",
    "Insurance - Reinsurance": "USFININSNGSP",
    "Insurance - Brokers": "USFININSNGSP",
    "Insurance - Specialty": "USFININSNGSP",
    # Health Care & Social Assistance (NAICS 62) ≈ $2.4T
    "Medical - Healthcare Plans": "USHLTHSOCASSNGSP",
    "Medical - Care Facilities": "USHLTHSOCASSNGSP",
    "Medical - Healthcare Information Services": "USHLTHSOCASSNGSP",
    "Medical - Distribution": "USHLTHSOCASSNGSP",
    "Medical - Diagnostics & Research": "USHLTHSOCASSNGSP",
    # Mining, Quarrying, Oil & Gas (NAICS 21)
    "Oil & Gas Integrated": "USMINNGSP",
    "Oil & Gas Exploration & Production": "USMINNGSP",
    "Oil & Gas Refining & Marketing": "USMINNGSP",
    "Oil & Gas Midstream": "USMINNGSP",
    "Oil & Gas Equipment & Services": "USMINNGSP",
    # Retail Trade (NAICS 44-45) ≈ $1.9T
    "Internet Retail": "USRETAILNGSP",
    "Specialty Retail": "USRETAILNGSP",
    "Discount Stores": "USRETAILNGSP",
    "Apparel - Retail": "USRETAILNGSP",
    "Apparel - Footwear & Accessories": "USRETAILNGSP",
    "Apparel - Manufacturers": "USRETAILNGSP",
    "Home Improvement Retail": "USRETAILNGSP",
    "Auto - Dealerships": "USRETAILNGSP",
    # Food Services (NAICS 722)
    "Restaurants": "USFOODDPNGSP",
    # Construction (NAICS 23)
    "Construction": "USCONSTNGSP",
    "Construction Materials": "USCONSTNGSP",
    "Engineering & Construction": "USCONSTNGSP",
    "Residential Construction": "USCONSTNGSP",
    # Utilities (NAICS 22)
    "Regulated Electric": "USUTILNGSP",
    "Regulated Gas": "USUTILNGSP",
    "Regulated Water": "USUTILNGSP",
    "Renewable Utilities": "USUTILNGSP",
    "Independent Power Producers": "USUTILNGSP",
    "Diversified Utilities": "USUTILNGSP",
    # Real Estate (NAICS 53)
    "Real Estate - Services": "USREALNGSP",
    "REIT - Healthcare Facilities": "USREALNGSP",
    "REIT - Hotel & Motel": "USREALNGSP",
    "REIT - Industrial": "USREALNGSP",
    "REIT - Office": "USREALNGSP",
    "REIT - Residential": "USREALNGSP",
    "REIT - Retail": "USREALNGSP",
    "REIT - Specialty": "USREALNGSP",
    "REIT - Diversified": "USREALNGSP",
    "REIT - Mortgage": "USREALNGSP",
    # Beverages (NAICS 3121)
    "Beverages - Alcoholic": "USMANNGSP",
    "Beverages - Non-Alcoholic": "USMANNGSP",
    "Beverages - Wineries & Distilleries": "USMANNGSP",
}


# ── Census NAICS mapping (4-digit, more precise than FRED) ─────────────
#
# FMP industry → NAICS 2017 code. Looked up via AIES (annual revenue,
# latest year) + Economic Census 2017 (5y-prior baseline for CAGR).
# All NAICS codes here were verified live to return data from both
# endpoints; codes that AIES doesn't cover (e.g., NAICS 2111 oil & gas
# extraction, NAICS 7221 restaurants) were intentionally left out so
# those industries fall through to FRED.

INDUSTRY_TO_CENSUS: Dict[str, str] = {
    # Software Publishers (NAICS 5112) — 2023 ≈ $526B, 2017 = $276B
    # (vs. FRED's broader Information sector ≈ $1.7T).
    "Software - Infrastructure": "5112",
    "Software - Application": "5112",
    "Software - Services": "5112",
    # Computer Systems Design Services (NAICS 5415) — 2023 ≈ $631B
    "Information Technology Services": "5415",
    # Semiconductor and Other Electronic Component Mfg (NAICS 3344) —
    # 2023 ≈ $117B. The actual semiconductor industry is larger globally
    # but this is the US-domestic NAICS bucket.
    "Semiconductors": "3344",
    "Semiconductor Equipment & Materials": "3344",
    "Electronic Components": "3344",
    # Pharmaceutical and Medicine Mfg (NAICS 3254) — 2023 ≈ $249B
    "Drug Manufacturers - General": "3254",
    "Drug Manufacturers - Specialty & Generic": "3254",
    "Biotechnology": "3254",
    # Medical Equipment and Supplies Mfg (NAICS 3391) — 2023 ≈ $98B
    "Medical - Devices": "3391",
    "Medical - Instruments & Supplies": "3391",
    # Motor Vehicle Mfg (NAICS 3361) — 2023 ≈ $481B
    "Auto - Manufacturers": "3361",
    # Motor Vehicle Parts Mfg (NAICS 3363) — 2023 ≈ $268B
    "Auto - Parts": "3363",
    # Electronic Shopping and Mail-Order Houses (NAICS 4541) — 2023 ≈ $1.16T
    "Internet Retail": "4541",
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
    "USREALNGSP": "BEA Real Estate & Rental GDP (via FRED)",
    "USUTILNGSP": "BEA Utilities GDP (via FRED)",
    "USCONSTNGSP": "BEA Construction GDP (via FRED)",
    "USNGSP": "BEA US Total GDP, all industries (via FRED)",
}


def _fred_source_label(series_id: str) -> str:
    return _FRED_SOURCE_LABELS.get(series_id, f"BEA {series_id} (via FRED)")


def _census_source_label(naics: str, label: str = "") -> str:
    """Caption shown under the TAM row when Census produced the figure.
    Includes the human-readable NAICS label when Economic Census gave us
    one ("Software Publishers"), falling back to just the code."""
    if label:
        return f"US Census AIES — {label} (NAICS {naics})"
    return f"US Census AIES (NAICS {naics})"


def _project_5y(latest_value: float, cagr_decimal: float) -> float:
    """Project a value 5 years forward at a clamped CAGR. Clamping keeps
    a one-off BEA / Census revision from blowing up the future TAM.
    """
    clamped = max(-0.20, min(0.20, cagr_decimal))
    return latest_value * math.pow(1.0 + clamped, 5)


async def fred_tam_for_series(
    series_id: str,
    source_label: Optional[str] = None,
) -> Optional[IndustryTAM]:
    """Fetch a FRED nominal-dollar series and build the IndustryTAM shape.

    Public-ish helper so callers outside this module (industry_dossier_service
    for sector / all-industry fallback) can reuse the snapshot → TAM logic
    without duplicating the millions-to-billions normalization and the 5y
    CAGR computation.
    """
    client = get_fred_client()
    if not client.is_configured:
        return None

    obs = await client.get_observations(series_id, limit=8)
    if len(obs) < 2:
        logger.warning(
            f"FRED series {series_id} returned {len(obs)} obs — "
            "check series exists and FRED_API_KEY is set"
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

    cagr_decimal = 0.0
    if len(obs) >= 6 and obs[5].value > 0:
        cagr_decimal = (latest.value / obs[5].value) ** (1.0 / 5) - 1.0
    elif len(obs) >= 2 and obs[-1].value > 0:
        years = len(obs) - 1
        cagr_decimal = (latest.value / obs[-1].value) ** (1.0 / years) - 1.0

    current_b = latest.value / 1000.0
    future_b = _project_5y(current_b, cagr_decimal)

    return IndustryTAM(
        current_tam=round(current_b, 1),
        future_tam=round(future_b, 1),
        current_year=current_year,
        future_year=str(int(current_year) + 5),
        source_label=source_label or _fred_source_label(series_id),
        cagr_5y_pct=round(cagr_decimal * 100, 1),
    )


async def _try_fred_tam(industry: str) -> Optional[IndustryTAM]:
    """FRED branch: BEA GDP-by-industry series. Returns None when the
    industry isn't mapped, the FRED key isn't set, or the series has
    insufficient observations.
    """
    series_id = INDUSTRY_TO_FRED_SERIES.get(industry)
    if not series_id:
        return None
    return await fred_tam_for_series(series_id)


async def _try_census_tam(industry: str) -> Optional[IndustryTAM]:
    """Census branch: NAICS-precise revenue from AIES (latest) +
    Economic Census 2017 (baseline for CAGR).

    Returns None when the industry isn't mapped, the Census API key
    isn't set (Census requires a key for every request, even free tier),
    or AIES doesn't cover the NAICS code (e.g., oil & gas extraction).
    """
    naics = INDUSTRY_TO_CENSUS.get(industry)
    if not naics:
        return None

    client = get_census_client()
    if not client.is_configured:
        return None

    snapshot = await client.get_industry_revenue_snapshot(naics)
    if snapshot is None:
        return None

    # CAGR over `years_apart` years (typically 6 = 2023 AIES - 2017 ECN).
    # `years_apart` is None when the baseline call failed; we still emit
    # TAM in that case, just without a CAGR.
    cagr_decimal: float = 0.0
    if (
        snapshot.revenue_usd_baseline
        and snapshot.revenue_usd_baseline > 0
        and snapshot.years_apart
        and snapshot.years_apart > 0
    ):
        cagr_decimal = (
            (snapshot.revenue_usd / snapshot.revenue_usd_baseline)
            ** (1.0 / snapshot.years_apart)
            - 1.0
        )

    current_b = snapshot.revenue_usd / 1e9
    future_b = _project_5y(current_b, cagr_decimal)

    return IndustryTAM(
        current_tam=round(current_b, 1),
        future_tam=round(future_b, 1),
        current_year=str(snapshot.year),
        future_year=str(snapshot.year + 5),
        source_label=_census_source_label(naics, snapshot.naics_label),
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
