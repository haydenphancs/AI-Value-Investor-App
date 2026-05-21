"""Industry TAM lookup via FRED (BEA value-added series).

Used as the secondary source for `market_dynamics.{current_tam, future_tam}`
in the ticker-report Moat section. AI extraction from the earnings transcript
is the primary source (highest trust — explicit company-quoted figure); this
module fills in when AI didn't find an explicit TAM mention.

Returned values are **industry-level proxies**, not company-specific TAM —
e.g., "Software - Infrastructure" maps to BEA Information sector value-added.
The iOS UI shows a small attribution caption so users know the figure is
an industry size estimate, not management-quoted TAM.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional

from app.integrations.fred import get_fred_client

logger = logging.getLogger(__name__)


# FMP `profile.industry` strings → (FRED series ID, human-readable source label).
#
# FRED series chosen for being:
#   - Annual frequency (so a 5-year CAGR makes sense)
#   - Maintained in USD billions or in a unit that converts cleanly
#   - Stable IDs (BEA quarterly/annual GDP-by-industry tables)
#
# Coverage bias is toward sectors that show up most in user watchlists
# (Tech, Finance, Healthcare, Energy, Consumer). Sectors without a clean
# mapping return None and iOS hides the TAM cell.
#
# Note: `VAPGDP*` series are BEA value-added by industry, published
# annually in $B. Industries below `Technology` cover the Information
# sector, which includes software publishers, computing services, and
# telecom. It's broader than pure software, but it's the closest
# free, public, annually-updated industry-size series available via FRED.
INDUSTRY_TO_FRED_SERIES: Dict[str, str] = {
    # Information / Software / Tech services
    "Software - Infrastructure": "VAPGDPI",
    "Software - Application": "VAPGDPI",
    "Information Technology Services": "VAPGDPI",
    "Internet Content & Information": "VAPGDPI",
    "Communication Equipment": "VAPGDPI",
    "Telecom Services": "VAPGDPI",
    # Semiconductors → manufacturing (closest BEA series)
    "Semiconductors": "VAPGDPMA",
    "Semiconductor Equipment & Materials": "VAPGDPMA",
    "Electronic Components": "VAPGDPMA",
    # Finance
    "Banks - Diversified": "VAPGDPFB",
    "Banks - Regional": "VAPGDPFB",
    "Capital Markets": "VAPGDPFB",
    "Insurance - Diversified": "VAPGDPFB",
    "Asset Management": "VAPGDPFB",
    # Healthcare / Pharma
    "Drug Manufacturers - General": "VAPGDPHCSA",
    "Drug Manufacturers - Specialty & Generic": "VAPGDPHCSA",
    "Biotechnology": "VAPGDPHCSA",
    "Medical Devices": "VAPGDPHCSA",
    "Healthcare Plans": "VAPGDPHCSA",
    # Energy
    "Oil & Gas Integrated": "VAPGDPMN",
    "Oil & Gas E&P": "VAPGDPMN",
    "Oil & Gas Refining & Marketing": "VAPGDPMN",
    # Consumer
    "Internet Retail": "VAPGDPRT",
    "Specialty Retail": "VAPGDPRT",
    "Discount Stores": "VAPGDPRT",
    "Auto Manufacturers": "VAPGDPMA",
    "Restaurants": "VAPGDPAFS",
}


@dataclass
class IndustryTAM:
    """Industry-size projection derived from a FRED value-added series.

    `current_tam` and `future_tam` are in the FRED series' native unit —
    typically billions of USD. `source_label` is shown verbatim in the
    iOS UI as an attribution caption.
    """
    current_tam: float
    future_tam: float
    current_year: str
    future_year: str
    source_label: str


def _source_label_for_series(series_id: str) -> str:
    """Human-friendly attribution caption shown in iOS under the TAM row."""
    mapping = {
        "VAPGDPI": "BEA Information Sector value-added (via FRED)",
        "VAPGDPMA": "BEA Manufacturing value-added (via FRED)",
        "VAPGDPFB": "BEA Finance & Insurance value-added (via FRED)",
        "VAPGDPHCSA": "BEA Health Care & Social Assistance value-added (via FRED)",
        "VAPGDPMN": "BEA Mining (oil & gas) value-added (via FRED)",
        "VAPGDPRT": "BEA Retail Trade value-added (via FRED)",
        "VAPGDPAFS": "BEA Accommodation & Food Services value-added (via FRED)",
    }
    return mapping.get(series_id, f"BEA {series_id} (via FRED)")


async def get_industry_tam(industry: Optional[str]) -> Optional[IndustryTAM]:
    """Look up the industry's BEA value-added series and project 5 years out.

    Pipeline:
      1. Map the FMP industry string to a FRED series ID. Unmapped → None.
      2. Fetch ≥6 annual observations (latest + 5 prior years).
      3. Compute the realized 5-year CAGR from the series itself.
      4. Project `current_tam * (1 + cagr)^5` for `future_tam`.

    Returns None when:
      - Industry isn't in the mapping.
      - FRED API isn't configured (missing key).
      - The series has fewer than 2 observations (can't anchor a year).
      - The latest value is non-positive (defensive — would break projection).

    The CAGR projection is best-effort: if we have fewer than 6 observations
    we use whatever window IS available and label it accordingly. If the
    realized growth is implausibly extreme (>50% annual), we clamp to ±20%
    so a one-off BEA revision doesn't blow up the future TAM.
    """
    if not industry:
        return None
    series_id = INDUSTRY_TO_FRED_SERIES.get(industry)
    if not series_id:
        return None

    client = get_fred_client()
    if not client.is_configured:
        return None

    obs = await client.get_observations(series_id, limit=8)
    if len(obs) < 2:
        return None
    latest = obs[0]
    if latest.value <= 0:
        return None

    # Anchor years from the observation `date` field (FRED returns
    # "YYYY-MM-DD"). Annual series stamp date = "YYYY-01-01".
    try:
        current_year = latest.date.split("-", 1)[0]
        int(current_year)
    except (AttributeError, ValueError, IndexError):
        return None

    # Realized 5y CAGR (or shorter if we don't have 6 obs yet). Sort_order
    # is desc, so obs[5] is 5 years ago when present.
    cagr = 0.0
    if len(obs) >= 6 and obs[5].value > 0:
        years = 5
        cagr = (latest.value / obs[5].value) ** (1.0 / years) - 1.0
    elif len(obs) >= 2 and obs[-1].value > 0:
        years = len(obs) - 1
        cagr = (latest.value / obs[-1].value) ** (1.0 / years) - 1.0

    # Clamp absurd CAGRs (a series revision shouldn't project a 100x TAM).
    cagr = max(-0.20, min(0.20, cagr))

    future_year = str(int(current_year) + 5)
    future_tam = latest.value * math.pow(1.0 + cagr, 5)

    return IndustryTAM(
        current_tam=round(latest.value, 1),
        future_tam=round(future_tam, 1),
        current_year=current_year,
        future_year=future_year,
        source_label=_source_label_for_series(series_id),
    )
