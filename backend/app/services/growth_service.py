"""
Growth service — fetches income statements from FMP, computes YoY growth
percentages for EPS & Revenue, and looks up pre-computed sector median YoY
from the sector_benchmarks table.

Matches the iOS GrowthSectionData struct.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import get_fmp_client
from app.services._earnings_common import (
    CALENDAR_UNKNOWN,
    EarningsStamp,
    stamp_is_persistable,
)
from app.utils.period_labels import (
    annual_benchmark_key,
    annual_fiscal_year,
    calendar_quarter_label,
    extract_year as _extract_year,
    quarterly_period_label,
)
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    benchmark_levels,
    flatten_benchmark_values,
    get_sector_benchmark_lookup,
    lookup_failed,
    series_peer_level,
)
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)


# Moved to sector_benchmark_lookup (shared with profit_power_service).
_series_peer_level = series_peer_level

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes

# Version stamped into every `growth_cache.response_json`. A row without it (or with an
# older one) is a MISS and is rebuilt, so a 24h row written by a build that computed a
# different number cannot outlive the deploy that fixed it.
# 2 (2026-09-30): YoY pairs rows by period-end DATE SPAN (52/53-week filers), annual
#     labels are fiscal years, the off-calendar quarterly peer line is hidden, and the
#     response carries `degraded` + `peer_group_levels`.
# 3 (2026-09-30, round 2): a quarter whose legacy benchmark key names another calendar
#     quarter (a 52/53-week Q4 closing Jan 1-7) draws no peer value, so a v2 row could
#     still show a peer value 6-12 months off.
# 4 (2026-09-30): quarterly peers come from the CALENDAR-quarter benchmark rows and join
#     on the calendar quarter of the period end; the off-calendar hide is gone.
# 5 (2026-10-07): ONE peer group per chart line (`get_benchmark_series`): a series' line
#     is its industry's medians when that industry is mature (>= 20 companies) at the
#     line's NEWEST period — its older, thinner periods included, each that industry's own
#     median — else the sector's. A period that is not fully reported is hidden, and no
#     period borrows another period's value; the unused QoQ peer value is gone.
_GROWTH_PAYLOAD_VERSION = 5
_VERSION_KEY = "payload_version"


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


# Degraded legs of the build CURRENTLY held in Tier 1, keyed like `_cache`. A degraded
# build is served from Tier 1 for 5 min but never persisted; without this memo a caller
# that hit Tier 1 (or joined the in-flight leader) could not tell it apart from a
# complete build, and the growth SNAPSHOT would write it to its own 24h tier. Updated on
# EVERY Tier-1 write, so an entry always describes the value `_cache` holds for its key.
_degraded_by_key: Dict[str, List[str]] = {}


def _note_degraded(key: str, degraded: List[str]) -> None:
    _degraded_by_key.pop(key, None)
    if not degraded:
        return
    _degraded_by_key[key] = list(degraded)
    if len(_degraded_by_key) > _CACHE_MAX_ENTRIES:
        for _old in list(_degraded_by_key.keys())[: len(_degraded_by_key) - _CACHE_MAX_ENTRIES]:
            _degraded_by_key.pop(_old, None)


def _degraded_of(key: str) -> List[str]:
    return list(_degraded_by_key.get(key) or [])


def _with_degraded(response: GrowthResponse, degraded: List[str]) -> GrowthResponse:
    """A COPY of ``response`` whose ``degraded`` is exactly ``degraded``.

    Never mutate the object held in Tier 1 or handed to in-flight joiners: it is shared
    by every concurrent caller, and the field must describe the value each one receives.
    """
    return response.model_copy(update={"degraded": list(degraded)})


# ── In-flight deduplication ───────────────────────────────────────
# One growth MISS costs TEN FMP calls. Without this, N concurrent viewers of the
# same cold ticker each fired the whole fan-out.
_inflight: Dict[str, asyncio.Future] = {}


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    """Safely extract a FINITE float from a dict.

    NaN / +-Inf coerce to None so a bad upstream value never reaches the
    (non-optional) growth-point ``value``. When the report freezes ``growth_chart``,
    a non-finite value would otherwise break serialization — Postgres JSONB rejects
    bare ``NaN`` / ``Infinity``, so the conditional report write would raise and the
    whole report would flip to ``status="failed"`` rather than degrading the point.
    A None value is skipped by the callers (``if current_val is None: continue``)."""
    val = record.get(key)
    if val is None:
        return None
    try:
        result = float(val)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _compute_yoy(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Year-over-year % change, SIGN-CORRECTED for negative bases.

    Uses abs(previous) in the denominator so the SIGN is always meaningful — an
    improvement (current > previous) reads positive and a deterioration reads
    negative, even when the base is negative (a deepening loss correctly reads
    negative instead of the +% that naive negative÷negative would give). The
    magnitude can be large across a sign change (e.g. +$0.4B → -$23.7B ≈ -5900%);
    that value is still CORRECT and is shown verbatim — the chart's YoY line uses
    a robust/compressed scale so one outlier doesn't flatten the rest. Matches
    the collector's _safe_pct_change convention. None only when an endpoint is
    missing or the base is exactly zero (undefined).
    """
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an FMP payload to a list of record dicts.

    ``FMPClient._make_request`` is typed ``-> Any`` and documented "list or dict":
    on some error shapes FMP answers 200 with a bare object. Iterating that dict
    yields its string KEYS, and the first ``rec.get(...)`` raises
    ``AttributeError: 'str' object has no attribute 'get'`` → a bare 502 for the
    whole section. Degrade to an empty series instead, loudly.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "growth: expected a list from FMP, got %s — degrading to empty series",
            type(payload).__name__,
        )
    return []


def _sort_key_date(record: Dict[str, Any]) -> str:
    """Sort key for an FMP record's period-end date.

    ``record.get("date", "")`` returns **None** when the key is present and null
    (the default only applies to a MISSING key), and ``None < str`` raises
    ``TypeError`` — 502-ing the section on one malformed upstream row. Coerce.
    """
    return record.get("date") or ""


# ``_extract_year`` is imported from app.utils.period_labels — the shared version
# is null-safe (``record.get("date") or ""``); the local copy this replaces did
# ``len(record.get("date", ""))`` and raised TypeError on a null date.


def _annual_period_label(record: Dict[str, Any]) -> str:
    """DISPLAY label for an annual row, e.g. '2025' — the FISCAL year.

    `/stable` rows carry no ``calendarYear``, so the old ``date[:4]`` label called a
    52/53-week filer's FY2025 (ended 2026-01-03: Cadence, Snap-on) "2026" — a year that
    has not ended — and gave Kellanova two bars both labelled "2022" (FY2021 ends
    2022-01-01, FY2022 ends 2022-12-31). The benchmark JOIN keeps the calendar key
    (``_match_period``), because the stored annual benchmarks are keyed that way.
    """
    return annual_fiscal_year(record) or _extract_year(record)


# A year-ago comparison row must END 50-55 weeks before the current one: 52 weeks is
# 364 days, a 53-week year 371, a calendar year 365/366. Pairing by YEAR LABEL instead
# compared Disney's Q1 FY23 (ended 2022-12-31) with Q1 FY21 (ended 2022-01-01 → the
# same "2022" key was overwritten) and printed +45% where the truth is +7.8%, and
# nulled every 53-week filer's latest annual YoY as a "2024 -> 2026 year gap".
_YOY_MIN_DAYS = 350
_YOY_MAX_DAYS = 385
_YOY_TARGET_DAYS = 364


def _period_end(record: Dict[str, Any]) -> Optional[datetime]:
    """Parsed period-END date of a statement row, or None when absent/unparsable."""
    raw = record.get("date")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fiscal_year_int(record: Dict[str, Any]) -> Optional[int]:
    try:
        return int(str(record.get("fiscalYear") or "").strip())
    except ValueError:
        return None


def _year_ago_row(
    record: Dict[str, Any],
    end: datetime,
    candidates: List[Tuple[datetime, Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """The candidate whose period ended 350-385 days before ``end`` (nearest 364 wins;
    a fiscalYear exactly one lower breaks a tie). None when no row qualifies — a gap,
    a fiscal-year-end change, or the oldest row — which the caller turns into a null
    YoY rather than a comparison against the wrong base."""
    cur_fy = _fiscal_year_int(record)
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, int]] = None
    for cand_end, cand in candidates:
        gap = (end - cand_end).days
        if not (_YOY_MIN_DAYS <= gap <= _YOY_MAX_DAYS):
            continue
        cand_fy = _fiscal_year_int(cand)
        fy_miss = 0 if (cur_fy is not None and cand_fy == cur_fy - 1) else 1
        key = (abs(gap - _YOY_TARGET_DAYS), fy_miss)
        if best_key is None or key < best_key:
            best, best_key = cand, key
    return best


def _ends_a_short_period(
    end: datetime, candidates: List[Tuple[datetime, Dict[str, Any]]],
) -> bool:
    """True when the annual row ending at ``end`` follows its predecessor by less than
    50 weeks — a fiscal-year-end TRANSITION stub (e.g. a 6-month period). A full year
    measured against a stub would print a doubled "growth"; the caller nulls that YoY.
    Same-date duplicates (gap 0) are not a predecessor."""
    return any(0 < (end - cand_end).days < _YOY_MIN_DAYS for cand_end, _ in candidates)


def _quarterly_period_label(
    record: Dict[str, Any], use_fiscal_year: bool = False
) -> str:
    """Build quarterly period label like \"Q1'21\" from FMP income statement."""
    period = record.get("period") or ""  # "Q1", "Q2", etc. (null-safe)
    # Off-calendar fiscal years (e.g. Oracle, FY ends May 31) get non-monotonic
    # quarter LABELS when the fiscal quarter is paired with the calendar year
    # (fiscal Q1/Aug shares a calendar year with the prior fiscal Q4/May).
    # use_fiscal_year pairs it with FMP's fiscalYear ("Q1'26") for DISPLAY only;
    # the sector-benchmark join stays on the calendar label (see `_match_period`).
    if use_fiscal_year and record.get("fiscalYear"):
        year = str(record.get("fiscalYear"))
    else:
        year = _extract_year(record)
    if len(year) >= 4:
        return f"{period}'{year[-2:]}"
    return f"{period}'{year}"


def _quarterly_join_key(record: Dict[str, Any]) -> str:
    """Benchmark JOIN key for one quarterly row: the CALENDAR quarter its period ends in
    (``period_labels.calendar_quarter_label``, e.g. "Q3'25"; an end on day 1-7 counts as
    the previous month, so a 52/53-week Q4 closing 2026-01-03 is Q4'25). The stored
    quarterly benchmarks are keyed the same way (period_type ``calendar_quarter``,
    migration 184), so Microsoft's fiscal Q1 (Jul-Sep) meets its peers' Jul-Sep and
    Nvidia's Q4 ending late January meets their Jan-Mar (its END date's quarter) — no
    more 6-10-month mis-joins, and no
    off-calendar hide. ``""`` (no peer value) for an undated row: there is no key to
    join on, and inventing one is how the old mis-joins began. Shared rule with
    profit_power_service so the two cards agree on which quarter a peer value is.
    """
    return calendar_quarter_label(record)


def _compute_growth_points(
    records: List[Dict[str, Any]],
    metric_key: str,
    is_quarterly: bool,
) -> List[Dict[str, Any]]:
    """
    Compute YoY growth data points from sorted income statement records.

    The year-ago base is chosen by PERIOD-END DATE SPAN (350-385 days earlier), never
    by year label — see ``_YOY_MIN_DAYS``. Quarterly additionally requires the same
    fiscal ``period`` ("Q1"). No qualifying base → the bar still charts with a null
    YoY (a gap or a fiscal-year-end change is a discontinuity, not zero growth).

    Returns list of dicts with period, value, yoy_change_percent.
    """
    records = _as_list(records)
    if not records:
        return []

    # Sort by date ascending (oldest first)
    sorted_recs = sorted(records, key=_sort_key_date)

    results = []

    if is_quarterly:
        # Same-fiscal-quarter rows with a usable period end, per period ("Q1" → rows).
        by_period: Dict[str, List[Tuple[datetime, Dict[str, Any]]]] = {}
        for rec in sorted_recs:
            end = _period_end(rec)
            if end is not None:
                by_period.setdefault(rec.get("period") or "", []).append((end, rec))

        for rec in sorted_recs:
            period = rec.get("period") or ""
            cal_year = _extract_year(rec)
            try:
                int(cal_year)
            except ValueError:
                continue  # no year at all → no label; unchanged from before

            current_val = _safe_float(rec, metric_key)
            if current_val is None:
                continue  # no chartable value for this quarter

            # A missing prior-year same quarter (FMP gap) must NOT drop the bar —
            # it has a real, chartable value. Emit it with a null YoY, mirroring
            # the annual branch's 'always emit the bar' invariant.
            end = _period_end(rec)
            prev_rec = (
                _year_ago_row(rec, end, by_period.get(period, []))
                if end is not None else None
            )
            prev_val = _safe_float(prev_rec, metric_key) if prev_rec is not None else None

            results.append({
                # period = fiscal label for DISPLAY; _match_period = the CALENDAR
                # quarter the period ends in (the benchmark join key) — see
                # `_quarterly_join_key`.
                "period": quarterly_period_label(rec, use_fiscal_year=True),
                "_match_period": _quarterly_join_key(rec),
                "value": current_val,
                "yoy_change_percent": _compute_yoy(current_val, prev_val),
                "cal_year": cal_year,
                "quarter": period,
            })
    else:
        # Annual: every year with a finite value gets a bar, the OLDEST included (a
        # single-filing listing would otherwise chart nothing). Whether a YoY is
        # meaningful is decided separately — it must NOT drop the bar: a gap year
        # still has a real, chartable value. Emit the bar, null the YoY, break the line.
        dated: List[Tuple[datetime, Dict[str, Any]]] = [
            (end, rec) for rec in sorted_recs if (end := _period_end(rec)) is not None
        ]
        for rec in sorted_recs:
            current_val = _safe_float(rec, metric_key)
            if current_val is None:
                continue  # non-finite / missing value: genuinely unchartable

            yoy: Optional[float] = None
            end = _period_end(rec)
            if end is not None:
                prev_rec = _year_ago_row(rec, end, dated)
                if prev_rec is None:
                    if any(cand_end < end for cand_end, _ in dated):
                        logger.warning(
                            "growth annual: no prior year ending 350-385d before %s for "
                            "metric=%s (gap or fiscal-year-end change); emitting bar with "
                            "null YoY", rec.get("date"), metric_key,
                        )
                    # else: oldest point — nothing to compare against
                else:
                    prev_end = _period_end(prev_rec)
                    if prev_end is not None and _ends_a_short_period(prev_end, dated):
                        logger.warning(
                            "growth annual: prior period ending %s is a short transition "
                            "period for metric=%s; null YoY for %s rather than a full "
                            "year vs a stub", prev_rec.get("date"), metric_key,
                            rec.get("date"),
                        )
                    else:
                        yoy = _compute_yoy(current_val, _safe_float(prev_rec, metric_key))

            results.append({
                # period = FISCAL year for display; _match_period = the benchmark join
                # key (year of the period end minus 7 days — see annual_benchmark_key;
                # shared with profit_power_service so the two cards agree).
                "period": _annual_period_label(rec),
                "_match_period": annual_benchmark_key(rec) or _extract_year(rec),
                "value": current_val,
                "yoy_change_percent": yoy,
                "cal_year": _extract_year(rec),
                "quarter": None,
            })

    return results


# ── Service ───────────────────────────────────────────────────────

class GrowthService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_growth(self, ticker: str) -> GrowthResponse:
        """Main entry point — two-tier cache-aside with in-flight dedup.

        Tier 1: in-memory dict (5 min) · Tier 2: Supabase ``growth_cache``
        (24h, invalidated early by the next earnings date). Mirrors
        profit_power_service, the reference template.
        """
        response, _degraded = await self.get_growth_with_status(ticker)
        return response

    async def get_growth_with_status(self, ticker: str) -> Tuple[GrowthResponse, List[str]]:
        """`get_growth` plus the legs that failed in the build being served — the FMP
        statement legs, and ``"benchmarks"`` when a peer-benchmark read failed.

        ``degraded`` is empty for a Tier-2 hit (only complete builds are persisted) and
        for a complete build. A Tier-1 hit or an in-flight join reports the degradation of
        the build it received, so a caller with its own long-lived cache (the growth
        snapshot) can refuse to persist what this service itself refused to persist.

        The same list rides on the response itself (``GrowthResponse.degraded``), set on
        EVERY path from the memo for the value actually handed out — never trusted from
        a cached object — so the endpoint's body tells iOS not to keep a partial build.
        """
        # UNIQUE(ticker) in growth_cache is case-SENSITIVE, so "aapl" and "AAPL"
        # would occupy two rows and cost two FMP fan-outs (and the
        # profit_power_cache lookup below would miss). Every current caller
        # already uppercases; normalise here so that stays true.
        ticker = ticker.upper().strip()
        cache_key = f"growth:{ticker}"

        # ── Tier 1: in-memory ──
        cached = _cache_get(cache_key)
        if cached is not None:
            degraded_now = _degraded_of(cache_key)
            return _with_degraded(cached, degraded_now), degraded_now

        # ── Tier 2: Supabase (in a thread — the SDK is sync) ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Growth Supabase HIT for {ticker}")
            # Only complete builds are persisted; force the field so a stray value in a
            # hand-edited row can never mark (or un-mark) what is served.
            db_cached = _with_degraded(db_cached, [])
            _cache_set(cache_key, db_cached)
            _note_degraded(cache_key, [])
            return db_cached, []

        # ── In-flight dedup ──
        if cache_key in _inflight:
            logger.info(f"Growth in-flight JOIN for {ticker}")
            # SHIELDED. Awaiting the shared future directly means a joiner that gives up
            # (client disconnect, request timeout) CANCELS THE FUTURE ITSELF — and the leader's
            # `set_result` then raises InvalidStateError, 500ing a request whose data loaded
            # perfectly, while every other joiner gets a CancelledError. Verified: an
            # unshielded joiner cancellation makes the leader's set_result raise; a shielded
            # one leaves it untouched. Matches profit_power_service.py.
            joined = await asyncio.shield(_inflight[cache_key])
            # The leader notes its degradation BEFORE resolving the future.
            degraded_now = _degraded_of(cache_key)
            return _with_degraded(joined, degraded_now), degraded_now

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Growth cache MISS for {ticker} — fetching from FMP")
            result, degraded = await self._build_growth(ticker)
            degraded = list(degraded)
            result = _with_degraded(result, degraded)
            next_earnings = await asyncio.to_thread(
                self._next_earnings_date_safe, ticker
            )

            # Best-effort write-through (never blocks the response) — but ONLY when the build
            # was complete. One transient FMP failure otherwise pins a hole in the chart for
            # 24 hours and bakes it into every paid report generated in that window.
            if degraded:
                logger.warning(
                    "Growth NOT persisted for %s (degraded: %s) — will rebuild after the "
                    "5-min in-memory TTL",
                    ticker, ", ".join(degraded),
                )
            elif next_earnings is CALENDAR_UNKNOWN:
                logger.warning(
                    "Growth NOT persisted for %s (next-earnings lookup failed — no "
                    "report-day bound for the 24h row); served from memory", ticker,
                )
            else:
                loop.run_in_executor(
                    None, self._upsert_supabase_cache_safe, ticker, result, next_earnings
                )

            _cache_set(cache_key, result)
            # A joiner reads the memo when it resumes after set_result below.
            _note_degraded(cache_key, degraded)
            # Guarded: a joiner that was cancelled before we shielded the join could have
            # already resolved this future, and a bare set_result would raise InvalidStateError.
            if not future.done():
                future.set_result(result)
            return result, list(degraded)
        except Exception as e:
            fail_shared_future(future, e)
            raise
        finally:
            # CancelledError is a BaseException, so the `except Exception` above does NOT
            # resolve the future when this coroutine is cancelled (a client disconnect
            # mid-fetch, or a pre-warm task cancelled at shutdown). Any joiner awaiting it
            # would then hang forever.
            #
            # Hand waiters a NORMAL exception rather than `future.cancel()`: a joiner awaiting
            # a cancelled future receives CancelledError, which inside an async handler reads
            # as "this request was cancelled" and propagates as task cancellation instead of
            # failing cleanly through the caller's own error path. Matches
            # profit_power_service.py and holders_service.py.
            if not future.done():
                future.set_exception(RuntimeError("in-flight growth fetch was cancelled"))
                future.exception()   # mark retrieved; silences the GC warning when unjoined
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[GrowthResponse]:
        """Return the cached response if fresh (<24h and before next earnings).
        Synchronous — call via asyncio.to_thread()."""
        try:
            row = (
                self.supabase.table("growth_cache")
                .select("response_json, cached_at, next_earnings_date")
                .eq("ticker", ticker)
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
            if cached_at.tzinfo is None:  # defensive: column is timestamptz
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Growth Supabase cache STALE (age={age}) for {ticker}")
                return None

            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(
                        f"Growth Supabase cache STALE (past earnings {next_earnings}) "
                        f"for {ticker}"
                    )
                    return None

            json_data = entry.get("response_json")
            if not isinstance(json_data, dict):
                logger.warning("Growth Supabase cache row for %s is not an object — rebuilding",
                               ticker)
                return None
            json_data = dict(json_data)
            version = json_data.pop(_VERSION_KEY, None)
            if version != _GROWTH_PAYLOAD_VERSION:
                # Written by a build that paired YoY by year label (see the version note):
                # rebuild rather than serve a number this build would not compute.
                logger.info(
                    "Growth Supabase cache STALE for %s (%s=%r, want %d) — rebuilding",
                    ticker, _VERSION_KEY, version, _GROWTH_PAYLOAD_VERSION,
                )
                return None
            return GrowthResponse(**json_data)
        except Exception as e:
            logger.warning(f"Growth Supabase cache check failed for {ticker}: {e}")
            return None

    def _next_earnings_date_safe(self, ticker: str) -> EarningsStamp:
        """Reuse the profit-power cache's next-earnings date when present.

        Growth doesn't fetch the earnings calendar itself (it would be an 11th
        FMP call); the sibling cache for the same ticker already stores it, so
        read it opportunistically. None just means "expire on the 24h TTL".

        CALENDAR_UNKNOWN when the lookup itself RAISED: the getter then serves the
        build from memory and does not persist it. An ABSENT profit_power row is still
        None (the documented 24h fallback) — so a cold ticker during an earnings-calendar
        outage, when profit_power stops writing, still gets a 24h row with no report-day
        bound (accepted residual, P20).

        The date must be TODAY or later. profit_power writes the next PENDING
        report date (``_earnings_common.next_pending_earnings_date``), which is
        TODAY on report day: copying it makes this row stale for the rest of
        that day (``today >= next_earnings`` in ``_check_supabase_cache``), so
        Growth rebuilds after each 5-minute in-memory window and picks up the
        just-reported quarter — the report-day trade-off the other five
        Financials services already make. Rejecting it (the old ``>`` rule)
        fell back to the plain 24h TTL and kept the pre-release morning build
        until the next day.

        A date BEFORE today is still rejected: that profit_power row is old (its
        date only refreshes when someone hits the profit-power path), and a
        growth row carrying it would be born stale — the Supabase tier would
        never hit and every 5-minute window would re-run the 10-call FMP
        fan-out for as long as the stale date stayed in profit_power_cache.
        """
        try:
            row = (
                self.supabase.table("profit_power_cache")
                .select("next_earnings_date")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if row.data:
                candidate = row.data[0].get("next_earnings_date")
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if candidate and candidate >= today_str:
                    return candidate
                if candidate:
                    logger.info(
                        "Growth %s: ignoring stale next_earnings_date %s from "
                        "profit_power_cache (before today) — using the 24h TTL",
                        ticker, candidate,
                    )
        except Exception as e:
            logger.warning(
                "[growth-next-earnings-unavailable] ticker=%s step=next_earnings_lookup: "
                "%s: %s — this build is served from memory, NOT persisted",
                ticker, type(e).__name__, e,
            )
            return CALENDAR_UNKNOWN
        return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: GrowthResponse,
        next_earnings: EarningsStamp,
    ) -> None:
        """Write-through to the Supabase tier. Best-effort: logged, never fatal."""
        if not stamp_is_persistable(next_earnings):
            # Belt-and-braces: the getter already refuses a CALENDAR_UNKNOWN build.
            logger.warning(
                "Growth upsert REFUSED for %s — next_earnings_date %r is not a date "
                "(a failed next-earnings lookup is never persisted)", ticker, next_earnings,
            )
            return
        try:
            self.supabase.table("growth_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": {
                        **result.model_dump(), _VERSION_KEY: _GROWTH_PAYLOAD_VERSION,
                    },
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Growth Supabase upsert failed for {ticker}: {e}")

    async def _build_growth(self, ticker: str) -> Tuple[GrowthResponse, list]:
        """Fetch income + cash flow statements, compute YoY growth, look up sector benchmarks."""

        # Phase 1: parallel fetch — profile + income + cash flow (5 FMP calls)
        (
            profile,
            annual_income,
            quarterly_income,
            annual_cashflow,
            quarterly_cashflow,
        ) = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, period="annual", limit=16),
            self.fmp.get_income_statement(ticker, period="quarter", limit=80),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=16),
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=80),
            return_exceptions=True,
        )

        # Handle failures gracefully
        # Legs replaced by an empty default. Such a build still renders — a missing
        # quarterly cash-flow leg simply yields no quarterly FCF series — but persisting it
        # to the 24h Supabase tier pins that hole for a day and freezes it into paid reports.
        degraded: list[str] = []

        if isinstance(profile, Exception):
            logger.warning(f"Profile fetch failed for {ticker}: {profile}")
            profile = {}
            degraded.append("profile")
        if isinstance(annual_income, Exception):
            logger.error(f"Annual income fetch failed for {ticker}: {annual_income}")
            annual_income = []
            degraded.append("annual_income")
        if isinstance(quarterly_income, Exception):
            logger.error(f"Quarterly income fetch failed for {ticker}: {quarterly_income}")
            quarterly_income = []
            degraded.append("quarterly_income")
        if isinstance(annual_cashflow, Exception):
            logger.error(f"Annual cash flow fetch failed for {ticker}: {annual_cashflow}")
            annual_cashflow = []
            degraded.append("annual_cashflow")
        if isinstance(quarterly_cashflow, Exception):
            logger.error(f"Quarterly cash flow fetch failed for {ticker}: {quarterly_cashflow}")
            quarterly_cashflow = []
            degraded.append("quarterly_cashflow")

        # Phase 2: get sector from profile (normalize to canonical name for benchmark lookup)
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)
        # Industry-relative benchmarks: prefer the company's INDUSTRY peer group,
        # fall back to its sector per (metric, period). FMP industry names match
        # the benchmark table directly, so no normalization is needed.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # Phase 3: compute target ticker's YoY growth for all 5 metrics
        # EPS & Revenue (from income statement)
        eps_annual_points = _compute_growth_points(annual_income, "epsDiluted", is_quarterly=False)
        eps_quarterly_points = _compute_growth_points(quarterly_income, "epsDiluted", is_quarterly=True)
        rev_annual_points = _compute_growth_points(annual_income, "revenue", is_quarterly=False)
        rev_quarterly_points = _compute_growth_points(quarterly_income, "revenue", is_quarterly=True)
        # Net Income & Operating Income (from income statement)
        ni_annual_points = _compute_growth_points(annual_income, "netIncome", is_quarterly=False)
        ni_quarterly_points = _compute_growth_points(quarterly_income, "netIncome", is_quarterly=True)
        op_annual_points = _compute_growth_points(annual_income, "operatingIncome", is_quarterly=False)
        op_quarterly_points = _compute_growth_points(quarterly_income, "operatingIncome", is_quarterly=True)
        # Free Cash Flow (from cash flow statement)
        fcf_annual_points = _compute_growth_points(annual_cashflow, "freeCashFlow", is_quarterly=False)
        fcf_quarterly_points = _compute_growth_points(quarterly_cashflow, "freeCashFlow", is_quarterly=True)

        # Phase 4: look up pre-computed sector benchmarks (fast DB lookup, cached)
        all_yoy_metrics = [
            "eps_yoy", "revenue_yoy", "net_income_yoy",
            "operating_income_yoy", "fcf_yoy",
        ]

        benchmarks_annual: Dict[str, Dict[str, float]] = {}
        benchmarks_quarterly: Dict[str, Dict[str, float]] = {}
        # Peer level of the value each benchmark cell SHOWS, per metric and period
        # label — feeds `peer_group_levels` for the legend wording.
        levels_annual: Dict[str, Dict[str, Optional[str]]] = {}
        levels_quarterly: Dict[str, Dict[str, Optional[str]]] = {}
        # Which benchmark reads FAILED (a DB error inside the lookup), as opposed to
        # answering "no rows for this peer group". The lookup degrades a failure to the
        # same empty shape and does not cache it, so the next build recovers — this
        # build must therefore not be persisted (a peer-less chart, and a snapshot
        # re-scored on absolute heuristics, pinned for 24h).
        failed_lookups: List[str] = []
        if sector:
            lookup = get_sector_benchmark_lookup()
            # The lookup is SYNCHRONOUS (sync supabase-py + a time.sleep retry), and a
            # cold key costs two paginated PostgREST reads. Run each on a worker thread
            # so a cache miss cannot stall the single uvicorn worker's event loop.
            # Sequential on purpose: the keys differ, and a thread per call would only
            # add concurrent use of the shared sync client.
            rich_annual = await asyncio.to_thread(
                lookup.get_benchmark_series, industry, sector, all_yoy_metrics, "annual",
            )
            # Quarterly peers are the CALENDAR-quarter rows (migration 184): never the
            # legacy fiscal-keyed 'quarterly' rows, which pooled peer quarters 3-10
            # months apart for every off-calendar company.
            rich_quarterly = await asyncio.to_thread(
                lookup.get_benchmark_series, industry, sector, all_yoy_metrics,
                CALENDAR_QUARTER_PERIOD_TYPE,
            )
            if lookup_failed(rich_annual):
                failed_lookups.append("annual")
            if lookup_failed(rich_quarterly):
                failed_lookups.append("quarterly")
            # One peer group per line (`get_benchmark_series`): every point of a metric's
            # line is the same group's median for its own period, and the lookup already
            # hid incomplete periods, so a just-closed year draws NO peer value (never an
            # earlier year's). The levels are read from the RICH cells: the flatten
            # discards them.
            levels_annual = benchmark_levels(rich_annual)
            levels_quarterly = benchmark_levels(rich_quarterly)
            benchmarks_annual = flatten_benchmark_values(rich_annual)
            benchmarks_quarterly = flatten_benchmark_values(rich_quarterly)

        if failed_lookups:
            logger.warning(
                "Growth %s: benchmark lookup FAILED (%s) — peer line omitted; build marked "
                "degraded (benchmarks) and NOT persisted", ticker, "+".join(failed_lookups),
            )
            degraded.append("benchmarks")

        # Phase 5: assemble response with sector averages matched by period label
        def _to_schemas(
            points: List[Dict],
            metric_name: str,
            benchmarks: Dict[str, Dict[str, float]],
        ) -> List[GrowthDataPointSchema]:
            metric_benchmarks = benchmarks.get(metric_name, {})

            def _peer(cells: Dict[str, float], p: Dict[str, Any]) -> Optional[float]:
                # Match on the join key (_match_period), not the fiscal display label,
                # for both annual and quarterly points. "" = this point draws no peer
                # value (a quarter whose legacy key names another calendar quarter).
                key = p.get("_match_period", p["period"])
                return cells.get(key) if key else None

            return [
                GrowthDataPointSchema(
                    period=p["period"],
                    value=p["value"],
                    yoy_change_percent=p["yoy_change_percent"],
                    sector_average_yoy=_peer(metric_benchmarks, p),
                    # sector_average_qoq stays None: no client reads a QoQ peer value,
                    # and its lookup's failure used to degrade the whole build.
                )
                for p in points
            ]

        # (wire key, points, yoy metric, flattened benchmarks, levels) per series.
        series_specs = [
            ("eps_annual", eps_annual_points, "eps_yoy", benchmarks_annual, levels_annual),
            ("eps_quarterly", eps_quarterly_points, "eps_yoy", benchmarks_quarterly, levels_quarterly),
            ("revenue_annual", rev_annual_points, "revenue_yoy", benchmarks_annual, levels_annual),
            ("revenue_quarterly", rev_quarterly_points, "revenue_yoy", benchmarks_quarterly, levels_quarterly),
            ("net_income_annual", ni_annual_points, "net_income_yoy", benchmarks_annual, levels_annual),
            ("net_income_quarterly", ni_quarterly_points, "net_income_yoy", benchmarks_quarterly, levels_quarterly),
            ("operating_profit_annual", op_annual_points, "operating_income_yoy", benchmarks_annual, levels_annual),
            ("operating_profit_quarterly", op_quarterly_points, "operating_income_yoy", benchmarks_quarterly, levels_quarterly),
            ("fcf_annual", fcf_annual_points, "fcf_yoy", benchmarks_annual, levels_annual),
            ("fcf_quarterly", fcf_quarterly_points, "fcf_yoy", benchmarks_quarterly, levels_quarterly),
        ]
        peer_group_levels: Dict[str, str] = {}
        for key, pts, metric, bench, lvls in series_specs:
            level = _series_peer_level(pts, bench.get(metric, {}), lvls.get(metric, {}))
            if level is not None:
                peer_group_levels[key] = level

        response = GrowthResponse(
            symbol=ticker,
            eps_annual=_to_schemas(eps_annual_points, "eps_yoy", benchmarks_annual),
            eps_quarterly=_to_schemas(eps_quarterly_points, "eps_yoy", benchmarks_quarterly),
            revenue_annual=_to_schemas(rev_annual_points, "revenue_yoy", benchmarks_annual),
            revenue_quarterly=_to_schemas(rev_quarterly_points, "revenue_yoy", benchmarks_quarterly),
            net_income_annual=_to_schemas(ni_annual_points, "net_income_yoy", benchmarks_annual),
            net_income_quarterly=_to_schemas(
                ni_quarterly_points, "net_income_yoy", benchmarks_quarterly,
            ),
            operating_profit_annual=_to_schemas(op_annual_points, "operating_income_yoy", benchmarks_annual),
            operating_profit_quarterly=_to_schemas(
                op_quarterly_points, "operating_income_yoy", benchmarks_quarterly,
            ),
            free_cash_flow_annual=_to_schemas(fcf_annual_points, "fcf_yoy", benchmarks_annual),
            free_cash_flow_quarterly=_to_schemas(
                fcf_quarterly_points, "fcf_yoy", benchmarks_quarterly,
            ),
            degraded=list(degraded),
            peer_group_levels=peer_group_levels,
        )
        return response, degraded


# ── Singleton ─────────────────────────────────────────────────────

_growth_service: Optional[GrowthService] = None


def get_growth_service() -> GrowthService:
    global _growth_service
    if _growth_service is None:
        _growth_service = GrowthService()
    return _growth_service
