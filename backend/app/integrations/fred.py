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
  - Missing API key → returns empty result, WARNED once per process (see
    `_warn_unconfigured_once`). Silence here is what let a production deploy with no
    FRED_API_KEY report a benign macro backdrop it had never measured.
  - HTTP / timeout error → logged, returns empty result. Callers gate
    risk-factor emission on a non-None payload, so a FRED outage just
    means the macro module shows fewer cards instead of crashing.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# One-shot unconfigured warning, per process.
#
# Every `if not self.is_configured: return []` used to be SILENT, while this module's own
# docstring promised "logged once per series". That was not a small inaccuracy: with
# FRED_API_KEY unset in production, the macro engine lost its entire authoritative tier (CPI,
# Core PCE, breakevens, Fed Funds, DGS10, T10Y2Y, UNRATE, ICSA, HY OAS), the factor set came
# back empty on a calm tape, and the report printed "Benign macro backdrop — no indicators
# tripping risk thresholds." A confident all-clear derived from NO DATA, with nothing in the
# logs to say so. CLAUDE.md: an intentionally non-fatal degradation must still say so.
_warned_unconfigured: set[str] = set()


def _warn_unconfigured_once(name: str, env_var: str) -> None:
    if name in _warned_unconfigured:
        return
    _warned_unconfigured.add(name)
    logger.warning(
        "%s IS NOT CONFIGURED (%s unset) — every %s read will return empty for the life of "
        "this process. Downstream: macro risk factors are suppressed, so the macro module "
        "reports no threats rather than unknown ones.",
        name, env_var, name,
    )



# Per-process in-memory cache. Key = (series_id, op_name); value =
# (timestamp, payload).
_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours

#: A FAILED fetch is remembered only this long — a transient timeout is not "FRED has no
#: observations for six hours". WTI and Henry Hub are FRED-sourced end to end, so one read
#: timeout used to answer `commodity core has no usable price for CL` (a 503 telling the
#: user to retry) on EVERY retry for the whole 6-hour window: `commodity_service` refuses
#: to cache its own empty results for exactly this reason, and the memo one layer down
#: defeated that. Long enough to be a herd guard, short enough to self-heal.
_FAILURE_TTL_SECONDS = 120


def _cache_get(key: Tuple[str, str]) -> Any:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        del _CACHE[key]
        return None
    return value


#: When a fetch FAILED, keyed like the value cache. Kept separate from `_CACHE` on
#: purpose: a failure and a real empty answer must not be the same entry, and a later
#: success must clear it. Mirrors `universe_data`'s `_failed_at` / `_FAILURE_RETRY_SECONDS`.
_FAILED_AT: Dict[Tuple[str, str], float] = {}


def _failed_recently(key: Tuple[str, str]) -> bool:
    ts = _FAILED_AT.get(key)
    if ts is None:
        return False
    if time.time() - ts > _FAILURE_TTL_SECONDS:
        del _FAILED_AT[key]
        return False
    return True


def _mark_failed(key: Tuple[str, str]) -> None:
    _FAILED_AT[key] = time.time()


def _cache_set(key: Tuple[str, str], value: Any) -> None:
    _CACHE[key] = (time.time(), value)
    _FAILED_AT.pop(key, None)   # a good answer retires the failure memo


@dataclass
class FREDObservation:
    """One observation row from a FRED series."""
    date: str       # ISO YYYY-MM-DD
    value: float    # numeric value


@dataclass
class FREDSeriesSnapshot:
    """Latest value + computed change windows for a FRED series.

    Built by `FREDClient.get_snapshot`. Both windows are counted BY DATE from the latest
    observation (`_dated_rows` / `_months_before`): `yoy_pct` against the observation dated
    exactly twelve months earlier, the 6-month fields against the one dated exactly six months
    earlier. Either is None when that observation is absent — a month FRED never published, a
    short series, or a daily / weekly series (only the newest 14 observations are read, so no
    row is that far back). Callers treat None as "no signal", never 0.
    """
    series_id: str
    latest: float
    as_of: str
    yoy_pct: Optional[float] = None
    change_6mo_pct: Optional[float] = None  # absolute pp change for rate series
    change_6mo_relative_pct: Optional[float] = None  # % change


def _dated_rows(observations: Any) -> Dict[date, float]:
    """``{date: value}`` from FRED observations, whatever their order. Pure.

    A row whose date does not parse, whose value is not a finite number (or is a bool) is
    skipped. A date carrying two DIFFERENT values is dropped as ambiguous — never resolved by
    whichever row came first; the same value twice is one row. The same rules as the chat's
    macro leg (`chat_market_tools._yoy_by_date`), so a chat answer and a report grade one CPI
    reading alike. Negative values are kept (the 10Y-2Y spread goes negative); a percentage
    is never taken from a non-positive base (see `get_snapshot`)."""
    rows: Dict[date, float] = {}
    ambiguous: set = set()
    for ob in observations if isinstance(observations, (list, tuple)) else []:
        raw_day = getattr(ob, "date", None)
        raw_val = getattr(ob, "value", None)
        if not isinstance(raw_day, str) or isinstance(raw_val, bool) or raw_val is None:
            continue
        try:
            day = date.fromisoformat(raw_day.strip()[:10])
            val = float(raw_val)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue
        if day in rows and rows[day] != val:
            ambiguous.add(day)
        rows.setdefault(day, val)
    for day in ambiguous:
        rows.pop(day, None)
    return rows


def _months_before(day: date, months: int) -> Optional[date]:
    """The same day-of-month `months` calendar months earlier, or None when that day does not
    exist (31 August → "31 February"; 29 February → a non-leap year). FRED dates a monthly
    observation on the 1st, so a monthly series always lands. Pure."""
    index = day.year * 12 + (day.month - 1) - months
    year, month0 = divmod(index, 12)
    try:
        return date(year, month0 + 1, day.day)
    except ValueError:
        return None


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
            _warn_unconfigured_once("FRED", "FRED_API_KEY")
            return []
        cache_key = (series_id, f"obs:{limit}")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        if _failed_recently(cache_key):
            # A recent FAILURE, not an answer — see `_mark_failed` below. Serving [] here
            # is the herd guard; the short window is what lets the next caller retry.
            return []

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
            # A FAILURE, memoised for `_FAILURE_TTL_SECONDS`, NOT the 6-hour success TTL.
            # This used to go into the normal cache, so one `httpx.ReadTimeout` became
            # "FRED has no observations for this series" for six hours — and because
            # `_fred_quote` turns an empty series into `{}`, `get_commodity_core` raised
            # `FMPUnavailableException` → a 503 that tells the user to retry, on a path
            # where every retry short-circuited on the memo without touching FRED. Both
            # the WTI and Henry Hub screens went down for up to 6 h after one blip.
            _mark_failed(cache_key)
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

        Returns None when there is no dated, finite observation. The change windows are
        counted by DATE from the latest observation (see `FREDSeriesSnapshot`); a window
        whose anniversary observation is absent is None, never a longer or shorter window.
        """
        cache_key = (series_id, "snapshot")
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        # 14 rows of a monthly series span at least 13 months, so its 12-month anniversary is
        # in the window even when a month or two was never published (the rows FRED marks
        # '.' are dropped before they reach us).
        # ⚠️ ONE definition of the limit, because the failure-memo key below is derived
        # from it. This read `limit=14` here and re-spelled the key as the literal
        # `(series_id, "obs:14")` five lines down: correct today, and silently vacuous the
        # moment anyone widens the window — `_failed_recently` would return False, the
        # `_cache_set(cache_key, None)` would run, and one FRED timeout would again mean
        # "this series has no observations" for six hours, which is what took the WTI and
        # Henry Hub screens down.
        obs_limit = 14
        obs = await self.get_observations(series_id, limit=obs_limit)
        if not obs:
            # Only memoise "this series genuinely has no observations". When the
            # observations read FAILED, `_failed_recently` is what holds the herd back —
            # caching None here for 6 h would re-create the bug one level up.
            if _failed_recently((series_id, f"obs:{obs_limit}")):
                return None
            _cache_set(cache_key, None)
            return None
        # Every window is counted BY DATE, never by position. The old `obs[12]` / `obs[6]` were
        # the 13th / 7th row of the newest 14 AFTER `get_observations` had dropped every
        # missing ('.') observation — so a month FRED never published (October 2025 CPI, the
        # shutdown lapse) silently turned "year-on-year" into a 13-month change for a whole
        # year, and the report's macro module graded inflation on it. A missing anniversary
        # row is now no reading at all (None), which the macro module already treats as "no
        # signal". The newest row is the latest DATE, whatever order the API answered in.
        rows = _dated_rows(obs)
        if not rows:
            # Rows came back but none is a dated, finite observation: no snapshot — and not
            # memoised, since this is a malformed answer rather than "no observations".
            logger.warning(
                "FRED snapshot for %s: %d observation(s) but none dated and finite — no "
                "snapshot", series_id, len(obs),
            )
            return None
        latest_day = max(rows)
        latest_value = rows[latest_day]

        # YoY (%) against the observation dated exactly twelve months earlier. A non-positive
        # base is not a price-index level, so no percentage is taken from it.
        yoy_pct: Optional[float] = None
        year_ago = _months_before(latest_day, 12)
        prior_year = rows.get(year_ago) if year_ago is not None else None
        if prior_year is not None and prior_year > 0:
            yoy_pct = (latest_value - prior_year) / prior_year * 100

        # 6-month delta against the observation dated exactly six months earlier. For
        # rate-of-change series (CPI level → YoY %) we expose both absolute (pp) and relative
        # (%) so callers can choose the right one for their threshold.
        change_6mo_abs: Optional[float] = None
        change_6mo_rel: Optional[float] = None
        half_year_ago = _months_before(latest_day, 6)
        prior_6mo = rows.get(half_year_ago) if half_year_ago is not None else None
        if prior_6mo is not None:
            change_6mo_abs = latest_value - prior_6mo
            if prior_6mo != 0:
                change_6mo_rel = (latest_value - prior_6mo) / abs(prior_6mo) * 100

        snap = FREDSeriesSnapshot(
            series_id=series_id,
            latest=latest_value,
            as_of=latest_day.isoformat(),
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
#
# Cadence note: get_snapshot returns the latest observation + the 6-month and 12-month
# deltas, counted BY DATE (2026-10-09; they used to be observation indexes 6 and 12, which a
# month FRED never published turned into 7- and 13-month windows). Only the newest 14
# observations are read, so a weekly (ICSA) or daily (T5YIE, DGS10, T10Y2Y, BAMLH0A0HYM2)
# series has no row six or twelve months back and its windowed fields are None. Callers in
# `ticker_report_data_collector` read only `latest` for those series; the windowed fields are
# read for the monthly CPI / core PCE (year-on-year) and FEDFUNDS / UNRATE (6-month).
MACRO_SERIES: Dict[str, Dict[str, str]] = {
    "CPIAUCSL": {
        "label": "Consumer Price Index (CPI)",
        "category": "inflation",
        "unit": "YoY %",
    },
    "PCEPILFE": {
        "label": "Core PCE Price Index",
        "category": "inflation",
        "unit": "YoY %",
    },
    "T5YIE": {
        "label": "5-Year Breakeven Inflation",
        "category": "inflation",
        "unit": "%",
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
    "UNRATE": {
        "label": "Unemployment Rate",
        "category": "recession",
        "unit": "%",
    },
    "ICSA": {
        "label": "Initial Jobless Claims (4-wk avg)",
        "category": "recession",
        "unit": "k",
    },
    "BAMLH0A0HYM2": {
        "label": "ICE BofA US High-Yield Spread",
        "category": "credit",
        "unit": "%",
    },
}
