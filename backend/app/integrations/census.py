"""US Census Bureau Data API client.

Used by `industry_tam_service` to fetch NAICS-precise industry revenue
for the ticker-report Moat section. Census API requires a key for every
request (the documented "free tier" still needs registration); sign up
at https://api.census.gov/data/key_signup.html and put the key in
`CENSUS_API_KEY`.

When the key is missing, `is_configured` returns False and every call
short-circuits to None — the caller (industry_tam_service) then falls
through to FRED.

Coverage:
  - SAS (Service Annual Survey) — services-providing sectors (NAICS 51,
    54, 55, 56, 61, 62, 71, 72, 81). Used for software publishers,
    IT services, restaurants, etc.
  - ASM (Annual Survey of Manufactures) — NAICS 31-33 manufacturers.
    Used for pharma, semiconductors, medical devices, autos.
  - ARTS (Annual Retail Trade Survey) — NAICS 44-45 retail. Used for
    internet retail, specialty retail, etc.

Note: SAS transitioned to AIES (Annual Integrated Economic Survey) in
March 2024 for new collection, but the legacy SAS endpoints still serve
historical data through ~2022. If/when AIES exposes a stable API, add
it as a survey variant here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# In-memory cache. Census data updates annually, so 24h is more than
# enough — the cache key includes survey + naics + year so different
# combos coexist.
_CACHE_TTL_SECONDS = 60 * 60 * 24
_cache: Dict[Tuple[str, str, int], Tuple[float, Any]] = {}


def _cache_get(key: Tuple[str, str, int]) -> Any:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at < datetime.now(timezone.utc).timestamp():
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: Tuple[str, str, int], value: Any) -> None:
    expires_at = datetime.now(timezone.utc).timestamp() + _CACHE_TTL_SECONDS
    _cache[key] = (expires_at, value)


@dataclass
class CensusRevenuePoint:
    """Single-year industry revenue from a Census survey.

    `revenue_usd` is in raw USD (not thousands / millions). `year` is the
    reference year of the survey. `naics_label` is the human-readable
    industry name Census returns alongside the code.
    """
    revenue_usd: float
    year: int
    naics: str
    naics_label: str


@dataclass
class CensusRevenueSnapshot:
    """Latest revenue + 5y-ago revenue, used to derive a 5y CAGR.

    `revenue_usd_5y_ago` is None when the prior year wasn't available
    (e.g., a NAICS code that's only been published for the last 2 years).
    The CAGR consumer must handle the None case.
    """
    revenue_usd: float
    year: int
    naics: str
    naics_label: str
    revenue_usd_5y_ago: Optional[float] = None


# Census API year recency lag. Annual surveys publish ~12-18 months
# after the reference year, so we start probing at `current_year - 2`
# and walk backward. Most surveys land in this 2-year window once they
# do publish; failures usually mean the NAICS code isn't in that survey.
_PROBE_YEAR_OFFSET = 2
_PROBE_YEAR_DEPTH = 4   # try 4 candidate years before giving up


class CensusClient:
    """Async client for the Census Data API.

    Census exposes one dataset per (year, survey) pair, e.g.
    `https://api.census.gov/data/2022/services/sas`. The `get=` parameter
    lists the variables to return (`RCPTOT` = total receipts/sales for
    SAS / ARTS, `VS` = value of shipments for ASM). Filtering on
    `NAICS<year>=<code>` narrows to one industry.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key if api_key is not None else settings.CENSUS_API_KEY
        self.base_url = settings.CENSUS_BASE_URL.rstrip("/")
        self._timeout = settings.HTTP_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _survey_path(self, survey: str) -> str:
        """Map our shorthand survey name to the Census URL fragment.
        Returns "" for unknown surveys (caller short-circuits)."""
        return {
            "sas": "services/sas",
            "arts": "services/arts",
            "asm": "asm",
        }.get(survey.lower(), "")

    def _revenue_variable(self, survey: str) -> str:
        """Which Census variable holds annual revenue for the survey."""
        return {
            "sas": "RCPTOT",
            "arts": "RCPTOT",
            "asm": "VS",
        }.get(survey.lower(), "RCPTOT")

    def _naics_filter_key(self, year: int) -> str:
        """The NAICS variable name carries the vintage of the NAICS code
        system the survey used. Census switched between NAICS 2012 and
        2017 around 2018, and a NAICS 2022 vintage exists for newer
        surveys. The filter key matches whichever vintage Census
        published for that survey year."""
        if year >= 2022:
            return "NAICS2017"
        if year >= 2017:
            return "NAICS2017"
        return "NAICS2012"

    async def get_industry_revenue(
        self, survey: str, naics: str, year: int,
    ) -> Optional[CensusRevenuePoint]:
        """Single-year revenue point from the given survey.

        Returns None when:
          - API key isn't configured
          - The survey name isn't recognized
          - HTTP error (404 = year not published; 4xx/5xx logged as warning)
          - Response has no data row for the NAICS code
        """
        if not self.is_configured:
            return None
        path = self._survey_path(survey)
        if not path:
            logger.debug(f"Census: unknown survey '{survey}'")
            return None

        cache_key = (survey.lower(), str(naics), int(year))
        cached = _cache_get(cache_key)
        if cached is not None:
            # `cached` can legitimately be `_MISS_SENTINEL` to memoize
            # known-empty combos and avoid re-probing every request.
            return cached if cached is not _MISS_SENTINEL else None

        revenue_var = self._revenue_variable(survey)
        naics_key = self._naics_filter_key(year)
        label_var = f"{naics_key}_LABEL"
        url = f"{self.base_url}/{year}/{path}"
        params = {
            "get": f"{revenue_var},{label_var}",
            naics_key: str(naics),
            "key": self.api_key,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    # The (survey, year) combo just isn't published yet.
                    _cache_set(cache_key, _MISS_SENTINEL)
                    return None
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(
                f"Census fetch failed for {survey}/{naics}/{year}: "
                f"{type(e).__name__}: {e}"
            )
            _cache_set(cache_key, _MISS_SENTINEL)
            return None

        # Census API returns a 2D array: header row + N data rows.
        # Header: [revenue_var, label_var, naics_filter_key]
        # Data: ["12345", "Industry Name", "5112"]
        if not isinstance(payload, list) or len(payload) < 2:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None

        header = payload[0]
        row = payload[1]
        if not isinstance(header, list) or not isinstance(row, list):
            _cache_set(cache_key, _MISS_SENTINEL)
            return None

        try:
            revenue_idx = header.index(revenue_var)
            label_idx = header.index(label_var)
        except ValueError:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None

        raw_revenue = row[revenue_idx]
        if raw_revenue in (None, "", "D", "S", "N", "X"):
            # Census suppression flags: "D" disclosure, "S" sampling
            # variability, "N" not available, "X" not applicable.
            _cache_set(cache_key, _MISS_SENTINEL)
            return None

        try:
            revenue_usd = float(raw_revenue) * 1000.0  # API returns $K
        except (TypeError, ValueError):
            _cache_set(cache_key, _MISS_SENTINEL)
            return None

        point = CensusRevenuePoint(
            revenue_usd=revenue_usd,
            year=year,
            naics=str(naics),
            naics_label=str(row[label_idx]) if label_idx < len(row) else "",
        )
        _cache_set(cache_key, point)
        return point

    async def get_industry_revenue_snapshot(
        self, survey: str, naics: str,
    ) -> Optional[CensusRevenueSnapshot]:
        """Latest available revenue + same-NAICS revenue from 5y earlier.

        Probes years descending from `current_year - 2` until one
        returns data (Census publishes annual surveys with a ~12-18
        month lag). When found, ALSO probes `latest_year - 5` for the
        CAGR baseline.

        Returns None when no recent year produces a value.
        """
        if not self.is_configured:
            return None

        current_year = datetime.now(timezone.utc).year
        latest: Optional[CensusRevenuePoint] = None
        for offset in range(_PROBE_YEAR_OFFSET, _PROBE_YEAR_OFFSET + _PROBE_YEAR_DEPTH):
            candidate = current_year - offset
            point = await self.get_industry_revenue(survey, naics, candidate)
            if point is not None and point.revenue_usd > 0:
                latest = point
                break

        if latest is None:
            return None

        prior = await self.get_industry_revenue(
            survey, naics, latest.year - 5,
        )
        return CensusRevenueSnapshot(
            revenue_usd=latest.revenue_usd,
            year=latest.year,
            naics=latest.naics,
            naics_label=latest.naics_label,
            revenue_usd_5y_ago=(
                prior.revenue_usd if prior and prior.revenue_usd > 0 else None
            ),
        )


# Sentinel for memoizing known-empty cache hits (so we don't re-probe).
_MISS_SENTINEL = object()


_singleton: Optional[CensusClient] = None


def get_census_client() -> CensusClient:
    """Module-level singleton, lazy."""
    global _singleton
    if _singleton is None:
        _singleton = CensusClient()
    return _singleton
