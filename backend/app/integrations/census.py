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
     survey that replaced SAS in March 2024. One dataset per vintage at
     `/data/{year}/aiesbasic` (2023 and 2024 published as of 2026-10;
     newer years appear ~Q4 each year), queried with `NAICS2017=<code>`.
     Covers most service / manufacturing / retail NAICS codes. Variable:
     `RCPT_TOT_VAL` ("Sales, value of shipments, or revenue" in $1,000s).
     ⚠️ The earlier time-series dataset `/data/timeseries/aies/basic`
     (`NAICS=`) began answering 404 for every code and year, and because a
     404 also means "this vintage isn't published", the whole Census tier
     went silently dead — every mapped industry fell through to FRED. A
     snapshot that finds NO AIES year now logs a WARNING. A future vintage
     may switch its parameter to `NAICS2022`; Census then answers HTTP 400
     "unknown predicate variable", which is logged at ERROR and treated as an
     unpublished vintage, so the probe steps back to the previous year.

  2. **Economic Census (ecnbasic)** — the every-5-years comprehensive
     industry census, used as the CAGR baseline. 2017 has full coverage
     under NAICS 2017 codes. 2022 also exists but uses NAICS 2022 codes
     (different vintage — software publishers moved from 5112 → 5132,
     etc.) so we'd need a NAICS-vintage crosswalk; the 2017→2024 window
     gives a usable 7-year rate without it (Census's own spliced series,
     /timeseries/aies/historical, holds the same 2017 and 2024 values).

Both endpoints accept NAICS 2017 codes and return revenue in $1,000s.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# One-shot unconfigured warning, per process. Same reasoning as fred.py: a silent empty
# return here zeroes industry TAM, and the quarterly `industry_dossier` recompute then
# OVERWRITES good rows with a "no public data" placeholder.
_warned_unconfigured = False


def _warn_unconfigured_once() -> None:
    global _warned_unconfigured
    if _warned_unconfigured:
        return
    _warned_unconfigured = True
    logger.warning(
        "CENSUS IS NOT CONFIGURED (CENSUS_API_KEY unset) — every Census read returns empty "
        "for the life of this process. Downstream: industry TAM falls through to FRED and "
        "then to a zero-TAM placeholder.",
    )



class CensusException(Exception):
    """Base for Census client errors."""


class CensusUnavailableException(CensusException):
    """A transient failure — a transport error, a 5xx / 429 or an unparseable
    body — as opposed to "this (year, NAICS) is not published" (204 / 404),
    which stays an ordinary None. Raised, never cached: a transient error used
    to be memoized for 24 h as a miss, so one blip on the 2017 baseline call
    served a CAGR-less TAM for a day, and on the quarterly run could persist a
    broader-source row for a quarter. Callers in `industry_dossier_service`
    catch it (the read path retries after 5 min; the quarterly run keeps the
    stored row). It never reaches an endpoint."""


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
    rate — 7 as of 2026-10 (latest=2024, baseline=2017); it grows by one
    each time a newer AIES vintage is published.

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
        self, url: str, params: Dict[str, str], *, allow_schema_skip: bool = False,
    ) -> Optional[list]:
        """One-row response parser. Returns the data row (list) when the
        Census API returns its standard 2D array [header, row], None when the
        answer is "no such data".

        Census returns 204 No Content when a query has no matching row
        (e.g., NAICS not covered by the survey for that year) and 404 for a
        vintage that is not published yet — both mean "no data" → None.
        A transport error, a 5xx / 429 or an unparseable body raises
        `CensusUnavailableException` instead: a blip is not an answer.
        """
        # Never log the key: it is a query parameter, so it is also inside an
        # httpx error's URL — hence the status code, not str(e).
        safe_params = {k: v for k, v in params.items() if k != "key"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code in (204, 404):
                    return None
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "?"
            if allow_schema_skip and status == 400 and _is_schema_mismatch(e.response):
                # AIES vintage probe ONLY: the vintage exists but its schema does not
                # take this request — e.g. a future AIES that switches `NAICS2017=` to
                # `NAICS2022=` answers 400 "unknown predicate variable". That is "this
                # vintage is not usable", not a transient outage: answer "no data" so the
                # probe tries the year before (raising here would freeze every Census
                # industry on one year's schema change). Loud, because it needs a code
                # change to read the new vintage. The fixed 2017 Economic Census baseline
                # has no earlier year to step to, so there a 400 still raises (uncached):
                # a cached miss would make the quarterly recompute null every CAGR.
                logger.error(
                    "Census AIES %s params=%s: HTTP 400 unknown predicate/variable — this "
                    "vintage's schema does not match the request (a NAICS2022 switch?); "
                    "treated as unpublished, the probe steps back to the previous year",
                    url, safe_params,
                )
                return None
            logger.warning(f"Census fetch failed for {url} params={safe_params}: HTTP {status}")
            raise CensusUnavailableException(f"Census HTTP {status} for {url}") from None
        except httpx.HTTPError as e:
            logger.warning(f"Census fetch failed for {url} params={safe_params}: {type(e).__name__}")
            raise CensusUnavailableException(f"Census {type(e).__name__} for {url}") from None
        except ValueError as e:
            logger.warning(f"Census returned an unparseable body for {url} params={safe_params}: {e}")
            raise CensusUnavailableException(f"Census unparseable body for {url}") from None
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
        isn't published (Census 204/404) or the cell is suppressed; raises
        `CensusUnavailableException` (uncached) on a transient failure."""
        cache_key = ("aies", str(naics), int(year))
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached if cached is not _MISS_SENTINEL else None

        # One dataset per vintage (see the module docstring for why the old
        # `/timeseries/aies/basic` URL is gone). An unpublished vintage answers
        # 404, which `_http_get_rows` folds into "no data" → the caller probes
        # the year before.
        url = f"{self.base_url}/{int(year)}/aiesbasic"
        params = {
            "get": "RCPT_TOT_VAL",
            "NAICS2017": str(naics),
            "for": "us:*",
            "key": self.api_key,
        }
        rows = await self._http_get_rows(url, params, allow_schema_skip=True)
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

    def aies_probe_years(self) -> List[int]:
        """The AIES vintages to probe, newest first (last year back `_AIES_PROBE_YEARS`).
        The one place the clock is read, so the snapshot and the per-year reads agree."""
        current_year = datetime.now(timezone.utc).year
        return [current_year - offset for offset in range(1, 1 + _AIES_PROBE_YEARS)]

    async def get_aies_revenue(
        self, naics: str, year: int,
    ) -> Optional[CensusRevenuePoint]:
        """One AIES (year, NAICS) revenue point — thin, no aggregation. None when the
        cell is unpublished, suppressed or the client is unconfigured; raises
        `CensusUnavailableException` on a transient failure. Shares the snapshot's
        24 h cache entry ("aies", code, year)."""
        if not self.is_configured:
            _warn_unconfigured_once()
            return None
        return await self._fetch_aies_year(naics, year)

    async def get_ecn_revenue(
        self, naics: str, year: int = _ECN_BASELINE_YEAR,
    ) -> Optional[CensusRevenuePoint]:
        """One Economic Census (year, NAICS) revenue point, with its NAICS label — the
        CAGR baseline. Same semantics and cache entry as the snapshot's baseline read."""
        if not self.is_configured:
            _warn_unconfigured_once()
            return None
        return await self._fetch_ecnbasic(naics, year)

    async def get_industry_revenue_snapshot(
        self, naics: str,
    ) -> Optional[CensusRevenueSnapshot]:
        """Latest AIES revenue + 2017 Economic Census baseline, in one shot.

        Returns None when AIES has no data for the NAICS (it's a survey
        of selected sectors — oil & gas extraction and a few others
        aren't covered, so the caller falls through to FRED).
        """
        if not self.is_configured:
            _warn_unconfigured_once()
            return None

        probed = self.aies_probe_years()
        latest: Optional[CensusRevenuePoint] = None
        for candidate in probed:
            point = await self._fetch_aies_year(naics, candidate)
            if point is not None and point.revenue_usd > 0:
                latest = point
                break
        if latest is None:
            # Loud on purpose: the Census tier once died silently for every
            # industry (dataset moved, 404 read as "no data"). A mapped NAICS
            # with no AIES figure in the whole probe window is either a
            # coverage gap or a broken endpoint — both worth seeing. The 24 h
            # miss cache keeps this to ~one line per NAICS per day.
            logger.warning(
                f"Census AIES: no revenue for NAICS {naics} in any of the years "
                f"{probed} — industry TAM falls through to FRED. Check the "
                f"aiesbasic endpoint if this appears for every NAICS."
            )
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


def _is_schema_mismatch(response: Optional[httpx.Response]) -> bool:
    """A Census 400 whose body names an unknown predicate or variable — the request's
    shape does not exist in that vintage. Reads at most the first 500 characters and
    never raises (an unreadable body is not a mismatch)."""
    if response is None:
        return False
    try:
        body = (response.text or "")[:500].lower()
    except Exception:  # noqa: BLE001 — a body we cannot decode is just "not a mismatch"
        return False
    return "unknown predicate" in body or "unknown variable" in body


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
