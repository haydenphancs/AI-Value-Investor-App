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
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
    snapshot_build_time,
    with_cached_build_time,
)
from app.services.asset_class import profile_is_fund
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup, lookup_failed
from app.services.sector_benchmark_service import _normalize_sector
from app.services.valuation_snapshot_service import split_peer_cells

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


# ── Fund-shape verdict of a build (2026-10-01) ────────────────────
# A fund (VB, SPY …) has no margins, ROE or ROA, so its build measures nothing and takes the
# all-absent branch of the gate below — which, for an operating company, deliberately keeps
# the build out of BOTH tiers so the next request retries a possible outage. For a fund that
# is the permanent answer, and every overview / chat / collector call re-ran three FMP legs.
# `_compute_with_status` notes here, by identity (the `_degraded_by_key` pattern; its
# 2-tuple return is unpacked and replaced by tests), that the build's OWN profile leg
# positively said fund and that the key-metrics-TTM and ratios-TTM legs answered raw lists.
# The gate pops it. Only a True verdict is noted; a missing entry means "not a fund".
_fund_shape_by_key: Dict[str, Any] = {}


def _note_fund_shape(key: str, value: Any) -> None:
    _fund_shape_by_key.pop(key, None)
    _fund_shape_by_key[key] = value
    if len(_fund_shape_by_key) > _CACHE_MAX_ENTRIES:
        for _old in list(_fund_shape_by_key.keys())[: len(_fund_shape_by_key) - _CACHE_MAX_ENTRIES]:
            _fund_shape_by_key.pop(_old, None)


def _pop_fund_shape(key: str, value: Any) -> bool:
    """True only when the build noted for ``key`` is exactly ``value`` (identity)."""
    noted = _fund_shape_by_key.pop(key, None)
    return noted is not None and noted is value


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
# 3 (2026-10-07): margins are TTM (ratios-ttm) against the TTM peer medians — they were the
#     latest FISCAL year's Profit Power margins scored against TTM medians; Profit Power is
#     only the fallback now, shown but neither compared nor scored. ROE on negative or
#     zero shareholder equity reads "N/M", unscored (health_check_service's test); a
#     negative value never prints a negative multiple ("-14.40x sector avg"); the rating
#     is weighted over the SCORED metrics only (renormalised, like the Growth card); each
#     metric carries `peer_level` and the card `computed_at`.
_SNAPSHOT_PAYLOAD_VERSION = 3
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
    # A bool is no figure: float(True) == 1.0 would read as a D/E of 1.0 — a positive
    # negative-equity "witness" (review round 3) — or a 100% margin.
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    # NaN / ±inf ("Infinity" strings, a computed overflow upstream) would be formatted
    # as "nan%" and scored — `_profitability_score(nan, …)` falls through to 1.
    return f if math.isfinite(f) else None


def _ttm_margin(
    ratios0: Dict[str, Any], ttm_key: str, legacy_key: str,
) -> Optional[float]:
    """A TTM ratios margin, as a percentage, ONLY when it can be a real margin.

    The card's PRIMARY margin since 2026-10-07 (it was the fallback for a margin the
    latest Profit Power fiscal year left empty): the peer medians it is compared with are
    TTM, so the company side must be TTM too.
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


def _usable_median(sector_decimal: Optional[float]) -> Optional[float]:
    """A peer median (decimal) the card may print and compare against, else None.

    Finite and still positive at the one decimal of percent it prints with: a
    non-positive median anchors no "x of peers" ratio, and one that prints "0.0%" would
    sit beside a "1500.00x" multiple. Scoring treats None as "no benchmark"."""
    if sector_decimal is None or not math.isfinite(sector_decimal):
        return None
    return sector_decimal if round(sector_decimal * 100, 1) > 0 else None


def _label_with_sector(
    label: str, val: Optional[float], sector_decimal: Optional[float],
) -> str:
    """Append sector context to a profitability metric label so the iOS
    `displayLabel` regex picks it up and renders the " *" footnote.

    `val` is the company's value in percentage form (e.g. 30.0 for 30%).
    `sector_decimal` is the sector median in decimal form (e.g. 0.15 for 15%).
    Returns the bare label when either is missing — iOS then renders no
    asterisk for that row, matching today's Valuation/Health behaviour.

    A NEGATIVE value prints the median without a multiple ("sector avg 15.0%"):
    a loss-maker's "-0.83x sector avg 15.0%" (or ROE's "-14.40x") reads as a
    quantity and is not one. The words stay "sector avg" whatever the peer
    level — shipped iOS builds strip the suffix by the word "sector".
    """
    median = _usable_median(sector_decimal)
    if val is None or median is None:
        return label
    sector_pct = median * 100
    if val < 0:
        return f"{label} (sector avg {sector_pct:.1f}%)"
    ratio = val / sector_pct
    return f"{label} ({ratio:.2f}x sector avg {sector_pct:.1f}%)"


# Value printed for ROE on negative or zero shareholder equity — the same "not meaningful"
# token the Financials tab's Health Check uses (health_check_service.NOT_MEANINGFUL).
_NOT_MEANINGFUL = "N/M"


def _equity_state(de_ratio: Optional[float], bs_equity: Optional[float]) -> Optional[str]:
    """Why ROE is not meaningful — "negative" / "reported as zero" — or None when it is.

    A replica of `health_check_service`'s negative-equity test (decided once before its
    metric loop), so the Profitability card and the Financials tab never disagree about one
    company: D/E (ratios-TTM) < 0, or the latest quarterly balance sheet's
    `totalStockholdersEquity` < 0, or that equity EXACTLY 0 while D/E does not contradict it
    (FMP zero-fills unreported fields, so a 0 beside a positive D/E is "unreported").
    FMP's ROE is net income / equity, so a negative denominator flips its sign: a
    McDonald's-shaped profitable company read -216% and a Boeing-shaped loss-maker +303%.
    Pinned against the Health Check by tests/test_snapshot_cards_2026_10_07.py."""
    de_negative = de_ratio is not None and de_ratio < 0
    bs_negative = bs_equity is not None and bs_equity < 0
    bs_zero = bs_equity is not None and bs_equity == 0
    zero_equity = bs_zero and (de_ratio is None or de_ratio <= 0)
    if de_negative or bs_negative:
        return "negative"
    if zero_equity:
        return "reported as zero"
    return None


def _weighted_rating(parts: List[Tuple[Optional[int], float]]) -> Optional[float]:
    """The card's continuous 1.0–5.0 composite over the SCORED metrics, re-normalised, or
    None when nothing is scored.

    `parts` is ``[(score | None, weight)]``. An unscored metric (no value, a fiscal-year
    fallback, ROE on negative equity) used to vote the neutral sentinel 3 at full weight,
    dragging a strong card toward "average" — the Growth card dropped that rule on
    2026-09-30. With NOTHING scored (only fiscal-year fallback margins, an "N/M" ROE, or no
    value at all) there is no composite: the caller rates the card 0 (unavailable) with no
    `weighted_score`, and `_compute_with_status` flags the build "no_values", so it is never
    persisted and the report leaves the card out. Until 2026-10-07 (review round 2) this
    returned a neutral 3.0 there, and a fiscal-year-only card was persisted as "3/5" and
    labelled "In Line With Industry" in the report. A weighted mean of scores in [1, 5]
    stays in [1, 5]; a non-positive total weight is "nothing scored"."""
    scored = [(score, weight) for score, weight in parts if score is not None]
    total = sum(weight for _, weight in scored)
    if not scored or total <= 0:
        return None
    return sum(score * weight for score, weight in scored) / total


# The build-status reason for a card that rates nothing (company state, not an outage: the
# report collector's `_SNAPSHOT_COMPANY_STATE_REASONS` leaves the card out and stays
# cacheable).
_NO_VALUES = "no_values"


def _with_no_values(degraded: List[str]) -> List[str]:
    """``degraded`` with "no_values" last, exactly once."""
    return [*(d for d in degraded if d != _NO_VALUES), _NO_VALUES]


def _first_record(raw: Any) -> Dict[str, Any]:
    """The first object of an FMP list answer (or the answer itself when it is one)."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return {}


def _finite(value: Any) -> Optional[float]:
    """A finite float, else None (Profit Power margins are floats or None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


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
            # Read (and drop) the build's fund verdict at once: a replaced
            # `_compute_with_status` notes nothing, which reads as "not a fund".
            fund_shape = _pop_fund_shape(cache_key, result)

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
            # `stock_overview_service`, which this service was missing. An "N/M" ROE is a
            # reading that is never scored, so it does not make the rating a measurement.
            _measured = [
                m for m in (result.metrics or [])
                if getattr(m, "value", None) not in (None, "", "—", _NOT_MEANINGFUL)
            ]
            # `_compute_with_status` itself flags a build that SCORES nothing "no_values"
            # (fiscal-year fallback margins only); the gate below adds it for one that
            # MEASURES nothing. Either way it appears once, last.
            outage = [d for d in degraded if d != _NO_VALUES]
            if not _measured and fund_shape and not outage:
                # A fund: nothing to measure is its permanent answer, not an outage. Keep it
                # in MEMORY for the normal 5 min so a burst of views makes no FMP call — but
                # never in Supabase (a Tier-2 hit notes [], which would freeze the unrated
                # card into a report), and its status still says `no_values`.
                logger.info("[fund-shape] ticker=%s step=%s", ticker, "profitability_snapshot")
                _cache_set(cache_key, result)
                _note_degraded(cache_key, result, _with_no_values(degraded))
                if not future.done():
                    future.set_result(result)
                return result

            if not _measured:
                logger.warning(
                    "Profitability snapshot NOT cached for %s — every metric is absent, "
                    "so there is no measurement to rate (rating %s). Serving it uncached so "
                    "the next request retries.",
                    ticker, getattr(result, "rating", "?"),
                )
                # Not cached, but still SERVED (to joiners too): its status must say so.
                _note_degraded(cache_key, result, _with_no_values(degraded))
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
            return SnapshotItemResponse(**with_cached_build_time(json_data, cached_at))

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
        """TTM margins (ratios-ttm), TTM ROE / ROA (key-metrics-ttm), scored against the
        CURRENT peer medians, which are TTM-first.

        Returns ``(snapshot, degraded)`` where ``degraded`` names every upstream leg that
        RAISED and that the card depended on (a permanent `FMPNotEntitledException`
        excluded), and ``benchmarks`` when the peer lookup failed;
        `get_profitability_snapshot` refuses to write such a build to the 24h tier. Two
        legs count only when the card needed them: Profit Power (the fallback for a margin
        TTM could not give) and the balance sheet (the second negative-equity witness — it
        counts only for a shown ROE with no usable D/E, i.e. D/E missing or exactly 0; a
        non-zero D/E carries equity's sign by itself).

        Margins used to be the latest FISCAL year's Profit Power figures, scored against
        TTM peer medians — a different twelve months on each side (2026-10-07 audit). Now
        both sides are TTM; Profit Power fills only a margin `_ttm_margin` refuses or the
        ratios leg lacks, and such a fiscal-year figure is SHOWN without a peer comparison
        or a score. ROE / ROA come from /key-metrics-ttm (/ratios-ttm carries neither).
        """
        from app.services.profit_power_service import get_profit_power_service

        pp_task = get_profit_power_service().get_profit_power(ticker)
        km_task = self.fmp.get_key_metrics_ttm(ticker)
        profile_task = self.fmp.get_company_profile(ticker)
        ratios_task = self.fmp.get_ratios_ttm(ticker)
        # Latest quarterly balance sheet: the Health Check's second negative-equity witness.
        bs_task = self.fmp.get_balance_sheet(ticker, period="quarter", limit=1)

        results = await asyncio.gather(
            pp_task, km_task, profile_task, ratios_task, bs_task, return_exceptions=True
        )
        leg_names = ("profit_power", "key_metrics_ttm", "profile", "ratios_ttm", "balance_sheet")
        failed = {
            name
            for name, raw in zip(leg_names, results)
            if isinstance(raw, Exception) and not isinstance(raw, FMPNotEntitledException)
        }

        km = _first_record(results[1]) if not isinstance(results[1], Exception) else {}
        profile = _first_record(results[2]) if not isinstance(results[2], Exception) else {}
        ratios0 = _first_record(results[3]) if not isinstance(results[3], Exception) else {}
        bs = _first_record(results[4]) if not isinstance(results[4], Exception) else {}

        # ── Margins: TTM first (guarded by `_ttm_margin`) ─────────────────────
        margins: Dict[str, Optional[float]] = {
            "gross_margin": _ttm_margin(ratios0, "grossProfitMarginTTM", "grossProfitMargin"),
            "operating_margin": _ttm_margin(
                ratios0, "operatingProfitMarginTTM", "operatingProfitMargin",
            ),
            "net_margin": _ttm_margin(ratios0, "netProfitMarginTTM", "netProfitMargin"),
        }
        ttm_missing = [key for key, value in margins.items() if value is None]

        # ── Profit Power fallback: a fiscal-year figure for a margin TTM lacks ──
        pp = results[0] if not isinstance(results[0], Exception) else None
        pp_degraded = list(getattr(pp, "degraded", None) or []) if pp is not None else []
        fiscal_year_margins: set = set()
        if ttm_missing and pp is not None and getattr(pp, "annual", None):
            # Sorted oldest→newest, and Profit Power keeps a zero/negative-revenue year as
            # an all-None gap rather than dropping it — so this really is the latest
            # fiscal year (it used to be an OLDER year presented and scored as current).
            latest = pp.annual[-1]
            for key in ttm_missing:
                fy_value = _finite(getattr(latest, key, None))
                if fy_value is not None:
                    margins[key] = fy_value
                    fiscal_year_margins.add(key)
            if fiscal_year_margins:
                logger.info(
                    "Profitability snapshot for %s: %s from the latest fiscal year (no "
                    "usable TTM ratio) — shown without a peer comparison or a score",
                    ticker, ", ".join(sorted(fiscal_year_margins)),
                )
        pp_needed = bool(ttm_missing)
        if pp_degraded:
            # Profit Power SERVES a degraded build (a failed income / cash-flow leg) rather
            # than raising. Its holes matter only when this card had to fall back on it.
            logger.warning(
                "Profitability snapshot for %s: Profit Power build is DEGRADED (%s) — %s",
                ticker, ", ".join(pp_degraded),
                "the card needed its fallback margins" if pp_needed
                else "unused, every margin is TTM",
            )
        elif "profit_power" in failed and not pp_needed:
            logger.info(
                "Profitability snapshot for %s: Profit Power leg failed but unused — every "
                "margin is TTM", ticker,
            )

        # ── ROE / ROA (key-metrics-ttm) and the negative-equity test ──────────
        # Field names: /key-metrics-ttm uses TTM-suffixed names; legacy
        # bare names are kept as fallbacks in case FMP rolls the schema.
        roe = _to_pct(_first_valid(
            _safe_float(km, "returnOnEquityTTM"),
            _safe_float(km, "returnOnEquity"),
        ))
        roa = _to_pct(_first_valid(
            _safe_float(km, "returnOnAssetsTTM"),
            _safe_float(km, "returnOnAssets"),
            _safe_float(km, "returnOnTangibleAssetsTTM"),
            _safe_float(km, "returnOnTangibleAssets"),
        ))
        de_ratio = _first_valid(
            _safe_float(ratios0, "debtToEquityRatioTTM"),
            _safe_float(ratios0, "debtToEquityRatio"),
        )
        bs_equity = _safe_float(bs, "totalStockholdersEquity")
        if bs_equity == 0 and de_ratio is not None and de_ratio > 0:
            logger.warning(
                "Profitability snapshot for %s: balance-sheet totalStockholdersEquity is 0 "
                "but D/E=%r is positive — treating the 0 as unreported, ROE judged",
                ticker, de_ratio,
            )
        equity_state = _equity_state(de_ratio, bs_equity) if roe is not None else None
        if equity_state is not None:
            logger.info(
                "Profitability snapshot for %s: roe %s%% is not meaningful — shareholder "
                "equity %s (D/E=%r, equity=%r) — shown as N/M, left out of the rating",
                ticker, roe, equity_state, de_ratio, bs_equity,
            )
        # The balance sheet is the SECOND negative-equity witness. Its failure degrades the
        # build (never persisted; the report drops the card and is not shared-cached) only
        # when it could change the outcome: a shown ROE with NO usable D/E — D/E missing,
        # or exactly 0 (zero debt, or FMP's zero-fill, says nothing about equity's sign).
        # A non-zero D/E carries the sign of equity on its own (debt is never negative): a
        # negative one has already ruled ROE "N/M", a positive one is judged alone. Review
        # round 3 (R3-CARDS-4): counting the leg whenever ROE was shown made a 429 on this
        # one extra call drop the card from almost every report during a rebuild burst.
        # Accepted cost: a ratios-TTM D/E that lags the quarter equity turned negative is
        # caught only when this leg answers — the WARNING below names every build judged
        # without it (and such a build is persisted for the 24 h tier like a clean one).
        de_witness = de_ratio is not None and de_ratio != 0
        bs_needed = roe is not None and not de_witness
        if "balance_sheet" in failed and roe is not None and de_witness and de_ratio > 0:
            bs_error = results[4]
            logger.warning(
                "Profitability snapshot for %s: balance-sheet leg FAILED (%s: %s) — ROE "
                "%s%% judged on ratios-TTM D/E=%r alone, no second negative-equity "
                "witness; the balance-sheet leg is not counted as degradation",
                ticker, type(bs_error).__name__, bs_error, roe, de_ratio,
            )

        degraded: List[str] = []
        for name in leg_names:
            if name == "profit_power":
                if pp_needed and ("profit_power" in failed or pp_degraded):
                    degraded.append("profit_power")
            elif name == "balance_sheet":
                if bs_needed and "balance_sheet" in failed:
                    degraded.append("balance_sheet")
            elif name in failed:
                degraded.append(name)

        raw_sector = profile.get("sector", "")
        sector = _normalize_sector(raw_sector) if raw_sector else ""
        # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # CURRENT benchmark per metric (`get_current_benchmarks`: mature industry TTM →
        # mature sector TTM → newest complete mature annual → none), with its peer level.
        cur_bench: Dict[str, Optional[float]] = {}
        cur_levels: Dict[str, Optional[str]] = {}
        if sector:
            try:
                lookup = get_sector_benchmark_lookup()
                # Sync lookup (supabase-py + time.sleep retry): keep it off the loop.
                cells = await asyncio.to_thread(
                    lookup.get_current_benchmarks,
                    industry,
                    sector,
                    ["gross_margin", "operating_margin", "net_margin", "roe", "roa"],
                )
                cur_bench, cur_levels = split_peer_cells(cells)
                # A FAILED lookup (swallowed DB error) answers the same all-None shape as
                # "this peer group has no rows" — but it is a transient hole, not an
                # answer: every score falls back to absolute thresholds and every label
                # loses its "vs sector". Serve it; never persist it.
                if lookup_failed(cells):
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
                cur_bench, cur_levels = {}, {}
                degraded.append("benchmarks")

        def _row(label: str, key: str, value: Optional[float], weight: float):
            """(metric, (score, weight)) for one row. A fiscal-year fallback is shown but
            neither compared nor scored; ROE on negative / zero equity reads "N/M"."""
            if key == "roe" and equity_state is not None:
                return SnapshotMetricResponse(
                    name=label, value=_NOT_MEANINGFUL, metric_key=key, score=None,
                ), (None, weight)
            if value is None:
                return SnapshotMetricResponse(
                    name=label, value=_fmt_pct(None), metric_key=key, score=None,
                ), (None, weight)
            if key in fiscal_year_margins:
                return SnapshotMetricResponse(
                    name=label, value=_fmt_pct(value), metric_key=key, score=None,
                ), (None, weight)
            median = _usable_median(cur_bench.get(key))
            score = _profitability_score(value, median)
            name = _label_with_sector(label, value, median)
            level = cur_levels.get(key) if name != label else None
            return SnapshotMetricResponse(
                name=name, value=_fmt_pct(value), metric_key=key, score=score,
                peer_level=level if level in ("industry", "sector") else None,
            ), (score, weight)

        # Weights: Gross 15% + Op 20% + Net 25% + ROE 25% + ROA 15% = 100% (re-normalised
        # over the scored rows). Net and ROE keep the largest share because they reflect
        # bottom-line efficiency and capital return — the two metrics value investors
        # weight most.
        rows = [
            _row("Gross Margin", "gross_margin", margins["gross_margin"], 0.15),
            _row("Operating Margin", "operating_margin", margins["operating_margin"], 0.20),
            _row("Net Margin", "net_margin", margins["net_margin"], 0.25),
            _row("Return on Equity (ROE)", "roe", roe, 0.25),
            _row("Return on Assets (ROA)", "roa", roa, 0.15),
        ]
        metrics = [metric for metric, _ in rows]
        weighted = _weighted_rating([part for _, part in rows])
        if weighted is None:
            # Nothing scored: the values (fiscal-year margins, an "N/M" ROE, em-dashes) are
            # shown, but no verdict is made up from them — rating 0 is "unavailable" on
            # every iOS build, and "no_values" keeps the build out of the 24h tier and out
            # of the report (company state, so the report stays cacheable). INFO: the gate
            # in `get_profitability_snapshot` logs the not-persisted WARNING itself (and a
            # fund lands here on every 5-min rebuild).
            logger.info(
                "Profitability snapshot for %s: no metric is scored (fiscal-year fallback "
                "margins: %s; ROE equity state: %s) — rating unavailable, build marked "
                "no_values (served, never persisted)",
                ticker, ", ".join(sorted(fiscal_year_margins)) or "none",
                equity_state or "n/a",
            )
            rating = 0
            degraded = _with_no_values(degraded)
        else:
            rating = max(1, min(5, round(weighted)))

        snapshot = SnapshotItemResponse(
            category="Profitability",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3) if weighted is not None else None,
            computed_at=snapshot_build_time(),
        )
        # Fund verdict for the gate (see `_fund_shape_by_key`): the RAW profile leg is a dict
        # that positively says fund, and both TTM metric legs answered raw lists — a leg that
        # raised or answered an error dict is a failure, never a fund's empty answer.
        if (
            profile_is_fund(results[2])
            and isinstance(results[1], list)
            and isinstance(results[3], list)
        ):
            _note_fund_shape(f"prof_snapshot:{ticker}", snapshot)
        return snapshot, degraded


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[ProfitabilitySnapshotService] = None


def get_profitability_snapshot_service() -> ProfitabilitySnapshotService:
    global _service
    if _service is None:
        _service = ProfitabilitySnapshotService()
    return _service
