"""
FRED (Federal Reserve Economic Data) client — free tier.

Used by the Macro & Geopolitical module to ground inflation / rate /
yield-curve risk factors in authoritative source data instead of AI
synthesis. Free-tier credentials are documented at
https://fred.stlouisfed.org/docs/api/api_key.html and supplied via
the `FRED_API_KEY` setting.

Per-process in-memory cache (6h TTL) keyed by `(series_id, op)`:
  - FRED series update daily/monthly, so a 6h cache is plenty.
  - Worker restarts re-fetch — fine; we're under the 120 req/min limit.
  - No Supabase round-trip per request.

Failure modes:
  - Missing API key → returns empty result, logged once per series.
  - HTTP / timeout error → logged, returns empty result. Callers gate
    risk-factor emission on a non-None payload, so a FRED outage just
    means the macro module shows fewer cards instead of crashing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# Per-process in-memory cache. Key = (series_id, op_name); value =
# (timestamp, payload).
_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours


def _cache_get(key: Tuple[str, str]) -> Any:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        del _CACHE[key]
        return None
    return value


def _cache_set(key: Tuple[str, str], value: Any) -> None:
    _CACHE[key] = (time.time(), value)


@dataclass
class FREDObservation:
    """One observation row from a FRED series."""
    date: str       # ISO YYYY-MM-DD
    value: float    # numeric value


@dataclass
class FREDSeriesSnapshot:
    """Latest value + computed change windows for a FRED series.

    Built by `FREDClient.get_snapshot`. `yoy_pct` is None when fewer
    than 12 monthly observations are available (newer series); callers
    treat that as "no signal" rather than 0.
    """
    series_id: str
    latest: float
    as_of: str
    yoy_pct: Optional[float] = None
    change_6mo_pct: Optional[float] = None  # absolute pp change for rate series
    change_6mo_relative_pct: Optional[float] = None  # % change


class FREDClient:
    """Async client for the FRED REST API.

    Construct once per process; the in-memory cache is module-level so
    multiple instances share state. Calls fan out to httpx and respect
    the global `HTTP_TIMEOUT_SECONDS` setting.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key if api_key is not None else settings.FRED_API_KEY
        self.base_url = settings.FRED_BASE_URL.rstrip("/")
        self._timeout = settings.HTTP_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        """True when an API key is present so callers can short-circuit."""
        return bool(self.api_key)

    async def get_observations(
        self, series_id: str, *, limit: int = 13,
    ) -> List[FREDObservation]:
        """Fetch the most-recent `limit` observations for a series.

        Sort order is newest-first (so index 0 is the latest). Returns
        empty list when the API key is missing, the request fails, or
        the API returns no observations.
        """
        if not self.is_configured:
            return []
        cache_key = (series_id, f"obs:{limit}")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/series/observations", params=params,
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(
                f"FRED observations failed for {series_id}: "
                f"{type(e).__name__}: {e}"
            )
            _cache_set(cache_key, [])
            return []

        out: List[FREDObservation] = []
        for row in payload.get("observations", []):
            raw = row.get("value")
            # FRED returns "." for missing observations on holidays etc.
            if raw is None or raw == "." or raw == "":
                continue
            try:
                out.append(FREDObservation(
                    date=row.get("date") or "",
                    value=float(raw),
                ))
            except (TypeError, ValueError):
                continue

        _cache_set(cache_key, out)
        return out

    async def get_snapshot(self, series_id: str) -> Optional[FREDSeriesSnapshot]:
        """Latest value + 1Y / 6M change for a FRED series.

        Returns None when the latest observation is missing. Change
        windows are best-effort — when fewer observations are available
        the corresponding field is None.
        """
        cache_key = (series_id, "snapshot")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        # Fetch enough rows for a 1Y window on monthly data (13 obs)
        # plus a buffer so the 6M point (obs[6]) lands on real data.
        obs = await self.get_observations(series_id, limit=14)
        if not obs:
            _cache_set(cache_key, None)
            return None
        latest = obs[0]

        def _at(idx: int) -> Optional[float]:
            return obs[idx].value if idx < len(obs) else None

        # YoY = (latest - obs[12]) / obs[12]; use None when we don't
        # have 13 observations yet.
        yoy_pct: Optional[float] = None
        prior_year = _at(12)
        if prior_year is not None and prior_year > 0:
            yoy_pct = (latest.value - prior_year) / prior_year * 100

        # 6-month delta. For rate-of-change series (CPI level → YoY %),
        # we expose both absolute (pp) and relative (%) so callers can
        # choose the right one for their threshold.
        change_6mo_abs: Optional[float] = None
        change_6mo_rel: Optional[float] = None
        prior_6mo = _at(6)
        if prior_6mo is not None:
            change_6mo_abs = latest.value - prior_6mo
            if prior_6mo != 0:
                change_6mo_rel = (latest.value - prior_6mo) / abs(prior_6mo) * 100

        snap = FREDSeriesSnapshot(
            series_id=series_id,
            latest=latest.value,
            as_of=latest.date,
            yoy_pct=yoy_pct,
            change_6mo_pct=change_6mo_abs,
            change_6mo_relative_pct=change_6mo_rel,
        )
        _cache_set(cache_key, snap)
        return snap


# ── Convenience: shared singleton ────────────────────────────────────


_client: Optional[FREDClient] = None


def get_fred_client() -> FREDClient:
    """Return the process-wide FRED client singleton.

    Safe to call repeatedly — initialization is cheap and the module
    cache is shared across instances anyway.
    """
    global _client
    if _client is None:
        _client = FREDClient()
    return _client


# ── Series IDs the Macro module consumes ─────────────────────────────


# Short-list of FRED series the ticker-report Macro module pulls. Each
# is mapped to a deterministic risk factor in `ticker_report_data_collector`.
MACRO_SERIES: Dict[str, Dict[str, str]] = {
    "CPIAUCSL": {
        "label": "Consumer Price Index (CPI)",
        "category": "inflation",
        "unit": "YoY %",
    },
    "FEDFUNDS": {
        "label": "Federal Funds Rate",
        "category": "interest_rates",
        "unit": "%",
    },
    "DGS10": {
        "label": "10-Year Treasury Yield",
        "category": "interest_rates",
        "unit": "%",
    },
    "T10Y2Y": {
        "label": "10Y-2Y Treasury Spread",
        "category": "interest_rates",
        "unit": "%",
    },
}
