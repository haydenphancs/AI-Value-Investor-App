"""Industry TAM lookup with cascading sources.

Used as a secondary source for `market_dynamics.{current_tam, future_tam,
cagr_5yr}` in the ticker-report Moat section. AI extraction from the
earnings transcript is the primary source (highest trust — explicit
company-quoted figure); this module fills in when AI didn't extract one.

Priority chain (best precision first):
  1. **Census Bureau** (NAICS 3- to 6-digit revenue) — e.g., NAICS 5112
     Software Publishers ≈ $568B. Requires CENSUS_API_KEY.
  2. **FRED** (BEA GDP-by-industry value added, 2- or 3-digit NAICS) — e.g.,
     Information sector (51) ≈ $1.7T, Rail Transportation (482) ≈ $51B.
     Requires FRED_API_KEY. The fallback when Census isn't configured or
     doesn't cover the industry.

Returned values are industry-level figures, not company-specific TAM.
They are MEASURED: the card shows `current_tam` at its own year and the
average yearly growth over `cagr_start_year`→`current_year` (owner decision
2026-10-22, "Measured only"); iOS builds from 2026-10-22 show `source_label`
as a caption under the row. `future_tam` / `future_year` (a 5-year trailing-rate
extension) are kept only because iOS builds up to 1.1 still read them.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from app.integrations.census import (
    CensusRevenueSnapshot,
    CensusUnavailableException,
    get_census_client,
)
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
    "Chemicals": "USMANNGSP",
    "Chemicals - Specialty": "USMANNGSP",
    "Agricultural - Machinery": "USMANNGSP",
    # NAICS 335 (electrical equipment, appliance & component mfg) — its own
    # BEA series, verified live 2026-10-01: 2025 ≈ $87.1B value added.
    "Electrical Equipment & Parts": "USELCEQAPMANNGSP",
    "Computer Hardware": "USMANNGSP",
    "Consumer Electronics": "USMANNGSP",
    # Finance & Insurance (NAICS 52)
    "Banks - Diversified": "USFININSNGSP",
    "Banks - Regional": "USFININSNGSP",
    "Capital Markets": "USFININSNGSP",
    "Asset Management": "USFININSNGSP",
    "Financial - Capital Markets": "USFININSNGSP",
    "Investment - Banking & Investment Services": "USFININSNGSP",
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
    "Oil & Gas Midstream": "USMINNGSP",
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
    # Narrow BEA series (3-digit NAICS, or a 3-digit group) — each verified live
    # 2026-10-01 and recorded in tests/fixtures/industry_tam/. Allow-listed in
    # FRED_SERIES_MATCHES_INDUSTRY below (argument written there) unless marked
    # "not allow-listed" — those stay grain 'sector' with the reason below.
    "Aerospace & Defense": "USOTRANEQMANNGSP",           # 3364-3369, not allow-listed
    "Airlines, Airports & Air Services": "USAIRTRANNGSP",  # 481
    "Construction Materials": "USNMMPMANNGSP",           # 327
    "Marine Shipping": "USWATTRANNGSP",                  # 483
    "Oil & Gas Equipment & Services": "USSAMINNGSP",     # 213, not allow-listed
    "Oil & Gas Exploration & Production": "USOILGASNGSP",  # 211
    "Oil & Gas Refining & Marketing": "USPETCOALMANNGSP",  # 324, not allow-listed
    "Industrial - Machinery": "USMACHMANNGSP",           # 333
    "Industrial Materials": "USMINEXOILGASNGSP",         # 212
    "Insurance - Diversified": "USINSCRANGSP",           # 524
    "Paper, Lumber & Forest Products": "USWPMANNGSP",    # 321
    "Railroads": "USRAILTRANNGSP",                       # 482
    "Trucking": "USTRUCKTRANNGSP",                       # 484
    "Waste Management": "USWASTENGSP",                   # 562
}


# Industries whose FRED series actually measures THAT industry (source_grain
# 'industry'). Every other entry above maps a narrow FMP industry onto a whole
# 2-digit NAICS sector — all of US manufacturing for "Computer Hardware",
# all of finance & insurance for "Banks - Regional" — which overstates the
# market by an order of magnitude or more. Those resolve with grain 'sector',
# and `_apply_tam_source` hides a non-industry TAM/CAGR (owner decision
# 2026-10-01: an honest "—" beats a sector GDP presented as an industry's TAM).
#
# Rule for membership: the BEA series' NAICS scope is the industry's own NAICS
# code, or the industry is (nearly) all of that sector. Add an industry here
# only with that argument written next to it.
#
# Deliberately NOT members (checked against their constituents 2026-10-01):
#   "Construction" — FMP files building-products makers and distributors here
#     (TT, LII, OC, BLDR, CSL, TREX: NAICS 3334 / 327x / 4233), not NAICS 23
#     contractors, so all-US construction GDP is not their market.
#   "Engineering & Construction" — PWR, EME, FIX, MTZ, J, WSP are a slice of
#     NAICS 23 (plus 5413 engineering); homebuilders, ~40% of the sector, are a
#     separate FMP industry.
#   A BEA backup for a Census-mapped industry must give the card the SAME
#     lifecycle label as its Census figure (`classify_lifecycle`: declining at or
#     below −1%/yr, secular growth above 15% — the ±1%/yr dead band is the owner's
#     2026-10-22 decision). BEA is value added, so it moves with margins rather than
#     sales. "Oil & Gas Equipment & Services" (213) still flips the label on a Census
#     outage (2018→2025 BEA −1.2% vs 2017→2024 Census +1.9% → "declining").
#     Two more stay off for SCOPE, though their labels now agree under the band:
#     "Oil & Gas Refining & Marketing" (BEA 324 incl. coal products −0.6%, value
#     added, vs Census refineries 324110 +4.0%) and "Aerospace & Defense" (BEA
#     3364-3369 incl. rail/ship/boat +3.4%, value added, vs the Census six-code
#     aerospace + defense composite). They show "—" while Census is down.
FRED_SERIES_MATCHES_INDUSTRY: frozenset[str] = frozenset({
    "Electrical Equipment & Parts",   # USELCEQAPMANNGSP = NAICS 335 itself
    "Restaurants",                    # USFOODDPNGSP = NAICS 722 food services & drinking places
    "Diversified Utilities",          # USUTILNGSP = NAICS 22 utilities as a whole
    "Regulated Electric",             # NAICS 22 — electric power is ~3/4 of the sector's
                                      # value added, about the size of US retail electricity sales
    # Added 2026-10-01 (narrow-source pass); constituents checked in industry_universe.json.
    "Railroads",                      # USRAILTRANNGSP = NAICS 482 rail itself (UNP, CSX, NSC, CNI, CP).
                                      # The ONLY source: the Economic Census does not cover 482
    "Trucking",                       # USTRUCKTRANNGSP = NAICS 484 truck transportation (ODFL, SAIA, KNX)
    "Waste Management",               # USWASTENGSP = NAICS 562 waste management & remediation (WM, RSG, WCN)
    "Industrial - Machinery",         # USMACHMANNGSP = NAICS 333 machinery mfg (PH, ITW, CMI, CARR, JCI)
    "Construction Materials",         # USNMMPMANNGSP = NAICS 327 nonmetallic mineral products: cement,
                                      # concrete, lime, gypsum (CRH, AMRZ, CX, EXP, USLM)
    "Paper, Lumber & Forest Products",  # USWPMANNGSP = NAICS 321 wood products (WY, LPX, UFPI, WFG)
    "Industrial Materials",           # USMINEXOILGASNGSP = NAICS 212 mining except oil & gas — the
                                      # diversified miners' industry (BHP, RIO, VALE, TECK, MP)
    "Insurance - Diversified",        # USINSCRANGSP = NAICS 524; carriers (5241) are ~85% of it, and a
                                      # diversified insurer's market is all lines (BRK-B, AIG)
    "Airlines, Airports & Air Services",  # USAIRTRANNGSP = NAICS 481 air transportation (DAL, UAL, LUV)
    "Marine Shipping",                # USWATTRANNGSP = NAICS 483 water transportation (MATX, KEX, ZIM)
    "Oil & Gas Exploration & Production",  # USOILGASNGSP = NAICS 211 oil & gas extraction (COP, EOG, OXY)
})


def fred_mapping_grain(industry: str) -> str:
    """`'industry'` when the industry's FRED series measures the industry
    itself, `'sector'` when it is a whole-sector GDP stand-in (see
    `FRED_SERIES_MATCHES_INDUSTRY`)."""
    return "industry" if industry in FRED_SERIES_MATCHES_INDUSTRY else "sector"


# ── Census NAICS mapping (revenue, more precise than FRED) ─────────────
#
# FMP industry → NAICS 2017 code. Looked up via AIES (annual revenue,
# latest year) + Economic Census 2017 (baseline for CAGR). Every code here
# was verified live to return data from BOTH endpoints; the figures are
# recorded in tests/fixtures/industry_tam/verified_sources_2026_10_01.json,
# and a code missing there fails test_industry_tam_narrow_sources.py.
#
# AIES 2024 (`/2024/aiesbasic`) publishes ~1,830 NAICS 2017 codes, mostly
# down to 6 digits, but mining (211/212/213) and construction (236/237/238)
# only at 3 digits — so Coal, Gold, Silver, Copper, Oil & Gas Drilling and
# homebuilders have no code of their own. Rail (482) is outside the Economic
# Census entirely (→ BEA, above).
#
# Rule for a new entry: the code is the primary NAICS of the industry's
# largest AND most of its constituents (check industry_universe.json), and
# the US figure exceeds the leading constituent's US revenue. Industries
# whose products are mostly made abroad (computers, phones, network gear,
# apparel, footwear) fail the second test — Census counts US plant
# shipments, not the US market — and are listed as deliberately unmapped
# in that test file. A code shared by several FMP industries is their SUM
# (5112 covers the three Software industries).
#
# A value is a NAICS code string, or — for an industry no single code measures — a
# `CensusComposite` of disjoint codes whose revenues are SUMMED (one today: Aerospace &
# Defense, owner decision 2026-10-22). Read codes through `census_codes()`, never by
# treating the value as a str: Census answers 204 (cached as a 24 h miss) to a joined code
# list, which would silently drop the industry to its hidden BEA backup.


@dataclass(frozen=True)
class CensusComposite:
    """Several NAICS 2017 codes summed into one industry figure. All-or-nothing per year:
    the composite has a year only when every code has a positive value for it, and a growth
    rate only when every code has its 2017 baseline (`_census_composite_snapshot`)."""
    label: str
    codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.label or not self.label.strip():
            raise ValueError("CensusComposite needs a label")
        if len(self.codes) < 2:
            raise ValueError(f"CensusComposite {self.label!r} needs at least 2 codes, got {self.codes!r}")
        if len(set(self.codes)) != len(self.codes):
            raise ValueError(f"CensusComposite {self.label!r} repeats a code: {self.codes!r}")
        for code in self.codes:
            if not isinstance(code, str) or not code.isdigit():
                raise ValueError(f"CensusComposite {self.label!r}: {code!r} is not a NAICS code")
        for a in self.codes:
            for b in self.codes:
                if a != b and b.startswith(a):
                    # A parent and its child (3364 + 336411) would count the child twice.
                    raise ValueError(f"CensusComposite {self.label!r}: {b} is nested in {a}")


CensusMapping = Union[str, CensusComposite]


def census_codes(mapping: CensusMapping) -> Tuple[str, ...]:
    """The NAICS codes a mapping reads — a plain code gives a 1-tuple."""
    if isinstance(mapping, CensusComposite):
        return mapping.codes
    return (mapping,)


INDUSTRY_TO_CENSUS: Dict[str, CensusMapping] = {
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
    # Electrical Equipment, Appliance & Component Mfg (NAICS 335) — 2024
    # AIES ≈ $195.0B, 2017 ECN ≈ $123.0B (verified live 2026-10-01). Fuel
    # cells, batteries, wiring, generators and motors all sit in 335.
    "Electrical Equipment & Parts": "335",
    # ── Added 2026-10-01 (narrow-source pass). NAICS argument, constituents and
    #    the live 2024 AIES figure with its 2017→2024 annual rate. ──
    # ── Basic materials ──
    # NAICS 3253 pesticide, fertilizer & ag chemical mfg — CF, MOS, NTR, CTVA, FMC.
    #   2024: $48.8B, +6.5%/yr since 2017.
    "Agricultural Inputs": "3253",
    # NAICS 3313 alumina & aluminum production and processing — AA, CENX, KALU, CSTM.
    #   2024: $41.1B, +2.0%/yr since 2017.
    "Aluminum": "3313",
    # NAICS 3251 basic chemical mfg — DOW, LYB, EMN, MEOH, HUN (resins, 3252, excluded).
    #   2024: $303.9B, +4.9%/yr since 2017.
    "Chemicals": "3251",
    # NAICS 327 nonmetallic mineral products: cement, concrete, lime, gypsum — CRH, AMRZ, CX, EXP, USLM.
    #   2024: $175.4B, +4.9%/yr since 2017.
    "Construction Materials": "327",
    # NAICS 212 mining except oil & gas — BHP, RIO, VALE, TECK, MP (diversified miners).
    #   2024: $102.5B, +2.9%/yr since 2017.
    "Industrial Materials": "212",
    # NAICS 321 wood product mfg — WY, LPX, UFPI, WFG, SJ.TO. 2024: $142.0B, +4.1%/yr since 2017.
    "Paper, Lumber & Forest Products": "321",
    # NAICS 3311 iron & steel mills — NUE, STLD, CLF, CMC. 2024: $100.5B, +2.0%/yr since 2017.
    "Steel": "3311",
    # ── Communication services ──
    # NAICS 5418 advertising, PR & related services — OMC, WPP, STGW.
    #   2024: $149.9B, +5.0%/yr since 2017.
    "Advertising Agencies": "5418",
    # NAICS 5151 radio & television broadcasting — IHRT, GTN, SSP.
    #   2024: $93.8B, +2.9%/yr since 2017.
    "Broadcasting": "5151",
    # NAICS 519130 internet publishing & broadcasting and web search portals — GOOGL, META, SPOT.
    #   2024: $391.3B, +13.1%/yr since 2017.
    "Internet Content & Information": "519130",
    # NAICS 5111 newspaper, periodical, book & directory publishers — NYT, WLY, SCHL.
    #   2024: $74.5B, -3.0%/yr since 2017.
    "Publishing": "5111",
    # NAICS 517 telecommunications — VZ, T, TMUS, CMCSA. 2024: $662.6B, +1.0%/yr since 2017.
    "Telecommunications Services": "517",
    # ── Consumer cyclical ──
    # NAICS 4481 clothing stores — TJX, ROST, BURL, GAP, URBN. 2024: $206.8B, +1.5%/yr since 2017.
    "Apparel - Retail": "4481",
    # NAICS 4411 automobile dealers — CVNA, KMX, AN, LAD, PAG. 2024: $1,355.1B, +4.3%/yr since 2017.
    "Auto - Dealerships": "4411",
    # NAICS 4522 department stores — M, KSS, DDS (a real decline).
    #   2024: $46.5B, -5.0%/yr since 2017.
    "Department Stores": "4522",
    # NAICS 444 building material & garden supply dealers — HD, LOW (home centers 444110 alone are ~HD+LOW).
    #   2024: $513.6B, +5.8%/yr since 2017.
    "Home Improvement": "444",
    # NAICS 4483 jewelry, luggage & leather goods stores — SIG, TPR, CPRI.
    #   2024: $44.1B, +4.5%/yr since 2017.
    "Luxury Goods": "4483",
    # NAICS 72111 hotels (except casino hotels) & motels — MAR, HLT, H, IHG.
    #   2024: $214.2B, +2.2%/yr since 2017.
    "Travel Lodging": "72111",
    # NAICS 5615 travel arrangement & reservation — BKNG, EXPE, ABNB, TCOM.
    #   2024: $91.7B, +7.5%/yr since 2017.
    "Travel Services": "5615",
    # ── Consumer defensive ──
    # NAICS 4523 general merchandise incl. warehouse clubs & supercenters — WMT, COST, TGT, DG, DLTR.
    #   2024: $871.9B, +4.9%/yr since 2017.
    "Discount Stores": "4523",
    # NAICS 3113 sugar & confectionery product mfg — HSY, MDLZ, TR.
    #   2024: $47.1B, +3.5%/yr since 2017.
    "Food Confectioners": "3113",
    # NAICS 4244 grocery & related product merchant wholesalers — SYY, USFD, PFGC, UNFI.
    #   2024: $1,340.7B, +4.4%/yr since 2017.
    "Food Distribution": "4244",
    # NAICS 4451 grocery stores — KR, ACI, SFM. 2024: $885.7B, +4.5%/yr since 2017.
    "Grocery Stores": "4451",
    # NAICS 311 food manufacturing — GIS, KHC, HRL, SJM, JBS. 2024: $1,054.3B, +4.5%/yr since 2017.
    "Packaged Foods": "311",
    # NAICS 3122 tobacco manufacturing — MO, PM. 2024: $60.7B, +3.5%/yr since 2017.
    "Tobacco": "3122",
    # ── Energy ──
    # NAICS 211 oil & gas extraction — COP, EOG, OXY, DVN. 2024: $370.6B, +7.1%/yr since 2017.
    "Oil & Gas Exploration & Production": "211",
    # NAICS 213 support activities for mining, ~95% oil & gas field services — SLB, HAL, BKR.
    #   2024: $99.0B, +1.9%/yr since 2017.
    "Oil & Gas Equipment & Services": "213",
    # NAICS 324110 petroleum refineries — MPC, VLO, PSX, DINO. 2024: $656.0B, +4.0%/yr since 2017.
    "Oil & Gas Refining & Marketing": "324110",
    # ── Financial services ──
    # NAICS 523920 portfolio management — BLK, BX, KKR, TROW. 2024: $370.4B, +6.0%/yr since 2017.
    "Asset Management": "523920",
    # NAICS 522110 commercial banking. 2024: $867.0B, +9.3%/yr since 2017.
    "Banks": "522110",
    # NAICS 522110 commercial banking — JPM, BAC, WFC, C. 2024: $867.0B, +9.3%/yr since 2017.
    "Banks - Diversified": "522110",
    # NAICS 522110 commercial banking — PNC, TFC, HBAN. 2024: $867.0B, +9.3%/yr since 2017.
    "Banks - Regional": "522110",
    # NAICS 5231 securities & commodity contracts intermediation & brokerage — MS, GS, SCHW.
    #   2024: $376.0B, +6.4%/yr since 2017.
    "Financial - Capital Markets": "5231",
    # NAICS 5231 same — IBKR. 2024: $376.0B, +6.4%/yr since 2017.
    "Investment - Banking & Investment Services": "5231",
    # NAICS 522292 real estate credit — RKT, UWMC, PFSI. 2024: $104.3B, +2.0%/yr since 2017.
    "Financial - Mortgages": "522292",
    # NAICS 52421 insurance agencies & brokerages — AON, AJG, WTW, BRO.
    #   2024: $232.2B, +7.7%/yr since 2017.
    "Insurance - Brokers": "52421",
    # NAICS 5241 insurance carriers, all lines — BRK-B, AIG. 2024: $3,271.8B, +7.2%/yr since 2017.
    "Insurance - Diversified": "5241",
    # NAICS 524113 direct life insurance carriers — MET, AFL, PRU.
    #   2024: $661.7B, +4.0%/yr since 2017.
    "Insurance - Life": "524113",
    # NAICS 524126 direct P&C insurance carriers — CB, PGR, TRV, ALL.
    #   2024: $875.2B, +6.7%/yr since 2017.
    "Insurance - Property & Casualty": "524126",
    # NAICS 524130 reinsurance carriers — RGA, EG, RNR. 2024: $120.9B, +8.9%/yr since 2017.
    "Insurance - Reinsurance": "524130",
    # ── Healthcare ──
    # NAICS 622 hospitals — HCA, THC, UHS. 2024: $1,610.5B, +6.0%/yr since 2017.
    "Medical - Care Facilities": "622",
    # NAICS 4242 drugs & druggists' sundries merchant wholesalers — MCK, COR, CAH.
    #   2024: $1,556.7B, +7.6%/yr since 2017.
    "Medical - Distribution": "4242",
    # NAICS 524114 direct health & medical insurance carriers — UNH, ELV, CI, HUM, CNC.
    #   2024: $1,566.5B, +9.0%/yr since 2017.
    "Medical - Healthcare Plans": "524114",
    # NAICS 3254 pharmaceutical & medicine mfg, as Drug Manufacturers.
    #   2024: $259.5B, +3.0%/yr since 2017.
    "Medical - Pharmaceuticals": "3254",
    # ── Industrials ──
    # Aerospace + defense manufacturing (owner decision 2026-10-22, "Aerospace + defense
    # mfg") — the SUM of six NAICS codes, because the FMP industry holds the defense primes
    # as well as the airframers, and NAICS 3364 alone ($247.0B 2024, -0.2%/yr since 2017,
    # driven by the 2017-peak → 2024-strike airliner code 336411) mislabelled the industry
    # 'declining':
    #   3364   aerospace product & parts — BA, RTX, GE, TDG, HWM, LMT (aircraft, missiles, space)
    #   334511 search, detection, navigation & guidance instruments — LHX, DRS, MRCY
    #          (includes civil avionics and nautical instruments)
    #   336992 military armored vehicles, tanks & components — GD land systems
    #   332992/332993/332994 small-arms ammunition, ammunition, small arms & ordnance —
    #          RGR, SWBI, POWW, NPK
    #   Excluded: 336611 ship building (HII, GD Electric Boat) — outside the owner's scope
    #   and suppressed in AIES 2024; 336612 is recreational boats.
    #   A GROSS US shipments sum: components shipped into finished systems (avionics in an
    #   aircraft, engines into an airframe) are counted again, inside 3364 and across codes,
    #   so read it as a growth proxy, not a net market. Verified live 2026-10-22 in
    #   tests/fixtures/industry_tam/verified_sources_2026_10_22.json.
    #   2024: $342.8B, +0.9%/yr since 2017.
    "Aerospace & Defense": CensusComposite(
        label="Aerospace & defense manufacturing",
        codes=("3364", "334511", "336992", "332992", "332993", "332994"),
    ),
    # NAICS 3331 agriculture, construction & mining machinery mfg — DE, CAT, CNH, AGCO.
    #   2024: $107.5B, +4.1%/yr since 2017.
    "Agricultural - Machinery": "3331",
    # NAICS 481 air transportation — DAL, UAL, LUV, AAL. 2024: $335.1B, +7.0%/yr since 2017.
    "Airlines, Airports & Air Services": "481",
    # NAICS 5416 management, scientific & technical consulting — BAH, FCN, HURN, EXPO, ICFI.
    #   2024: $463.5B, +8.7%/yr since 2017.
    "Consulting Services": "5416",
    # NAICS 4238 machinery, equipment & supplies merchant wholesalers — GWW, FAST, AIT.
    #   2024: $765.7B, +4.4%/yr since 2017.
    "Industrial - Distribution": "4238",
    # NAICS 333 machinery manufacturing — PH, ITW, CMI, CARR, JCI.
    #   2024: $473.9B, +3.8%/yr since 2017.
    "Industrial - Machinery": "333",
    # NAICS 492 couriers & messengers — UPS, FDX (brokers CHRW/EXPD sit in 4885).
    #   2024: $160.6B, +8.6%/yr since 2017.
    "Integrated Freight & Logistics": "492",
    # NAICS 313 textile mills — MAGN (nonwovens). 2024: $23.9B, -2.0%/yr since 2017.
    "Manufacturing - Textiles": "313",
    # NAICS 483 water transportation — MATX, KEX, ZIM. 2024: $66.7B, +6.5%/yr since 2017.
    "Marine Shipping": "483",
    # NAICS 532 rental & leasing services — URI, AER, CAR, R. 2024: $239.1B, +6.1%/yr since 2017.
    "Rental & Leasing Services": "532",
    # NAICS 5613 employment services incl. PEOs — ADP, PAYX, RHI, TNET.
    #   2024: $562.1B, +5.9%/yr since 2017.
    "Staffing & Employment Services": "5613",
    # NAICS 484 truck transportation — ODFL, SAIA, KNX, ARCB. 2024: $402.8B, +4.8%/yr since 2017.
    "Trucking": "484",
    # NAICS 562 waste management & remediation — WM, RSG, WCN, CLH.
    #   2024: $159.4B, +6.9%/yr since 2017.
    "Waste Management": "562",
    # ── Real estate ──
    # NAICS 5311 lessors of real estate — WPC, EPRT, BNL. 2024: $373.6B, +5.4%/yr since 2017.
    "REIT - Diversified": "5311",
    # NAICS 72111 hotels & motels: hotel REITs report hotel revenue — HST, PK.
    #   2024: $214.2B, +2.2%/yr since 2017.
    "REIT - Hotel & Motel": "72111",
    # NAICS 531120 lessors of nonresidential buildings — PLD, EGP, REXR (self-storage is 531130).
    #   2024: $166.1B, +4.7%/yr since 2017.
    "REIT - Industrial": "531120",
    # NAICS 531120 lessors of nonresidential buildings — BXP, VNO, KRC.
    #   2024: $166.1B, +4.7%/yr since 2017.
    "REIT - Office": "531120",
    # NAICS 531110 lessors of residential buildings — AVB, EQR, INVH, MAA.
    #   2024: $175.1B, +5.8%/yr since 2017.
    "REIT - Residential": "531110",
    # NAICS 531120 lessors of nonresidential buildings — SPG, O, KIM.
    #   2024: $166.1B, +4.7%/yr since 2017.
    "REIT - Retail": "531120",
    # ── Technology ──
    # NAICS 42343 computer & peripheral equipment & software wholesalers — SNX, NSIT, SCSC.
    #   2024: $305.5B, +2.3%/yr since 2017.
    "Technology Distributors": "42343",
    # ── Utilities and restaurants (upgraded from BEA value added to Census revenue) ──
    # NAICS 22111 electric power generation — VST, NRG, TLN. 2024: $159.5B, +4.2%/yr since 2017.
    "Independent Power Producers": "22111",
    # NAICS 22111 electric power generation — CEG, BEP, ORA. 2024: $159.5B, +4.2%/yr since 2017.
    "Renewable Utilities": "22111",
    # NAICS 2212 natural gas distribution — ATO, NI, SWX, OGS. 2024: $156.2B, +6.5%/yr since 2017.
    "Regulated Gas": "2212",
    # NAICS 2213 water, sewage & other systems (private systems) — AWK, WTRG, AWR.
    #   2024: $21.5B, +5.7%/yr since 2017.
    "Regulated Water": "2213",
    # NAICS 2211 electric power generation, transmission & distribution — NEE, SO, DUK.
    #   2024: $612.8B, +4.1%/yr since 2017.
    "Regulated Electric": "2211",
    # NAICS 221 utilities as a whole. 2024: $790.5B, +4.6%/yr since 2017.
    "Diversified Utilities": "221",
    # NAICS 7225 restaurants & other eating places — MCD, SBUX, CMG.
    #   2024: $864.9B, +5.4%/yr since 2017.
    "Restaurants": "7225",
}


def expects_industry_grain(industry: str) -> bool:
    """True when the industry is MAPPED to an industry-grain source (a Census
    NAICS code, or a FRED series that measures the industry itself). A run
    that resolves such an industry to anything broader hit a transient miss
    on that source, not a change of truth."""
    return industry in INDUSTRY_TO_CENSUS or industry in FRED_SERIES_MATCHES_INDUSTRY


@dataclass
class IndustryTAM:
    """Industry-size projection from a public-data source.

    `current_tam` and `future_tam` are in **billions USD** (already
    normalized from the source's native unit — FRED reports in $M, Census
    in $K, both get converted here). `cagr_5y_pct` is the realized average
    yearly growth over `cagr_start_year` → `current_year` (7 years as of
    2026-10, despite the "5y" name; None when there is no usable base, never
    a fake 0). `source_label` is shown verbatim under the TAM row.
    `future_tam` / `future_year` extend that trailing rate 5 years; the
    current card never shows them — they exist for iOS builds up to 1.1.
    """
    current_tam: float
    future_tam: float
    current_year: str
    future_year: str
    source_label: str
    cagr_5y_pct: Optional[float] = None
    # First year of the growth window (Census: the 2017 Economic Census; FRED: the
    # oldest valid observation). None exactly when cagr_5y_pct is None.
    cagr_start_year: Optional[int] = None


_FRED_SOURCE_LABELS: Dict[str, str] = {
    "USINFONGSP": "BEA Information Sector GDP (via FRED)",
    "USMANNGSP": "BEA Manufacturing GDP (via FRED)",
    "USELCEQAPMANNGSP": "BEA Electrical Equipment, Appliance & Component Mfg GDP (via FRED)",
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
    "USRAILTRANNGSP": "BEA Rail Transportation GDP (via FRED)",
    "USTRUCKTRANNGSP": "BEA Truck Transportation GDP (via FRED)",
    "USWASTENGSP": "BEA Waste Management & Remediation GDP (via FRED)",
    "USMACHMANNGSP": "BEA Machinery Mfg GDP (via FRED)",
    "USNMMPMANNGSP": "BEA Nonmetallic Mineral Product Mfg GDP (via FRED)",
    "USWPMANNGSP": "BEA Wood Product Mfg GDP (via FRED)",
    "USMINEXOILGASNGSP": "BEA Mining (except oil & gas) GDP (via FRED)",
    "USINSCRANGSP": "BEA Insurance Carriers & Related Activities GDP (via FRED)",
    "USOTRANEQMANNGSP": "BEA Aerospace & Other Transportation Equipment Mfg GDP (via FRED)",
    "USAIRTRANNGSP": "BEA Air Transportation GDP (via FRED)",
    "USWATTRANNGSP": "BEA Water Transportation GDP (via FRED)",
    "USOILGASNGSP": "BEA Oil & Gas Extraction GDP (via FRED)",
    "USPETCOALMANNGSP": "BEA Petroleum & Coal Products Mfg GDP (via FRED)",
    "USSAMINNGSP": "BEA Support Activities for Mining GDP (via FRED)",
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


# The inverse of `_census_source_label`: the label's own trailing "(NAICS a, b, …)" group.
_CENSUS_LABEL_RE = re.compile(r"US Census AIES(?: — .{1,300})? \(NAICS (\d{2,6}(?:, \d{2,6}){0,11})\)")


def census_label_codes(source_label: Any) -> Optional[Tuple[str, ...]]:
    """The NAICS codes a dossier's `source_label` says Census measured, or None when the label is
    not a Census AIES label (a BEA / FRED row, a placeholder, a withdrawn grounded row, garbage).

    The projected Moat row (`industry_tam_projection`) keys on this: a projection exists only for
    the exact codes it was backtested on, so a stale single-code row (A&D before its composite
    refresh) or a BEA value-added backup never borrows a Census industry's path."""
    if not isinstance(source_label, str) or len(source_label) > 400:
        return None
    m = _CENSUS_LABEL_RE.fullmatch(source_label)
    if m is None:
        return None
    return tuple(m.group(1).split(", "))


def _project_5y(latest_value: float, cagr_decimal: float) -> float:
    """Project a value 5 years forward at a clamped CAGR. Clamping keeps
    a one-off BEA / Census revision from blowing up the future TAM.
    """
    clamped = max(-0.20, min(0.20, cagr_decimal))
    return latest_value * math.pow(1.0 + clamped, 5)


def _pct_1dp(rate_decimal: Optional[float]) -> Optional[float]:
    """A decimal growth rate as a 1-dp percentage. None for None / non-finite.
    `+ 0.0` turns a rounded `-0.0` into `0.0`, so a −0.04%/yr rate can never
    render as "+-0.0%"; an exact 0.0 stays 0.0 (it used to become None)."""
    if rate_decimal is None or not math.isfinite(rate_decimal):
        return None
    return round(rate_decimal * 100, 1) + 0.0


async def fred_tam_for_series(
    series_id: str,
    source_label: Optional[str] = None,
) -> Optional[IndustryTAM]:
    """Fetch a FRED nominal-dollar series and build the IndustryTAM shape.

    Public-ish helper so callers outside this module (industry_dossier_service
    for sector / all-industry fallback) can reuse the snapshot → TAM logic
    without duplicating the millions-to-billions normalization and the CAGR
    computation (over the whole fetched window — see below).
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
    # `not isfinite` before `<= 0`: NaN compares False to everything, so a NaN
    # latest value would otherwise pass and block the sector fallback.
    if not math.isfinite(latest.value) or latest.value <= 0:
        return None

    try:
        current_year = latest.date.split("-", 1)[0]
        int(current_year)
    except (AttributeError, ValueError, IndexError):
        return None

    # CAGR over the WHOLE fetched window — the oldest valid observation, 2018 →
    # 2025 as of 2026-10 (7 years) — not `obs[5]`. A 5-year window ending 2025
    # starts at the 2020 COVID trough and overstated growth by 1-9 points
    # (Restaurants read 12.4% against ~6.4% from 2019; owner decision
    # 2026-10-01). Seven years also matches the Census tier's 2017→2024 span.
    # The span comes from the observation DATES, so a gap in a series cannot
    # mis-annualize the rate. No valid base → no CAGR (None), never a fake 0.0.
    cagr_decimal: Optional[float] = None
    base_year: Optional[int] = None
    for base in reversed(obs[1:]):
        if not math.isfinite(base.value) or base.value <= 0:
            continue
        try:
            candidate_year = int(base.date.split("-", 1)[0])
        except (AttributeError, ValueError, IndexError):
            continue
        years = int(current_year) - candidate_year
        if years > 0:
            cagr_decimal = (latest.value / base.value) ** (1.0 / years) - 1.0
            base_year = candidate_year
            break

    current_b = latest.value / 1000.0
    future_b = _project_5y(current_b, cagr_decimal or 0.0)

    return IndustryTAM(
        current_tam=round(current_b, 1),
        future_tam=round(future_b, 1),
        current_year=current_year,
        future_year=str(int(current_year) + 5),
        source_label=source_label or _fred_source_label(series_id),
        cagr_5y_pct=_pct_1dp(cagr_decimal),
        cagr_start_year=base_year if cagr_decimal is not None else None,
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


def _raise_first_failure(
    results: Sequence[Any], industry: str, comp: "CensusComposite", what: str, year: int,
) -> None:
    """Inspect EVERY gathered result before raising: a transient Census failure wins (the
    read path retries it, the quarterly run keeps the stored row), a cancellation is
    re-raised as is, any other exception is logged with its stack and re-raised. Never sum
    a partial set."""
    failures = [(code, r) for code, r in zip(comp.codes, results) if isinstance(r, BaseException)]
    if not failures:
        return
    # Every unexpected failure is logged with its stack BEFORE any re-raise, so one hidden
    # behind a simultaneous transient error (which wins the raise) still leaves a trace.
    for code, r in failures:
        if not isinstance(r, (CensusUnavailableException, asyncio.CancelledError)):
            logger.error(
                "Census composite %r (%s) for %r: %s %d read for NAICS %s failed — %s: %s",
                comp.label, ",".join(comp.codes), industry, what, year, code,
                type(r).__name__, r, exc_info=r,
            )
    for code, r in failures:
        if isinstance(r, CensusUnavailableException):
            raise r
    for code, r in failures:
        if isinstance(r, asyncio.CancelledError):
            raise r
    raise failures[0][1]


async def _census_composite_snapshot(
    client: Any, industry: str, comp: "CensusComposite",
) -> Optional[CensusRevenueSnapshot]:
    """Sum a composite's codes into one snapshot. Year alignment and summing live HERE (the
    integration stays thin: one (code, year) per read). The codes are read concurrently.

      * Latest year: the newest probed AIES year in which EVERY code has a positive value.
        A year with only some codes is skipped with a WARNING naming the missing ones —
        never a mixed-year or partial sum. No such year → WARNING, None (falls through to
        the hidden BEA backup, like an uncovered single code).
      * Baseline: the 2017 Economic Census for EVERY code, else no baseline (TAM without a
        growth rate; `_try_census_tam` logs it).
      * A transient failure on any code raises `CensusUnavailableException`.
    """
    latest: Optional[List[Any]] = None
    latest_year: Optional[int] = None
    for year in client.aies_probe_years():
        results = await asyncio.gather(
            *(client.get_aies_revenue(code, year) for code in comp.codes),
            return_exceptions=True,
        )
        _raise_first_failure(results, industry, comp, "AIES", year)
        present = [r for r in results if r is not None and r.revenue_usd > 0]
        if len(present) == len(comp.codes):
            latest, latest_year = present, year
            break
        if present:
            missing = [code for code, r in zip(comp.codes, results)
                       if r is None or not r.revenue_usd > 0]
            logger.warning(
                "Census composite %r for %r: NAICS %s missing in AIES %d (suppressed or "
                "unpublished) — the whole composite steps back a year",
                comp.label, industry, ", ".join(missing), year,
            )
    if latest is None or latest_year is None:
        logger.warning(
            "Census composite %r for %r: no AIES year in which every code (%s) has revenue — "
            "industry TAM falls through to FRED",
            comp.label, industry, ", ".join(comp.codes),
        )
        return None

    base_results = await asyncio.gather(
        *(client.get_ecn_revenue(code) for code in comp.codes),
        return_exceptions=True,
    )
    _raise_first_failure(base_results, industry, comp, "ECN", 2017)
    base_present = [r for r in base_results if r is not None and r.revenue_usd > 0]
    baseline_usd: Optional[float] = None
    baseline_year: Optional[int] = None
    if len(base_present) == len(comp.codes) and len({r.year for r in base_present}) == 1:
        baseline_usd = sum(r.revenue_usd for r in base_present)
        baseline_year = base_present[0].year
    else:
        missing = [code for code, r in zip(comp.codes, base_results)
                   if r is None or not r.revenue_usd > 0]
        logger.warning(
            "Census composite %r for %r: no complete Economic Census baseline (missing NAICS %s) "
            "— TAM shown without a growth rate",
            comp.label, industry, ", ".join(missing) or "none (mixed years)",
        )

    return CensusRevenueSnapshot(
        revenue_usd=sum(r.revenue_usd for r in latest),
        year=latest_year,
        naics="+".join(comp.codes),   # informational, never sent back to Census
        naics_label=comp.label,
        revenue_usd_baseline=baseline_usd,
        baseline_year=baseline_year,
    )


async def _try_census_tam(industry: str) -> Optional[IndustryTAM]:
    """Census branch: NAICS-precise revenue from AIES (latest) +
    Economic Census 2017 (baseline for CAGR).

    Returns None when the industry isn't mapped, the Census API key
    isn't set (Census requires a key for every request, even free tier),
    or AIES doesn't cover the NAICS code (e.g., oil & gas extraction).
    Lets `CensusUnavailableException` (a transient Census failure)
    propagate: falling through to a broader FRED series on a blip would
    swap an industry-grain figure for a hidden sector one.
    """
    mapping = INDUSTRY_TO_CENSUS.get(industry)
    if not mapping:
        return None

    client = get_census_client()
    if not client.is_configured:
        return None

    if isinstance(mapping, CensusComposite):
        snapshot = await _census_composite_snapshot(client, industry, mapping)
        naics, naics_label = ", ".join(mapping.codes), mapping.label
    else:
        snapshot = await client.get_industry_revenue_snapshot(mapping)
        naics, naics_label = mapping, (snapshot.naics_label if snapshot else "")
    if snapshot is None:
        return None

    # Average yearly growth over `years_apart` years (7 = 2024 AIES - 2017 ECN as
    # of 2026-10). With no usable baseline (missing, suppressed, or not older) we
    # still emit the TAM, without a growth rate — WARNING, never a fake 0.
    cagr_decimal: Optional[float] = None
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
    else:
        logger.warning(
            "Census TAM for %r (NAICS %s): no usable 2017 Economic Census baseline — "
            "TAM shown without a growth rate", industry, snapshot.naics,
        )

    current_b = snapshot.revenue_usd / 1e9
    # Flat (never 0) without a rate: iOS builds up to 1.1 render a 0 future as "$X → $0B".
    future_b = _project_5y(current_b, cagr_decimal or 0.0)

    return IndustryTAM(
        current_tam=round(current_b, 1),
        future_tam=round(future_b, 1),
        current_year=str(snapshot.year),
        future_year=str(snapshot.year + 5),
        source_label=_census_source_label(naics, naics_label),
        cagr_5y_pct=_pct_1dp(cagr_decimal),
        cagr_start_year=snapshot.baseline_year if cagr_decimal is not None else None,
    )
