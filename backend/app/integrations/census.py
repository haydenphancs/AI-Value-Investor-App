"""US Census Bureau Data API client — NAICS-precise industry revenue.

Used by `industry_tam_service` to fetch the most precise public estimate
of an industry's annual revenue (e.g. NAICS 5112 Software Publishers
≈ $526B for 2023, vs. FRED's broader Information sector at ~$1.7T).

The Census API requires a key for every request — sign up free at
https://api.census.gov/data/key_signup.html and put it in
`CENSUS_API_KEY`. When the key is missing, `is_configured` returns False
and every call short-circuits to None so the caller falls through to FRED.

Two endpoints used:

  1. **AIES (Annual Integrated Economic Survey)** — the modern annual
     survey that replaced SAS in March 2024. Time-series dataset at
     `/data/timeseries/aies/basic`, covers most service / manufacturing /
     retail NAICS codes. Has 2023 published as of 2026-05; newer years
     will appear ~Q4 each year. Variable: `RCPT_TOT_VAL` ("Sales, value
     of shipments, or revenue" in $1,000s).

  2. **Economic Census (ecnbasic)** — the every-5-years comprehensive
     industry census, used as the CAGR baseline. 2017 has full coverage
     under NAICS 2017 codes. 2022 also exists but uses NAICS 2022 codes
     (different vintage — software publishers moved from 5112 → 5132,
     etc.) so we'd need a NAICS-vintage crosswalk; the 2017→2023 window
     gives a usable 6-year CAGR without it.

Both endpoints accept NAICS 2017 codes and return revenue in $1,000s.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# In-memory cache. Census data updates annually so 24h is plenty; the
# cache key includes endpoint + naics + year so combinations coexist.
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
    """Single-year industry revenue from a Census endpoint.

    `revenue_usd` is in raw USD (already converted from the API's $1,000
    unit). `year` is the survey reference year. `naics_label` is the
    industry name when available, empty string otherwise.
    """
    revenue_usd: float
    year: int
    naics: str
    naics_label: str = ""


@dataclass
class CensusRevenueSnapshot:
    """Latest revenue + earlier-year revenue for CAGR computation.

    `revenue_usd_baseline` and `baseline_year` come from the 2017
    Economic Census (or the closest older snapshot we have). `years_apart`
    is what the caller divides into the log ratio to get an annualized
    rate — usually 6 (latest=2023, baseline=2017).

    `revenue_usd_baseline` is None when the baseline call failed — caller
    must skip CAGR and only show TAM in that case.
    """
    revenue_usd: float
    year: int
    naics: str
    naics_label: str
    revenue_usd_baseline: Optional[float] = None
    baseline_year: Optional[int] = None

    @property
    def years_apart(self) -> Optional[int]:
        if self.baseline_year is None:
            return None
        return self.year - self.baseline_year


# AIES probes most-recent year first. AIES publishes data ~12-18 months
# after the reference year, so probing back 4 years covers the lag plus
# a small buffer.
_AIES_PROBE_YEARS = 4
# Economic Census baseline year. 2017 uses NAICS 2017 codes which match
# what AIES uses, so no NAICS-vintage crosswalk needed.
_ECN_BASELINE_YEAR = 2017


class CensusClient:
    """Async client for the Census Data API."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key if api_key is not None else settings.CENSUS_API_KEY
        self.base_url = settings.CENSUS_BASE_URL.rstrip("/")
        self._timeout = settings.HTTP_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _http_get_rows(
        self, url: str, params: Dict[str, str],
    ) -> Optional[list]:
        """One-row response parser. Returns the data row (list) when the
        Census API returns its standard 2D array [header, row], None on
        any error condition.

        Census returns 204 No Content when a query has no matching row
        (e.g., NAICS not covered by the survey for that year). We treat
        that as the same kind of "no data" as a 404.
        """
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code in (204, 404):
                    return None
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(
                f"Census fetch failed for {url} params={params}: "
                f"{type(e).__name__}: {e}"
            )
            return None
        if not isinstance(payload, list) or len(payload) < 2:
            return None
        header = payload[0]
        row = payload[1]
        if not isinstance(header, list) or not isinstance(row, list):
            return None
        return [header, row]

    async def _fetch_aies_year(
        self, naics: str, year: int,
    ) -> Optional[CensusRevenuePoint]:
        """One AIES-basic year. Returns None when the (year, NAICS) combo
        isn't published (Census 204) or any other fetch error."""
        cache_key = ("aies", str(naics), int(year))
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached if cached is not _MISS_SENTINEL else None

        url = f"{self.base_url}/timeseries/aies/basic"
        params = {
            "get": "RCPT_TOT_VAL,YEAR",
            "NAICS": str(naics),
            "for": "us:*",
            "YEAR": str(year),
            "key": self.api_key,
        }
        rows = await self._http_get_rows(url, params)
        if rows is None:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None
        header, row = rows
        try:
            rev_idx = header.index("RCPT_TOT_VAL")
        except ValueError:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None
        raw = row[rev_idx]
        revenue_usd = _parse_revenue(raw)
        if revenue_usd is None:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None
        point = CensusRevenuePoint(
            revenue_usd=revenue_usd,
            year=year,
            naics=str(naics),
        )
        _cache_set(cache_key, point)
        return point

    async def _fetch_ecnbasic(
        self, naics: str, year: int,
    ) -> Optional[CensusRevenuePoint]:
        """One Economic Census year. NAICS vintage matches the survey
        year — we only call this with year=2017 for now, which uses
        NAICS 2017 codes (same as AIES).
        """
        cache_key = ("ecnbasic", str(naics), int(year))
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached if cached is not _MISS_SENTINEL else None

        url = f"{self.base_url}/{year}/ecnbasic"
        params = {
            "get": f"RCPTOT,NAICS{year}_LABEL",
            f"NAICS{year}": str(naics),
            "for": "us:*",
            "key": self.api_key,
        }
        rows = await self._http_get_rows(url, params)
        if rows is None:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None
        header, row = rows
        try:
            rev_idx = header.index("RCPTOT")
            label_idx = header.index(f"NAICS{year}_LABEL")
        except ValueError:
            _cache_set(cache_key, _MISS_SENTINEL)
            return None
        raw = row[rev_idx]
        revenue_usd = _parse_revenue(raw)
        if revenue_usd is None:
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
        self, naics: str,
    ) -> Optional[CensusRevenueSnapshot]:
        """Latest AIES revenue + 2017 Economic Census baseline, in one shot.

        Returns None when AIES has no data for the NAICS (it's a survey
        of selected sectors — oil & gas extraction and a few others
        aren't covered, so the caller falls through to FRED).
        """
        if not self.is_configured:
            return None

        current_year = datetime.now(timezone.utc).year
        latest: Optional[CensusRevenuePoint] = None
        for offset in range(1, 1 + _AIES_PROBE_YEARS):
            candidate = current_year - offset
            point = await self._fetch_aies_year(naics, candidate)
            if point is not None and point.revenue_usd > 0:
                latest = point
                break
        if latest is None:
            return None

        baseline = await self._fetch_ecnbasic(naics, _ECN_BASELINE_YEAR)
        return CensusRevenueSnapshot(
            revenue_usd=latest.revenue_usd,
            year=latest.year,
            naics=latest.naics,
            naics_label=(baseline.naics_label if baseline else ""),
            revenue_usd_baseline=(
                baseline.revenue_usd if baseline and baseline.revenue_usd > 0 else None
            ),
            baseline_year=(baseline.year if baseline else None),
        )


def _parse_revenue(raw: Any) -> Optional[float]:
    """Convert a Census API revenue cell to raw USD. The API returns
    integer strings in $1,000s; numeric or empty strings get coerced or
    rejected. Suppression flags ("D" disclosure, "S" sampling, "N" not
    available, "X" not applicable) return None.
    """
    if raw in (None, "", "D", "S", "N", "X"):
        return None
    try:
        return float(raw) * 1000.0
    except (TypeError, ValueError):
        return None


# Sentinel for memoizing "this combo is empty" so we don't re-probe.
_MISS_SENTINEL = object()


_singleton: Optional[CensusClient] = None


def get_census_client() -> CensusClient:
    """Module-level singleton, lazy."""
    global _singleton
    if _singleton is None:
        _singleton = CensusClient()
    return _singleton
