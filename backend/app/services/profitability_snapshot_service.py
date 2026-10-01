"""
Profitability Snapshot service — computes sector-relative profitability
ratings using Operating Margin, Net Margin, ROE, ROA compared against
pre-computed sector medians from the sector_benchmarks table.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import FMPNotEntitledException, get_fmp_client
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup, lookup_failed
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── Build status of the value each key serves ─────────────────────
# A degraded build is SERVED (Tier 1 for 5 min, and to every in-flight joiner) but never
# persisted. The report collector freezes what it receives into the close-aligned
# ticker_data_cache and a paid report — the card's `weighted_score` is the Profitability
# vital for every persona — so `get_profitability_snapshot_with_status` must report the
# status of the EXACT object handed out, on a Tier-1 hit and an in-flight join as much as on
# the build. Same role as growth_service's `_degraded_by_key`, but each entry holds
# `(value, degraded)` and a status is reported only for that very object (identity).
_degraded_by_key: Dict[str, Tuple[Any, List[str]]] = {}
# Larger than the Tier-1 cap: entries are written in lockstep with `_cache_set` (plus the
# never-cached all-absent build), so the memo can only lose an entry Tier 1 still holds
# after twice as many writes.
_DEGRADED_MAX_ENTRIES = 2 * _CACHE_MAX_ENTRIES
# Reported when the served object has no memo entry (evicted, or a value reached Tier 1 by a
# path that never noted it). Fail CLOSED: an unknown provenance must not be frozen.
_STATUS_UNKNOWN = "status_unknown"


def _note_degraded(key: str, value: Any, degraded: List[str]) -> None:
    """Record the degraded legs of ``value``, the object now served under ``key``.

    Call it on EVERY write of a value to Tier 1, and before resolving the in-flight future
    with it, so a joiner resuming after `set_result` reads the leader's status."""
    _degraded_by_key.pop(key, None)
    _degraded_by_key[key] = (value, list(degraded))
    if len(_degraded_by_key) > _DEGRADED_MAX_ENTRIES:
        for _old in list(_degraded_by_key.keys())[: len(_degraded_by_key) - _DEGRADED_MAX_ENTRIES]:
            _degraded_by_key.pop(_old, None)


def _degraded_of(key: str, value: Any) -> Optional[List[str]]:
    """The degraded legs noted for exactly ``value`` under ``key``; None when unknown."""
    entry = _degraded_by_key.get(key)
    if entry is None or entry[0] is not value:
        return None
    return list(entry[1])


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# Version stamped into this card's `snapshot_cache.response_json` (same key as the sibling
# snapshot cards on the shared table). A row without it, or with an older one, is a MISS and
# is rebuilt — the 24h tier must not outlive a change to the figures. Bump it whenever a
# stored value's computation changes.
# 2 (2026-09-30): the TTM margin fallback is guarded (`_ttm_margin_fallback` refuses a
#     non-positive TTM revenue and an exact-zero ratio — the sign-flipped "+113.6% net
#     margin, rated 5/5"), NaN/inf inputs are dropped, the latest Profit Power year is the
#     real latest year (a zero-revenue year is a gap, not skipped to an older one), and a
#     degraded Profit Power build marks the card degraded.
_SNAPSHOT_PAYLOAD_VERSION = 2
_VERSION_KEY = "_schema_v"

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _first_valid(*vals: Optional[float]) -> Optional[float]:
    """Return the first non-None value, or None if all are None."""
    for v in vals:
        if v is not None:
            return v
    return None


def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    # NaN / ±inf ("Infinity" strings, a computed overflow upstream) would be formatted
    # as "nan%" and scored — `_profitability_score(nan, …)` falls through to 1.
    return f if math.isfinite(f) else None


def _ttm_margin_fallback(
    ratios0: Dict[str, Any], ttm_key: str, legacy_key: str,
) -> Optional[float]:
    """A TTM ratios margin, as a percentage, ONLY when it can be a real margin.

    This fills a margin the latest Profit Power year left empty — which, since Profit
    Power keeps a zero/negative-revenue year as a gap, is exactly the no-revenue case.
    FMP's ``*ProfitMarginTTM`` is netIncome / revenue over the same trailing year, so:
      * revenue <= 0 sign-flips it (a $1.25B loss read +113.6% and scored 5/5) — so
        the TTM revenue (``revenuePerShareTTM``) must be present and positive;
      * zero revenue comes back as 0, which would print and score a fabricated
        "0.00%" — so an exact 0 is treated as absent.
    Otherwise the margin stays None: the row shows "—" and is not scored.
    """
    rev_ps = _safe_float(ratios0, "revenuePerShareTTM")
    if rev_ps is None or rev_ps <= 0:
        return None
    raw = _first_valid(_safe_float(ratios0, ttm_key), _safe_float(ratios0, legacy_key))
    if raw is None or raw == 0:
        return None
    return _to_pct(raw)


def _to_pct(val: Optional[float]) -> Optional[float]:
    """Convert decimal to percentage if needed. FMP ratios are decimals (0.25 = 25%)."""
    if val is None:
        return None
    # FMP ratios/margins are ALWAYS decimals (0.25 = 25%), and ROE/ROA can exceed
    # 1.0 (AAPL ROE 1.54 = 154%). The old `abs(val) < 1` heuristic treated any
    # decimal >= 1 as an already-scaled percent, under-reporting those 100x
    # (154% shown as 1.54%). Scale unconditionally.
    return round(val * 100, 2)


def _fmt_pct(val: Optional[float]) -> str:
    """Format as percentage string for display."""
    if val is None:
        return "—"
    return f"{val:.2f}%"


def _profitability_score(value: Optional[float], sector_median_decimal: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's metric compares to sector median.

    Args:
        value: Company metric as percentage (e.g., 25.0 for 25%)
        sector_median_decimal: Sector median as decimal from benchmarks (e.g., 0.15 for 15%)
    """
    if value is None:
        return 3  # neutral if no data

    if sector_median_decimal is None or sector_median_decimal <= 0:
        # No sector benchmark — use absolute thresholds as fallback
        if value >= 20:
            return 5
        if value >= 12:
            return 4
        if value >= 5:
            return 3
        if value >= 0:
            return 2
        return 1

    sector_pct = sector_median_decimal * 100  # convert to percentage
    if sector_pct == 0:
        return 3

    ratio = value / sector_pct
    if ratio >= 1.5:
        return 5  # 50%+ above sector
    if ratio >= 1.1:
        return 4  # 10-50% above
    if ratio >= 0.8:
        return 3  # within 20% of sector
    if ratio >= 0.5:
        return 2  # 20-50% below
    return 1      # 50%+ below sector


# Single-value sector comparisons use the mature-period picker
# (`mature_benchmark_value`) so a thin just-closed year never decides the
# comparison; the old `_get_latest_benchmark` max-year helper had no floor.


def _label_with_sector(
    label: str, val: Optional[float], sector_decimal: Optional[float],
) -> str:
    """Append sector context to a profitability metric label so the iOS
    `displayLabel` regex picks it up and renders the " *" footnote.

    `val` is the company's value in percentage form (e.g. 30.0 for 30%).
    `sector_decimal` is the sector median in decimal form (e.g. 0.15 for 15%).
    Returns the bare label when sector data is missing — iOS then renders
    no asterisk for that row, matching today's Valuation/Health behaviour.
    """
    if val is None or sector_decimal is None or sector_decimal <= 0:
        return label
    sector_pct = sector_decimal * 100
    if sector_pct == 0:
        return label
    ratio = val / sector_pct
    return f"{label} ({ratio:.2f}x sector avg {sector_pct:.1f}%)"


# ── Service ───────────────────────────────────────────────────────

class ProfitabilitySnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_profitability_snapshot_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """`get_profitability_snapshot` plus the degraded legs of the build being served.

        ``degraded`` is ``[]`` for a clean build and for a Supabase-tier hit (only clean
        builds are persisted). A Tier-1 hit and an in-flight join report the status of the
        build that produced the value they received. The all-absent build (served, never
        cached) reports ``"no_values"``. A served object with no recorded status reports
        ``["status_unknown"]`` (fail closed).
        """
        snapshot = await self.get_profitability_snapshot(ticker)
        # No await between the return above and this read: the memo describes this object.
        cache_key = f"prof_snapshot:{_validate_ticker(ticker)}"
        degraded = _degraded_of(cache_key, snapshot)
        if degraded is None:
            logger.warning(
                "Profitability snapshot status UNKNOWN for %s — reporting it degraded so "
                "it is not frozen into a long-lived cache", ticker,
            )
            degraded = [_STATUS_UNKNOWN]
        return snapshot, degraded

    async def get_profitability_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup.

        Every value it hands out has its build status noted (`_note_degraded`), which
        `get_profitability_snapshot_with_status` reads back."""
        ticker = _validate_ticker(ticker)
        cache_key = f"prof_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Profitability snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Profitability snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            # Only clean builds are persisted, so a Tier-2 row is clean by construction.
            _note_degraded(cache_key, db_cached, [])
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Profitability snapshot in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Profitability snapshot cache MISS for {ticker} — computing")
            result, degraded = await self._compute_with_status(ticker)

            # ── Degradation gate ────────────────────────────────────────────────
            # `_profitability_score(None, ...)` returns the sentinel 3 ("neutral if no
            # data"), so when every upstream leg fails all five sub-scores are 3, the
            # weighted mean is exactly 3.0, and the card renders a confident
            # "3/5 Moderate" beside five em-dashes. That is a fabricated verdict, and
            # caching it made it STICKY for the full 24h TTL in Supabase — a transient
            # FMP 429 pinning a made-up rating on a stock for a day.
            #
            # Serve it (the em-dashes are honest about the metrics) but do NOT persist,
            # so the next request retries. Same shape as the gate in
            # `stock_overview_service`, which this service was missing.
            _measured = [
                m for m in (result.metrics or [])
                if getattr(m, "value", None) not in (None, "", "—")
            ]
            if not _measured:
                logger.warning(
                    "Profitability snapshot NOT cached for %s — every metric is absent, "
                    "so the %s/5 rating is the neutral sentinel rather than a measurement. "
                    "Serving it uncached so the next request retries.",
                    ticker, getattr(result, "rating", "?"),
                )
                # Not cached, but still SERVED (to joiners too): its status must say so.
                _note_degraded(cache_key, result, [*degraded, "no_values"])
                if not future.done():
                    future.set_result(result)
                return result

            # A PARTIAL failure is subtler than the all-absent case above: a failed profile
            # leg drops the sector context (every label loses "vs sector", every score goes
            # absolute), a failed key-metrics leg shows ROE/ROA as "—" scored 3, and a
            # failed profit_power leg swaps the annual margins for TTM ratios. Serve it and
            # keep the 5-min memory tier, but do NOT persist it for 24h. Mirrors
            # valuation_snapshot_service.
            if degraded:
                logger.warning(
                    "Profitability snapshot NOT persisted for %s (degraded: %s) — will "
                    "rebuild after the in-memory TTL", ticker, ", ".join(degraded),
                )
            else:
                # Persist to Supabase in background thread
                asyncio.get_running_loop().run_in_executor(
                    None, self._upsert_supabase_cache, ticker, result,
                )

            _cache_set(cache_key, result)
            # Noted BEFORE set_result: a joiner reads it when it resumes.
            _note_degraded(cache_key, result, degraded)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, NOT an Exception, so it skips the handler
            # below and used to leave this future unresolved forever — every joiner attached
            # via `await _inflight[...]` then hung for the life of the process. Reachable
            # whenever the LEADER is a cancellable caller: a report run hitting
            # RESEARCH_PIPELINE_TIMEOUT_SECONDS, or any pre-warm task cancelled at shutdown.
            # Hand waiters a normal exception so they fail fast through their own error path.
            fail_shared_future(future, RuntimeError("in-flight fetch was cancelled"))
            raise
        except Exception as e:
            fail_shared_future(future, e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[SnapshotItemResponse]:
        """Return cached response if fresh (< 24h). Synchronous — call via to_thread."""
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Profitability")
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Profitability snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry.get("response_json")
            if not isinstance(json_data, dict):
                logger.warning(
                    "Profitability snapshot cache row for %s is not an object — rebuilding",
                    ticker,
                )
                return None
            # Copy before popping: never mutate the SDK's row.
            json_data = dict(json_data)
            version = json_data.pop(_VERSION_KEY, 1)
            if version != _SNAPSHOT_PAYLOAD_VERSION:
                logger.info(
                    "Profitability snapshot payload v%s != v%s for %s — rebuilding",
                    version, _SNAPSHOT_PAYLOAD_VERSION, ticker,
                )
                return None
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(
                "Profitability snapshot cache check failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        """Upsert to Supabase. Synchronous — call via run_in_executor."""
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Profitability",
                    "response_json": {
                        **result.model_dump(), _VERSION_KEY: _SNAPSHOT_PAYLOAD_VERSION,
                    },
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(
                "Profitability snapshot upsert failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """`_compute_with_status` without the degradation list."""
        snapshot, _degraded = await self._compute_with_status(ticker)
        return snapshot

    async def _compute_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """Reuse ProfitPowerService (Financials tab) for margins, FMP for ROE/ROA.

        Returns ``(snapshot, degraded)`` where ``degraded`` names every upstream leg that
        RAISED (a permanent `FMPNotEntitledException` excluded), ``profit_power`` when
        Profit Power served a build it marked degraded, and ``benchmarks`` when the peer
        lookup failed; `get_profitability_snapshot` refuses to write such a build to the
        24h tier.

        Ratios endpoint is fetched in parallel as a fallback: ProfitPowerService
        leaves a margin empty when the latest filing has no positive `revenue`. The
        FMP `/ratios-ttm` fields (`operatingProfitMarginTTM` etc.) fill it — but only
        through `_ttm_margin_fallback`, which refuses a non-positive TTM revenue and an
        exact-zero ratio.
        """
        from app.services.profit_power_service import get_profit_power_service

        # TTM endpoints for ratios + key_metrics so ROE/ROA/margins reflect
        # the last 4 quarters instead of an up-to-12-months-stale fiscal-year
        # snapshot. profit_power still drives margins when its annual data
        # is fresh; TTM ratios are the fallback that matches what Webull
        # and other consumer apps display.
        pp_task = get_profit_power_service().get_profit_power(ticker)
        km_task = self.fmp.get_key_metrics_ttm(ticker)
        profile_task = self.fmp.get_company_profile(ticker)
        ratios_task = self.fmp.get_ratios_ttm(ticker)

        results = await asyncio.gather(
            pp_task, km_task, profile_task, ratios_task, return_exceptions=True
        )
        degraded: List[str] = [
            name
            for name, raw in zip(("profit_power", "key_metrics_ttm", "profile", "ratios_ttm"), results)
            if isinstance(raw, Exception) and not isinstance(raw, FMPNotEntitledException)
        ]

        # Margins from profit_power (exact same as Financials tab)
        pp = results[0] if not isinstance(results[0], Exception) else None
        # Profit Power SERVES a degraded build (a failed income / cash-flow leg) rather
        # than raising, so the leg above "succeeded". Its margins may be holes this card
        # then backfills from TTM ratios — a different basis — and persisting that for
        # 24h would outlive the outage the Profit Power build itself refuses to persist.
        pp_degraded = list(getattr(pp, "degraded", None) or []) if pp is not None else []
        if pp_degraded:
            degraded.append("profit_power")
            logger.warning(
                "Profitability snapshot for %s uses a DEGRADED Profit Power build (%s)",
                ticker, ", ".join(pp_degraded),
            )
        gross_margin = None
        op_margin = None
        net_margin = None
        if pp and pp.annual:
            # Sorted oldest→newest, and Profit Power keeps a zero/negative-revenue year as
            # an all-None gap rather than dropping it — so this really is the latest
            # fiscal year (it used to be an OLDER year presented and scored as current).
            latest = pp.annual[-1]
            gross_margin = latest.gross_margin
            op_margin = latest.operating_margin
            net_margin = latest.net_margin

        # ROE/ROA from FMP key-metrics (ratios endpoint returns None for these)
        km_raw = results[1]
        km = {}
        if isinstance(km_raw, list) and km_raw:
            km = km_raw[0]
        elif isinstance(km_raw, dict):
            km = km_raw

        # Field names: /key-metrics-ttm uses TTM-suffixed names; legacy
        # bare names are kept as fallbacks in case FMP rolls the schema.
        roe = _to_pct(_first_valid(
            _safe_float(km, "returnOnEquityTTM"),
            _safe_float(km, "returnOnEquity"),
        ))
        roa_raw = _first_valid(
            _safe_float(km, "returnOnAssetsTTM"),
            _safe_float(km, "returnOnAssets"),
            _safe_float(km, "returnOnTangibleAssetsTTM"),
            _safe_float(km, "returnOnTangibleAssets"),
        )
        roa = _to_pct(roa_raw)

        # Sector for benchmark comparison
        profile_raw = results[2]
        profile = {}
        if isinstance(profile_raw, dict):
            profile = profile_raw
        elif isinstance(profile_raw, list) and profile_raw:
            profile = profile_raw[0]

        # Ratios fallback for margins the latest Profit Power year left empty (no
        # revenue that year, a bank's missing gross profit, or no Profit Power build at
        # all). Guarded by `_ttm_margin_fallback`: it must not bring a sign-flipped or a
        # fabricated-zero margin back by another route.
        ratios_raw = results[3] if not isinstance(results[3], Exception) else []
        ratios0: Dict[str, Any] = {}
        if isinstance(ratios_raw, list) and ratios_raw and isinstance(ratios_raw[0], dict):
            ratios0 = ratios_raw[0]
        elif isinstance(ratios_raw, dict):
            ratios0 = ratios_raw

        if gross_margin is None:
            gross_margin = _ttm_margin_fallback(ratios0, "grossProfitMarginTTM", "grossProfitMargin")
        if op_margin is None:
            op_margin = _ttm_margin_fallback(
                ratios0, "operatingProfitMarginTTM", "operatingProfitMargin",
            )
        if net_margin is None:
            net_margin = _ttm_margin_fallback(ratios0, "netProfitMarginTTM", "netProfitMargin")

        raw_sector = profile.get("sector", "")
        sector = _normalize_sector(raw_sector) if raw_sector else ""
        # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # CURRENT benchmark per metric: TTM row if present, else latest mature
        # annual value (fallback). {metric: value | None}.
        cur_bench: Dict[str, Optional[float]] = {}
        if sector:
            try:
                lookup = get_sector_benchmark_lookup()
                # Sync lookup (supabase-py + time.sleep retry): keep it off the loop.
                cur_bench = await asyncio.to_thread(
                    lookup.get_current_benchmark_values,
                    industry,
                    sector,
                    ["gross_margin", "operating_margin", "net_margin", "roe", "roa"],
                )
                # A FAILED lookup (swallowed DB error) answers the same all-None shape as
                # "this peer group has no rows" — but it is a transient hole, not an
                # answer: every score falls back to absolute thresholds and every label
                # loses its "vs sector". Serve it; never persist it.
                if lookup_failed(cur_bench):
                    logger.warning(
                        "Profitability snapshot: benchmark lookup FAILED for %s "
                        "(industry=%r, sector=%r) — scoring on absolute thresholds, build "
                        "marked degraded", ticker, industry, sector,
                    )
                    degraded.append("benchmarks")
            except Exception as e:
                logger.warning(
                    "Profitability snapshot: benchmark lookup raised for %s: %s: %s — "
                    "scoring on absolute thresholds, build marked degraded",
                    ticker, type(e).__name__, e,
                )
                cur_bench = {}
                degraded.append("benchmarks")

        # Score each metric against the CURRENT (TTM-first) sector/industry median
        sector_gross = cur_bench.get("gross_margin")
        sector_op = cur_bench.get("operating_margin")
        sector_net = cur_bench.get("net_margin")
        sector_roe = cur_bench.get("roe")
        sector_roa = cur_bench.get("roa")

        score_gross = _profitability_score(gross_margin, sector_gross)
        score_op = _profitability_score(op_margin, sector_op)
        score_net = _profitability_score(net_margin, sector_net)
        score_roe = _profitability_score(roe, sector_roe)
        score_roa = _profitability_score(roa, sector_roa)

        # Weighted: Gross 15% + Op 20% + Net 25% + ROE 25% + ROA 15% = 100%.
        # Net and ROE keep the largest share because they reflect bottom-line
        # efficiency and capital return — the two metrics value investors weight most.
        weighted = (
            score_gross * 0.15
            + score_op * 0.20
            + score_net * 0.25
            + score_roe * 0.25
            + score_roa * 0.15
        )
        rating = max(1, min(5, round(weighted)))

        metrics = [
            SnapshotMetricResponse(
                name=_label_with_sector("Gross Margin", gross_margin, sector_gross),
                value=_fmt_pct(gross_margin),
                metric_key="gross_margin",
                score=score_gross if gross_margin is not None else None,
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Operating Margin", op_margin, sector_op),
                value=_fmt_pct(op_margin),
                metric_key="operating_margin",
                score=score_op if op_margin is not None else None,
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Net Margin", net_margin, sector_net),
                value=_fmt_pct(net_margin),
                metric_key="net_margin",
                score=score_net if net_margin is not None else None,
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Return on Equity (ROE)", roe, sector_roe),
                value=_fmt_pct(roe),
                metric_key="roe",
                score=score_roe if roe is not None else None,
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Return on Assets (ROA)", roa, sector_roa),
                value=_fmt_pct(roa),
                metric_key="roa",
                score=score_roa if roa is not None else None,
            ),
        ]

        snapshot = SnapshotItemResponse(
            category="Profitability",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3),
        )
        return snapshot, degraded


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[ProfitabilitySnapshotService] = None


def get_profitability_snapshot_service() -> ProfitabilitySnapshotService:
    global _service
    if _service is None:
        _service = ProfitabilitySnapshotService()
    return _service
