"""
Growth Snapshot service — computes sector-relative growth ratings by
reusing the existing GrowthService (Financials tab) to ensure data consistency.

Extracts the most recent annual YoY growth for Revenue, EPS, FCF, and
Operating Income, along with their sector benchmarks, then scores 1-5.

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
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
    snapshot_build_time,
    with_cached_build_time,
)
from app.services.financials_metric_gate import resolve_payment_network, score_excluded_rows

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
# ticker_data_cache and a paid report, so `get_growth_snapshot_with_status` must report
# the status of the EXACT object handed out — on a Tier-1 hit and an in-flight join as much
# as on the build itself. Same role as growth_service's `_degraded_by_key`, but each entry
# holds `(value, degraded)` and a status is reported only for that very object (identity),
# so an entry can never describe a different build than the one being served.
_degraded_by_key: Dict[str, Tuple[Any, List[str]]] = {}
# Larger than the Tier-1 cap: entries are written in lockstep with `_cache_set`, so the
# memo can only lose an entry Tier 1 still holds after twice as many writes.
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

# Version stamped into this card's `snapshot_cache.response_json` (same key as the
# valuation/ownership cards on the shared table). A row without it, or with an older one,
# is a MISS and is rebuilt — the 24h tier must not outlive a change to the figures.
# 2 (2026-09-30): the latest annual YoY only (no backward scan to an older year), the
#     rating weighted over present metrics, and YoY paired by period-end date span.
# 3 (2026-10-07): scores read Growth's peer values, which now come from ONE peer group per
#     chart line (the industry when it is mature at the line's newest period, else the
#     sector), are absent for a period not yet fully reported, and never borrow another
#     period's value — a v2 row could score against a held-back or partial-year median.
# 4 (2026-10-09, owner decision NET-4; Growth is v6): a listed non-lender member of
#     "Financial - Credit Services" (`financials_metric_gate.NON_LENDER_MEMBERS`) gets no
#     peer value from Growth, so its rows score on the absolute bands. A v3 row of MA
#     scored +15.7% operating-income growth 2/5 against the lenders' +24.6%.
# 5 (2026-10-21; Growth is v7): the Financial Services peer rule (owner decision
#     2026-10-20). A v4 row of a small FS industry scored against the sector's growth.
# 6 (2026-11-05): `score_excluded_rows`: a bank's, an insurer's or a lender's FCF growth reads
#     "N/M" and does not count. A v5 row counted it.
# 7 (2026-11-13, owner decision "Name the periods"): each row with a value carries
#     `numeric_value`, `peer_median` and `period_label` ("FY2026") for the report Growth
#     drill-down's card line, and a NaN/inf YoY or median reads as absent. A v6 row has none.
_SNAPSHOT_PAYLOAD_VERSION = 7
_VERSION_KEY = "_schema_v"

# GrowthService legs this card does NOT read: it scores only the newest ANNUAL point of
# each series, so a failed quarterly statement leaves its figures exactly as a clean
# build's. Inheriting them refused (and never persisted) a correctly measured card and
# dropped it from the report (round 3, P24). "benchmarks" is NOT here: the service does
# not say whether the annual or the quarterly peer read failed, so it stays blocking.
_QUARTERLY_ONLY_LEGS = frozenset({"quarterly_income", "quarterly_cashflow"})

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _fmt_growth(val: Optional[float]) -> str:
    """Format growth as +X.X% or -X.X% string."""
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _finite(val: Any) -> Optional[float]:
    """``float(val)`` when that is a real number, else None (bool, NaN/±inf, junk).

    A NaN YoY printed "nan%" and a NaN median scored the row 1/5 (every comparison with
    NaN is False, so `_growth_score` fell to its last band); neither may reach the row.
    A negative zero (growth_service's ``round(-0.001, 2)``) becomes 0.0: it printed
    "+-0.0%" (``-0.0 >= 0`` is True, then ``f"{-0.0:.1f}"`` is "-0.0")."""
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f + 0.0 if math.isfinite(f) else None


_ANNUAL_LABEL_RE = re.compile(r"[0-9]{4}")


def _fiscal_year_label(period: Any) -> Optional[str]:
    """``"FY2026"`` for an annual point's display period ``"2026"`` (growth_service's
    `_annual_period_label`, the FISCAL year) — the label the report's Growth drill-down
    names the card's figure by (owner decision 2026-11-13, "Name the periods"). None for
    anything that is not a 4-digit year: an unnamed period is never guessed."""
    if not isinstance(period, str):
        return None
    text = period.strip()
    return f"FY{text}" if _ANNUAL_LABEL_RE.fullmatch(text) else None


def _growth_score(value: Optional[float], sector_median: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's growth compares to sector median.
    Both value and sector_median are in percentage points (e.g., 12.5 = 12.5%).

    Blends a sector-RELATIVE read with an ABSOLUTE-growth floor: a metric growing
    strongly in absolute terms is never scored "weak" merely because a thin /
    contaminated latest-period sector benchmark is even higher. Without this, a
    65%-grower whose semiconductor peers' FY benchmark reads an (uncredible) 79%
    YoY scored a 1/5 ("below sector") — see the persona-scoring validation. The
    relative read still drives the UPSIDE (true outperformers reach 5).
    """
    if value is None:
        return 3  # neutral if no data

    # Absolute floor: strong absolute growth can't read as weak regardless of peers.
    abs_floor = 4 if value >= 40 else 3 if value >= 20 else 1

    if sector_median is None:
        # Absolute-only when no sector benchmark.
        if value > 20:
            rel = 5
        elif value > 10:
            rel = 4
        elif value > 0:
            rel = 3
        elif value > -10:
            rel = 2
        else:
            rel = 1
        return max(rel, abs_floor)

    diff = value - sector_median  # percentage points above/below sector
    if diff > 10:
        rel = 5  # 10pp+ above sector
    elif diff > 3:
        rel = 4  # 3-10pp above
    elif diff > -3:
        rel = 3  # within 3pp of sector
    elif diff > -10:
        rel = 2  # 3-10pp below
    else:
        rel = 1  # 10pp+ below sector
    return max(rel, abs_floor)


# ── Service ───────────────────────────────────────────────────────

class GrowthSnapshotService:
    def __init__(self):
        self.supabase = get_supabase()

    async def get_growth_snapshot_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """`get_growth_snapshot` plus the degraded legs of the build being served.

        ``degraded`` is ``[]`` for a clean build and for a Supabase-tier hit (only clean
        builds are persisted). A Tier-1 hit and an in-flight join report the status of the
        build that produced the value they received. A served object with no recorded
        status reports ``["status_unknown"]`` — fail closed, so a caller with its own
        long-lived cache (the report collector) never freezes a build of unknown quality.
        """
        snapshot = await self.get_growth_snapshot(ticker)
        # No await between the return above and this read: the memo describes this object.
        cache_key = f"growth_snapshot:{_validate_ticker(ticker)}"
        degraded = _degraded_of(cache_key, snapshot)
        if degraded is None:
            logger.warning(
                "Growth snapshot status UNKNOWN for %s — reporting it degraded so it is "
                "not frozen into a long-lived cache", ticker,
            )
            degraded = [_STATUS_UNKNOWN]
        return snapshot, degraded

    async def get_growth_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup.

        Every value it hands out has its build status noted (`_note_degraded`), which
        `get_growth_snapshot_with_status` reads back."""
        ticker = _validate_ticker(ticker)
        cache_key = f"growth_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Growth snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Growth snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            # Only clean builds are persisted, so a Tier-2 row is clean by construction.
            _note_degraded(cache_key, db_cached, [])
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Growth snapshot in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Growth snapshot cache MISS for {ticker} — computing")
            result, degraded = await self._compute_with_status(ticker)

            # NEVER persist a degraded build. GrowthService turns a failed FMP leg into an
            # empty series and refuses to write that build to its own 24h tier; this card
            # would render the hole as "—" scored with the neutral sentinel 3 and then pin
            # that made-up rating in snapshot_cache for a day (the Overview card and the
            # report's snap_growth both read it). Serve it, keep the 5-min memory tier to
            # absorb the retry storm, and let the next miss rebuild. Mirrors
            # valuation_snapshot_service.
            if degraded:
                logger.warning(
                    "Growth snapshot NOT persisted for %s (degraded: %s) — will rebuild "
                    "after the in-memory TTL", ticker, ", ".join(degraded),
                )
            else:
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
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Growth")
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
                logger.info(f"Growth snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry.get("response_json")
            if not isinstance(json_data, dict):
                logger.warning(
                    "Growth snapshot cache row for %s is not an object — rebuilding", ticker,
                )
                return None
            json_data = dict(json_data)
            version = json_data.pop(_VERSION_KEY, 1)
            if version != _SNAPSHOT_PAYLOAD_VERSION:
                logger.info(
                    "Growth snapshot payload v%s != v%s for %s — rebuilding",
                    version, _SNAPSHOT_PAYLOAD_VERSION, ticker,
                )
                return None
            # A row from before `computed_at` existed: its build time is its cached_at.
            return SnapshotItemResponse(**with_cached_build_time(json_data, cached_at))

        except Exception as e:
            logger.warning(f"Growth snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Growth",
                    "response_json": {
                        **result.model_dump(), _VERSION_KEY: _SNAPSHOT_PAYLOAD_VERSION,
                    },
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Growth snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """`_compute_with_status` without the degradation list."""
        snapshot, _degraded = await self._compute_with_status(ticker)
        return snapshot

    async def _compute_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """Reuse GrowthService (Financials tab) to get exact same data the user sees.

        Returns ``(snapshot, degraded)``. ``degraded`` carries GrowthService's own list of
        failed FMP legs for the build it served — minus the quarterly-only legs this
        card never reads (`_QUARTERLY_ONLY_LEGS`) — plus ``"no_values"`` when none of the
        four metrics has a value (every score is then the neutral sentinel 3).
        `get_growth_snapshot` refuses to persist a build with a non-empty list.
        """
        from app.services.growth_service import get_growth_service

        growth, upstream_degraded = await get_growth_service().get_growth_with_status(ticker)
        upstream = [str(r) for r in upstream_degraded]
        degraded: List[str] = [r for r in upstream if r not in _QUARTERLY_ONLY_LEGS]
        if len(degraded) != len(upstream):
            logger.info(
                "[growth-snapshot-quarterly-only] ticker=%s step=snapshot: ignoring the "
                "quarterly-only failed leg(s) %s — this card reads the annual series only",
                ticker, ", ".join(r for r in upstream if r in _QUARTERLY_ONLY_LEGS),
            )

        # The LATEST annual point's YoY + sector benchmark for each metric — and only
        # that point. GrowthResponse lists are sorted oldest→newest. This used to walk
        # back to the newest NON-NULL YoY and present it, with no period attached, as the
        # current "Revenue Growth (YoY)": a latest year whose YoY is n/m (a 0 base, a gap,
        # a loss→profit flip) showed LAST year's growth, scored it, and pinned it for 24h,
        # while GrowthChartSheet (which deliberately refuses that backfill) said "n/m".
        # A null latest YoY now renders "—" with no score, like any absent metric.
        # A non-finite YoY or median is treated as absent (`_finite`).
        def _latest(points, series: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
            """Return (yoy_change_percent, sector_average_yoy, "FY…" label) of the newest
            point — all three of the SAME point, so the drill-down's line names the year
            the figure and its median belong to. The median and the label only beside a
            value: a "—" row compares nothing."""
            if not points:
                return None, None, None
            newest = points[-1]
            yoy = _finite(getattr(newest, "yoy_change_percent", None))
            if yoy is None:
                return None, None, None
            label = _fiscal_year_label(getattr(newest, "period", None))
            if label is None:
                logger.warning(
                    "[growth-snapshot-period-label] ticker=%s series=%s step=snapshot: newest "
                    "annual point's period %r is not a fiscal year — the row carries its YoY "
                    "with no period label", ticker, series, getattr(newest, "period", None),
                )
            return yoy, _finite(getattr(newest, "sector_average_yoy", None)), label

        rev_growth, sector_rev, period_rev = _latest(growth.revenue_annual, "revenue_annual")
        eps_growth, sector_eps, period_eps = _latest(growth.eps_annual, "eps_annual")
        fcf_growth, sector_fcf, period_fcf = _latest(
            growth.free_cash_flow_annual, "free_cash_flow_annual")
        op_growth, sector_op, period_op = _latest(
            growth.operating_profit_annual, "operating_profit_annual")

        # Score each metric against sector median
        score_rev = _growth_score(rev_growth, sector_rev)
        score_eps = _growth_score(eps_growth, sector_eps)
        score_fcf = _growth_score(fcf_growth, sector_fcf)
        score_op = _growth_score(op_growth, sector_op)

        # `score_excluded_rows`: cash flow means nothing for a bank's or an insurer's growth.
        # `GrowthResponse.industry` is None on a failed profile leg, which is a degraded build.
        industry = getattr(growth, "industry", None)
        excluded_rows = score_excluded_rows(
            industry,
            network=resolve_payment_network(ticker, industry, None, source="growth_snapshot"),
            ticker=ticker,
        )
        fcf_excluded = "fcf_growth" in excluded_rows
        if fcf_excluded:
            fcf_growth = None
            # The one row that leg feeds is a fixed N/M: its loss costs this card nothing.
            degraded = [leg for leg in degraded if leg != "annual_cashflow"]

        if all(v is None for v in (rev_growth, eps_growth, fcf_growth, op_growth)):
            degraded.append("no_values")

        # Weighted average: Revenue 30%, EPS 30%, FCF 20%, Op Income 20% — over the
        # metrics that HAVE a value, re-normalised. An absent metric used to vote the
        # neutral sentinel 3 at full weight, dragging a 5/5 grower whose FCF YoY is n/m
        # toward "average". Neutral 3.0 only when none has a value (that build is also
        # flagged "no_values" above and never persisted). Stays inside [1, 5]: a weighted
        # mean of scores that are each in [1, 5].
        weighted_parts = [
            (score, weight)
            for value, score, weight in (
                (rev_growth, score_rev, 0.30),
                (eps_growth, score_eps, 0.30),
                (fcf_growth, score_fcf, 0.20),
                (op_growth, score_op, 0.20),
            )
            if value is not None
        ]
        if weighted_parts:
            weighted = (
                sum(score * weight for score, weight in weighted_parts)
                / sum(weight for _, weight in weighted_parts)
            )
        else:
            weighted = 3.0
        rating = max(1, min(5, round(weighted)))

        def _row(name: str, key: str, value: Optional[float], median: Optional[float],
                 period: Optional[str], score: int) -> SnapshotMetricResponse:
            """One row. A row with a value also carries it as a number with its own year's
            peer median and that year's label (owner decision 2026-11-13, the drill-down's
            "FY2026 34.33% · Industry …" line); a "—" row carries none of them."""
            if value is None:
                return SnapshotMetricResponse(name=name, value="—", metric_key=key, score=None)
            return SnapshotMetricResponse(
                name=name, value=_fmt_growth(value), metric_key=key, score=score,
                numeric_value=value, peer_median=median, period_label=period,
            )

        metrics = [
            _row("Revenue Growth (YoY)", "revenue_growth", rev_growth, sector_rev, period_rev,
                 score_rev),
            _row("EPS Growth", "eps_growth", eps_growth, sector_eps, period_eps, score_eps),
            # `score_excluded_rows`: a fixed "N/M" with no number, median or period.
            SnapshotMetricResponse(name="Free Cash Flow Growth (YoY)", value="N/M",
                                   metric_key="fcf_growth", score=None)
            if fcf_excluded else
            _row("Free Cash Flow Growth (YoY)", "fcf_growth", fcf_growth, sector_fcf,
                 period_fcf, score_fcf),
            _row("Operating Income Growth", "operating_income_growth", op_growth, sector_op,
                 period_op, score_op),
        ]

        snapshot = SnapshotItemResponse(
            category="Growth",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3),
            computed_at=snapshot_build_time(),
        )
        return snapshot, degraded


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[GrowthSnapshotService] = None


def get_growth_snapshot_service() -> GrowthSnapshotService:
    global _service
    if _service is None:
        _service = GrowthSnapshotService()
    return _service
